# Ink Drop

Push Twitter/X threads to your Kindle with a single tap from your iPhone.

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

1. Share a Twitter/X URL from your iPhone
2. iOS Shortcut POSTs it to your server
3. Playwright fetches the page with your auth cookies
4. Readability extracts clean article content
5. Formatted HTML is emailed to your Kindle

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

```bash
curl -X POST http://localhost:3000/send-to-kindle \
  -H "Content-Type: application/json" \
  -d '{"url": "https://x.com/user/status/123"}'
```

```json
{
  "success": true,
  "title": "Article Title",
  "message": "Article sent to Kindle!"
}
```

Returns 409 if the article was already sent (dedup).

### GET /

Health check.

## Stack

- Python 3.11+
- FastAPI
- Playwright
- readability-lxml
- BeautifulSoup4
