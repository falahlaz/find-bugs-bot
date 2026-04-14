import json
import logging

from openai import AsyncOpenAI

import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a backend debugging assistant for a software engineering team.
You will receive raw application logs from a production system, identified by a transaction ID.
Your job is to analyze what went wrong and explain it clearly to the backend engineer who will fix it.
Be precise, technical, and concise. Do not speculate beyond what the logs show."""

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
    client = AsyncOpenAI(api_key=config.LLM_API_KEY)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        transaction_id=transaction_id,
        log_lines=log_lines,
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