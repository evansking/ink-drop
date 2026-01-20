"""
Email sender for Kindle.
Sends articles as HTML attachments to your Kindle email address.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

load_dotenv()


def get_email_config() -> dict:
    """Load email configuration from environment variables."""
    config = {
        "smtp_host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "smtp_user": os.getenv("SMTP_USER"),
        "smtp_pass": os.getenv("SMTP_PASS"),
        "kindle_email": os.getenv("KINDLE_EMAIL"),
        "from_email": os.getenv("FROM_EMAIL"),
    }

    # Use SMTP_USER as FROM_EMAIL if not specified
    if not config["from_email"]:
        config["from_email"] = config["smtp_user"]

    missing = [k for k in ["smtp_user", "smtp_pass", "kindle_email"] if not config[k]]
    if missing:
        raise ValueError(f"Missing email config: {', '.join(missing)}")

    return config


def send_to_kindle(title: str, html_content: str) -> bool:
    """
    Send an article to Kindle as an HTML attachment.

    Args:
        title: Article title (used for filename and subject)
        html_content: The formatted HTML content

    Returns:
        True if sent successfully
    """
    config = get_email_config()

    # Create message
    msg = MIMEMultipart()
    msg["From"] = config["from_email"]
    msg["To"] = config["kindle_email"]
    msg["Subject"] = title

    # Add a simple body
    body = f"Article: {title}\n\nSent via Ink Drop"
    msg.attach(MIMEText(body, "plain"))

    # Create HTML attachment
    # Kindle accepts .html files
    filename = _sanitize_filename(title) + ".html"

    attachment = MIMEBase("text", "html")
    attachment.set_payload(html_content.encode("utf-8"))
    encoders.encode_base64(attachment)
    attachment.add_header(
        "Content-Disposition",
        f'attachment; filename="{filename}"',
    )
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
