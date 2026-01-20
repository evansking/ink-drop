"""
Test script for iterating on article formatting.
Loads raw HTML from file so we don't need to hit Twitter each time.
"""

from bs4 import BeautifulSoup
from readability import Document

from extractor import clean_html_for_kindle


def load_raw_html() -> str:
    with open("raw_page.html", "r") as f:
        return f.read()


if __name__ == "__main__":
    raw_html = load_raw_html()

    # Use Readability for initial extraction
    doc = Document(raw_html)
    readable_html = doc.summary()
    title = doc.title()

    # Clean and format
    kindle_html = clean_html_for_kindle(readable_html, title)

    # Save for inspection
    with open("test_output.html", "w") as f:
        f.write(kindle_html)

    # Also print plain text preview
    soup = BeautifulSoup(kindle_html, "html.parser")
    plain_text = soup.get_text(separator="\n\n", strip=True)

    print(f"Title: {title}\n")
    print("Content preview:")
    print("-" * 40)
    print(plain_text[:2000])
    print("-" * 40)
    print("\nSaved to test_output.html")
