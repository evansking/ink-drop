# Ink Drop

Push web articles and Twitter/X threads to your Kindle with a single tap from
your iPhone - inline images included.

## How It Works

```
┌─────────────┐      POST /send-to-kindle      ┌─────────────┐
│ iOS Shortcut │ ─────────────────────────────▶ │  VPS Server │
└─────────────┘                                 └──────┬──────┘
                                                       │
                                                       ▼
                                                ┌─────────────┐
                                                │  Playwright │
                                                │  + Cookies  │
                                                └──────┬──────┘
                                                       │
                                                       ▼
                                                ┌─────────────┐
                                                │ Readability │
                                                │  (Extract)  │
                                                └──────┬──────┘
                                                       │
                                                       ▼
                                                ┌─────────────┐
                                                │ SMTP Email  │──────▶ Kindle
                                                └─────────────┘
```

1. Share any article URL from your iPhone
2. iOS Shortcut POSTs it to your server
3. Playwright fetches the page (with your Twitter cookies for x.com links)
4. Readability extracts clean article content
5. Inline images are downloaded and packaged with the text
6. The article is emailed to your Kindle as an EPUB

### About images

Readability discards images and section headings on a lot of sites. Ink Drop
re-reads the original page and puts them back where they belong, matching each
dropped element to the paragraph it sat next to. It picks a sensible resolution
out of `srcset`/`<picture>`, skips avatars and tracking pixels, and asks image
CDNs for JPEG/PNG rather than WebP.

Images have to travel *with* the article - Kindle will not fetch remote images
out of an emailed HTML file - which is why articles are sent as EPUB. Set
`KINDLE_FORMAT=html` for the old text-only attachment.

## Setup

### Server

```bash
git clone https://github.com/evansking/ink-drop.git
cd ink-drop
python3 -m venv .venv
source .venv/bin/activate
pip install uv
uv pip install -e .
playwright install chromium
```

### Environment Variables

Create a `.env` file:

```
# Twitter/X cookies (from browser dev tools)
TWITTER_AUTH_TOKEN=your_auth_token
TWITTER_CT0=your_ct0_token

# Gmail SMTP (use an app password)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASS=your_app_password

# Your Kindle email address
KINDLE_EMAIL=your_kindle@kindle.com

# Attachment format: epub (default, keeps images) or html (text only)
KINDLE_FORMAT=epub
```

### Run

```bash
# Development
uvicorn main:app --host 0.0.0.0 --port 3000

# Production - use systemd (see deploy/)
```

### iOS Shortcut

1. Create a new Shortcut
2. Set it to receive URLs from the Share Sheet
3. Add "Get Contents of URL" action:
   - URL: `http://your-server:3000/send-to-kindle`
   - Method: POST
   - Headers: `Content-Type: application/json`
   - Request Body: JSON with `url` set to "Shortcut Input"

## API

### POST /send-to-kindle

Accepts any http(s) URL.

```bash
curl -X POST http://localhost:3000/send-to-kindle \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.datadoghq.com/blog/engineering/gitretriever/"}'
```

```json
{
  "success": true,
  "title": "Article Title",
  "images": 4,
  "message": "Article sent to Kindle with 4 images!"
}
```

Returns 409 if the article was already sent (dedup), 401 if Twitter cookies
have expired.

### GET /

Health check.

## Stack

- Python 3.11+
- FastAPI
- Playwright
- readability-lxml
- BeautifulSoup4

EPUB packaging and image fetching use only the standard library.
