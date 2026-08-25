"""
Ink Drop - FastAPI server for sending web articles to Kindle.
"""

import os
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from extractor import extract_article, AuthExpiredError
from emailer import send_to_kindle, send_alert

SENT_LOG = "sent_articles.txt"


def normalize_url(url: str) -> str:
    """Normalize a URL to canonical form for dedup."""
    # Convert twitter.com to x.com
    url = re.sub(r"https?://(www\.)?twitter\.com", "https://x.com", url)
    # Remove query params and trailing slash
    url = url.split("?")[0].rstrip("/")
    return url


def was_already_sent(url: str) -> bool:
    """Check if URL was already sent to Kindle."""
    if not os.path.exists(SENT_LOG):
        return False
    normalized = normalize_url(url)
    with open(SENT_LOG, "r") as f:
        return normalized in {line.strip() for line in f}


def mark_as_sent(url: str) -> None:
    """Record URL as sent."""
    normalized = normalize_url(url)
    with open(SENT_LOG, "a") as f:
        f.write(normalized + "\n")

app = FastAPI(
    title="Ink Drop",
    description="Push web articles to your Kindle, images and all",
    version="0.2.0",
)


class SendRequest(BaseModel):
    url: HttpUrl


class SendResponse(BaseModel):
    success: bool
    title: str
    message: str
    images: int = 0


@app.get("/")
def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "service": "ink-drop"}


@app.post("/send-to-kindle", response_model=SendResponse)
def send_article_to_kindle(request: SendRequest):
    """
    Extract an article and send it to your Kindle.

    - Fetches the page using Playwright (with Twitter cookies for x.com links)
    - Extracts clean, formatted content and downloads its inline images
    - Sends as an EPUB attachment to your Kindle email
    """
    url = str(request.url)

    if request.url.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail="URL must be http or https",
        )

    # Check for duplicate
    if was_already_sent(url):
        raise HTTPException(
            status_code=409,
            detail="Article already sent to Kindle",
        )

    try:
        # Extract article
        article = extract_article(url)

        # Send to Kindle
        send_to_kindle(
            article["title"],
            article["html"],
            images=article["images"],
            source_url=url,
        )

        # Mark as sent
        mark_as_sent(url)

        image_count = len(article["images"])
        return SendResponse(
            success=True,
            title=article["title"],
            images=image_count,
            message=(
                f"Article sent to Kindle with {image_count} image"
                f"{'' if image_count == 1 else 's'}!"
                if image_count
                else "Article sent to Kindle!"
            ),
        )

    except AuthExpiredError:
        # Send alert email with instructions
        send_alert(
            subject="Twitter Cookies Expired",
            message="""Your Twitter authentication cookies have expired.

To fix this:
1. Log into Twitter/X in your browser
2. Open DevTools (F12) → Application → Cookies → x.com
3. Copy the values for 'auth_token' and 'ct0'
4. SSH into your server and update /opt/ink-drop/.env
5. Run: systemctl restart ink-drop

The article you tried to send was not processed."""
        )
        raise HTTPException(
            status_code=401,
            detail="Twitter cookies expired. Alert email sent with instructions.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=3000)
