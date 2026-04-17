import json
import os
from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str | None = None) -> str:
    value = os.getenv(key, default)
    if value is None:
        raise EnvironmentError(f"Missing required env var: {key}")
    return value


def _env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _env_json(key: str, default: dict | None = None) -> dict:
    value = os.getenv(key)
    if value is None:
        if default is not None:
            return default
        raise EnvironmentError(f"Missing required env var: {key}")
    return json.loads(value)


TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_YOUR_CHAT_ID = int(_env("TELEGRAM_YOUR_CHAT_ID"))
TELEGRAM_ALLOWED_CHAT_IDS = {
    int(cid.strip())
    for cid in _env("TELEGRAM_ALLOWED_CHAT_IDS").split(",")
    if cid.strip()
}

SPLUNK_URL = _env("SPLUNK_URL")
SPLUNK_SPL_TEMPLATES = _env_json("SPLUNK_SPL_TEMPLATES")
SPLUNK_ENVIRONMENTS = list(SPLUNK_SPL_TEMPLATES.keys())
SPLUNK_SESSION_PATH = _env("SPLUNK_SESSION_PATH", "splunk_session.json")
SPLUNK_API_SESSION_PATH = _env("SPLUNK_API_SESSION_PATH", "splunk_api_session.json")
SPLUNK_RESULT_WAIT_TIMEOUT = _env_int("SPLUNK_RESULT_WAIT_TIMEOUT", 30)
SPLUNK_SSO_DOMAIN = _env("SPLUNK_SSO_DOMAIN")
SPLUNK_SSO_EMAIL = os.getenv("SPLUNK_SSO_EMAIL")
SPLUNK_SSO_EMPLOYEE_ID = os.getenv("SPLUNK_SSO_EMPLOYEE_ID")
SPLUNK_SSO_PASSWORD = os.getenv("SPLUNK_SSO_PASSWORD")
SPLUNK_POLL_INTERVAL = _env_int("SPLUNK_POLL_INTERVAL", 2)
MAX_LOG_LINES = _env_int("MAX_LOG_LINES", 100)
SPLUNK_DEFAULT_TIME_RANGE = _env("SPLUNK_DEFAULT_TIME_RANGE", "24h")
SPLUNK_TIME_RANGES = {"24": "24h", "48": "48h"}

VPN_CHECK_HOST = _env("VPN_CHECK_HOST")
TRANSACTION_ID_HEADER = _env("TRANSACTION_ID_HEADER", "X-Transaction-ID")

LLM_API_KEY = _env("LLM_API_KEY")
LLM_MODEL = _env("LLM_MODEL", "gpt-4o")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_PROXY = os.getenv("LLM_PROXY") or None
LLM_SKIP_SSL_VERIFY = os.getenv("LLM_SKIP_SSL_VERIFY", "true").lower() == "true"
SPLUNK_SKIP_SSL_VERIFY = os.getenv("SPLUNK_SKIP_SSL_VERIFY", "true").lower() == "true"

TIMEZONE = _env("TIMEZONE", "Asia/Jakarta")
DB_PATH = _env("DB_PATH", "investigations.db")

LOG_DIR = _env("LOG_DIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "bot.log")