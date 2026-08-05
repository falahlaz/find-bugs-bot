import sqlite3
import asyncio
import json
import logging
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

_db_path = config.DB_PATH


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS investigations (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id      TEXT NOT NULL,
            resolved_transaction_id TEXT,
            requester_chat_id   INTEGER NOT NULL,
            environment         TEXT NOT NULL DEFAULT 'prod',
            time_range          TEXT NOT NULL DEFAULT '24h',
            submitted_at        TEXT NOT NULL,
            status              TEXT NOT NULL,
            error_type          TEXT,
            failed_component    TEXT,
            severity            TEXT,
            summary             TEXT,
            likely_cause        TEXT,
            suggested_action    TEXT,
            raw_log_snippet     TEXT,
            failure_reason      TEXT,
            engineer_report     TEXT,
            assigned_to         TEXT
        )
    """)

    try:
        conn.execute("ALTER TABLE investigations ADD COLUMN environment TEXT NOT NULL DEFAULT 'prod'")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE investigations ADD COLUMN time_range TEXT NOT NULL DEFAULT '24h'")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE investigations ADD COLUMN resolved_transaction_id TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE investigations ADD COLUMN engineer_report TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE investigations ADD COLUMN assigned_to TEXT")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", _db_path)


def _save_investigation_sync(
    transaction_id: str,
    requester_chat_id: int,
    status: str,
    environment: str = "prod",
    error_type: str | None = None,
    failed_component: str | None = None,
    severity: str | None = None,
    summary: str | None = None,
    likely_cause: str | None = None,
    suggested_action: str | None = None,
    raw_log_snippet: str | None = None,
    failure_reason: str | None = None,
    time_range: str = "24h",
    resolved_transaction_id: str | None = None,
    engineer_report: str | None = None,
) -> int:
    conn = _get_conn()
    try:
        cursor = conn.execute(
            """INSERT INTO investigations
               (transaction_id, resolved_transaction_id, requester_chat_id, environment,
                time_range, submitted_at, status,
                error_type, failed_component, severity, summary,
                likely_cause, suggested_action, raw_log_snippet, failure_reason,
                engineer_report)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                transaction_id,
                resolved_transaction_id,
                requester_chat_id,
                environment,
                time_range,
                datetime.now(timezone.utc).isoformat(),
                status,
                error_type,
                failed_component,
                severity,
                summary,
                likely_cause,
                suggested_action,
                raw_log_snippet[:2000] if raw_log_snippet else None,
                failure_reason,
                engineer_report,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


async def save_investigation(
    transaction_id: str,
    requester_chat_id: int,
    status: str,
    environment: str = "prod",
    error_type: str | None = None,
    failed_component: str | None = None,
    severity: str | None = None,
    summary: str | None = None,
    likely_cause: str | None = None,
    suggested_action: str | None = None,
    raw_log_snippet: str | None = None,
    failure_reason: str | None = None,
    time_range: str = "24h",
    resolved_transaction_id: str | None = None,
    engineer_report: str | None = None,
) -> int:
    return await asyncio.to_thread(
        _save_investigation_sync,
        transaction_id, requester_chat_id, status,
        environment, error_type, failed_component, severity, summary,
        likely_cause, suggested_action, raw_log_snippet, failure_reason,
        time_range, resolved_transaction_id, engineer_report,
    )


def _get_recent_sync(limit: int = 5) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM investigations ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


async def get_recent(limit: int = 5) -> list[dict]:
    return await asyncio.to_thread(_get_recent_sync, limit)


def _get_by_transaction_id_sync(transaction_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM investigations WHERE transaction_id = ? ORDER BY id DESC LIMIT 1",
            (transaction_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def get_by_transaction_id(transaction_id: str) -> dict | None:
    return await asyncio.to_thread(_get_by_transaction_id_sync, transaction_id)


def _get_by_id_sync(investigation_id: int) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM investigations WHERE id = ?",
            (investigation_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def get_by_id(investigation_id: int) -> dict | None:
    return await asyncio.to_thread(_get_by_id_sync, investigation_id)


def _add_assignees_sync(investigation_id: int, chat_ids: list[int]) -> list[int]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT assigned_to FROM investigations WHERE id = ?",
            (investigation_id,),
        ).fetchone()
        if row is None:
            return []

        try:
            existing = json.loads(row["assigned_to"]) if row["assigned_to"] else []
        except json.JSONDecodeError:
            logger.warning("Corrupt assigned_to for investigation id=%d, resetting", investigation_id)
            existing = []

        merged = list(existing)
        for chat_id in chat_ids:
            if chat_id not in merged:
                merged.append(chat_id)

        conn.execute(
            "UPDATE investigations SET assigned_to = ? WHERE id = ?",
            (json.dumps(merged), investigation_id),
        )
        conn.commit()
        return merged
    finally:
        conn.close()


async def add_assignees(investigation_id: int, chat_ids: list[int]) -> list[int]:
    """Merge chat_ids into the investigation's assignee list, returning the full list."""
    return await asyncio.to_thread(_add_assignees_sync, investigation_id, chat_ids)