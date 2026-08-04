import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

import config

logger = logging.getLogger(__name__)

ENV_DISPLAY_NAMES = {
    "prod": "production",
    "dev": "development",
}


def _env_display(env_key: str) -> str:
    return ENV_DISPLAY_NAMES.get(env_key, env_key)


MAX_TXN_ID_LEN = 128


def _looks_like_transaction_id(text: str) -> bool:
    if not text:
        return False
    return re.fullmatch(r'[A-Za-z0-9\-_.]{1,128}', text) is not None


def make_env_command(env_key: str):
    async def _env_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _set_environment(update, context, env_key)
    _env_handler.__name__ = f"env_{env_key}"
    return _env_handler


def make_time_range_command(tr_key: str):
    async def _tr_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        time_range = config.SPLUNK_TIME_RANGES[tr_key]
        context.user_data["time_range"] = time_range
        await update.message.reply_text(
            f"✅ Time range set to **last {time_range}**.",
            parse_mode="Markdown",
        )
    _tr_handler.__name__ = f"time_range_{tr_key}"
    return _tr_handler


def _env_commands_help() -> str:
    commands = []
    for env in config.SPLUNK_ENVIRONMENTS:
        display = _env_display(env)
        commands.append(f"`/{env}` — set environment to **{display}**")
    return "\n".join(commands)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    env_cmds = _env_commands_help()
    tr_cmds = "\n".join(f"`/{k}` — set time range to last {v}" for k, v in config.SPLUNK_TIME_RANGES.items())
    text = (
        "🐛 *Debug Bot Help*\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "1. Select an environment:\n"
        f"{env_cmds}\n\n"
        "2. Set time range (default: last 24h):\n"
        f"{tr_cmds}\n\n"
        "3. Send your transaction ID (the value of the "
        f"`{config.TRANSACTION_ID_HEADER}` header).\n\n"
        "Examples:\n"
        "`abc-123`\n"
        "`ABC-123-XYZ`\n\n"
        "Or paste a full curl command:\n"
        f"`curl -H \"{config.TRANSACTION_ID_HEADER}: abc-123\" https://api.example.com`\n\n"
        "The bot will:\n"
        " • Search Splunk for logs in the selected environment\n"
        " • Analyze with AI\n"
        " • Send you a diagnosis\n\n"
        "Commands:\n"
        "/help — Show this message\n"
        "/status — Queue & bot status\n"
        "/myid — Show your chat ID (for whitelist requests)\n"
        "/history — Last 5 investigations (engineer only)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report the caller's own chat ID so they can request whitelist access.

    Intentionally not gated on TELEGRAM_ALLOWED_CHAT_IDS — a user needs their ID
    before they can be whitelisted. Only ever discloses the caller's own ID.
    """
    chat_id = update.effective_chat.id
    allowed = chat_id in config.TELEGRAM_ALLOWED_CHAT_IDS
    status = "✅ already whitelisted" if allowed else "⛔️ not whitelisted yet"
    await update.message.reply_text(
        f"🆔 Your chat ID: `{chat_id}`\n"
        f"Status: {status}\n\n"
        "Send this ID to the bot engineer to get access.",
        parse_mode="Markdown",
    )
    logger.info("myid requested by chat_id=%d allowed=%s", chat_id, allowed)


async def _set_environment(update: Update, context: ContextTypes.DEFAULT_TYPE, env_key: str) -> None:
    context.user_data["environment"] = env_key
    display = _env_display(env_key)
    await update.message.reply_text(
        f"✅ Environment set to **{display}** (`/{env_key}`).\n"
        "Now send your transaction ID (or curl command).",
        parse_mode="Markdown",
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.bot_data.get("job_queue"):
        await update.message.reply_text("📊 Bot status: initializing...")
        return

    from scraper.vpn_check import is_vpn_connected
    from scraper.browser import browser_manager

    jq = context.bot_data["job_queue"]
    vpn_ok = await is_vpn_connected()

    session_status = "❓ Not loaded"
    if browser_manager and browser_manager.session_file_path:
        import os
        from datetime import datetime, timezone

        path = browser_manager.session_file_path
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            age_hours = (datetime.now(timezone.utc).timestamp() - mtime) / 3600
            if age_hours < 1:
                session_age = f" (saved {int(age_hours * 60)}m ago)"
            else:
                session_age = f" (saved {int(age_hours)}h ago)"
            session_status = f"✅ Valid{session_age}"
        else:
            session_status = "❌ File missing"

    current = jq.current_job
    current_str = f"`{current['transaction_id']}` [{current['environment']}]" if current else "—"

    text = (
        "📊 *Bot status*\n"
        f"• Queue: {jq.queue_depth} jobs pending\n"
        f"• Now processing: {current_str}\n"
        f"• VPN: {'✅ Connected' if vpn_ok else '❌ Disconnected'}\n"
        f"• Splunk session: {session_status}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != config.TELEGRAM_YOUR_CHAT_ID:
        await update.message.reply_text("⛔ This command is for the engineering team only.")
        return

    from storage.database import get_recent

    rows = await get_recent(limit=5)
    if not rows:
        await update.message.reply_text("📜 No investigations yet.")
        return

    status_emoji = {"success": "✅", "failed": "❌", "no_logs": "⚠️"}
    from zoneinfo import ZoneInfo
    from datetime import datetime

    tz_info = ZoneInfo(config.TIMEZONE)
    lines = ["📜 *Last 5 investigations:*"]
    for row in rows:
        emoji = status_emoji.get(row["status"], "❓")
        dt = row["submitted_at"]
        if dt.endswith("Z"):
            dt = dt.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(dt).astimezone(tz_info)
        ts = parsed.strftime("%Y-%m-%d %H:%M") + f" {parsed.strftime('%Z')}"

        env_str = f"[{row.get('environment', '?')}]" if row.get("environment") else ""

        if row["status"] == "success":
            detail = f"{row['severity'].title()} | {row['error_type'] or '—'}"
        else:
            detail = f"failed: {row['failure_reason'] or 'unknown'}"

        lines.append(f"{emoji} `{row['transaction_id']}` {env_str} | {detail} | {ts}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    if update.effective_chat.type != "private":
        return

    chat_id = update.effective_chat.id
    if chat_id not in config.TELEGRAM_ALLOWED_CHAT_IDS:
        return

    if context.bot_data.get("shutting_down"):
        await update.message.reply_text("🛑 Bot is shutting down, not accepting new jobs.")
        return

    text = update.message.text.strip()

    environment = context.user_data.get("environment")
    if not environment:
        env_list = ", ".join(f"`/{env}`" for env in config.SPLUNK_ENVIRONMENTS)
        await update.message.reply_text(
            "⚠️ Please select an environment first:\n"
            f"{env_list}\n\n"
            "Then send your transaction ID (or curl command).",
            parse_mode="Markdown",
        )
        return

    time_range = context.user_data.get("time_range", config.SPLUNK_DEFAULT_TIME_RANGE)

    is_curl = text.lower().startswith("curl") or (
        "-h " in text.lower() and "http" in text.lower()
    )

    if is_curl:
        from parser.curl_parser import parse_curl, CurlParseError

        try:
            parsed = parse_curl(text)
            transaction_id = parsed["transaction_id"]
        except CurlParseError as e:
            await update.message.reply_text(str(e))
            return
    else:
        txn_candidate = text.strip()
        if _looks_like_transaction_id(txn_candidate):
            transaction_id = txn_candidate
        else:
            await update.message.reply_text(
                "That doesn't look like a valid transaction ID or curl command. "
                f"Please paste your `{config.TRANSACTION_ID_HEADER}` value or a full curl command.\n\n"
                "Example transaction ID: `abc-123`\n"
                "Type /help for more info.",
                parse_mode="Markdown",
            )
            return

    jq = context.bot_data.get("job_queue")
    if not jq:
        await update.message.reply_text("❌ Bot is still initializing. Please try again.")
        return

    if jq.jobs_for_chat(chat_id) >= 3:
        await update.message.reply_text(
            "You already have 3 jobs queued. Please wait for them to complete before submitting more."
        )
        return

    job = {
        "transaction_id": transaction_id,
        "requester_chat_id": chat_id,
        "raw_curl": text,
        "vpn_retries": 0,
        "environment": environment,
        "time_range": time_range,
    }

    enqueued = await jq.enqueue(job)
    if not enqueued:
        await update.message.reply_text(
            f"Bot is busy ({jq.queue_depth}/10 jobs). Please retry in a few minutes."
        )
        return

    await update.message.reply_text(
        f"✅ Received. Investigating `{transaction_id}` in **{_env_display(environment)}** (last {time_range})...",
        parse_mode="Markdown",
    )
    logger.info(
        "Enqueued job for transaction_id=%s environment=%s from chat_id=%d",
        transaction_id, environment, chat_id,
    )