from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config

ENV_DISPLAY_NAMES = {
    "prod": "production",
    "dev": "development",
}

MARKDOWN_SPECIAL_CHARS = ("_", "*", "[", "`", ">", "+")


def _escape_markdown(text: str) -> str:
    for char in MARKDOWN_SPECIAL_CHARS:
        text = text.replace(char, f"\\{char}")
    return text


def _sanitize_code_block(text: str) -> str:
    return text.replace("```", "'''")


def _truncate_error(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _env_display(env_key: str) -> str:
    return ENV_DISPLAY_NAMES.get(env_key, env_key)


def _now_formatted() -> str:
    tz = ZoneInfo(config.TIMEZONE)
    now = datetime.now(timezone.utc).astimezone(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S ") + now.strftime("%Z")


def format_raw_log_message(
    transaction_id: str,
    environment: str,
    time_range: str,
    raw_log_snippet: str,
) -> list[str]:
    env_label = _env_display(environment)
    header = f"📄 Raw logs — transaction-id: `{transaction_id}` [{env_label}, last {time_range}]"
    sanitized = _sanitize_code_block(raw_log_snippet)
    body = f"```\n{sanitized}\n```"
    text = f"{header}\n{body}"
    return _split_message(text)


def format_engineer_report(
    transaction_id: str,
    diagnosis: dict | None,
    requester_chat_id: int,
    environment: str = "prod",
    raw_log_snippet: str | None = None,
    llm_failed: bool = False,
    llm_raw_text: str | None = None,
    status: str = "success",
    failure_reason: str | None = None,
    time_range: str = "24h",
) -> list[str]:
    env_label = _env_display(environment)
    header = f"🐛 Bug Report — transaction-id: `{transaction_id}` [{env_label}, last {time_range}]"

    if status == "no_logs":
        text = (
            f"{header}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📭 No logs found\n\n"
            f"No logs were found in Splunk for transaction ID `{transaction_id}` in **{env_label}**.\n"
            "The ID may be incorrect or the logs may have rolled off.\n\n"
            f"👤 Reported by: chat_id {requester_chat_id}\n"
            f"🕐 Queried at: {_now_formatted()}"
        )
        return _split_message(text)

    if status == "failed":
        text = (
            f"{header}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "❌ Investigation failed\n\n"
        )
        if failure_reason:
            text += f"Reason: {_escape_markdown(failure_reason)}\n\n"
        if raw_log_snippet:
            snippet = raw_log_snippet[-3000:] if len(raw_log_snippet) > 3000 else raw_log_snippet
            text += f"⚠️ Last ~3000 chars of logs:\n```\n{_sanitize_code_block(snippet)}\n```\n\n"
        text += f"👤 Reported by: chat_id {requester_chat_id}\n"
        text += f"🕐 Queried at: {_now_formatted()}"
        return _split_message(text)

    severity_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "🚨"}

    if llm_failed and llm_raw_text:
        safe_error = _truncate_error(llm_raw_text, 500)
        text = (
            f"{header}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ LLM analysis failed\n\n"
            f"```\n{_sanitize_code_block(safe_error)}\n```\n\n"
        )
        if raw_log_snippet:
            snippet = raw_log_snippet[-3000:] if len(raw_log_snippet) > 3000 else raw_log_snippet
            text += f"📄 Raw logs (last ~3000 chars):\n```\n{_sanitize_code_block(snippet)}\n```\n\n"
        text += f"👤 Reported by: chat_id {requester_chat_id}\n"
        text += f"🕐 Queried at: {_now_formatted()}"
        return _split_message(text)

    d = diagnosis or {}
    sev = d.get("severity", "unknown").lower()
    sev_icon = severity_emoji.get(sev, "❓")

    text = (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Failed component:  {_escape_markdown(d.get('failed_component', '—'))}\n"
        f"❌ Error type:        {_escape_markdown(d.get('error_type', '—'))}\n"
        f"🔍 Likely cause:      {_escape_markdown(d.get('likely_cause', '—'))}\n"
        f"{sev_icon} Severity:          {sev.title()}\n"
        f"💡 Suggested action:  {_escape_markdown(d.get('suggested_action', '—'))}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    relevant_logs = d.get("relevant_logs", [])
    if relevant_logs:
        logs_text = "\n".join(f"  {i+1}. `{line}`" for i, line in enumerate(relevant_logs))
        text += f"📄 Relevant logs:\n{logs_text}\n\n"

    text += (
        f"📋 Summary:\n{_escape_markdown(d.get('summary', '—'))}\n\n"
        f"👤 Reported by: chat_id {requester_chat_id}\n"
        f"🕐 Queried at: {_now_formatted()}"
    )

    return _split_message(text)


def format_qa_report(
    transaction_id: str,
    diagnosis: dict | None,
    environment: str = "prod",
    status: str = "success",
    llm_failed: bool = False,
    failure_reason: str | None = None,
    time_range: str = "24h",
) -> list[str]:
    env_label = _env_display(environment)

    if status == "no_logs":
        text = (
            f"📭 No logs found for transaction-id: `{transaction_id}` [{env_label}, last {time_range}]\n\n"
            "No logs were found in Splunk for your transaction ID. "
            "The engineering team has been notified and may follow up."
        )
        return _split_message(text)

    if status == "failed":
        if failure_reason and "VPN" in failure_reason:
            text = (
                f"❌ Investigation failed for transaction-id: `{transaction_id}` [{env_label}, last {time_range}]\n\n"
                "VPN connectivity issue — please resubmit when the team is available."
            )
        elif failure_reason and "session" in failure_reason.lower():
            text = (
                f"⏸️ Investigation paused for transaction-id: `{transaction_id}` [{env_label}, last {time_range}]\n\n"
                "The engineering team has been notified and will resume shortly."
            )
        else:
            text = (
                f"❌ Investigation failed for transaction-id: `{transaction_id}` [{env_label}, last {time_range}]\n\n"
                "Something went wrong. The engineering team has been notified."
            )
        return _split_message(text)

    if llm_failed:
        text = (
            f"✅ Investigation complete — transaction-id: `{transaction_id}` [{env_label}, last {time_range}]\n\n"
            "The engineering team is reviewing the logs manually and will follow up."
        )
        return _split_message(text)

    d = diagnosis or {}
    severity_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "🚨"}
    sev = d.get("severity", "unknown").lower()

    text = (
        f"✅ Investigation complete — transaction-id: `{transaction_id}` [{env_label}, last {time_range}]\n\n"
        f"{severity_emoji.get(sev, '❓')} Severity: {sev.title()}\n"
        f"📋 What happened:\n{_escape_markdown(d.get('summary', '—'))}\n\n"
        "The engineering team has been notified and is looking into it."
    )
    return _split_message(text)


MAX_MSG_LEN = 4096


def _split_message(text: str) -> list[str]:
    if len(text) <= MAX_MSG_LEN:
        return [text]

    parts = []
    while text:
        if len(text) <= MAX_MSG_LEN:
            parts.append(text)
            break

        split_at = text.rfind("\n", 0, MAX_MSG_LEN)
        if split_at == -1:
            split_at = MAX_MSG_LEN

        chunk = text[:split_at]
        open_count = chunk.count("```") % 2
        if open_count:
            chunk += "\n```"
            text = text[split_at:].lstrip("\n")
            text = "```\n" + text
        else:
            parts.append(chunk)
            text = text[split_at:].lstrip("\n")

    return parts