# Telegram Debugging Bot — Comprehensive Build Plan

> A bot that accepts curl snippets from QA, searches Splunk automatically, and delivers an AI-summarized diagnosis to you on Telegram.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Project Structure](#3-project-structure)
4. [Module Breakdown](#4-module-breakdown)
5. [Data Flow](#5-data-flow)
6. [Configuration & Environment](#6-configuration--environment)
7. [Splunk Session Management](#7-splunk-session-management)
8. [LLM Prompt Design](#8-llm-prompt-design)
9. [Database Schema](#9-database-schema)
10. [Error Handling Strategy](#10-error-handling-strategy)
11. [Build Phases](#11-build-phases)
12. [Running the Bot](#12-running-the-bot)
13. [Known Risks & Mitigations](#13-known-risks--mitigations)

---

## 1. Project Overview

### Problem

- Bugs are reported concurrently by QA
- Each bug requires manually asking for a curl, searching Splunk by transaction ID, reading logs, then starting debugging
- This is repetitive and unscalable as QA team grows

### Solution

A Telegram bot running on your local machine that:

1. Receives a raw curl snippet from QA via Telegram
2. Parses the `transaction-id` (or equivalent) header from the curl
3. Enqueues the job so concurrent reports are handled gracefully
4. Opens Splunk headlessly via Playwright (using your saved SSO session)
5. Runs a SPL query with the transaction ID
6. Sends the raw logs to an LLM for diagnosis
7. Reports back to you on Telegram with a structured summary

### Constraints

- Splunk is only accessible via **Global Protect VPN**
- Splunk login is via **SSO** — automated via saved Playwright session state
- Bot runs **locally on your machine** (no cloud deployment)
- QA team is **small and trusted** — no auth layer needed on the bot

---

## 2. Architecture

```
QA / Reporter
     │
     │  sends raw curl snippet
     ▼
┌─────────────────────┐
│   Telegram Bot      │  python-telegram-bot
│   (message handler) │
└────────┬────────────┘
         │ extract transaction-id
         ▼
┌─────────────────────┐
│   Curl Parser       │  regex-based header extractor
└────────┬────────────┘
         │ enqueue job
         ▼
┌─────────────────────┐
│   Asyncio Job Queue │  FIFO, one worker at a time
│   + Graceful Drain  │  full drain on SIGTERM/Ctrl+C
└────────┬────────────┘
         │ dequeue
         ▼
┌─────────────────────┐        ┌──────────────────────┐
│   Splunk Scraper    │───────▶│  Splunk (web browser) │
│   (Playwright)      │◀───────│  via Global Protect   │
└────────┬────────────┘        └──────────────────────┘
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
│   + Dual Delivery   │──────▶ QA: simplified summary
│   + SQLite Writer   │──────▶ You: full technical report
└────────┬────────────┘
         │
         ▼
   investigations.db  (local SQLite)
```

---

## 3. Project Structure

```
debug-bot/
├── main.py                  # entry point, starts bot + worker, handles shutdown
├── save_session.py          # one-time script to save Splunk SSO session
├── config.py                # loads .env, exposes typed settings
├── .env                     # secrets (never commit this)
├── .env.example             # template for onboarding
├── splunk_session.json      # saved Playwright session state (gitignored)
├── investigations.db        # SQLite database (gitignored)
├── requirements.txt
├── logs/
│   └── bot.log              # rotating log file (auto-created)
│
├── bot/
│   ├── __init__.py
│   ├── handler.py           # Telegram message handlers
│   └── formatter.py         # formats both engineer + QA report messages
│
├── parser/
│   ├── __init__.py
│   └── curl_parser.py       # extracts transaction-id from raw curl
│
├── queue/
│   ├── __init__.py
│   └── job_queue.py         # asyncio queue + background worker + graceful drain
│
├── scraper/
│   ├── __init__.py
│   ├── browser.py           # browser lifecycle manager (launch, crash recovery)
│   ├── splunk_scraper.py    # Playwright automation for Splunk
│   └── vpn_check.py        # checks VPN connectivity before scraping
│
├── analyzer/
│   ├── __init__.py
│   └── llm_analyzer.py      # sends logs to OpenAI, returns structured diagnosis
│
└── storage/
    ├── __init__.py
    └── database.py          # SQLite read/write for past investigations
```

---

## 4. Module Breakdown

### `main.py`

- Loads config
- Configures structured logging (console + rotating file `logs/bot.log`, max 5MB, keep 3 files) using Python's built-in `logging` module. Format: `[timestamp] [LEVEL] [module] message`
- Initializes SQLite database via `storage/database.py`
- Launches and holds the Playwright browser instance via `scraper/browser.py`
- Starts the asyncio job queue worker as a background task
- Starts the Telegram polling loop
- Registers `SIGTERM` and `SIGINT` (Ctrl+C) signal handlers for graceful shutdown:
  - Sets a global `shutting_down` flag so the handler stops accepting new jobs
  - Sends Telegram message to engineer: "🛑 Bot is shutting down. Draining {n} remaining jobs..."
  - Waits for the queue to fully drain (all pending jobs complete)
  - Closes the Playwright browser cleanly
  - Exits

**Python version:** 3.11+ required. Use `zoneinfo` (stdlib) for timezone handling — no third-party dependency needed.

---

### `save_session.py`

A **standalone script**, run manually by you — not part of the bot's runtime.

**Responsibilities:**
- Launch a real (non-headless) Playwright browser
- Navigate to the Splunk URL
- Wait for you to complete SSO login manually
- Save the full browser session (cookies + local storage) to `splunk_session.json`

**When to run:**
- First time setup
- Whenever the bot sends a "session expired" alert

---

### `config.py`

Loads all values from `.env` and exposes them as typed constants. Every other module imports from here — no module reads `.env` directly.

**Values it must expose:**
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_YOUR_CHAT_ID` — engineer's personal chat ID for full technical reports
- `TELEGRAM_ALLOWED_CHAT_IDS` — comma-separated list of QA chat IDs permitted to submit curls
- `SPLUNK_URL` — base URL of your Splunk instance
- `SPLUNK_SPL_TEMPLATE` — your SPL query with a `{transaction_id}` placeholder
- `SPLUNK_SESSION_PATH` — path to `splunk_session.json`
- `SPLUNK_RESULT_WAIT_TIMEOUT` — how long to wait for Splunk results (seconds)
- `MAX_LOG_LINES` — max log lines to extract and send to LLM (default: 100)
- `LLM_API_KEY` — your OpenAI API key
- `LLM_MODEL` — e.g. `gpt-4o` or `gpt-4o-mini` (mini is cheaper for high-volume use)
- `VPN_CHECK_HOST` — internal hostname only reachable via VPN, used to detect connectivity
- `TRANSACTION_ID_HEADER` — exact header name QA includes in their curl (e.g. `X-Transaction-ID`)
- `TIMEZONE` — IANA timezone string for timestamps in reports (default: `Asia/Jakarta`)
- `DB_PATH` — path to SQLite file (default: `investigations.db`)

---

### `bot/handler.py`

Handles all incoming Telegram messages.

**Guards (checked on every message):**
- Ignore any message where `chat.type != "private"` — group chats are not supported
- If `shutting_down` flag is set: reply "🛑 Bot is shutting down, not accepting new jobs." and stop

**On receiving a curl message:**
1. Check if the sender's chat ID is in `TELEGRAM_ALLOWED_CHAT_IDS`
2. Check if the message looks like a curl command (starts with `curl` or contains `-H`)
3. If valid: reply "✅ Received. Investigating `{transaction_id}`..." and enqueue the job
4. If invalid: reply with a usage hint showing the expected format

**On receiving `/status` command:**

Reply with a full status block:
```
📊 Bot status
• Queue: 2 jobs pending
• Now processing: txn-abc-123
• VPN: ✅ Connected
• Splunk session: ✅ Valid (saved 3h ago)
```
The VPN check is a live TCP probe. Session age is derived from `splunk_session.json` file modification time.

**On receiving `/help` command:**
- Reply with instructions on how to submit a curl, including which header must be present

**On receiving `/history` command (engineer only):**
- Queries SQLite for the last 5 investigations and returns a compact summary list

---

### `parser/curl_parser.py`

Parses a raw curl string and extracts relevant data.

**Input:** raw curl string (multiline is fine)

**Output:**
```python
{
  "transaction_id": "abc-123-xyz",
  "method": "POST",
  "url": "https://api.yourapp.com/v1/orders",
  "headers": { ... },
  "body": "..."   # optional
}
```

**Logic:**
- Use `shlex.split()` to tokenize the curl command safely
- Extract `-H` flags and parse each as `Key: Value`
- Look for the transaction ID header by name (configurable in `.env` as `TRANSACTION_ID_HEADER`)
- If transaction ID is not found, raise a descriptive error so the bot can reply to QA with a clear message

**Edge cases to handle:**
- Multiline curl with `\` line continuations
- Header name is case-insensitive
- curl with `--header` (long form) instead of `-H`
- Missing transaction ID header → bot tells QA exactly which header is missing

---

### `queue/job_queue.py`

Manages concurrent bug reports gracefully.

**Structure:**
- An `asyncio.Queue` holds incoming jobs
- A single background worker coroutine processes jobs one at a time (FIFO)
- Each job is a dict:
```python
{
  "transaction_id": "abc-123-xyz",
  "requester_chat_id": 123456789,
  "raw_curl": "curl ...",
  "vpn_retries": 0          # incremented on each VPN failure, max 3
}
```

**Why one worker at a time:**
- Playwright runs one browser instance — parallel jobs would require multiple browsers, which is too heavy for a local machine
- Splunk may rate-limit rapid queries
- Sequential processing gives you clean, non-interleaved Telegram messages

**Queue depth limit:**
- Cap at 10 jobs max. If QA floods the bot, reply: "Queue is full ({n}/10), please retry in a few minutes."

**Graceful shutdown (full drain):**
- On `SIGTERM` or `Ctrl+C`, the `shutting_down` flag is set in `main.py`
- The handler stops accepting new jobs immediately
- The worker finishes the current job, then continues draining remaining queued jobs one by one
- Once the queue is empty, the worker exits and `main.py` proceeds with cleanup
- If drain takes longer than 5 minutes, force-exit with a warning message to the engineer

**VPN retry state:**
- Retry count lives inside the job dict (`vpn_retries`). Max 3 retries, 60 seconds apart
- If the bot restarts mid-retry, the in-memory queue is lost — this is acceptable. Bot sends "Bot started. Queue is empty." on startup so you know state was reset
- After 3 failed VPN retries, the job is abandoned and you receive: "❌ Job for `{transaction_id}` abandoned after 3 VPN retries. Please resubmit when VPN is stable."

---

### `scraper/browser.py`

Manages the Playwright browser lifecycle for the entire bot process.

**Responsibilities:**
- Launch a single Chromium instance at bot startup (headless, with `splunk_session.json` loaded)
- Expose a `get_page()` method that returns a fresh page from the persistent browser context
- If a page or browser crashes mid-job, catch the exception and attempt a full browser restart (re-launch + reload session). Retry the failed job once automatically
- If restart fails, alert the engineer: "🚨 Playwright browser crashed and could not recover. Restart the bot."
- On graceful shutdown, close the browser cleanly after the queue is drained

**Why one shared browser instance:**
- Avoids the overhead of launching a new browser per job (slow + memory-heavy on a local machine)
- The session state (cookies) is loaded once and reused across all jobs

---

### `scraper/vpn_check.py`

Before every Splunk query, verify VPN is active.

**Logic:**
- Attempt a TCP connection to `VPN_CHECK_HOST` on port 443 with a short timeout (2–3 seconds)
- If it fails → do not attempt Splunk, immediately alert you on Telegram: "⚠️ VPN not connected. Job for `{transaction_id}` paused."
- Re-queue the job with a delay (e.g. retry after 60 seconds, max 3 retries)

---

### `scraper/splunk_scraper.py`

The most critical and most fragile component.

**Startup:**
- Load `splunk_session.json` into Playwright's browser context
- Run in headless mode

**Per-job flow:**

1. Navigate to Splunk search page
2. Detect if redirected to SSO login page → if yes, send Telegram alert: "🔐 Splunk session expired. Run `python save_session.py` to renew." and abort job
3. Clear the search bar
4. Type the SPL query (populated from `SPLUNK_SPL_TEMPLATE` with the transaction ID)
5. Submit the search
6. Wait for results to load — use Playwright's `wait_for_selector` on the results container, not a fixed `sleep`
7. Extract log lines from the results table as plain text
8. If no results found → return a "no logs found" signal so the bot can report that to you
9. Close the page (not the browser context — reuse it for the next job)

**Selector strategy:**
- Use `playwright codegen https://your-splunk-url` to auto-record the exact selectors for your Splunk instance
- Store selectors as named constants at the top of the file so they're easy to update if Splunk's UI changes

**Result extraction:**
- Extract up to `MAX_LOG_LINES` lines (configurable, default 100) to avoid sending too much to the LLM
- Return as a plain string, one log line per line

---

### `analyzer/llm_analyzer.py`

Sends raw logs to the LLM and returns a structured diagnosis.

**Input:** `transaction_id` + raw log string

**Output:**
```python
{
  "summary": "A NullPointerException was raised in OrderService.processPayment()",
  "error_type": "NullPointerException",
  "failed_component": "OrderService",
  "likely_cause": "paymentMethod was null — not validated before processing",
  "severity": "high",
  "suggested_action": "Add null check on paymentMethod before calling .charge()"
}
```

**Provider:** OpenAI only — uses the `openai` Python SDK directly. No abstraction layer needed.

Use `response_format={ "type": "json_object" }` in the API call to enforce JSON output natively — this eliminates the need to parse or clean the response manually.

---

### `bot/formatter.py`

Formats two distinct Telegram messages from the same diagnosis result.

**Engineer report (full technical):**
```
🐛 Bug Report — transaction-id: abc-123-xyz
━━━━━━━━━━━━━━━━━━━━━━━━━
📍 Failed component:  OrderService
❌ Error type:        NullPointerException
🔍 Likely cause:      paymentMethod was null — not validated before processing
🚨 Severity:          High
💡 Suggested action:  Add null check on paymentMethod before calling .charge()
━━━━━━━━━━━━━━━━━━━━━━━━━
📋 Summary:
A NullPointerException was raised in OrderService.processPayment()
when the request reached the payment processing step.

👤 Reported by: chat_id 987654321
🕐 Queried at: 2025-04-14 10:32:01 WIB
```

**QA report (simplified):**
```
✅ Investigation complete — transaction-id: abc-123-xyz

🚨 Severity: High
📋 What happened:
A NullPointerException was raised in OrderService.processPayment()
when the request reached the payment processing step.

The engineering team has been notified and is looking into it.
```

**Telegram 4096-character limit handling:**
- The structured diagnosis card will always stay well under the limit
- The only risk is the LLM failure fallback (raw logs). Strategy: truncate raw logs to the **last 3000 characters** (most recent = most relevant) and prepend: `⚠️ LLM analysis failed — last 3000 chars of logs below:`
- If even the truncated message exceeds 4096 chars, split into two sequential messages

**Timezone:**
- All timestamps use `TIMEZONE` from `.env` (default: `Asia/Jakarta` → WIB)
- Use Python `zoneinfo.ZoneInfo(TIMEZONE)` — no third-party library needed (stdlib in 3.11+)

### `storage/database.py`

Manages all SQLite read/write for past investigations. See [Section 9](#9-database-schema) for the full schema.

**Responsibilities:**
- Initialize the database and create tables on first run if they don't exist
- `save_investigation(job, diagnosis, status)` — called after every job completes (success or failure)
- `get_recent(limit=5)` — returns the last N investigations for the `/history` command
- `get_by_transaction_id(txn_id)` — look up a specific past investigation

**Notes:**
- Use Python's built-in `sqlite3` module — no ORM needed
- All writes are synchronous but wrapped in `asyncio.to_thread()` so they don't block the event loop
- `investigations.db` is gitignored

---



## 5. Data Flow

```
[QA sends curl in private chat]
      │
      ├─ group chat? → ignore silently
      ├─ shutting_down? → reply "not accepting jobs"
      │
      ▼
handler.py receives message
      │
      ├─ not in allowlist? → ignore silently
      ├─ invalid curl? → reply with error to QA, stop
      │
      ▼
curl_parser.py extracts transaction_id
      │
      ├─ no transaction_id header? → reply "missing header X" to QA, stop
      │
      ▼
job_queue.py enqueues job { transaction_id, requester_chat_id, raw_curl, vpn_retries: 0 }
handler.py replies to QA: "✅ Investigating abc-123..."
      │
      ▼
worker picks up job
      │
vpn_check.py checks VPN (TCP probe)
      │
      ├─ VPN down, retries < 3? → wait 60s, increment vpn_retries, retry
      ├─ VPN down, retries == 3? → alert engineer, notify QA "investigation failed", save to DB, stop
      │
      ▼
browser.py provides a page from the shared browser instance
      │
      ├─ browser crashed? → auto-restart once, retry job
      ├─ restart failed? → alert engineer, stop
      │
      ▼
splunk_scraper.py runs SPL query
      │
      ├─ session expired? → alert engineer to re-run save_session.py, stop (pause queue)
      ├─ no results? → report "no logs found" to engineer + QA, save to DB, stop
      │
      ▼
llm_analyzer.py sends logs to OpenAI
      │
      ├─ API error? → send raw logs (truncated to 3000 chars) to engineer, notify QA "check with engineer", save to DB, stop
      │
      ▼
formatter.py builds two messages:
      ├─ full technical report → send to TELEGRAM_YOUR_CHAT_ID (engineer)
      └─ simplified summary   → send to requester_chat_id (QA)
      │
      ▼
storage/database.py saves investigation to SQLite
```

---

## 6. Configuration & Environment

### `.env.example`

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_YOUR_CHAT_ID=your_personal_chat_id
TELEGRAM_ALLOWED_CHAT_IDS=chat_id_1,chat_id_2,chat_id_3   # QA members

# Splunk
SPLUNK_URL=https://your-splunk-instance.example.com
SPLUNK_SPL_TEMPLATE=index="prod" transaction_id="{transaction_id}" | head 100
SPLUNK_SESSION_PATH=splunk_session.json
SPLUNK_RESULT_WAIT_TIMEOUT=30
MAX_LOG_LINES=100

# VPN check
VPN_CHECK_HOST=internal.your-company.com
TRANSACTION_ID_HEADER=X-Transaction-ID   # exact header name QA sends

# LLM (OpenAI)
LLM_API_KEY=your_openai_api_key
LLM_MODEL=gpt-4o                         # or: gpt-4o-mini (cheaper, slightly less accurate)

# App
TIMEZONE=Asia/Jakarta                    # IANA timezone string
DB_PATH=investigations.db
```

### `requirements.txt`

```
python-telegram-bot[asyncio]
playwright
openai
python-dotenv
```

> All other dependencies (`sqlite3`, `logging`, `zoneinfo`, `asyncio`, `shlex`) are Python 3.11+ stdlib — no extra packages needed.

---

## 7. Splunk Session Management

### First-time Setup

```bash
python save_session.py
# A browser window opens
# Complete your SSO login normally
# Press Enter in the terminal when done
# splunk_session.json is saved
```

### Session Reuse

Every time `splunk_scraper.py` starts, it loads `splunk_session.json` into the Playwright browser context. No login needed unless the session expires.

### Session Expiry Detection

Inside the scraper, after navigating to Splunk, check:

```
if current URL contains SSO provider domain → session expired
```

When detected:
- Send Telegram message to you: "🔐 Splunk session expired. Run `python save_session.py` to renew."
- Mark the job as failed
- Do not process further jobs until session is renewed (or optionally keep processing if you want — configure this behavior in `.env`)

### Security Note

`splunk_session.json` contains your Splunk session cookies. Add it to `.gitignore`. Never commit it.

---

## 8. LLM Prompt Design

### System Prompt

```
You are a backend debugging assistant for a software engineering team.
You will receive raw application logs from a production system, identified by a transaction ID.
Your job is to analyze what went wrong and explain it clearly to the backend engineer who will fix it.
Be precise, technical, and concise. Do not speculate beyond what the logs show.
```

### User Prompt

```
Transaction ID: {transaction_id}

Raw logs:
---
{log_lines}
---

Analyze the logs above and respond in the following JSON format only, no other text:

{
  "summary": "one or two sentence description of what happened",
  "error_type": "e.g. NullPointerException, TimeoutError, 404, etc.",
  "failed_component": "the service, class, function, or endpoint where it failed",
  "likely_cause": "your best diagnosis of root cause based on the logs",
  "severity": "low | medium | high | critical",
  "suggested_action": "specific next step the engineer should take"
}

If the logs do not contain enough information to diagnose the issue, set likely_cause to
"Insufficient log detail" and suggest what additional logging would help.
```

### Why JSON response

- Easy to parse and map to `formatter.py` fields
- OpenAI's `response_format: json_object` enforces valid JSON natively — no cleanup needed
- Allows future features like storing diagnoses to a local SQLite database

---

## 9. Database Schema

Single table in `investigations.db`. Created automatically on first bot startup.

```sql
CREATE TABLE IF NOT EXISTS investigations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id      TEXT NOT NULL,
    requester_chat_id   INTEGER NOT NULL,        -- QA member who submitted
    submitted_at        TEXT NOT NULL,            -- ISO8601 timestamp (UTC)
    status              TEXT NOT NULL,            -- 'success' | 'failed' | 'no_logs'
    error_type          TEXT,                     -- from LLM diagnosis
    failed_component    TEXT,                     -- from LLM diagnosis
    severity            TEXT,                     -- 'low' | 'medium' | 'high' | 'critical'
    summary             TEXT,                     -- full LLM summary
    likely_cause        TEXT,                     -- from LLM diagnosis
    suggested_action    TEXT,                     -- from LLM diagnosis
    raw_log_snippet     TEXT,                     -- first 2000 chars of log for reference
    failure_reason      TEXT                      -- populated on status != 'success'
);
```

**Usage patterns:**
- Every completed job writes one row, regardless of outcome
- `/history` command queries: `SELECT * FROM investigations ORDER BY id DESC LIMIT 5`
- No cleanup/TTL policy for now — local SQLite file, disk space is not a concern at this scale

---

## 10. Error Handling Strategy

Every failure should produce a useful output — never silent failures. All failures are saved to SQLite with `status = 'failed'` and a `failure_reason`.

| Failure point | Engineer receives | QA receives |
|---|---|---|
| Invalid curl | — | "That doesn't look like a valid curl. Please paste the full curl command." |
| Missing transaction ID header | — | "Could not find `X-Transaction-ID` header. Please make sure it's included." |
| Not in allowlist | — | *(silently ignored)* |
| Group chat message | — | *(silently ignored)* |
| Queue full | — | "Bot is busy ({n}/10 jobs). Please retry in a few minutes." |
| VPN down after 3 retries | "❌ Job for `{txn_id}` abandoned after 3 VPN retries." | "Investigation failed — please resubmit when the team is available." |
| Playwright browser crash (unrecoverable) | "🚨 Playwright crashed and could not recover. Restart the bot." | "Investigation failed — technical issue on our end." |
| Splunk session expired | "🔐 Session expired. Run `python save_session.py`." | "Investigation paused — will resume shortly." |
| No logs found | "No logs found for `{txn_id}`. ID may be wrong or logs may have rolled off." | "No logs found for your report. The engineer has been notified." |
| LLM API error | Raw logs truncated to 3000 chars + "LLM failed — raw logs attached." | "Investigation complete — engineer is reviewing manually." |
| LLM returns malformed JSON | Raw LLM text response + "Could not parse structured diagnosis." | "Investigation complete — engineer is reviewing." |
| Shutdown while jobs pending | "🛑 Shutting down. Draining {n} jobs..." then job-by-job completion | *(jobs still processed normally)* |

---

## 11. Build Phases

Build in this order — each phase is independently runnable and testable.

### Phase 1 — Bot skeleton (1–2 hrs)

- Set up the project folder, `requirements.txt`, and `venv`
- Configure structured logging to console + `logs/bot.log` (rotating, 5MB, 3 files)
- Create a Telegram bot via BotFather, get the token
- Build `handler.py` with private-chat-only guard and allowlist check; replies "echo: {message}" for now
- Get your own chat ID and add it to `.env`
- Confirm the bot responds on Telegram

**Done when:** you can send a message in private chat and get an echo reply.

---

### Phase 2 — Curl parser (1 hr)

- Build and unit test `curl_parser.py` with real curl examples from your QA workflow
- Wire it into `handler.py` — bot now replies with the extracted transaction ID
- Handle all edge cases: multiline curl, `--header` long form, case-insensitive header names

**Done when:** bot replies "Got it — transaction ID: `abc-123`" when QA sends a curl.

---

### Phase 3 — Job queue + graceful shutdown (2–3 hrs)

- Build `job_queue.py` with asyncio queue, FIFO worker, and 10-job cap
- Worker just logs "processing job {id}" for now
- Bot replies "✅ Investigating..." immediately, processes asynchronously
- Implement `SIGTERM`/`SIGINT` handlers in `main.py` with full-drain behavior
- Test: send 3 curls, then hit Ctrl+C — confirm all 3 complete before exit

**Done when:** queue drains cleanly on shutdown, and concurrent curls are processed one by one.

---

### Phase 4 — SQLite storage (1 hr)

- Build `storage/database.py` with the schema from Section 9
- Wire `save_investigation()` call into the worker (initially saves stub data)
- Add `/history` command to `handler.py` (engineer only)

**Done when:** `/history` returns the last 5 investigations from the database.

---

### Phase 5 — Splunk scraper (4–8 hrs)

This is the biggest unknown. Allocate extra time.

- Run `playwright codegen {SPLUNK_URL}` to auto-record your exact click/type/wait sequence
- Build `save_session.py` — confirm session saves and reloads correctly
- Build `scraper/browser.py` — shared browser instance with crash recovery
- Build `vpn_check.py` — test with VPN on and off
- Build `splunk_scraper.py` using the codegen output; store all selectors as named constants at the top of the file
- Test with a real transaction ID you already know the logs for

**Done when:** worker successfully returns raw log text for a known transaction ID.

---

### Phase 6 — LLM integration + dual delivery (2–3 hrs)

- Build `llm_analyzer.py` using `response_format: json_object` for clean parsing
- Build `formatter.py` with both engineer (full) and QA (simplified) message formats
- Implement Telegram 4096-char truncation logic
- Wire everything together in the worker pipeline
- Test with real log samples — tune the prompt from Section 8 if needed

**Done when:** engineer receives a full diagnosis card, QA receives a simplified summary.

---

### Phase 7 — Polish & hardening (1–2 hrs)

- Expand `/status` to show queue depth, VPN status, session file age, current job
- Add `TIMEZONE` support to all timestamps using `zoneinfo`
- Test all error paths from Section 10
- Add the shutdown drain message: "🛑 Shutting down, draining {n} jobs..."
- Write a startup shell script or alias (`./start.sh`)
- Add `investigations.db` and `splunk_session.json` to `.gitignore`

**Done when:** every error path sends the right message to the right person, and the bot shuts down cleanly.

---

## 12. Running the Bot

### Requirements

- Python **3.11+** (uses `zoneinfo` and modern asyncio features from stdlib)
- Global Protect VPN must be connected before starting

### First-time setup

```bash
cd debug-bot
python3.11 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# fill in .env with your actual values

python save_session.py         # complete SSO login in the browser that opens
# splunk_session.json is created
```

### Daily use

```bash
# 1. Connect Global Protect VPN first
# 2. Then:
source venv/bin/activate
python main.py
# Bot logs to console + logs/bot.log
# investigations.db is created automatically on first run
```

### Renewing the Splunk session

```bash
python save_session.py
```

Run whenever the bot sends you a "🔐 Splunk session expired" alert. Typically needed every few hours to a few days depending on your company's SSO timeout policy.

### Checking past investigations

Use the `/history` command in Telegram (engineer chat only) to see the last 5 investigations, or query `investigations.db` directly with any SQLite client.

---

## 13. Known Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Splunk UI update breaks selectors | Medium | High | Store selectors as named constants in `splunk_scraper.py`; re-run `playwright codegen` to regenerate |
| SSO session expires mid-shift | High | Medium | Bot detects redirect to SSO URL and alerts immediately; re-running `save_session.py` takes < 2 min |
| VPN disconnects during query | Medium | Low | VPN check before each job; auto-retry up to 3× with 60s delay; job abandoned gracefully after max retries |
| Playwright browser crash | Low | Medium | `browser.py` auto-restarts the browser and retries the job once; alerts engineer if unrecoverable |
| LLM gives wrong diagnosis | Medium | Medium | Raw log snippet always saved to SQLite and included in engineer report for manual verification |
| QA sends malformed curl | High | Low | Parser handles gracefully and replies with specific error message |
| Splunk rate-limits rapid queries | Low | Medium | One worker at a time naturally limits query rate |
| Local machine goes to sleep | High | High | Disable sleep mode when the bot needs to be available; consider running on an always-on machine long-term |
| Queue lost on restart | Medium | Low | In-memory only by design — acceptable for local use. Bot sends "Queue is empty" on startup so you know state was reset |
| SQLite file grows unbounded | Low | Low | No TTL implemented. At ~1KB per row, 10,000 investigations ≈ 10MB — not a concern at this scale |

---

## Appendix: Suggested Claude Code Prompt to Start

When you open Claude Code, start with this:

```
I want to build a Telegram bot in Python called "debug-bot".
Here is the full build plan: [paste this document]

Please start with Phase 1: create the project structure and a working
Telegram bot skeleton that receives messages in private chat only and
echoes them back. Use python-telegram-bot with asyncio. Configure
structured logging to console and a rotating log file. Load all config
from a .env file via config.py. Python 3.11+ required.
```

Then proceed phase by phase, referencing the relevant section of this plan each time. For Phase 5 (Splunk scraper), paste your `playwright codegen` output directly into the Claude Code session before asking it to write the scraper.