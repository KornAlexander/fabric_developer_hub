"""SQLite persistence for mission events.

Mission events live in the in-memory ``_JobExecution._ring`` while a job is
running. Once the execution exits (mission completed, backend restarted, or
the user navigates away long enough that the ring rotates), those events
are gone. That made re-loading a session show an empty live log even though
the run produced rich orchestrator/verifier traces.

This module persists every public (non-trace) event to SQLite as it is
emitted, and provides a read API for replaying them when no live execution
is present. Trace events are intentionally excluded — they remain
internal-only per `/memories/repo/log-categories.md`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import Any, Iterable

from services.agenthub._db import connect as _connect

logger = logging.getLogger(__name__)

_INIT_LOCK = threading.Lock()
_INITIALISED = False


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    """Idempotent schema setup. Safe to call from any code path."""
    global _INITIALISED
    if _INITIALISED:
        return
    with _INIT_LOCK:
        if _INITIALISED:
            return
        owns_conn = conn is None
        conn = conn or _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS session_events (
                    session_id   TEXT NOT NULL,
                    seq          INTEGER NOT NULL,
                    ts           TEXT NOT NULL,
                    event_type   TEXT NOT NULL,
                    log_category TEXT NOT NULL DEFAULT 'detailed',
                    payload      TEXT NOT NULL,
                    PRIMARY KEY (session_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_session_events_session
                    ON session_events(session_id);
                CREATE INDEX IF NOT EXISTS idx_session_events_type
                    ON session_events(session_id, event_type);
                """
            )
            conn.commit()
            _INITIALISED = True
        finally:
            if owns_conn:
                conn.close()


def reset_init_for_tests() -> None:
    global _INITIALISED
    _INITIALISED = False


def append_event(session_id: str, payload: dict[str, Any]) -> None:
    """Persist one event. Failures are non-fatal (observability invariant)."""
    try:
        init_schema()
        seq = int(payload.get("seq") or 0)
        if seq <= 0:
            return  # trace events have seq=None and live in their own ring
        ts = str(payload.get("ts") or "")
        event_type = str(payload.get("type") or "")
        log_category = str(payload.get("logCategory") or "detailed")
        body = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO session_events "
                "(session_id, seq, ts, event_type, log_category, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, seq, ts, event_type, log_category, body),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("session_events append failed", exc_info=True)


def load_events(
    session_id: str,
    *,
    types: Iterable[str] | None = None,
    limit: int | None = None,
    after_seq: int | None = None,
) -> list[dict[str, Any]]:
    """Return persisted events for a session in emit order (seq ascending)."""
    init_schema()
    conn = _connect()
    try:
        sql = "SELECT payload FROM session_events WHERE session_id = ?"
        args: list[Any] = [session_id]
        if types:
            placeholders = ",".join("?" for _ in types)
            sql += f" AND event_type IN ({placeholders})"
            args.extend(types)
        if after_seq is not None:
            sql += " AND seq > ?"
            args.append(int(after_seq))
        sql += " ORDER BY seq ASC"
        if limit and limit > 0:
            sql += " LIMIT ?"
            args.append(int(limit))
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            out.append(json.loads(row[0]))
        except Exception:
            continue
    return out


def event_count(session_id: str) -> int:
    init_schema()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM session_events WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def delete_session_events(session_id: str) -> int:
    init_schema()
    conn = _connect()
    try:
        cur = conn.execute(
            "DELETE FROM session_events WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()
