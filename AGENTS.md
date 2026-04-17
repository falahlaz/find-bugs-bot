# AGENTS.md

## What this is

A Telegram bot that takes curl snippets from QA, queries Splunk via REST API, runs logs through OpenAI, and returns a diagnosis card on Telegram. Python 3.11+, no framework (pure asyncio + python-telegram-bot). **Version 2.0.0** — includes automated SSO re-authentication.

## Commands

```bash
# Setup (first time)
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Save Splunk SSO session — auto-fills credentials, just approve 2FA on phone
python save_session_auto.py

# Run the bot (VPN must be connected)
source venv/bin/activate
python main.py

# Or: ./start.sh  (auto-activates venv)

# Inspect Splunk network API calls (captures XHR/fetch requests during browser session)
python splunk_inspector.py

# Capture exact SSO page selectors (for updating login automation)
python sso_scraper.py
```

No test suite, no linter, no type checker configured.

## Architecture

- **Entry point**: `main.py` — wires Telegram handlers, asyncio job queue, and the processing pipeline.
- **Config**: `config.py` — loads `.env` via `python-dotenv`, exposes typed module-level constants. All env vars are required unless noted in `.env.example`.
- **Pipeline**: Telegram message → `parser/curl_parser.py` (extract transaction ID) → `jobqueue/job_queue.py` (FIFO, max 10) → `scraper/splunk_scraper.py` → `scraper/splunk_api.py` (Splunk REST API via httpx) → `analyzer/llm_analyzer.py` (OpenAI) → `bot/formatter.py` (dual delivery: engineer gets full report, QA gets summary) → `storage/database.py` (SQLite).

## Key conventions

- **No `tests/` directory** — this project has no automated tests.
- **No CI** — no `.github/workflows` or pre-commit hooks.
- **Package imports use relative paths within the repo** — e.g. `from scraper.splunk_api import splunk_api`. All packages are plain directories with `__init__.py`.
- **Playwright is sync only** in `save_session_auto.py` for SSO auto-login. The main bot uses pure HTTP (httpx) — no browser at runtime.
- **`requirements/`** contains prompt/requirement docs (`prompt.md`, `requirement.md`), not pip requirement files.
- **Auto re-authentication** — when the Splunk session expires, the bot automatically relaunches the browser, fills credentials, and waits for 2FA approval without requiring manual intervention.

## Splunk API integration

The bot queries Splunk through its REST API, proxied via the web UI path `/en-US/splunkd/__raw/`. Authentication uses SSO session cookies extracted from `save_session_auto.py`. The 3-step search flow:

1. **POST** `/services/search/v2/jobs` — create search job, returns `sid`
2. **GET** `/services/search/v2/jobs/{sid}` — poll until `isDone=true`
3. **GET** `/services/search/v2/jobs/{sid}/events` — fetch raw log events as JSON

Required cookies (extracted by `save_session_auto.py`): `splunkd_8008`, `session_id_8008`, `splunkweb_csrf_token_8008`, `token_key`. The CSRF token must also be sent as `X-Splunk-Form-Key` header.

## Gotchas

- **VPN required at runtime** — the bot TCP-probes `VPN_CHECK_HOST` before each Splunk query. Jobs retry 3× with 60s delays if VPN is down.
- **Splunk session cookies expire** — the bot auto-re-authenticates when session expiry is detected. If auto re-auth fails, run `python save_session_auto.py` to manually refresh.
- **`SPLUNK_SPL_TEMPLATES` in `.env`** is a JSON string on a single line — `config.py` parses it with `json.loads()`. Environments are dynamically registered as Telegram commands from the keys of this dict.
- **`LLM_SKIP_SSL_VERIFY`** defaults to `true` (corporate VPN/proxy environment).
- **SQLite database** (`investigations.db`) is gitignored and created at runtime.
- **SSO credentials** — set `SPLUNK_SSO_EMAIL`, `SPLUNK_SSO_EMPLOYEE_ID`, and `SPLUNK_SSO_PASSWORD` in `.env` to enable auto-login.

## Directories

| Path | Purpose |
|---|---|
| `bot/` | Telegram message handlers and report formatters |
| `parser/` | Curl command parser (extracts headers, URL, method) |
| `jobqueue/` | Asyncio FIFO queue with graceful drain on shutdown |
| `scraper/` | Splunk API client (`splunk_api.py`), VPN check, legacy browser manager |
| `analyzer/` | OpenAI LLM integration |
| `storage/` | SQLite read/write for investigation records |
| `evidences/` | Evidence artifacts — SSO selector captures, error screenshots (gitignored) |
| `logs/` | Rotating log files (gitignored) |
