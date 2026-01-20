"""
Test script to verify we can scrape Twitter/X articles using cookies.
"""

import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from readability import Document

load_dotenv()

TWITTER_URL = "https://x.com/thedankoe/status/2012956603297964167"


def get_cookies():
    """Load Twitter cookies from environment variables."""
    auth_token = os.getenv("TWITTER_AUTH_TOKEN")
    ct0 = os.getenv("TWITTER_CT0")

    if not auth_token or not ct0:
        raise ValueError(
            "Missing cookies. Set TWITTER_AUTH_TOKEN and TWITTER_CT0 in .env"
        )

    return [
        {
            "name": "auth_token",
            "value": auth_token,
            "domain": ".x.com",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        },
        {
            "name": "ct0",
            "value": ct0,
            "domain": ".x.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
        },
    ]


def scrape_article(url: str) -> dict:
    """Scrape a Twitter article using Playwright + cookies."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        # Add cookies before navigating
        context.add_cookies(get_cookies())

        page = context.new_page()
        print(f"Loading: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Wait for tweet content to appear
        print("Waiting for content to load...")
        page.wait_for_timeout(5000)

        # Get the page content
        html = page.content()
        title = page.title()

        # Screenshot for debugging
        page.screenshot(path="debug_screenshot.png")
        print("Saved debug screenshot to debug_screenshot.png")

        browser.close()

    # Use readability to extract clean content
    doc = Document(html)

    return {
        "title": doc.title(),
        "content": doc.summary(),
        "raw_title": title,
    }


if __name__ == "__main__":
    print("Testing Twitter article scrape...\n")

    try:
        result = scrape_article(TWITTER_URL)
        print(f"Title: {result['title']}")
        print(f"Page title: {result['raw_title']}")
        print(f"\nContent preview:\n{'-' * 40}")
        # Strip HTML tags for preview
        import re
        clean_text = re.sub(r'<[^>]+>', '', result['content'])
        print(clean_text[:1000])
        print(f"\n{'=' * 40}")
        print("SUCCESS! Scraping works.")
    except Exception as e:
        print(f"ERROR: {e}")
        raise
