"""
Article extractor for Twitter/X threads.
Extracts clean, formatted HTML preserving headers, paragraphs, and structure.
"""

import os
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from readability import Document
from dotenv import load_dotenv

load_dotenv()


class AuthExpiredError(Exception):
    """Raised when Twitter authentication cookies have expired."""
    pass


def get_twitter_cookies() -> list[dict]:
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


def _check_auth_failure(html: str) -> bool:
    """Check if the page indicates an authentication failure."""
    auth_failure_indicators = [
        "Sign in to X",
        "Log in to X",
        "Sign in to Twitter",
        "Log in to Twitter",
        'href="/login"',
        'href="/i/flow/login"',
        "This account doesn't exist",
        "Something went wrong. Try reloading",
    ]
    return any(indicator in html for indicator in auth_failure_indicators)


def fetch_page(url: str, save_raw: bool = False) -> tuple[str, str]:
    """Fetch page HTML using Playwright with Twitter cookies."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(get_twitter_cookies())

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Wait for article content to load (Twitter's longform articles use this class)
        try:
            page.wait_for_selector('[data-testid="tweetText"], .longform-unstyled', timeout=15000)
        except Exception:
            pass  # Continue even if selector not found

        # Additional wait for dynamic content
        page.wait_for_timeout(3000)

        html = page.content()
        title = page.title()

        browser.close()

    # Check for auth failure before saving/returning
    if _check_auth_failure(html):
        raise AuthExpiredError("Twitter cookies have expired or are invalid")

    if save_raw:
        with open("raw_page.html", "w") as f:
            f.write(html)
        print("Saved raw HTML to raw_page.html")

    return html, title


def clean_html_for_kindle(html: str, title: str) -> str:
    """
    Clean and format HTML for Kindle readability.
    Preserves headers, paragraphs, lists, and basic formatting.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "aside", "iframe"]):
        tag.decompose()

    # Get all paragraph elements from the readability output
    paragraphs = soup.find_all("p")

    content_parts = []
    seen_text = set()

    for p in paragraphs:
        # Don't use strip=True - it removes spaces between inline styled elements
        text = p.get_text()
        # Normalize whitespace: collapse multiple spaces/newlines into single space
        text = " ".join(text.split())

        # Skip empty, short, or UI text
        if not text or len(text) < 20 or _is_ui_text(text):
            continue

        # Skip if we've seen this exact text (dedup)
        if text in seen_text:
            continue

        # Skip if this text is a substring of something we already have
        # (handles the case where Twitter splits formatted text)
        if any(text in existing for existing in seen_text):
            continue

        seen_text.add(text)
        content_parts.append(text)

    # Build clean HTML for Kindle
    kindle_html = _format_for_kindle(content_parts, title)

    return kindle_html


def _is_ui_text(text: str) -> bool:
    """Check if text is likely UI/navigation rather than content."""
    ui_patterns = [
        r"^follow$",
        r"^repost$",
        r"^like$",
        r"^share$",
        r"^reply$",
        r"^home$",
        r"^explore$",
        r"^notifications$",
        r"^messages$",
        r"^bookmarks$",
        r"^profile$",
        r"^more$",
        r"^post$",
        r"^\d+$",  # Just numbers
        r"^\d+[KMB]?$",  # Engagement counts like 5K, 10M
        r"^[A-Z][a-z]+ \d+$",  # Dates like "Jan 15"
        r"^Show more$",
        r"^Show this thread$",
    ]

    text_lower = text.lower().strip()
    return any(re.match(pattern, text_lower, re.IGNORECASE) for pattern in ui_patterns)


def _format_for_kindle(content_parts: list[str], title: str) -> str:
    """Format content as clean HTML for Kindle."""
    # Clean up title
    title = re.sub(r"\s*[/|]\s*X$", "", title)
    title = re.sub(r"^\(\d+\)\s*", "", title)  # Remove notification count

    paragraphs = "\n".join(f"<p>{part}</p>" for part in content_parts)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: Georgia, serif;
            line-height: 1.6;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        h1 {{
            font-size: 1.5em;
            margin-bottom: 1em;
            border-bottom: 1px solid #ccc;
            padding-bottom: 0.5em;
        }}
        p {{
            margin-bottom: 1em;
            text-align: justify;
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    {paragraphs}
</body>
</html>"""

    return html


def _remove_twitter_errors(html: str) -> str:
    """Remove Twitter's error/noscript containers that interfere with extraction."""
    soup = BeautifulSoup(html, "html.parser")
    for error in soup.find_all(class_="errorContainer"):
        error.decompose()
    for noscript in soup.find_all("noscript"):
        noscript.decompose()
    return str(soup)


def extract_article(url: str) -> dict:
    """
    Main extraction function.
    Returns dict with title, html (for Kindle), and plain text.
    """
    # Fetch page
    raw_html, page_title = fetch_page(url)

    # Remove Twitter error containers before extraction
    clean_raw = _remove_twitter_errors(raw_html)

    # Use Readability for initial extraction
    doc = Document(clean_raw)
    readable_html = doc.summary()
    readable_title = doc.title() or page_title

    # Clean and format for Kindle
    kindle_html = clean_html_for_kindle(readable_html, readable_title)

    # Also generate plain text version
    soup = BeautifulSoup(kindle_html, "html.parser")
    plain_text = soup.get_text(separator="\n\n", strip=True)

    return {
        "title": readable_title,
        "html": kindle_html,
        "text": plain_text,
        "url": url,
    }


if __name__ == "__main__":
    # Test extraction
    test_url = "https://x.com/thedankoe/status/2012956603297964167"
    result = extract_article(test_url)

    print(f"Title: {result['title']}")
    print(f"\nPlain text preview:\n{'-' * 40}")
    print(result["text"][:1500])

    # Save HTML for inspection
    with open("test_output.html", "w") as f:
        f.write(result["html"])
    print(f"\n{'-' * 40}")
    print("Saved full HTML to test_output.html")
