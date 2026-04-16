import json
import logging
import re

import httpx
from openai import AsyncOpenAI

import config

logger = logging.getLogger(__name__)

_http_client: httpx.AsyncClient | None = None
_openai_client: AsyncOpenAI | None = None


def _get_client() -> tuple[httpx.AsyncClient, AsyncOpenAI]:
    global _http_client, _openai_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            verify=not config.LLM_SKIP_SSL_VERIFY,
            proxy=config.LLM_PROXY,
        )
    if _openai_client is None:
        _openai_client = AsyncOpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
            http_client=_http_client,
        )
    return _http_client, _openai_client


async def close_client():
    global _http_client, _openai_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
        _openai_client = None


def _sanitize_log_lines(log_lines: str, max_chars: int = 80000) -> str:
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", log_lines)
    sanitized = sanitized.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = sanitized.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
    if len(sanitized) > max_chars:
        sanitized = sanitized[:max_chars] + f"\n... [truncated {len(log_lines) - max_chars} chars]"
    return sanitized

SYSTEM_PROMPT = """You are a backend debugging assistant for a software engineering team.
You will receive raw application logs from a production system, identified by a transaction ID.
Your job is to analyze what went wrong and explain it clearly to the backend engineer who will fix it.
Be precise, technical, and concise. Do not speculate beyond what the logs show.

If the logs contain ESB (Enterprise Service Bus) errors:
- Put the ESB error message in the "error_type" field
- Put the ESB endpoint URL in the "failed_component" field
- Include the full ESB response body in the "likely_cause" field
- Include the HTTP status code and any correlation IDs in the "suggested_action" field"""

USER_PROMPT_TEMPLATE = """Transaction ID: {transaction_id}

Raw logs:
---
{log_lines}
---

Analyze the logs above and respond in the following JSON format only, no other text:

{{
  "summary": "one or two sentence description of what happened",
  "error_type": "e.g. NullPointerException, TimeoutError, 404, etc.",
  "failed_component": "the service, class, function, or endpoint where it failed",
  "likely_cause": "your best diagnosis of root cause based on the logs",
  "severity": "low | medium | high | critical",
  "suggested_action": "specific next step the engineer should take"
}}

If the logs do not contain enough information to diagnose the issue, set likely_cause to
"Insufficient log detail" and suggest what additional logging would help."""


class LLMAnalysisError(Exception):
    pass


async def analyze(transaction_id: str, log_lines: str) -> dict:
    _, client = _get_client()

    sanitized_logs = _sanitize_log_lines(log_lines)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        transaction_id=transaction_id,
        log_lines=sanitized_logs,
    )

    try:
        response = await client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
    except Exception as e:
        logger.error("OpenAI API error for transaction_id=%s: %s", transaction_id, e)
        logger.debug("Failed prompt (first 500 chars): %s", user_prompt[:500])
        raise LLMAnalysisError(f"OpenAI API error: {e}") from e

    content = response.choices[0].message.content

    try:
        diagnosis = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("LLM returned malformed JSON for transaction_id=%s", transaction_id)
        raise LLMAnalysisError(f"Malformed JSON response: {content}")

    required_fields = {"summary", "error_type", "failed_component", "likely_cause", "severity", "suggested_action"}
    if not required_fields.issubset(diagnosis.keys()):
        logger.warning("LLM response missing fields for transaction_id=%s: %s", transaction_id, diagnosis)

    diagnosis["raw_llm_response"] = content

    return diagnosis