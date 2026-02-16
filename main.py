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

class ScrapeRequest(BaseModel):
    url: str = Field(..., description="The URL of the page to scrape")
    wait_ms: Optional[int] = Field(2000, description="Additional wait time after page load")
    format: Optional[str] = Field("markdown", description="Output format: 'markdown' or 'json'")

@app.get("/", tags=["General"])
async def home():
    return {"status": "online", "message": "Firecrawl-lite is ready. Visit /docs for Swagger UI."}

def extract_structured_data(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, 'html.parser')
    results = {
        "metadata": {
            "title": soup.title.string if soup.title else "",
            "description": "",
            "og_image": ""
        },
        "json_ld": []
    }
    for meta in soup.find_all("meta"):
        name = meta.get("name") or meta.get("property")
        if name == "description":
            results["metadata"]["description"] = meta.get("content")
        elif name == "og:image":
            results["metadata"]["og_image"] = meta.get("content")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            results["json_ld"].append(json.loads(script.string))
        except: pass
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
            
            if request.format == "json":
                data = extract_structured_data(html_content)
            else:
                downloaded = trafilatura.extract(
                    html_content, 
                    output_format='markdown', 
                    include_links=True,
                    include_images=False,
                    deduplicate=True
                )
                data = {
                    "markdown": downloaded or "Could not extract content.", 
                    "title": await page.title(),
                    "status_code": response.status if response else None
                }
            
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
