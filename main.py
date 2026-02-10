import asyncio
import json
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import trafilatura
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from bs4 import BeautifulSoup
import urllib.parse

app = FastAPI(
    title="Firecrawl-lite API",
    description="Powerful scraping and Search API. Supports Markdown, JSON extraction, and Google Search.",
    version="1.2.0"
)

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
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        stealth = Stealth()
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        try:
            await page.goto(request.url, wait_until="networkidle", timeout=60000)
            if request.wait_ms: await asyncio.sleep(request.wait_ms / 1000)
            html_content = await page.content()
            if request.format == "json":
                data = extract_structured_data(html_content)
            else:
                downloaded = trafilatura.extract(html_content, output_format='markdown', include_links=True)
                data = {"markdown": downloaded or "Could not extract content.", "title": await page.title()}
            return {"url": request.url, "success": True, "data": data}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            await browser.close()

@app.get("/search", tags=["Search"])
async def search(q: str = Query(..., description="Search query")):
    """Google Search without API Key"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        stealth = Stealth()
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        try:
            # hl=en helps with consistent parsing
            search_url = f"https://www.google.com/search?q={urllib.parse.quote(q)}&hl=en"
            await page.goto(search_url, wait_until="networkidle", timeout=30000)
            
            # Handle possible Cookie Consent
            buttons = ["Accept all", "I agree", "Agree"]
            for btn_text in buttons:
                try:
                    btn = page.get_by_role("button", name=btn_text, exact=False)
                    if await btn.is_visible():
                        await btn.click()
                        await page.wait_for_load_state("networkidle")
                        break
                except: continue

            # Wait for any result
            try:
                await page.wait_for_selector("h3", timeout=5000)
            except:
                pass
            
            # Save screenshot for debugging
            await page.screenshot(path="search_debug.png")
            
            html = await page.content()
            soup = BeautifulSoup(html, 'html.parser')
            
            results = []
            # Improved extraction
            results = []
            
            # Look for common Google result patterns
            search_results = soup.select('.g') or soup.select('[data-hveid]')
            
            for g in search_results:
                title_elem = g.select_one('h3')
                link_elem = g.select_one('a')
                snippet_elem = g.select_one('.VwiC3b') or g.select_one('.st')
                
                if title_elem and link_elem:
                    url = link_elem.get('href')
                    if url and url.startswith('http'):
                        results.append({
                            "title": title_elem.get_text(),
                            "link": url,
                            "snippet": snippet_elem.get_text() if snippet_elem else ""
                        })
            
            # Special case for currency/weather boxes
            if not results:
                # Try to find specific data in knowledge graphs or snippets
                answer = soup.select_one('.DNoAnf') or soup.select_one('.LGOEob')
                if answer:
                    results.append({"title": "Direct Answer", "link": search_url, "snippet": answer.get_text()})

            return {"query": q, "count": len(results), "results": results}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            await browser.close()

@app.get("/exchange", tags=["Search"])
async def exchange(base: str = Query("USD", description="Base currency"), to: str = Query("KRW", description="Target currency")):
    """Get realtime exchange rate from Investing.com"""
    pair_map = {
        "USD/KRW": "https://kr.investing.com/currencies/usd-krw",
        "EUR/KRW": "https://kr.investing.com/currencies/eur-krw",
        "SGD/KRW": "https://kr.investing.com/currencies/sgd-krw",
    }
    
    pair = f"{base.upper()}/{to.upper()}"
    url = pair_map.get(pair)
    
    if not url:
        # Fallback to general search if pair not in map
        return await search(f"{pair} exchange rate")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        stealth = Stealth()
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            # Investing.com price selector
            price_selector = '[data-test="instrument-price-last"]'
            await page.wait_for_selector(price_selector, timeout=10000)
            
            price_text = await page.inner_text(price_selector)
            # Remove commas and convert to float
            price = float(price_text.replace(",", ""))
            
            return {
                "pair": pair,
                "rate": price,
                "source": "Investing.com",
                "timestamp": str(asyncio.get_event_loop().time())
            }
        except Exception as e:
            # Fallback to Google Search if Investing.com fails
            return await search(f"{pair} realtime rate")
        finally:
            await browser.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9001)
