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

Writes are dispatched to a single background thread via an unbounded
``queue.Queue``. The producer side (``append_event``) only enqueues a
tuple, so the orchestrator's emit hot path never blocks on disk I/O,
``PRAGMA`` round-trips, or WAL writer contention. Failures during the
async write are logged at DEBUG and dropped — observability invariant
still holds because the in-memory ring + JSONL audit sink remain the
source of truth while the process is alive.
"""

from __future__ import annotations

import atexit
import json
import logging
import queue
import sqlite3
import threading
from typing import Any, Iterable

from services.agenthub._db import connect as _connect

logger = logging.getLogger(__name__)

_INIT_LOCK = threading.Lock()
_INITIALISED = False

# Background writer state. ``_writer_queue`` is unbounded so the producer
# never blocks; under sustained pressure (which we have not measured) we
# would switch to bounded + drop-oldest, but for now the simple model
# matches the existing best-effort semantics.
_writer_queue: "queue.Queue[tuple[str, tuple[Any, ...]] | None]" = queue.Queue()
_writer_thread: threading.Thread | None = None
_writer_lock = threading.Lock()


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


def _writer_loop() -> None:
    """Drain ``_writer_queue`` on a single connection.

    Batches up to 64 events per ``commit`` so a burst from the
    orchestrator (composition_ready / run_overview / slot_progress … in
    the same tick) goes to disk in one fsync rather than 50.
    """
    init_schema()
    conn = _connect()
    try:
        while True:
            try:
                item = _writer_queue.get()
            except Exception:
                continue
            if item is None:
                break
            batch: list[tuple[Any, ...]] = []
            sql, args = item
            batch.append(args)
            # Drain anything else that's already queued so we commit
            # the whole burst atomically.
            try:
                while len(batch) < 64:
                    nxt = _writer_queue.get_nowait()
                    if nxt is None:
                        # Shutdown sentinel — flush the batch first, then exit.
                        try:
                            conn.executemany(sql, batch)
                            conn.commit()
                        except Exception:
                            logger.debug("session_events flush failed", exc_info=True)
                        return
                    if nxt[0] != sql:
                        # Different statement shape — flush, then keep
                        # processing. (We only have one shape today, but
                        # this keeps the loop honest if more are added.)
                        try:
                            conn.executemany(sql, batch)
                            conn.commit()
                        except Exception:
                            logger.debug("session_events flush failed", exc_info=True)
                        sql, args = nxt
                        batch = [args]
                    else:
                        batch.append(nxt[1])
            except queue.Empty:
                pass
            try:
                conn.executemany(sql, batch)
                conn.commit()
            except Exception:
                logger.debug("session_events batch insert failed", exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_writer() -> None:
    global _writer_thread
    if _writer_thread is not None and _writer_thread.is_alive():
        return
    with _writer_lock:
        if _writer_thread is not None and _writer_thread.is_alive():
            return
        _writer_thread = threading.Thread(
            target=_writer_loop,
            name="session-events-writer",
            daemon=True,
        )
        _writer_thread.start()


def _shutdown_writer() -> None:
    if _writer_thread is None:
        return
    try:
        _writer_queue.put(None)
        _writer_thread.join(timeout=2.0)
    except Exception:
        pass


atexit.register(_shutdown_writer)


_INSERT_SQL = (
    "INSERT OR IGNORE INTO session_events "
    "(session_id, seq, ts, event_type, log_category, payload) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def append_event(session_id: str, payload: dict[str, Any]) -> None:
    """Enqueue one event for asynchronous persistence.

    Returns immediately after a queue put — the actual SQLite INSERT runs
    on a dedicated background thread that batches commits. Failures are
    non-fatal (observability invariant): the in-memory event ring + JSONL
    audit sink still surface the event even if the DB write is lost.
    """
    try:
        seq = int(payload.get("seq") or 0)
        if seq <= 0:
            return  # trace events have seq=None and live in their own ring
        ts = str(payload.get("ts") or "")
        event_type = str(payload.get("type") or "")
        log_category = str(payload.get("logCategory") or "detailed")
        body = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        _ensure_writer()
        _writer_queue.put_nowait(
            (_INSERT_SQL, (session_id, seq, ts, event_type, log_category, body))
        )
    except Exception:
        logger.debug("session_events enqueue failed", exc_info=True)


def flush_pending_events(timeout: float = 2.0) -> None:
    """Test/shutdown helper: block until the writer queue is drained.

    Used by tests that read events back immediately after appending and
    by graceful-shutdown paths that want all pending writes to land.
    """
    import time
    if _writer_thread is None or not _writer_thread.is_alive():
        return
    end = time.monotonic() + timeout
    # Poll the queue's qsize() (advisory but accurate enough): once it
    # reads zero and stays there for one tick, the writer has committed.
    while time.monotonic() < end:
        if _writer_queue.empty():
            # One more tick to let an in-flight commit finish.
            time.sleep(0.005)
            if _writer_queue.empty():
                return
        time.sleep(0.005)


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
