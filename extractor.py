"""
Article extractor.

Handles two kinds of page:
  - Twitter/X threads, which need auth cookies and a lot of UI filtering
  - ordinary web articles, where Readability's output is already close to right

Both paths download inline images and hand back markup pointing at local files.
"""

import os
import re
import urllib.parse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from readability import Document
from dotenv import load_dotenv

from images import process_images

load_dotenv()

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class AuthExpiredError(Exception):
    """Raised when Twitter authentication cookies have expired."""
    pass


def is_twitter_url(url: str) -> bool:
    """Whether this URL needs the Twitter-specific fetch and cleanup path."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("x.com", "twitter.com") or host.endswith((".x.com", ".twitter.com"))


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
    """Fetch page HTML using Playwright, with Twitter cookies where relevant."""
    twitter = is_twitter_url(url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=_UA)
        if twitter:
            context.add_cookies(get_twitter_cookies())

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        try:
            if twitter:
                # Twitter's longform articles use these containers.
                page.wait_for_selector(
                    '[data-testid="tweetText"], .longform-unstyled', timeout=15000
                )
            else:
                page.wait_for_selector("article, main, .post, .entry-content", timeout=10000)
        except Exception:
            pass  # Continue even if selector not found

        # Let lazy-loaded images swap in their real sources.
        _scroll_page(page)
        page.wait_for_timeout(2000)

        html = page.content()
        title = page.title()

        browser.close()

    # Check for auth failure before saving/returning
    if twitter and _check_auth_failure(html):
        raise AuthExpiredError("Twitter cookies have expired or are invalid")

    if save_raw:
        with open("raw_page.html", "w") as f:
            f.write(html)
        print("Saved raw HTML to raw_page.html")

    return html, title


def _scroll_page(page) -> None:
    """Scroll to the bottom so lazy-loaded images resolve before we read the DOM."""
    try:
        page.evaluate(
            """
            () => new Promise((resolve) => {
                let y = 0;
                const step = () => {
                    window.scrollBy(0, window.innerHeight);
                    y += window.innerHeight;
                    if (y >= document.body.scrollHeight || y > 40000) {
                        window.scrollTo(0, 0);
                        resolve();
                    } else {
                        setTimeout(step, 120);
                    }
                };
                step();
            })
            """
        )
    except Exception:
        pass


def clean_twitter_html(html: str, base_url: str, raw_html: str = "") -> tuple[str, list]:
    """
    Build Kindle markup from a Twitter thread.

    Twitter repeats and fragments its text across nested elements, so paragraphs
    are deduplicated and UI strings filtered out. Images are kept in the
    positions they appear in the thread.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "aside", "iframe"]):
        tag.decompose()

    if raw_html:
        restore_structure(soup, raw_html)

    images = process_images(soup, base_url)

    content_parts = []
    seen_text = set()

    # Walk in document order so images stay interleaved with the text.
    for node in soup.find_all(["p", "img"]):
        if node.name == "img":
            src = node.get("src", "")
            if src.startswith("images/"):
                alt = node.get("alt", "")
                alt_attr = f' alt="{alt}"' if alt else ""
                content_parts.append(f'<figure><img src="{src}"{alt_attr}/></figure>')
            continue

        # Don't use strip=True - it removes spaces between inline styled elements
        text = node.get_text()
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
        content_parts.append(f"<p>{_esc(text)}</p>")

    return "\n".join(content_parts), images


def _normalize(text: str) -> str:
    """Collapse whitespace and truncate, for matching elements across two trees."""
    return " ".join((text or "").split())[:120]


def _common_ancestor(a, b):
    """The deepest element containing both nodes - i.e. the content container."""
    if a is b:
        return a.parent
    seen = {id(p): p for p in a.parents}
    for parent in b.parents:
        if id(parent) in seen:
            return parent
    return None


def restore_structure(readable_soup, raw_html: str) -> tuple[int, int]:
    """
    Put back section headings and images that Readability discarded.

    Readability routinely drops both - headings whose CSS classes score badly,
    and images it doesn't consider content - which leaves a long article as one
    undifferentiated run of text. The paragraphs it kept still come from the
    original page, so anything dropped can be matched to the paragraph it
    preceded and reinserted at that spot.

    Returns (headings restored, media restored).
    """
    heading_tags = ["h1", "h2", "h3", "h4"]
    body_tags = ["p", "li", "pre", "blockquote"]
    media_tags = ["figure", "picture", "img"]

    # Where each kept block of text ended up in the readable tree.
    index: dict[str, object] = {}
    for el in readable_soup.find_all(body_tags):
        key = _normalize(el.get_text())
        if len(key) >= 25 and key not in index:
            index[key] = el
    if not index:
        return 0, 0

    existing = {_normalize(h.get_text()) for h in readable_soup.find_all(heading_tags)}
    existing_media = {
        img.get("src") for img in readable_soup.find_all("img") if img.get("src")
    }

    raw = BeautifulSoup(raw_html, "html.parser")
    for tag in raw.find_all(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()

    # Walk the original page once, in document order, tagging each element as a
    # heading, a piece of media, or an anchor - a block of text that survived
    # extraction and so marks a known position in the readable tree.
    #
    # Anchors are matched by text rather than by tag name, because sites differ
    # wildly in what they wrap paragraphs in (Twitter threads use divs and
    # spans, not <p>), and dropped elements can only be placed relative to text
    # that exists in both trees.
    sequence: list = []
    for el in raw.find_all(True):
        if el.name in heading_tags:
            sequence.append(("heading", el, None))
        elif el.name in media_tags:
            # Keep only the outermost of nested media (figure > picture > img).
            if not any(parent.name in media_tags for parent in el.parents):
                sequence.append(("media", el, None))
        else:
            key = _normalize(el.get_text())
            if len(key) < 25 or key not in index:
                continue
            # Prefer the innermost element carrying this text, so media that
            # sits beside it isn't swallowed by an outer wrapper.
            if any(
                _normalize(child.get_text()) == key
                for child in el.find_all(True, recursive=False)
            ):
                continue
            sequence.append(("anchor", el, key))

    # Only trust the span between the first and last anchor - anything outside
    # it is page furniture, not article content.
    matched = [i for i, item in enumerate(sequence) if item[0] == "anchor"]
    if not matched:
        return 0, 0
    first, last = matched[0], matched[-1]

    # An article can open or close on an image, so the positional window alone
    # would drop a leading or trailing picture. Widen it over adjacent media,
    # but confine what gets accepted to the container the article text lives in
    # - that keeps a lead image while leaving out the author headshots, hero
    # banners and footer promos that sit outside it.
    content_root = _common_ancestor(sequence[first][1], sequence[last][1])
    while first > 0 and sequence[first - 1][0] == "media":
        first -= 1
    while last + 1 < len(sequence) and sequence[last + 1][0] == "media":
        last += 1

    headings_restored = 0
    media_restored = 0
    pending: list = []
    anchor = None

    for kind, el, key in sequence[first : last + 1]:
        if kind == "heading":
            text = " ".join(el.get_text().split())
            if text and _normalize(text) not in existing:
                pending.append(("heading", el.name, text))
            continue

        if kind == "media":
            if content_root is not None and not any(
                parent is content_root for parent in el.parents
            ):
                continue
            src = el.get("src") if el.name == "img" else None
            if src is None or src not in existing_media:
                pending.append(("media", el, None))
            continue

        target = index.get(key)
        if target is None:
            continue
        if target.parent is not None:
            anchor = target
        if not pending:
            continue
        if target.parent is None:
            pending = []
            continue

        for kind, a, b in pending:
            if kind == "heading":
                # Demote by one level: the article title owns <h1>.
                new_tag = readable_soup.new_tag("h2" if a in ("h1", "h2") else "h3")
                new_tag.string = b
                target.insert_before(new_tag)
                existing.add(_normalize(b))
                headings_restored += 1
            else:
                target.insert_before(a.extract())
                media_restored += 1
        pending = []

    # Anything still pending trailed the last matched paragraph - append it
    # after that paragraph rather than dropping it.
    if pending and anchor is not None and anchor.parent is not None:
        for kind, a, b in pending:
            if kind == "heading":
                new_tag = readable_soup.new_tag("h2" if a in ("h1", "h2") else "h3")
                new_tag.string = b
                anchor.insert_after(new_tag)
                anchor = new_tag
                headings_restored += 1
            else:
                node = a.extract()
                anchor.insert_after(node)
                anchor = node
                media_restored += 1

    return headings_restored, media_restored


def clean_article_html(html: str, base_url: str, raw_html: str = "") -> tuple[str, list]:
    """
    Build Kindle markup from an ordinary web article.

    Readability has already isolated the content, so the structure it found -
    headings, lists, code blocks, figures - is kept as-is rather than flattened
    to paragraphs.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["script", "style", "nav", "footer", "aside", "iframe", "form"]):
        tag.decompose()

    if raw_html:
        restore_structure(soup, raw_html)

    images = process_images(soup, base_url)

    # The article title is rendered separately, so demote any h1 in the body to
    # keep a single top-level heading.
    for h1 in soup.find_all("h1"):
        h1.name = "h2"

    return str(soup), images


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


def _clean_title(title: str, twitter: bool = True) -> str:
    """Clean up a page title to extract just the article name."""
    title = re.sub(r"^\(\d+\)\s*", "", title)  # Remove notification count

    if twitter:
        title = re.sub(r"\s*[/|]\s*X$", "", title)
        # Remove "username on X: " prefix, extract quoted title if present
        title = re.sub(r"^.+? on X: \"(.+)\"$", r"\1", title)
        title = re.sub(r"^.+? on X: ", "", title)  # Fallback for unquoted titles
    else:
        # Trim a trailing " | Site Name" / " - Site Name" suffix, but only when
        # what's left is still a plausible title.
        trimmed = re.sub(r"\s*[|–—-]\s*[^|–—-]{1,40}$", "", title)
        if len(trimmed) >= 15:
            title = trimmed

    return title.strip()


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

    Returns a dict with the title, the article body markup (images referenced as
    "images/..."), the downloaded images, and a plain text rendering.
    """
    twitter = is_twitter_url(url)

    raw_html, page_title = fetch_page(url)

    if twitter:
        raw_html = _remove_twitter_errors(raw_html)

    # Use Readability for initial extraction
    doc = Document(raw_html)
    readable_html = doc.summary()
    readable_title = doc.title() or page_title

    if twitter:
        body_html, images = clean_twitter_html(readable_html, url, raw_html=raw_html)
    else:
        body_html, images = clean_article_html(readable_html, url, raw_html=raw_html)

    title = _clean_title(readable_title, twitter=twitter)

    plain_text = BeautifulSoup(body_html, "html.parser").get_text(separator="\n\n", strip=True)

    return {
        "title": title,
        "html": body_html,
        "text": plain_text,
        "images": images,
        "url": url,
    }


if __name__ == "__main__":
    import sys

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://x.com/thedankoe/status/2012956603297964167"
    result = extract_article(test_url)

    print(f"Title: {result['title']}")
    print(f"Images: {len(result['images'])}")
    print(f"\nPlain text preview:\n{'-' * 40}")
    print(result["text"][:1500])
