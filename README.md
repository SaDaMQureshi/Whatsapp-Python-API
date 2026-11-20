# WhatsApp Sender API

A FastAPI-based HTTP API for sending WhatsApp messages programmatically. Instead of using an unofficial WhatsApp API or pywhatkit, it connects to a real Chrome browser running WhatsApp Web via Playwright's Chrome DevTools Protocol (CDP), fills the message composer, and clicks the Send button — exactly like a human would.

## Features

- **Single message sending** — `POST /send`
- **Bulk message sending** — `POST /send-bulk` (same message to many recipients, sequential with configurable delay)
- **Phone number normalization** — accepts international, Pakistani local (`0300...`), and `00...` formats; converts to `+92...` international format
- **Message verification** — waits until the composer contains the exact expected text before clicking Send
- **Concurrent-safe** — a lock ensures only one send runs at a time, protecting the shared WhatsApp Web session
- **Send confirmation** — waits for the composer to clear after clicking Send to confirm delivery
- **Interactive API docs** — Swagger UI at `/docs`

## Architecture

```
HTTP Client (Postman / curl / any app)
        |
        v
  FastAPI (main.py, port 8000)
        |
        v
  Playwright sync API
        |
        v
  Chrome with --remote-debugging-port=9222
  (logged in to web.whatsapp.com)
```

The API does **not** maintain its own browser session. You must keep a Chrome window open, logged in to WhatsApp Web, with remote debugging enabled. The API reuses that tab for each send.

## Prerequisites

- Python 3.10+
- Google Chrome installed
- A WhatsApp account logged in inside the Chrome profile used below

## Setup

### 1. Install dependencies

```powershell
pip install -r requirements.txt
pip install playwright   # if not already pulled in
playwright install chromium
```

### 2. Launch Chrome with remote debugging

```powershell
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="E:\My Data\Project\Whatsapp API\ChromeProfile"
```

- Open WhatsApp Web (`https://web.whatsapp.com`) in that Chrome window and scan the QR code to log in **once** — the session persists in the `ChromeProfile` directory.
- Keep this Chrome window open while the API is running.

### 3. Start the API server

```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

The server runs at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

## API Reference

### Health check

```
GET /
```

Response:
```json
{
  "status": "running",
  "service": "WhatsApp Sender API",
  "version": "2.1.0"
}
```

### Send a single message

```
POST /send
Content-Type: application/json
```

Request body:
```json
{
  "phone": "+923001234567",
  "message": "Hello from the WhatsApp API!"
}
```

Response:
```json
{
  "phone": "+923001234567",
  "message": "Hello from the WhatsApp API!",
  "status": "sent"
}
```

### Send a bulk message

```
POST /send-bulk
Content-Type: application/json
```

Request body:
```json
{
  "phones": ["+923001234567", "03001234568", "00923001234569"],
  "message": "Hello everyone!"
}
```

Response:
```json
{
  "results": [
    { "phone": "+923001234567", "message": "Hello everyone!", "status": "sent" },
    { "phone": "+923001234568", "message": "Hello everyone!", "status": "sent" }
  ],
  "total": 3,
  "successful": 3,
  "failed": 0
}
```

Bulk sends are sequential. Individual failures are reported per-recipient in `results` without aborting the whole batch.

### Phone number formats

The API normalizes phone numbers. All of these work:

| Input | Result |
|---|---|
| `+923001234567` | `+923001234567` |
| `03001234567` | `+923001234567` |
| `3001234567` | `+923001234567` |
| `00923001234567` | `+923001234567` |
| `+92 300 1234567` | `+923001234567` |
| `+92-300-1234567` | `+923001234567` |

Valid numbers must contain 10–15 digits after normalization.

### Message constraints

- `message` is required, 1–4000 characters
- `phones` in bulk requests is a list of phone strings

### Error responses

| Status | Meaning |
|---|---|
| `422` | Invalid/missing phone number or message |
| `500` | WhatsApp send failed (timeout, chat not found, send button missing, etc.) |

## Configuration

All settings are optional environment variables with sensible defaults:

| Variable | Default | Description |
|---|---|---|
| `WA_CHROME_DEBUG_URL` | `http://127.0.0.1:9222` | Chrome DevTools Protocol endpoint |
| `WA_AFTER_SEND_WAIT` | `5` | Seconds to wait after a send before returning |
| `WA_BULK_DELAY` | `3` | Seconds between recipients in bulk sends |
| `WA_CHAT_LOAD_TIMEOUT` | `60000` | Chat/composer load timeout (ms) |
| `WA_MESSAGE_LOAD_TIMEOUT` | `20000` | Composer message verification timeout (ms) |
| `WA_SEND_CONFIRM_TIMEOUT` | `20000` | Post-send confirmation timeout (ms) |

Example:

```powershell
$env:WA_BULK_DELAY = "5"
$env:WA_CHROME_DEBUG_URL = "http://127.0.0.1:9223"
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

## Project structure

```
Whatsapp API/
├── main.py              # FastAPI app + Playwright sender logic
├── requirements.txt     # Python dependencies
├── ChromeProfile/       # Persistent Chrome profile (WhatsApp session)
├── test_chrome.py       # Quick CDP connectivity check
├── SETUP_GUIDE.txt      # Original manual setup notes
└── POSTMAN_GUIDE.txt    # Postman request walkthrough
```

## Notes & limitations

- **One message at a time.** The `send_lock` serializes all sends; concurrent requests wait their turn.
- **WhatsApp Web required.** The Chrome window must stay open and logged in. If WhatsApp Web logs out or shows a "linked device" prompt, re-scan the QR code.
- **Sending limits.** WhatsApp restricts bulk messaging on regular accounts. Use modest volumes and delays (`WA_BULK_DELAY`) to avoid bans.
- **Do not close Chrome mid-send.** The API attaches to your existing Chrome via CDP and never closes it.
- **`test_chrome.py`** verifies that Chrome's debugging port is reachable before you rely on the API.

## Troubleshooting

- **"No Chrome browser context found"** — Chrome isn't running with `--remote-debugging-port=9222`. Check the launch command.
- **Timeouts opening chat** — confirm the recipient's number is on WhatsApp; invalid numbers can hang the chat page.
- **Send button not found** — WhatsApp Web UI may have changed; check the selectors in `find_send_button()` in `main.py`.
- **Port already in use** — change `--port` on the uvicorn command or set a different port.
