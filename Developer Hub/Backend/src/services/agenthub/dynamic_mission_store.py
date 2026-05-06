"""SQLite persistence for dynamic mission orchestration state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from typing import Any

from domain.models.dynamic_orchestration import (
    AgentResult,
    MissionBrief,
    MissionState,
    MissionStatus,
    OrchestratorAction,
    ResourceLock,
    SubagentRun,
    TaskNode,
)
from services.agenthub._db import connect as _connect, db_path as _db_path

_INIT_LOCK = threading.Lock()
_INITIALISED = False
_INITIALISED_DB_PATH: str | None = None

# Per-session in-process cache of the SHA-1 of every payload we've
# already persisted. The dynamic orchestrator calls
# ``save_mission_state`` on every tiny state mutation, so without this
# cache the 115 KB-average ``blackboard`` JSON (and every child row)
# was UPSERT'd on each call even when nothing about that row had
# changed. Cache key shape:
#
#     ("mission", session_id) -> hash of (status, brief, blackboard, replans_used)
#     ("task", session_id, task_id) -> hash of payload
#     ("run", session_id, run_id) -> ...
#     ("result", session_id, result_id) -> ...
#     ("lock", session_id, key) -> ...
#     ("decision", session_id, decision_id) -> ...
#
# The cache is process-local — it's only an optimisation; correctness
# does not depend on it. A fresh process re-hashes from scratch on
# the next save and writes everything once, then settles into the
# steady-state where most saves are no-ops on disk.
_DIRTY_CACHE_LOCK = threading.Lock()
_DIRTY_CACHE: dict[tuple, str] = {}


def _digest(*parts: Any) -> str:
    h = hashlib.sha1(usedforsecurity=False)
    for part in parts:
        if isinstance(part, str):
            h.update(part.encode("utf-8"))
        elif isinstance(part, bytes):
            h.update(part)
        else:
            h.update(str(part).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def reset_init_for_tests() -> None:
    """Test hook: forget the cached one-shot schema flag."""
    global _INITIALISED, _INITIALISED_DB_PATH
    _INITIALISED = False
    _INITIALISED_DB_PATH = None
    with _DIRTY_CACHE_LOCK:
        _DIRTY_CACHE.clear()


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    """Idempotent schema setup. Skips work after the first successful call.

    Previously this ran six ``CREATE TABLE IF NOT EXISTS`` and two
    ``CREATE INDEX IF NOT EXISTS`` on every ``save_mission_state`` /
    ``load_mission_state`` call (plus opening a fresh connection).
    Under load that dominated SQLite latency for the
    ``dynamic_missions`` family of tables. The flag below makes
    subsequent calls a no-op once the schema is in place.
    """
    global _INITIALISED, _INITIALISED_DB_PATH
    current_db_path = _db_path()
    if _INITIALISED and conn is None and _INITIALISED_DB_PATH == current_db_path:
        return
    with _INIT_LOCK:
        if _INITIALISED and conn is None and _INITIALISED_DB_PATH == current_db_path:
            return
        owns_connection = conn is None
        active_conn = conn or _connect()
        try:
            active_conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS dynamic_missions (
                    session_id    TEXT PRIMARY KEY,
                    status        TEXT NOT NULL,
                    brief         TEXT NOT NULL,
                    blackboard    TEXT NOT NULL DEFAULT '{}',
                    replans_used  INTEGER NOT NULL DEFAULT 0,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dynamic_task_nodes (
                    session_id TEXT NOT NULL,
                    task_id    TEXT NOT NULL,
                    status     TEXT NOT NULL,
                    payload    TEXT NOT NULL,
                    PRIMARY KEY (session_id, task_id),
                    FOREIGN KEY (session_id) REFERENCES dynamic_missions(session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_dynamic_tasks_session_status
                    ON dynamic_task_nodes(session_id, status);

                CREATE TABLE IF NOT EXISTS dynamic_subagent_runs (
                    session_id TEXT NOT NULL,
                    run_id     TEXT NOT NULL,
                    task_id    TEXT NOT NULL,
                    agent_id   TEXT NOT NULL,
                    status     TEXT NOT NULL,
                    payload    TEXT NOT NULL,
                    PRIMARY KEY (session_id, run_id),
                    FOREIGN KEY (session_id) REFERENCES dynamic_missions(session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_dynamic_runs_session_task
                    ON dynamic_subagent_runs(session_id, task_id);

                CREATE TABLE IF NOT EXISTS dynamic_agent_results (
                    session_id TEXT NOT NULL,
                    result_id  TEXT NOT NULL,
                    run_id     TEXT NOT NULL,
                    task_id    TEXT NOT NULL,
                    status     TEXT NOT NULL,
                    payload    TEXT NOT NULL,
                    PRIMARY KEY (session_id, result_id),
                    FOREIGN KEY (session_id) REFERENCES dynamic_missions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS dynamic_resource_locks (
                    session_id TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    mode       TEXT NOT NULL,
                    payload    TEXT NOT NULL,
                    PRIMARY KEY (session_id, key),
                    FOREIGN KEY (session_id) REFERENCES dynamic_missions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS dynamic_orchestrator_decisions (
                    session_id    TEXT NOT NULL,
                    decision_id   TEXT NOT NULL,
                    type          TEXT NOT NULL,
                    task_id       TEXT,
                    target_run_id TEXT,
                    created_at    TEXT NOT NULL,
                    payload       TEXT NOT NULL,
                    PRIMARY KEY (session_id, decision_id),
                    FOREIGN KEY (session_id) REFERENCES dynamic_missions(session_id) ON DELETE CASCADE
                );

                -- Backs the ``ORDER BY created_at`` replay query in
                -- ``load_mission_state``; without it SQLite uses a
                -- TEMP B-TREE per load.
                CREATE INDEX IF NOT EXISTS idx_dynamic_decisions_session_created
                    ON dynamic_orchestrator_decisions(session_id, created_at);
                """
            )
            if owns_connection:
                active_conn.commit()
        finally:
            if owns_connection:
                active_conn.close()
        _INITIALISED = True
        _INITIALISED_DB_PATH = current_db_path


def save_mission_state(state: MissionState) -> MissionState:
    """Persist (or upsert) the mission state for a session.

    Earlier versions did ``DELETE FROM dynamic_missions WHERE session_id=?``
    and then re-inserted the parent row plus every task / run / result /
    decision via ``ON DELETE CASCADE``. The dynamic orchestrator calls this
    on **every** state mutation, so a session with a few dozen rows ended
    up rewriting all of them — including the multi-hundred-KB
    ``blackboard`` JSON — for each tiny status change. That dominated the
    write hot path and was visible as ``dynamic_missions``-table latency.

    This implementation does two layers of skip-on-no-change:

    1. Per-row payload SHA-1s are cached in ``_DIRTY_CACHE``. Rows whose
       hash matches the last-persisted value are dropped from the UPSERT
       batch entirely, so SQLite doesn't rewrite their pages.
    2. The mission row itself (status / brief / blackboard / replans_used)
       is hashed; if nothing in that group changed we skip the parent
       UPSERT, which means the multi-hundred-KB ``blackboard`` JSON is
       only re-written on saves that actually mutated the blackboard.

    ``updated_at`` is intentionally **not** part of the parent hash —
    bumping the timestamp alone is not worth a 115 KB rewrite.
    """
    init_schema()
    session_id = state.brief.session_id
    brief_json = state.brief.model_dump_json(by_alias=True)
    blackboard_json = json.dumps(state.blackboard, default=str)
    parent_digest = _digest(
        state.status.value,
        brief_json,
        blackboard_json,
        state.replans_used,
    )

    # Snapshot child digests so we can decide which UPSERTs to skip.
    task_rows = _digest_rows(
        session_id,
        prefix="task",
        rows=[
            (task.id, (task.status.value, task.model_dump_json(by_alias=True)))
            for task in state.tasks.values()
        ],
        row_builder=lambda task_id, fields: (
            session_id, task_id, fields[0], fields[1],
        ),
    )
    run_rows = _digest_rows(
        session_id,
        prefix="run",
        rows=[
            (
                run.id,
                (run.task_id, run.agent_id, run.status.value, run.model_dump_json(by_alias=True)),
            )
            for run in state.subagent_runs.values()
        ],
        row_builder=lambda run_id, fields: (
            session_id, run_id, fields[0], fields[1], fields[2], fields[3],
        ),
    )
    result_rows = _digest_rows(
        session_id,
        prefix="result",
        rows=[
            (
                result.id,
                (
                    result.run_id,
                    result.task_id,
                    result.status.value,
                    result.model_dump_json(by_alias=True),
                ),
            )
            for result in state.results.values()
        ],
        row_builder=lambda result_id, fields: (
            session_id, result_id, fields[0], fields[1], fields[2], fields[3],
        ),
    )
    lock_rows = _digest_rows(
        session_id,
        prefix="lock",
        rows=[
            (
                lock.key,
                (lock.mode.value, lock.model_dump_json(by_alias=True)),
            )
            for lock in state.resource_locks.values()
        ],
        row_builder=lambda key, fields: (session_id, key, fields[0], fields[1]),
    )
    decision_rows = _digest_rows(
        session_id,
        prefix="decision",
        rows=[
            (
                decision.id,
                (
                    decision.type.value,
                    decision.task_id,
                    decision.target_run_id,
                    decision.created_at.isoformat(),
                    decision.model_dump_json(by_alias=True),
                ),
            )
            for decision in state.decisions
        ],
        row_builder=lambda decision_id, fields: (
            session_id, decision_id, fields[0], fields[1], fields[2], fields[3], fields[4],
        ),
    )

    parent_key = ("mission", session_id)
    with _DIRTY_CACHE_LOCK:
        parent_unchanged = _DIRTY_CACHE.get(parent_key) == parent_digest

    conn = _connect()
    try:
        with conn:
            if not parent_unchanged:
                conn.execute(
                    """
                    INSERT INTO dynamic_missions
                        (session_id, status, brief, blackboard, replans_used, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        status       = excluded.status,
                        brief        = excluded.brief,
                        blackboard   = excluded.blackboard,
                        replans_used = excluded.replans_used,
                        updated_at   = excluded.updated_at
                    """,
                    (
                        session_id,
                        state.status.value,
                        brief_json,
                        blackboard_json,
                        state.replans_used,
                        state.created_at.isoformat(),
                        state.updated_at.isoformat(),
                    ),
                )

            _upsert_children(
                conn,
                table="dynamic_task_nodes",
                key_columns=("session_id", "task_id"),
                update_columns=("status", "payload"),
                session_id=session_id,
                dirty_rows=task_rows,
            )
            _upsert_children(
                conn,
                table="dynamic_subagent_runs",
                key_columns=("session_id", "run_id"),
                update_columns=("task_id", "agent_id", "status", "payload"),
                session_id=session_id,
                dirty_rows=run_rows,
            )
            _upsert_children(
                conn,
                table="dynamic_agent_results",
                key_columns=("session_id", "result_id"),
                update_columns=("run_id", "task_id", "status", "payload"),
                session_id=session_id,
                dirty_rows=result_rows,
            )
            _upsert_children(
                conn,
                table="dynamic_resource_locks",
                key_columns=("session_id", "key"),
                update_columns=("mode", "payload"),
                session_id=session_id,
                dirty_rows=lock_rows,
            )
            _upsert_children(
                conn,
                table="dynamic_orchestrator_decisions",
                key_columns=("session_id", "decision_id"),
                update_columns=("type", "task_id", "target_run_id", "created_at", "payload"),
                session_id=session_id,
                dirty_rows=decision_rows,
            )

        # Commit: only update the cache after a successful disk commit.
        with _DIRTY_CACHE_LOCK:
            _DIRTY_CACHE[parent_key] = parent_digest
            for entry in (task_rows, run_rows, result_rows, lock_rows, decision_rows):
                for cache_key, digest, _row, _is_dirty in entry["all"]:
                    _DIRTY_CACHE[cache_key] = digest
                for cache_key in entry["dropped_keys"]:
                    _DIRTY_CACHE.pop(cache_key, None)
        return state
    finally:
        conn.close()


def _digest_rows(
    session_id: str,
    *,
    prefix: str,
    rows: list[tuple[str, tuple[Any, ...]]],
    row_builder: Any,
) -> dict[str, Any]:
    """Build the per-row digest+row tuples used by ``_upsert_children``.

    Returns a dict with:
      - ``all``: list of ``(cache_key, digest, full_row, is_dirty)`` for
        every row currently in the in-memory state.
      - ``keep_ids``: list of per-row ids to keep on disk.
      - ``dirty_rows``: list of full row tuples that need an UPSERT.
      - ``dropped_keys``: cache keys for rows that disappeared from the
        in-memory state and must be removed from ``_DIRTY_CACHE``.
    """
    all_entries: list[tuple[tuple, str, tuple[Any, ...], bool]] = []
    keep_ids: list[str] = []
    dirty_rows: list[tuple[Any, ...]] = []
    seen_keys: set[tuple] = set()
    with _DIRTY_CACHE_LOCK:
        for row_id, fields in rows:
            digest = _digest(prefix, *fields)
            cache_key = (prefix, session_id, row_id)
            seen_keys.add(cache_key)
            is_dirty = _DIRTY_CACHE.get(cache_key) != digest
            full_row = row_builder(row_id, fields)
            all_entries.append((cache_key, digest, full_row, is_dirty))
            keep_ids.append(row_id)
            if is_dirty:
                dirty_rows.append(full_row)
        # Identify cache entries (for this session+prefix) that the new
        # state no longer references; the SQL DELETE will drop the rows
        # and we want the cache to stay in sync.
        dropped_keys = [
            key for key in _DIRTY_CACHE
            if key[0] == prefix and key[1] == session_id and key not in seen_keys
        ]
    return {
        "all": all_entries,
        "keep_ids": keep_ids,
        "dirty_rows": dirty_rows,
        "dropped_keys": dropped_keys,
    }


def _upsert_children(
    conn: sqlite3.Connection,
    *,
    table: str,
    key_columns: tuple[str, ...],
    update_columns: tuple[str, ...],
    session_id: str,
    dirty_rows: dict[str, Any],
) -> None:
    """Apply an ``INSERT … ON CONFLICT DO UPDATE`` per **dirty** row, then
    drop any children that no longer appear in the in-memory state.

    Rows whose payload digest matches the cached value from the previous
    save are skipped entirely — under steady-state orchestration most
    saves only mutate one or two child rows, so this turns the rest into
    pure no-ops at the SQLite layer.
    """
    rows = dirty_rows["dirty_rows"]
    keep_ids = dirty_rows["keep_ids"]
    if rows:
        all_columns = list(key_columns) + list(update_columns)
        placeholders = ",".join("?" for _ in all_columns)
        set_clause = ", ".join(f"{col} = excluded.{col}" for col in update_columns)
        conflict_target = ", ".join(key_columns)
        sql = (
            f"INSERT INTO {table} ({', '.join(all_columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_target}) DO UPDATE SET {set_clause}"
        )
        conn.executemany(sql, rows)

    # Delete rows that the in-memory state no longer references. Skip
    # the DELETE entirely when the cache says nothing was dropped: under
    # steady state the keep-set is unchanged from the previous save, so
    # the ``NOT IN`` scan is wasted work.
    id_column = key_columns[1]
    if not dirty_rows["dropped_keys"]:
        return
    if keep_ids:
        keep_placeholders = ",".join("?" for _ in keep_ids)
        conn.execute(
            f"DELETE FROM {table} "
            f"WHERE session_id = ? AND {id_column} NOT IN ({keep_placeholders})",
            (session_id, *keep_ids),
        )
    else:
        conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))


def load_mission_state(session_id: str) -> MissionState | None:
    init_schema()
    conn = _connect()
    try:
        mission_row = conn.execute(
            "SELECT * FROM dynamic_missions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if mission_row is None:
            return None

        state = MissionState(
            brief=MissionBrief.model_validate_json(mission_row["brief"]),
            status=MissionStatus(mission_row["status"]),
            blackboard=json.loads(mission_row["blackboard"]),
            replans_used=int(mission_row["replans_used"]),
            created_at=_from_iso(mission_row["created_at"]),
            updated_at=_from_iso(mission_row["updated_at"]),
        )
        for row in conn.execute(
            "SELECT payload FROM dynamic_task_nodes WHERE session_id = ?",
            (session_id,),
        ).fetchall():
            task = TaskNode.model_validate_json(row["payload"])
            state.tasks[task.id] = task
        for row in conn.execute(
            "SELECT payload FROM dynamic_subagent_runs WHERE session_id = ?",
            (session_id,),
        ).fetchall():
            run = SubagentRun.model_validate_json(row["payload"])
            state.subagent_runs[run.id] = run
        for row in conn.execute(
            "SELECT payload FROM dynamic_agent_results WHERE session_id = ?",
            (session_id,),
        ).fetchall():
            result = AgentResult.model_validate_json(row["payload"])
            state.results[result.id] = result
        for row in conn.execute(
            "SELECT payload FROM dynamic_resource_locks WHERE session_id = ?",
            (session_id,),
        ).fetchall():
            lock = ResourceLock.model_validate_json(row["payload"])
            state.resource_locks[lock.key] = lock
        decision_rows = conn.execute(
            "SELECT payload FROM dynamic_orchestrator_decisions WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        state.decisions = [OrchestratorAction.model_validate_json(row["payload"]) for row in decision_rows]
        return state
    finally:
        conn.close()


def delete_mission_state(session_id: str) -> bool:
    init_schema()
    conn = _connect()
    try:
        with conn:
            cursor = conn.execute("DELETE FROM dynamic_missions WHERE session_id = ?", (session_id,))
        deleted = cursor.rowcount > 0
    finally:
        conn.close()
    # Drop every cache entry for this session so a future save_mission_state
    # for the same id starts fresh (otherwise stale digests would suppress
    # the first re-insert).
    with _DIRTY_CACHE_LOCK:
        stale = [k for k in _DIRTY_CACHE if len(k) >= 2 and k[1] == session_id]
        for k in stale:
            _DIRTY_CACHE.pop(k, None)
        _DIRTY_CACHE.pop(("mission", session_id), None)
    return deleted


def _from_iso(value: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(value)
