import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import trafilatura
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

app = FastAPI(title="Firecrawl-lite API")

class ScrapeRequest(BaseModel):
    url: str
    wait_ms: Optional[int] = 2000

@app.get("/")
async def home():
    return {"status": "online", "message": "Firecrawl-lite is ready"}

@app.post("/scrape")
async def scrape(request: ScrapeRequest):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Use stealth directly on context or page
        stealth = Stealth()
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        await stealth.apply_stealth_async(page)
        
        try:
            # Navigate to URL
            await page.goto(request.url, wait_until="networkidle", timeout=60000)
            
            # Wait for extra time if requested
            if request.wait_ms:
                await asyncio.sleep(request.wait_ms / 1000)
            
            # Get HTML content
            content = await page.content()
            
            # Convert to Markdown using trafilatura
            downloaded = trafilatura.extract(content, output_format='markdown', include_links=True, include_images=False)
            
            if not downloaded:
                downloaded = "Could not extract structured content. Check the URL or site complexity."

            return {
                "url": request.url,
                "success": True,
                "data": {
                    "markdown": downloaded,
                    "metadata": {
                        "title": await page.title(),
                        "source": request.url
                    }
                }
            }
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            await browser.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9001)
