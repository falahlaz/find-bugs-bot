# Debug Bot — Telegram Splunk Debugging Bot **v2.0.0**

A Telegram bot that accepts curl snippets from QA, searches Splunk automatically (across multiple environments), and delivers an AI-summarized diagnosis to you on Telegram.

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
# JSON object mapping environment names to SPL templates. Must be valid JSON on a single line.
# {transaction_id} is replaced at query time. Environments sharing the same index reuse the same template.
SPLUNK_SPL_TEMPLATES={"prod":"index=\"your_prod_index\" \"{transaction_id}\"","dev":"index=\"your_preprod_index\" \"{transaction_id}\"","staging":"index=\"your_preprod_index\" \"{transaction_id}\"","uat":"index=\"your_preprod_index\" \"{transaction_id}\""}
SPLUNK_SESSION_PATH=splunk_session.json
SPLUNK_RESULT_WAIT_TIMEOUT=30
SPLUNK_SSO_DOMAIN=login.your-company.com
SPLUNK_SSO_EMAIL=your_work_email@company.com
SPLUNK_SSO_EMPLOYEE_ID=your_employee_id
SPLUNK_SSO_PASSWORD=your_sso_password
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
| `SPLUNK_SSO_EMAIL` | Work email for Azure AD login (used by auto-login) |
| `SPLUNK_SSO_EMPLOYEE_ID` | Employee ID for corporate SSO login (used by auto-login) |
| `SPLUNK_SSO_PASSWORD` | SSO password for auto-login |
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
python save_session_auto.py
```

A Chromium browser window opens, auto-fills your SSO credentials, then waits for you to approve the 2FA push on your phone. No manual typing required — just tap approve. The session is saved automatically.

> **Session auto-renews** — when the bot detects an expired session, it automatically relaunches the browser and waits for 2FA. You only need to manually run this if auto-reauth fails.

### 5b. Inspect SSO Selectors (optional)

If your SSO page structure changes, update the selectors in `save_session_auto.py` using:

```bash
python sso_scraper.py
```

Walk through the login flow in the browser — it captures all form inputs, buttons, and selectors at each step and saves them to `evidences/sso_selectors.json`.

## Running the Bot

### Foreground (for testing)

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
Press `Ctrl+C` for graceful shutdown (drains pending jobs before exiting).

### Background Service (macOS, recommended for daily use)

The bot can run as a **launchd background service** — start and stop it from anywhere without keeping a terminal open.

**One-time setup:**

```bash
chmod +x botctl
./botctl start
```

This installs a launchd plist to `~/Library/LaunchAgents/` and starts the bot immediately.

**Command reference:**

| Command | Description |
|---|---|
| `./botctl start` | Start the bot (installs plist if needed) |
| `./botctl stop` | Stop the bot completely |
| `./botctl restart` | Stop then start |
| `./botctl status` | Show running state and PID |
| `./botctl logs` | Tail `logs/bot.log` |
| `./botctl uninstall` | Remove plist and stop bot |

**Behavior:**

- **Does NOT auto-start on login or reboot** — you must run `./botctl start` manually
- **Auto-restarts on crash** — if the bot crashes while running, launchd restarts it automatically
- **`./botctl stop` fully stops** — kills the process and prevents auto-restart until you `start` again

> **Tip:** To run `botctl` from anywhere, symlink it:  
> `ln -s /path/to/bugs-bot/botctl /usr/local/bin/botctl`

**Updating configuration:**

If you edit `.env`, restart to pick up new values:

```bash
./botctl restart
```

**Renewing Splunk session:**

The bot auto-re-authenticates when session expiry is detected — no manual action needed. Just approve the 2FA push on your phone when the browser pops open.

If auto-reauth fails:

```bash
python save_session_auto.py   # approve 2FA in browser
./botctl restart             # if running as background service
```

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
QA selects env (/prod, /dev, etc.)
      │
      │  sends raw curl snippet
      ▼
┌─────────────────────┐
│   Telegram Bot      │  checks: private chat? allowlisted? env set?
│   (message handler) │
└────────┬────────────┘
         │ extract transaction-id + environment
         ▼
┌─────────────────────┐
│   Curl Parser       │  extracts headers, url, method
└────────┬────────────┘
         │ enqueue job with environment
         ▼
┌─────────────────────┐
│   Asyncio Job Queue │  FIFO, one worker at a time, max 10
│   + Graceful Drain  │
└────────┬────────────┘
         │ dequeue
         ▼
┌─────────────────────┐        ┌──────────────────────┐
│   Splunk Scraper    │───────▶│  Splunk (web browser) │
│   (Playwright)      │◀───────│  via Global Protect   │
│   uses env-specific │        └──────────────────────┘
│   SPL template      │
└────────┬────────────┘
         │ raw log lines
         ▼
┌─────────────────────┐        ┌──────────────────────┐
│   LLM Analyzer      │───────▶│  OpenAI API           │
│                     │◀───────│  (e.g. gpt-4o)        │
└────────┬────────────┘        └──────────────────────┘
         │ structured diagnosis
         ▼
┌─────────────────────┐
│   Report Formatter  │
│   + Dual Delivery   │──────▶ QA: simplified summary [dev]
│   + SQLite Writer    │──────▶ You: full technical report [prod]
└────────┬────────────┘
         │
         ▼
   investigations.db  (local SQLite)
```

## Error Handling

| Scenario | Engineer gets | QA gets |
|---|---|---|
| No environment selected | — | "⚠️ Please select an environment first: /prod, /dev, /staging, /uat" |
| Invalid curl | — | Error message with format hint |
| Missing transaction ID header | — | "Could not find X-Transaction-ID header" |
| Queue full (10 jobs) | — | "Bot is busy, please retry" |
| VPN down (3 retries) | "❌ Job abandoned after 3 VPN retries" | "Investigation failed — VPN issue" |
| Splunk session expired | "🔐 Session expired. Auto-re-authenticating..." | "Investigation paused" |
| No logs found | "No logs found for transaction ID [env]" | "No logs found" |
| LLM API error | Raw logs (truncated 3000 chars) + error note | "Engineer is reviewing" |
| Browser crash | "🚨 Browser crashed. Restart the bot." | "Technical issue on our end" |

## Graceful Shutdown

**Foreground:** Press `Ctrl+C`. The bot will:
1. Stop accepting new jobs
2. Send you: "🛑 Shutting down. Draining N remaining job(s)..."
3. Finish all pending jobs
4. Close connections
5. Exit

**Background service:** Run `./botctl stop` — same graceful drain behavior.

If drain takes longer than 5 minutes, it force-exits with a warning.

## Project Structure

```
bugs-bot/
├── main.py                  # Entry point, signal handlers, job pipeline
├── save_session_auto.py     # Auto-login script: fills SSO credentials, waits for 2FA
├── save_session.py          # Legacy manual login script (fallback)
├── sso_scraper.py           # SSO page selector inspector
├── config.py                # Loads .env, exposes typed constants
├── .env                     # Secrets (gitignored)
├── .env.example             # Template for onboarding
├── requirements.txt
├── start.sh                 # Convenience startup script (foreground)
├── botctl                   # Background service control script
├── com.findbugs.bot.plist.template # launchd service definition template
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

**Automatic (v2.0.0):** When the session expires, the bot automatically relaunches the browser, fills credentials, and waits for you to approve the 2FA push on your phone. No manual intervention needed.

**Manual fallback:** If auto-reauth fails, run:

```bash
python save_session_auto.py
# Approve 2FA on your phone in the browser
# Bot picks up the new session automatically
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `splunk_api_session.json not found` | Bot auto-logins if credentials are set in `.env`. Otherwise run `python save_session_auto.py` |
| Bot doesn't respond to group messages | Group chats are not supported — use private chat only |
| VPN check always fails | Ensure Global Protect is connected and `VPN_CHECK_HOST` is correct |
| SSO auto-login fails | Check `SPLUNK_SSO_EMAIL`, `SPLUNK_SSO_EMPLOYEE_ID`, and `SPLUNK_SSO_PASSWORD` in `.env`. Run `python sso_scraper.py` to update selectors if SSO page changed |
| LLM gives wrong diagnosis | Raw logs are always saved to SQLite. Check `/history` or query `investigations.db` directly |
| Bot not running after reboot | Expected — run `./botctl start` manually |
| `launchctl` error / plist not found | Run `./botctl start` to install the plist |
| Bot keeps restarting on crash | Check `logs/launchd-stderr.log` — likely VPN down or session expired |