"""
Email sender for Kindle.

Sends articles to your Kindle email address as EPUB (which carries inline images
with it) or, if KINDLE_FORMAT=html, as a plain HTML attachment.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

from epub import build_epub, sanitize_to_xhtml, STYLESHEET

load_dotenv()

# Amazon rejects mail over 50 MB; stay comfortably below it.
MAX_ATTACHMENT_BYTES = 40 * 1024 * 1024


def get_email_config() -> dict:
    """Load email configuration from environment variables."""
    config = {
        "smtp_host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "smtp_user": os.getenv("SMTP_USER"),
        "smtp_pass": os.getenv("SMTP_PASS"),
        "kindle_email": os.getenv("KINDLE_EMAIL"),
        "from_email": os.getenv("FROM_EMAIL"),
        # "epub" keeps inline images; "html" is the older text-only behaviour.
        "format": (os.getenv("KINDLE_FORMAT") or "epub").strip().lower(),
    }

    # Use SMTP_USER as FROM_EMAIL if not specified
    if not config["from_email"]:
        config["from_email"] = config["smtp_user"]

    missing = [k for k in ["smtp_user", "smtp_pass", "kindle_email"] if not config[k]]
    if missing:
        raise ValueError(f"Missing email config: {', '.join(missing)}")

    return config


def _standalone_html(title: str, body_html: str, source_url: str = "") -> str:
    """Wrap an article body in a complete HTML document."""
    safe_title = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    source_line = f'<p class="source">{source_url}</p>' if source_url else ""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<title>{safe_title}</title>
<style>
{STYLESHEET}</style>
</head>
<body>
<h1>{safe_title}</h1>
{source_line}
{sanitize_to_xhtml(body_html)}
</body>
</html>"""


def send_to_kindle(title: str, html_content: str, images=None, source_url: str = "") -> bool:
    """
    Send an article to Kindle.

    Args:
        title: Article title (used for filename and subject)
        html_content: The article body markup, with images referenced as "images/..."
        images: Image objects from images.process_images(), packaged into the EPUB
        source_url: Original article URL, shown under the title

    Returns:
        True if sent successfully
    """
    config = get_email_config()
    images = images or []

    # Create message
    msg = MIMEMultipart()
    msg["From"] = config["from_email"]
    msg["To"] = config["kindle_email"]
    msg["Subject"] = title

    # Add a simple body
    body = f"Article: {title}\n\nSent via Ink Drop"
    msg.attach(MIMEText(body, "plain"))

    base_name = _sanitize_filename(title)

    if config["format"] == "html":
        # Images can't travel in a bare HTML attachment - Kindle won't fetch them.
        attachment = MIMEText(
            _standalone_html(title, html_content, source_url), "html", "utf-8"
        )
        attachment.add_header("Content-Disposition", "attachment", filename=base_name + ".html")
    else:
        epub_bytes = build_epub(title, html_content, images, source_url=source_url)
        if len(epub_bytes) > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"EPUB is {len(epub_bytes) // (1024 * 1024)} MB, over the Kindle email limit"
            )
        attachment = MIMEBase("application", "epub+zip")
        attachment.set_payload(epub_bytes)
        encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", "attachment", filename=base_name + ".epub")

    msg.attach(attachment)

    # Send email
    try:
        with smtplib.SMTP(config["smtp_host"], config["smtp_port"]) as server:
            server.starttls()
            server.login(config["smtp_user"], config["smtp_pass"])
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        raise


def _sanitize_filename(title: str) -> str:
    """Sanitize title for use as filename."""
    invalid_chars = '<>:"/\\|?*'
    filename = title.translate(str.maketrans("", "", invalid_chars))
    filename = filename[:100].strip()
    return filename or "article"


def send_alert(subject: str, message: str) -> bool:
    """
    Send an alert email to yourself (SMTP_USER).
    Used for notifying about issues like expired cookies.
    """
    config = get_email_config()

    msg = MIMEMultipart()
    msg["From"] = config["from_email"]
    msg["To"] = config["smtp_user"]  # Send to yourself
    msg["Subject"] = f"[Ink Drop Alert] {subject}"

    msg.attach(MIMEText(message, "plain"))

    try:
        with smtplib.SMTP(config["smtp_host"], config["smtp_port"]) as server:
            server.starttls()
            server.login(config["smtp_user"], config["smtp_pass"])
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Failed to send alert: {e}")
        return False


if __name__ == "__main__":
    # Test email sending (requires valid config)
    test_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Test Article</title></head>
    <body>
        <h1>Test Article</h1>
        <p>This is a test article sent to Kindle.</p>
    </body>
    </html>
    """

    try:
        send_to_kindle("Test Article from Ink Drop", test_html)
        print("Email sent successfully!")
    except ValueError as e:
        print(f"Config error: {e}")
    except Exception as e:
        print(f"Send error: {e}")
