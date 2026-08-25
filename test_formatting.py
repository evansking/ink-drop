"""
Test script for iterating on article formatting.
Loads raw HTML from file so we don't need to hit the network each time.

Usage: python test_formatting.py [raw_page.html]
"""

import sys
from bs4 import BeautifulSoup
from readability import Document

from extractor import clean_twitter_html, clean_article_html, is_twitter_url, _clean_title
from epub import build_epub


def load_raw_html(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "raw_page.html"
    url = sys.argv[2] if len(sys.argv) > 2 else "https://x.com/i/status/0"

    raw_html = load_raw_html(path)

    # Use Readability for initial extraction
    doc = Document(raw_html)
    readable_html = doc.summary()
    title = _clean_title(doc.title(), twitter=is_twitter_url(url))

    if is_twitter_url(url):
        body, images = clean_twitter_html(readable_html, url, raw_html=raw_html)
    else:
        body, images = clean_article_html(readable_html, url, raw_html=raw_html)

    print(f"Title: {title}")
    print(f"Images: {len(images)}")
    print(f"Body: {len(body)} chars")

    soup = BeautifulSoup(body, "html.parser")
    print(f"Paragraphs: {len(soup.find_all('p'))}")
    print(f"Headings: {len(soup.find_all(['h2', 'h3']))}")

    with open("test_output.epub", "wb") as f:
        f.write(build_epub(title, body, images, source_url=url))
    print("Wrote test_output.epub")
