import asyncio
import json
import time
import random
from functools import wraps
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from enum import Enum
import trafilatura
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth
from bs4 import BeautifulSoup
import urllib.parse
from cachetools import TTLCache
import hashlib
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Caching
scraping_cache = TTLCache(maxsize=100, ttl=300)  # 5 min cache for scrape
search_cache = TTLCache(maxsize=50, ttl=60)     # 1 min cache for search

# Browser pool management
class BrowserPool:
    def __init__(self, max_browsers: int = 3):
        self.max_browsers = max_browsers
        self._pool: asyncio.Queue = asyncio.Queue(maxsize=max_browsers)
        self._playwright = None
        self._initialized = False
        self._lock = asyncio.Lock()
    
    async def initialize(self):
        async with self._lock:
            if not self._initialized:
                self._playwright = await async_playwright().start()
                for _ in range(self.max_browsers):
                    browser = await self._playwright.chromium.launch(headless=True)
                    await self._pool.put(browser)
                self._initialized = True
                logger.info(f"Browser pool initialized with {self.max_browsers} browsers")
    
    @asynccontextmanager
    async def acquire(self):
        if not self._initialized:
            await self.initialize()
        browser = await self._pool.get()
        try:
            yield browser
        finally:
            await self._pool.put(browser)
    
    async def close(self):
        async with self._lock:
            if self._initialized:
                while not self._pool.empty():
                    try:
                        browser = self._pool.get_nowait()
                        await browser.close()
                    except asyncio.QueueEmpty:
                        break
                await self._playwright.stop()
                self._initialized = False

browser_pool = BrowserPool(max_browsers=3)

# User agent rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]

def get_random_ua():
    return random.choice(USER_AGENTS)

# Startup and shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await browser_pool.initialize()
    yield
    # Shutdown
    await browser_pool.close()

app = FastAPI(
    title="Firecrawl-lite API",
    description="Powerful scraping and Search API. Supports Markdown, JSON extraction, and Google Search.",
    version="2.0.0",
    lifespan=lifespan
)

# Rate limiting simple implementation
rate_limit_data = {}
RATE_LIMIT = 30  # requests per minute
RATE_WINDOW = 60  # seconds

def check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    if client_ip not in rate_limit_data:
        rate_limit_data[client_ip] = []
    
    # Remove old requests outside the window
    rate_limit_data[client_ip] = [t for t in rate_limit_data[client_ip] if now - t < RATE_WINDOW]
    
    if len(rate_limit_data[client_ip]) >= RATE_LIMIT:
        return False
    
    rate_limit_data[client_ip].append(now)
    return True

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded. Max 30 requests per minute."}
        )
    return await call_next(request)

class ScrapeFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    DETAILED = "detailed"

class ScrapeRequest(BaseModel):
    url: str = Field(..., description="The URL of the page to scrape")
    wait_ms: Optional[int] = Field(2000, description="Additional wait time after page load")
    format: ScrapeFormat = Field(ScrapeFormat.MARKDOWN, description="Output format: 'markdown', 'json', or 'detailed'")
    include_links: bool = Field(True, description="Include all links in response")
    include_images: bool = Field(True, description="Include all images in response")
    extract_tables: bool = Field(True, description="Extract tables in structured format")

@app.get("/", tags=["General"])
async def home():
    return {"status": "online", "message": "Firecrawl-lite is ready. Visit /docs for Swagger UI."}

def extract_structured_markdown(html: str, base_url: str = "") -> Dict[str, Any]:
    """Extract content as structured markdown blocks"""
    soup = BeautifulSoup(html, 'html.parser')
    
    def html_to_markdown(element) -> str:
        """Convert HTML element to markdown string"""
        if not element:
            return ""
        
        # Get text content preserving some formatting
        text = element.get_text(strip=True)
        
        if element.name == 'h1':
            return f"# {text}\n\n"
        elif element.name == 'h2':
            return f"## {text}\n\n"
        elif element.name == 'h3':
            return f"### {text}\n\n"
        elif element.name == 'h4':
            return f"#### {text}\n\n"
        elif element.name == 'h5':
            return f"##### {text}\n\n"
        elif element.name == 'h6':
            return f"###### {text}\n\n"
        elif element.name == 'p':
            # Convert inline links
            md_text = ""
            for child in element.children:
                if child.name == 'a' and child.get('href'):
                    href = child.get('href')
                    link_text = child.get_text(strip=True)
                    md_text += f"[{link_text}]({href})"
                elif child.name == 'strong' or child.name == 'b':
                    md_text += f"**{child.get_text(strip=True)}**"
                elif child.name == 'em' or child.name == 'i':
                    md_text += f"*{child.get_text(strip=True)}*"
                elif child.name == 'code':
                    md_text += f"`{child.get_text(strip=True)}`"
                elif child.name == 'br':
                    md_text += "\n"
                else:
                    md_text += str(child) if not hasattr(child, 'get_text') else child.get_text()
            return f"{md_text}\n\n" if md_text else ""
        elif element.name == 'ul':
            md = ""
            for li in element.find_all('li', recursive=False):
                text = li.get_text(strip=True)
                md += f"- {text}\n"
            return md + "\n"
        elif element.name == 'ol':
            md = ""
            for i, li in enumerate(element.find_all('li', recursive=False), 1):
                text = li.get_text(strip=True)
                md += f"{i}. {text}\n"
            return md + "\n"
        elif element.name == 'blockquote':
            text = element.get_text(strip=True)
            return f"> {text}\n\n"
        elif element.name == 'pre':
            code_elem = element.find('code')
            lang = ""
            if code_elem and code_elem.get('class'):
                for cls in code_elem.get('class'):
                    if cls.startswith('language-'):
                        lang = cls.replace('language-', '')
                        break
            content = code_elem.get_text() if code_elem else element.get_text()
            return f"```{lang}\n{content}\n```\n\n"
        elif element.name == 'code':
            if element.parent.name != 'pre':
                return f"`{text}`"
        elif element.name == 'hr':
            return "---\n\n"
        elif element.name == 'table':
            return html_table_to_markdown(element)
        return ""
    
    def html_table_to_markdown(table) -> str:
        """Convert HTML table to markdown table"""
        md = []
        rows = []
        
        # Get headers
        headers = []
        thead = table.find('thead')
        if thead:
            headers = [th.get_text(strip=True) for th in thead.find_all(['th', 'td'])]
        
        # Get all rows
        for tr in table.find_all('tr'):
            row = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
            if row:
                rows.append(row)
        
        # If no headers but has rows, use first row as header
        if not headers and rows:
            headers = rows.pop(0)
        
        if not headers:
            return ""
        
        # Build markdown table
        md.append("| " + " | ".join(headers) + " |")
        md.append("|" + "|".join(["---"] * len(headers)) + "|")
        
        for row in rows:
            # Pad row if shorter than headers
            while len(row) < len(headers):
                row.append("")
            md.append("| " + " | ".join(row[:len(headers)]) + " |")
        
        return "\n".join(md) + "\n\n"
    
    def find_content_elements(soup) -> List:
        """Find main content elements"""
        # Try to find main content area
        main_selectors = ['article', 'main', '[role="main"]', '.content', '.article', '.post-content', '.entry-content']
        container = None
        
        for selector in main_selectors:
            container = soup.select_one(selector)
            if container:
                break
        
        if not container:
            container = soup.body or soup
        
        # Get all content elements
        content_tags = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'blockquote', 'pre', 'hr', 'table']
        elements = []
        
        for tag in content_tags:
            for elem in container.find_all(tag):
                # Skip empty elements
                if elem.get_text(strip=True):
                    elements.append(elem)
        
        return elements
    
    # Build structured markdown
    title = soup.title.string if soup.title else ""
    content_elements = find_content_elements(soup)
    
    # Build blocks
    blocks = []
    full_markdown = ""
    
    for elem in content_elements:
        md = html_to_markdown(elem)
        if md.strip():
            block = {
                "type": elem.name,
                "markdown": md.strip(),
                "text": elem.get_text(strip=True)
            }
            
            # Add extra metadata based on type
            if elem.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                block["level"] = int(elem.name[1])
            elif elem.name == 'a':
                block["href"] = elem.get('href', '')
            elif elem.name == 'img':
                block["src"] = elem.get('src', '')
                block["alt"] = elem.get('alt', '')
            
            blocks.append(block)
            full_markdown += md
    
    # Also extract links and images from the whole document
    all_links = []
    all_images = []
    
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        if href.startswith('http'):
            all_links.append({
                "text": a.get_text(strip=True),
                "href": href
            })
    
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if src:
            all_images.append({
                "src": src,
                "alt": img.get('alt', '')
            })
    
    return {
        "title": title,
        "full_markdown": full_markdown.strip(),
        "blocks": blocks,
        "stats": {
            "total_blocks": len(blocks),
            "headings": sum(1 for b in blocks if b["type"].startswith('h')),
            "paragraphs": sum(1 for b in blocks if b["type"] == 'p'),
            "lists": sum(1 for b in blocks if b["type"] in ['ul', 'ol']),
            "code_blocks": sum(1 for b in blocks if b["type"] == 'pre'),
            "tables": sum(1 for b in blocks if b["type"] == 'table')
        },
        "links": all_links[:50],  # Limit to 50 links
        "images": all_images[:20]  # Limit to 20 images
    }

def extract_structured_data(html: str, base_url: str = "") -> Dict[str, Any]:
    """Extract detailed structured content from HTML"""
    soup = BeautifulSoup(html, 'html.parser')
    
    def get_text_safe(element) -> str:
        """Safely extract clean text from element"""
        if not element:
            return ""
        return element.get_text(strip=True)
    
    def extract_links(element) -> List[Dict]:
        """Extract all links from element with context"""
        links = []
        for a in element.find_all('a', href=True):
            href = a.get('href', '')
            if href.startswith('#'):
                continue
            if href.startswith('/'):
                href = base_url.rstrip('/') + href
            links.append({
                "text": get_text_safe(a),
                "href": href,
                "title": a.get('title', '')
            })
        return links
    
    def extract_images(element) -> List[Dict]:
        """Extract all images from element"""
        images = []
        for img in element.find_all('img'):
            src = img.get('src', '')
            if src.startswith('/'):
                src = base_url.rstrip('/') + src
            images.append({
                "src": src,
                "alt": img.get('alt', ''),
                "title": img.get('title', ''),
                "width": img.get('width', ''),
                "height": img.get('height', '')
            })
        return images
    
    def extract_table(table) -> Dict:
        """Extract table with headers and rows"""
        headers = []
        th_row = table.find('thead')
        if th_row:
            headers = [get_text_safe(th) for th in th_row.find_all(['th', 'td'])]
        
        rows = []
        for tr in table.find_all('tr'):
            row_data = [get_text_safe(td) for td in tr.find_all(['td', 'th'])]
            if row_data:
                rows.append(row_data)
        
        return {"headers": headers, "rows": rows}
    
    def extract_list(lst) -> List[Dict]:
        """Extract list items with nested structure"""
        items = []
        for li in lst.find_all('li', recursive=False):
            item_data = {"text": get_text_safe(li), "children": []}
            
            # Check for nested lists
            for sub_list in li.find_all(['ul', 'ol'], recursive=False):
                nested_type = 'bullet' if sub_list.name == 'ul' else 'numbered'
                item_data["children"].append({
                    "type": nested_type,
                    "items": extract_list(sub_list)
                })
            
            items.append(item_data)
        return items
    
    def extract_content_structure(element, depth: int = 0, max_depth: int = 10) -> List[Dict]:
        """Recursively extract content structure preserving hierarchy"""
        if depth > max_depth:
            return []
        
        structure = []
        content_tags = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'blockquote', 'code', 'pre']
        
        for child in element.children:
            if child.name is None:  # NavigableString
                continue
            
            item = {"type": child.name, "depth": depth}
            
            if child.name == 'h1':
                item.update({"text": get_text_safe(child), "level": 1})
            elif child.name == 'h2':
                item.update({"text": get_text_safe(child), "level": 2})
            elif child.name == 'h3':
                item.update({"text": get_text_safe(child), "level": 3})
            elif child.name == 'h4':
                item.update({"text": get_text_safe(child), "level": 4})
            elif child.name == 'h5':
                item.update({"text": get_text_safe(child), "level": 5})
            elif child.name == 'h6':
                item.update({"text": get_text_safe(child), "level": 6})
            elif child.name == 'p':
                item.update({"text": get_text_safe(child)})
                # Check for links in paragraph
                links = extract_links(child)
                if links:
                    item["links"] = links
            elif child.name == 'ul':
                item.update({"type": "bullet_list", "items": extract_list(child)})
            elif child.name == 'ol':
                item.update({"type": "numbered_list", "items": extract_list(child)})
            elif child.name == 'table':
                item.update(extract_table(child))
            elif child.name == 'blockquote':
                item.update({"text": get_text_safe(child), "source": child.get('cite', '')})
            elif child.name == 'pre':
                code_elem = child.find('code')
                item.update({
                    "content": get_text_safe(child),
                    "language": code_elem.get('class', [''])[0].replace('language-', '') if code_elem else ""
                })
            elif child.name == 'code':
                if child.parent.name != 'pre':
                    item.update({"text": get_text_safe(child), "inline": True})
            elif child.name == 'section':
                item["type"] = "section"
                item["heading"] = get_text_safe(child.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']))
                item["content"] = extract_content_structure(child, depth + 1, max_depth)
            elif child.name == 'article':
                item["type"] = "article"
                item["heading"] = get_text_safe(child.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']))
                item["content"] = extract_content_structure(child, depth + 1, max_depth)
            elif child.name == 'div':
                # Check if div has semantic meaning
                item_id = child.get('id', '')
                item_class = ' '.join(child.get('class', []))
                
                if any(keyword in item_id.lower() or keyword in item_class.lower() for keyword in ['content', 'article', 'post', 'body', 'main', 'text']):
                    item["type"] = "content_block"
                    item["identifier"] = item_id or item_class
                    item["content"] = extract_content_structure(child, depth + 1, max_depth)
                    structure.append(item)
                    continue
                else:
                    # Skip generic divs but check children
                    structure.extend(extract_content_structure(child, depth + 1, max_depth))
                    continue
            else:
                # For other tags, extract children
                structure.extend(extract_content_structure(child, depth + 1, max_depth))
                continue
            
            structure.append(item)
        
        return structure
    
    def find_main_content(soup) -> BeautifulSoup:
        """Find main content area"""
        # Try semantic tags first
        for selector in ['article', 'main', '[role="main"]']:
            elem = soup.select_one(selector)
            if elem:
                return elem
        
        # Try common content containers
        for selector in ['.content', '.article', '.post-content', '.entry-content', '#content', '#main']:
            elem = soup.select_one(selector)
            if elem:
                return elem
        
        # Fallback to body
        return soup.body or soup
    
    # Main extraction
    results = {
        "metadata": {
            "title": soup.title.string if soup.title else "",
            "description": "",
            "og_image": "",
            "og_title": "",
            "canonical": "",
            "lang": soup.html.get('lang', '') if soup.html else ""
        },
        "header_structure": {
            "h1": [h.get_text(strip=True) for h in soup.find_all('h1')],
            "h2": [h.get_text(strip=True) for h in soup.find_all('h2')],
            "h3": [h.get_text(strip=True) for h in soup.find_all('h3')],
            "h4": [h.get_text(strip=True) for h in soup.find_all('h4')],
            "h5": [h.get_text(strip=True) for h in soup.find_all('h5')],
            "h6": [h.get_text(strip=True) for h in soup.find_all('h6')]
        },
        "content": [],
        "links": [],
        "images": [],
        "tables": [],
        "lists": {
            "bullet": [],
            "numbered": []
        },
        "json_ld": []
    }
    
    # Extract meta tags
    for meta in soup.find_all("meta"):
        name = meta.get("name") or meta.get("property")
        if name == "description":
            results["metadata"]["description"] = meta.get("content", "")
        elif name == "og:image":
            results["metadata"]["og_image"] = meta.get("content", "")
        elif name == "og:title":
            results["metadata"]["og_title"] = meta.get("content", "")
        elif name == "og:description":
            results["metadata"]["og_description"] = meta.get("content", "")
    
    # Canonical URL
    canonical = soup.find('link', {'rel': 'canonical'})
    if canonical:
        results["metadata"]["canonical"] = canonical.get('href', '')
    
    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            if script.string:
                results["json_ld"].append(json.loads(script.string))
        except: pass
    
    # Find main content area
    main_content = find_main_content(soup)
    
    # Extract content structure
    results["content"] = extract_content_structure(main_content)
    
    # Extract all links
    results["links"] = extract_links(soup.body) if soup.body else []
    
    # Extract all images
    results["images"] = extract_images(soup.body) if soup.body else []
    
    # Extract tables
    for table in soup.find_all('table'):
        results["tables"].append(extract_table(table))
    
    # Extract lists
    for ul in soup.find_all('ul'):
        results["lists"]["bullet"].append(extract_list(ul))
    for ol in soup.find_all('ol'):
        results["lists"]["numbered"].append(extract_list(ol))
    
    # Clean up empty lists
    results["lists"]["bullet"] = [l for l in results["lists"]["bullet"] if l]
    results["lists"]["numbered"] = [l for l in results["lists"]["numbered"] if l]
    
    return results

@app.post("/scrape", tags=["Scraping"])
async def scrape(request: ScrapeRequest):
    # Cache key
    cache_key = hashlib.md5(f"{request.url}:{request.format}".encode()).hexdigest()
    
    # Check cache
    if cache_key in scraping_cache:
        logger.info(f"Cache hit for {request.url}")
        return {"url": request.url, "success": True, "data": scraping_cache[cache_key], "cached": True}
    
    async with browser_pool.acquire() as browser:
        stealth = Stealth()
        context = await browser.new_context(
            user_agent=get_random_ua(),
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        
        try:
            # Block unnecessary resources for faster loading
            await page.route("**/*", lambda route, request: 
                route.abort() if request.resource_type in ["image", "stylesheet", "font", "media"] 
                else route.continue_())
            
            response = await page.goto(
                request.url, 
                wait_until="domcontentloaded",  # Faster than networkidle
                timeout=30000
            )
            
            if request.wait_ms:
                await asyncio.sleep(request.wait_ms / 1000)
            
            # Wait for key elements to be ready
            await page.wait_for_load_state("networkidle", timeout=10000)
            
            html_content = await page.content()
            
            if request.format == ScrapeFormat.JSON or request.format == "json":
                data = extract_structured_data(html_content, request.url)
            elif request.format == ScrapeFormat.DETAILED or request.format == "detailed":
                # Detailed format with comprehensive extraction
                structured = extract_structured_data(html_content, request.url)
                markdown_content = trafilatura.extract(
                    html_content, 
                    output_format='markdown', 
                    include_links=True,
                    include_images=request.include_images,
                    deduplicate=True
                ) or "Could not extract content."
                
                data = {
                    "markdown": markdown_content,
                    "structured": structured,
                    "title": await page.title(),
                    "status_code": response.status if response else None,
                    "html_stats": {
                        "total_headings": sum(len(structured.get("header_structure", {}).get(f"h{i}", [])) for i in range(1, 7)),
                        "total_links": len(structured.get("links", [])),
                        "total_images": len(structured.get("images", [])),
                        "total_tables": len(structured.get("tables", [])),
                        "content_blocks": len(structured.get("content", []))
                    }
                }
            else:  # markdown - now with structure
                md_data = extract_structured_markdown(html_content, request.url)
                md_data["status_code"] = response.status if response else None
                data = md_data
            
            # Filter data based on request flags (for json/detailed)
            if request.format in [ScrapeFormat.JSON, ScrapeFormat.DETAILED, "json", "detailed"]:
                if not request.include_links and isinstance(data, dict):
                    data.pop("links", None)
                    if "structured" in data:
                        data["structured"].pop("links", None)
                if not request.include_images and isinstance(data, dict):
                    data.pop("images", None)
                    if "structured" in data:
                        data["structured"].pop("images", None)
                if not request.extract_tables and isinstance(data, dict):
                    data.pop("tables", None)
                    if "structured" in data:
                        data["structured"].pop("tables", None)
            
            # Store in cache
            scraping_cache[cache_key] = data
            
            return {"url": request.url, "success": True, "data": data}
            
        except Exception as e:
            logger.error(f"Scraping error for {request.url}: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            await context.close()

@app.get("/search", tags=["Search"])
async def search(
    q: str = Query(..., description="Search query"),
    num_results: int = Query(10, ge=1, le=20, description="Number of results (1-20)")
):
    """Google Search without API Key - with caching and improved extraction"""
    
    # Cache key
    cache_key = hashlib.md5(f"{q}:{num_results}".encode()).hexdigest()
    
    # Check cache
    if cache_key in search_cache:
        logger.info(f"Cache hit for search: {q}")
        cached = search_cache[cache_key]
        return {"query": q, "count": len(cached), "results": cached, "cached": True}
    
    async with browser_pool.acquire() as browser:
        stealth = Stealth()
        context = await browser.new_context(
            user_agent=get_random_ua(),
            locale="en-US",
            timezone_id="America/New_York"
        )
        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        
        try:
            # hl=en helps with consistent parsing
            search_url = f"https://www.google.com/search?q={urllib.parse.quote(q)}&hl=en&num={num_results + 5}"
            
            response = await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            
            # Handle possible Cookie Consent
            consent_buttons = [
                ("button", "Accept all"),
                ("button", "I agree"),
                ("button", "Agree"),
                ("div", "Accept all"),
            ]
            
            for role, name in consent_buttons:
                try:
                    btn = page.locator(f"[{role}]").filter(has_text=name).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        await page.wait_for_load_state("networkidle", timeout=5000)
                        break
                except:
                    continue

            # Wait for results with multiple strategies
            selectors = [
                "div[data-hveid]",
                "div.g",
                "h3",
                "[data-ved]",
            ]
            
            for selector in selectors:
                try:
                    await page.wait_for_selector(selector, timeout=5000)
                    break
                except:
                    continue
            
            html = await page.content()
            soup = BeautifulSoup(html, 'html.parser')
            
            results = []
            seen_urls = set()
            
            # Multiple extraction strategies
            
            # Strategy 1: Standard Google results
            for result in soup.find_all('div', {'data-hveid': True}):
                title_elem = result.find('h3')
                link_elem = result.find('a', href=True)
                snippet_elem = result.find(['div', 'span'], class_=lambda x: x and ('VwiC3b' in x or 's3v94d' in x))
                
                if title_elem and link_elem:
                    url = link_elem.get('href')
                    if url and url.startswith('http') and url not in seen_urls:
                        seen_urls.add(url)
                        results.append({
                            "title": title_elem.get_text(strip=True),
                            "link": url,
                            "snippet": snippet_elem.get_text(strip=True) if snippet_elem else ""
                        })
            
            # Strategy 2: Alternative selectors
            if len(results) < num_results:
                for result in soup.find_all('div', class_='g'):
                    if len(results) >= num_results:
                        break
                    
                    title_elem = result.find('h3')
                    link_elem = result.find('a', href=True)
                    snippet_elem = result.find('div', class_=['VwiC3b', 'st'])
                    
                    if title_elem and link_elem:
                        url = link_elem.get('href')
                        if url and url.startswith('http') and url not in seen_urls:
                            seen_urls.add(url)
                            results.append({
                                "title": title_elem.get_text(strip=True),
                                "link": url,
                                "snippet": snippet_elem.get_text(strip=True) if snippet_elem else ""
                            })
            
            # Strategy 3: Knowledge panel / direct answers
            if not results:
                answer_selectors = [
                    '.DNoAnf',  # Knowledge panel
                    '.LGOEob',  # Featured snippet
                    '.IZ6rdc',  # Direct answer
                    '.HwtpBd',  # Knowledge graph
                    '[data-attrid="wa:/description"]',
                ]
                
                for selector in answer_selectors:
                    answer = soup.select_one(selector)
                    if answer:
                        results.append({
                            "title": "Direct Answer / Knowledge Panel",
                            "link": search_url,
                            "snippet": answer.get_text(strip=True)
                        })
                        break
            
            # Limit results
            results = results[:num_results]
            
            # Cache the results
            search_cache[cache_key] = results
            
            return {
                "query": q,
                "count": len(results),
                "results": results,
                "cached": False
            }
            
        except Exception as e:
            logger.error(f"Search error for '{q}': {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            await context.close()

@app.get("/exchange", tags=["Finance"])
async def exchange(
    base: str = Query("USD", description="Base currency"),
    to: str = Query("KRW", description="Target currency")
):
    """Get realtime exchange rate from Investing.com"""
    
    pair = f"{base.upper()}/{to.upper()}"
    cache_key = f"exchange:{pair}"
    
    # Check cache
    if cache_key in scraping_cache:
        cached = scraping_cache[cache_key]
        return {**cached, "cached": True}
    
    pair_map = {
        "USD/KRW": "https://kr.investing.com/currencies/usd-krw",
        "EUR/KRW": "https://kr.investing.com/currencies/eur-krw",
        "SGD/KRW": "https://kr.investing.com/currencies/sgd-krw",
        "JPY/KRW": "https://kr.investing.com/currencies/jpy-krw",
        "CNY/KRW": "https://kr.investing.com/currencies/cny-krw",
        "GBP/KRW": "https://kr.investing.com/currencies/gbp-krw",
        "EUR/USD": "https://www.investing.com/currencies/eur-usd",
        "GBP/USD": "https://www.investing.com/currencies/gbp-usd",
        "USD/JPY": "https://www.investing.com/currencies/usd-jpy",
    }
    
    url = pair_map.get(pair)
    
    if not url:
        # Fallback to search
        return await search(f"{pair} exchange rate")

    async with browser_pool.acquire() as browser:
        stealth = Stealth()
        context = await browser.new_context(user_agent=get_random_ua())
        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            # Multiple selector strategies
            price_selectors = [
                '[data-test="instrument-price-last"]',
                '.last-price-value',
                '.text-5xl',
                '[class*="price"]',
            ]
            
            price = None
            for selector in price_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=3000)
                    price_text = await page.inner_text(selector)
                    # Extract numeric value
                    import re
                    price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(",", ""))
                    if price_match:
                        price = float(price_match.group().replace(",", ""))
                        break
                except:
                    continue
            
            if price is None:
                raise Exception("Could not extract price")
            
            result = {
                "pair": pair,
                "rate": price,
                "source": "Investing.com",
                "timestamp": time.time(),
                "cached": False
            }
            
            # Cache the result
            scraping_cache[cache_key] = result
            
            return result
            
        except Exception as e:
            logger.error(f"Exchange rate error for {pair}: {str(e)}")
            # Fallback to search
            return await search(f"{pair} realtime rate")
        finally:
            await context.close()

@app.get("/health", tags=["System"])
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "browser_pool_initialized": browser_pool._initialized,
        "cache_sizes": {
            "scrape": len(scraping_cache),
            "search": len(search_cache)
        }
    }

@app.delete("/cache", tags=["System"])
async def clear_cache():
    """Clear all caches"""
    scraping_cache.clear()
    search_cache.clear()
    return {"status": "ok", "message": "All caches cleared"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9001)
