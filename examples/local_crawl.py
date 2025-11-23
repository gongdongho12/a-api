import asyncio
from crawl4ai import AsyncWebCrawler


async def main():
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url="https://www.nbcnews.com/business",
        )
        print("--- markdown ---")
        print(result.markdown)
        print("--- html ---")
        print(getattr(result, "html", "<no html>"))


if __name__ == "__main__":
    asyncio.run(main())
