# Debug Bot — Telegram Splunk Debugging Bot

A Telegram bot that accepts curl snippets from QA, searches Splunk automatically, and delivers an AI-summarized diagnosis to you on Telegram.

## Requirements

- **Python 3.11+**
- **Global Protect VPN** connected (for Splunk access)
- **OpenAI API key** (for log analysis)
- **Telegram Bot Token** (from [@BotFather](https://t.me/BotFather))

## Setup

### 1. Create a Telegram Bot

1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the bot token

### 2. Get Your Chat ID

1. Send a message to your bot
2. Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
3. Find `"chat":{"id": <YOUR_CHAT_ID>}` in the response
4. Repeat for each QA member who needs access

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_YOUR_CHAT_ID=your_personal_chat_id
TELEGRAM_ALLOWED_CHAT_IDS=chat_id_1,chat_id_2,chat_id_3

# Splunk
SPLUNK_URL=https://your-splunk-instance.example.com
SPLUNK_SPL_TEMPLATES={"prod":"index=\"your_prod_index\" \"{transaction_id}\"","dev":"index=\"your_preprod_index\" \"{transaction_id}\"","staging":"index=\"your_preprod_index\" \"{transaction_id}\"","uat":"index=\"your_preprod_index\" \"{transaction_id}\""}
SPLUNK_SESSION_PATH=splunk_session.json
SPLUNK_RESULT_WAIT_TIMEOUT=30
SPLUNK_SSO_DOMAIN=login.your-company.com
MAX_LOG_LINES=100

# VPN check
VPN_CHECK_HOST=internal.your-company.com
TRANSACTION_ID_HEADER=X-Transaction-ID

# LLM (OpenAI)
LLM_API_KEY=your_openai_api_key
LLM_MODEL=gpt-4o

# App
TIMEZONE=Asia/Jakarta
DB_PATH=investigations.db
```

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_YOUR_CHAT_ID` | Your chat ID (receives full technical reports) |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Comma-separated chat IDs allowed to submit curls |
| `SPLUNK_URL` | Base URL of your Splunk instance |
| `SPLUNK_SPL_TEMPLATES` | JSON mapping of environment names to SPL query templates with `{transaction_id}` placeholder |
| `SPLUNK_SESSION_PATH` | Path to saved session file (default: `splunk_session.json`) |
| `SPLUNK_RESULT_WAIT_TIMEOUT` | Seconds to wait for Splunk results (default: 30) |
| `SPLUNK_SSO_DOMAIN` | Domain in URL that indicates SSO login redirect |
| `MAX_LOG_LINES` | Max log lines to extract per query (default: 100) |
| `VPN_CHECK_HOST` | Internal hostname only reachable via VPN |
| `TRANSACTION_ID_HEADER` | Header name containing the transaction ID |
| `LLM_API_KEY` | OpenAI API key |
| `LLM_MODEL` | OpenAI model to use (default: `gpt-4o`) |
| `TIMEZONE` | IANA timezone for report timestamps (default: `Asia/Jakarta`) |
| `DB_PATH` | SQLite database path (default: `investigations.db`) |

### 4. Install Dependencies

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 5. Save Splunk Session

```bash
python save_session.py
```

A Chromium browser window opens. Complete your SSO login, then press Enter in the terminal. This creates `splunk_session.json`.

> Re-run this whenever the bot sends you a "🔐 Session expired" alert.

### 6. Customize Splunk Selectors

The bot needs accurate CSS selectors for your Splunk instance's UI. Run:

```bash
playwright codegen <YOUR_SPLUNK_URL>
```

Perform a search interactively. Then update the selector constants at the top of `scraper/splunk_scraper.py`:

```python
SPLUNK_SEARCH_INPUT = 'textarea[data-test="search-input"]'   # your search bar selector
SPLUNK_SEARCH_BUTTON = 'button[data-test="search-button"]'   # your submit button selector
SPLUNK_RESULTS_CONTAINER = 'div[data-test="results-container"]'  # your results container
```

## Running the Bot

```bash
# Make sure Global Protect VPN is connected
source venv/bin/activate
python main.py
```

Or use the convenience script:

```bash
chmod +x start.sh
./start.sh
```

The bot logs to both the console and `logs/bot.log` (rotating, 5MB max, 3 files kept).

## Usage

### Submit a Bug Report

**Step 1:** Select an environment by sending one of these commands:

| Command | Environment |
|---|---|
| `/prod` | Production |
| `/dev` | Development |
| `/staging` | Staging |
| `/uat` | UAT |

The bot will confirm: "✅ Environment set to **production** (`/prod`)."

> Additional environments can be added in `.env` via `SPLUNK_SPL_TEMPLATES`.

**Step 2:** Send a `curl` command containing the `X-Transaction-ID` header:

```
curl -H "X-Transaction-ID: abc-123" -H "Authorization: Bearer token" https://api.example.com/v1/orders
```

If you send a curl without selecting an environment first, the bot will ask you to pick one.

The bot will:
1. Extract the transaction ID
2. Check VPN connectivity
3. Search Splunk for logs using the environment-specific index
4. Send logs to OpenAI for analysis
5. Deliver a diagnosis card to you and a summary to QA

### Commands

| Command | Who | Description |
|---|---|---|
| `/prod` | Anyone (allowlisted) | Set environment to production |
| `/dev` | Anyone (allowlisted) | Set environment to development |
| `/staging` | Anyone (allowlisted) | Set environment to staging |
| `/uat` | Anyone (allowlisted) | Set environment to UAT |
| `/help` | Anyone (allowlisted) | Show usage instructions |
| `/status` | Anyone (allowlisted) | Show queue depth, VPN status, session age |
| `/history` | Engineer only | Show last 5 investigations |

## Architecture

```
QA sends curl → Telegram Bot → Curl Parser → Job Queue (FIFO, max 10)
                                                      ↓
                                              VPN Check
                                                      ↓
                                              Splunk Scraper (Playwright)
                                                      ↓
                                              LLM Analyzer (OpenAI)
                                                      ↓
                                        ┌─────────────┴──────────────┐
                                        ↓                             ↓
                                  Engineer (full report)      QA (simplified)
                                        ↓
                                  SQLite (investigations.db)
```

## Error Handling

| Scenario | Engineer gets | QA gets |
|---|---|---|
| Invalid curl | — | Error message with format hint |
| Missing transaction ID header | — | "Could not find X-Transaction-ID header" |
| Queue full (10 jobs) | — | "Bot is busy, please retry" |
| VPN down (3 retries) | "❌ Job abandoned after 3 VPN retries" | "Investigation failed — VPN issue" |
| Splunk session expired | "🔐 Session expired. Run save_session.py" | "Investigation paused" |
| No logs found | "No logs found for transaction ID" | "No logs found" |
| LLM API error | Raw logs (truncated 3000 chars) + error note | "Engineer is reviewing" |
| Browser crash | "🚨 Browser crashed. Restart the bot." | "Technical issue on our end" |

## Graceful Shutdown

Press `Ctrl+C` to shut down. The bot will:
1. Stop accepting new jobs
2. Send you: "🛑 Shutting down. Draining N remaining job(s)..."
3. Finish all pending jobs
4. Close the browser
5. Exit

If drain takes longer than 5 minutes, it force-exits with a warning.

## Project Structure

```
bugs-bot/
├── main.py                  # Entry point, signal handlers, job pipeline
├── save_session.py          # One-time script to save Splunk SSO session
├── config.py                # Loads .env, exposes typed constants
├── .env                     # Secrets (gitignored)
├── .env.example             # Template for onboarding
├── requirements.txt
├── start.sh                 # Convenience startup script
│
├── bot/
│   ├── handler.py           # Telegram message handlers
│   └── formatter.py         # Engineer + QA report formatting
│
├── parser/
│   └── curl_parser.py       # Extracts transaction ID from raw curl
│
├── queue/
│   └── job_queue.py         # asyncio queue, FIFO worker, graceful drain
│
├── scraper/
│   ├── browser.py           # Playwright browser lifecycle + crash recovery
│   ├── splunk_scraper.py    # Splunk search automation
│   └── vpn_check.py         # TCP probe to verify VPN connectivity
│
├── analyzer/
│   └── llm_analyzer.py      # OpenAI integration, structured JSON output
│
├── storage/
│   └── database.py          # SQLite read/write for investigations
│
└── logs/
    └── bot.log              # Rotating log file (gitignored)
```

## Renewing the Splunk Session

When the bot detects an expired session, it will alert you. To renew:

```bash
python save_session.py
# Complete SSO login in the browser, press Enter
# Then restart the bot:
python main.py
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `splunk_session.json not found` | Run `python save_session.py` first |
| Bot doesn't respond to group messages | Group chats are not supported — use private chat only |
| VPN check always fails | Ensure Global Protect is connected and `VPN_CHECK_HOST` is correct |
| Splunk selectors broken | Splunk UI may have updated — re-run `playwright codegen` and update selectors in `scraper/splunk_scraper.py` |
| Browser crashes repeatedly | Restart the bot. If persistent, check Playwright installation: `playwright install chromium` |
| LLM gives wrong diagnosis | Raw logs are always saved to SQLite. Check `/history` or query `investigations.db` directly |