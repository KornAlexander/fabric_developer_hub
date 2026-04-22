"""SQLite-backed persistence for AgentHub sessions, agent configs, and audit trail.

Note: the in-memory Pydantic model is still called ``Job`` (legacy domain
name). User-facing terminology and the persistence layer use ``session``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from domain.models.agent_models import (
    AgentAssignment,
    Job,
    JobStatus,
    UserAgentConfig,
)
from domain.models.composition import Composition
from services.agenthub import workspaces_cache
from services.agenthub._db import connect as _connect
from services.agenthub._db import db_path as _db_path

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

# Sessions whose persisted ``composition`` column can't be parsed as the
# current ``Composition`` model (e.g. rows written by a previous build that
# stored a legacy ``Plan``). We log one DEBUG per session id so the log
# stream isn't spammed on every dashboard open. These rows stay visible in
# the history list with ``composition=None`` — they can be viewed/deleted
# but not re-run.
_legacy_composition_warned: set[str] = set()


def init_db() -> None:
    """Create tables if they don't exist, then migrate any pre-rename schema."""
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                user_upn    TEXT,
                workspace_id TEXT NOT NULL,
                task_description TEXT NOT NULL,
                context     TEXT,
                status      TEXT NOT NULL DEFAULT 'planned',
                composition TEXT,
                created_at  TEXT NOT NULL,
                started_at  TEXT,
                completed_at TEXT,
                cancelled_at TEXT,
                cancelled_by_user_id TEXT,
                cancelled_by_upn TEXT,
                agents      TEXT NOT NULL DEFAULT '[]'
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

            CREATE TABLE IF NOT EXISTS user_agent_configs (
                id                  TEXT PRIMARY KEY,
                user_id             TEXT NOT NULL,
                user_upn            TEXT,
                agent_template_id   TEXT NOT NULL,
                access_levels       TEXT NOT NULL DEFAULT '{}',
                tool_integrations   TEXT NOT NULL DEFAULT '{}',
                runtime_schedule    TEXT,
                custom_prompt_additions TEXT,
                created_at          TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_uac_user ON user_agent_configs(user_id);

            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                agent_id    TEXT,
                tool_name   TEXT,
                tool_args   TEXT,
                result_summary TEXT,
                timestamp   TEXT NOT NULL,
                user_id     TEXT,
                user_upn    TEXT,
                success     INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id);
            """
        )
        _migrate_legacy_jobs_table(conn)
        _migrate_add_user_upn(conn)
        _migrate_add_cancellation_columns(conn)
        _migrate_plan_to_composition(conn)
        # Workspace cache lives in the same DB so all SQLite state is
        # under one bind-mounted file.
        workspaces_cache.init_schema(conn)
        conn.commit()
        logger.info("AgentHub database initialized at %s", _db_path())
    finally:
        conn.close()


def _migrate_add_user_upn(conn: sqlite3.Connection) -> None:
    """Idempotent migration: add ``user_upn`` column to pre-existing tables.

    The column exists in CREATE TABLE above for fresh DBs, but installs from
    before this change need an ALTER TABLE. SQLite ``ADD COLUMN`` only fails
    if the column already exists, so we guard by PRAGMA inspection.
    """
    for table in ("sessions", "user_agent_configs", "audit_log"):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "user_upn" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_upn TEXT")
            logger.info("Added user_upn column to %s", table)
    # audit_log gained a success flag as part of the security hardening; add
    # it in place on pre-existing DBs.
    audit_cols = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
    if "success" not in audit_cols:
        conn.execute("ALTER TABLE audit_log ADD COLUMN success INTEGER")
        logger.info("Added success column to audit_log")


def _migrate_add_cancellation_columns(conn: sqlite3.Connection) -> None:
    """Idempotent migration: add cancellation audit columns to ``sessions``.

    Populated when a user cancels a running or waiting session from the UI
    so the Recent-Sessions list can show who stopped the session and when.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    for col in ("cancelled_at", "cancelled_by_user_id", "cancelled_by_upn"):
        if col not in cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")
            logger.info("Added %s column to sessions", col)


def _migrate_legacy_jobs_table(conn: sqlite3.Connection) -> None:
    """One-shot migration: copy rows from legacy ``jobs`` table into ``sessions``
    and from legacy ``audit_log.job_id`` into ``audit_log.session_id``.

    Idempotent: it's safe to call on every startup.
    """
    legacy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()
    if legacy:
        # Legacy jobs table pre-dates the plan→composition rename; bring rows
        # in with their plan column copied into composition (parse will fail
        # and the row shows composition=None, which is fine — these are
        # archived sessions the user can still view/delete).
        moved = conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, user_id, workspace_id, task_description, context, status, composition, "
            " created_at, started_at, completed_at, agents) "
            "SELECT id, user_id, workspace_id, task_description, context, status, plan, "
            "       created_at, started_at, completed_at, agents FROM jobs"
        ).rowcount
        conn.execute("DROP TABLE jobs")
        logger.info("Migrated %d row(s) from legacy 'jobs' table to 'sessions'", moved)

    # audit_log column rename: detect old job_id column.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
    if "job_id" in cols and "session_id" not in cols:
        conn.execute("ALTER TABLE audit_log RENAME COLUMN job_id TO session_id")
        logger.info("Renamed audit_log.job_id -> audit_log.session_id")


def _migrate_plan_to_composition(conn: sqlite3.Connection) -> None:
    """Idempotent migration: rename legacy ``plan`` column to ``composition``.

    Old builds stored the planner output under ``plan``; the composition
    refactor moved to ``composition`` with a different schema. SQLite
    ``RENAME COLUMN`` is available from 3.25; if a pre-existing DB has
    ``plan`` but not ``composition``, rename in place. Stored JSON blobs
    are left untouched — the row reader tolerates unparseable legacy
    payloads by returning ``composition=None``.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "plan" in cols and "composition" not in cols:
        conn.execute("ALTER TABLE sessions RENAME COLUMN plan TO composition")
        logger.info("Renamed sessions.plan -> sessions.composition")
    elif "plan" in cols and "composition" in cols:
        # Unusual: both columns exist (manual tampering). Drop plan to keep
        # the schema in sync with the model.
        conn.execute("ALTER TABLE sessions DROP COLUMN plan")
        logger.info("Dropped legacy sessions.plan column (composition already present)")


# ── Session CRUD ────────────────────────────────────────────

def create_session(job: Job) -> Job:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO sessions (id, user_id, user_upn, workspace_id, task_description, context, status, composition, created_at, started_at, completed_at, cancelled_at, cancelled_by_user_id, cancelled_by_upn, agents) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job.id,
                job.user_id,
                job.user_upn,
                job.workspace_id,
                job.task_description,
                json.dumps(job.context) if job.context else None,
                job.status.value,
                job.composition.model_dump_json(by_alias=True) if job.composition else None,
                job.created_at.isoformat(),
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                job.cancelled_at.isoformat() if job.cancelled_at else None,
                job.cancelled_by_user_id,
                job.cancelled_by_upn,
                json.dumps([a.model_dump() for a in job.agents], default=str),
            ),
        )
        conn.commit()
        return job
    finally:
        conn.close()


def get_session(session_id: str) -> Job | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        return _row_to_session(row)
    finally:
        conn.close()


def list_sessions(
    user_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Job]:
    """Return the caller's sessions, newest first.

    ``offset`` enables keyset-less pagination for the Recent-prompts UI,
    which lazy-loads older sessions as the user scrolls. Negative values are
    clamped to 0 to match SQLite semantics on other backends.
    """
    offset = max(0, int(offset))
    conn = _connect()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE user_id = ? AND status = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (user_id, status, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()


def update_session(job: Job) -> Job:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE sessions SET status=?, composition=?, started_at=?, completed_at=?, cancelled_at=?, cancelled_by_user_id=?, cancelled_by_upn=?, agents=? WHERE id=?",
            (
                job.status.value,
                job.composition.model_dump_json(by_alias=True) if job.composition else None,
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                job.cancelled_at.isoformat() if job.cancelled_at else None,
                job.cancelled_by_user_id,
                job.cancelled_by_upn,
                json.dumps([a.model_dump() for a in job.agents], default=str),
                job.id,
            ),
        )
        conn.commit()
        return job
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    conn = _connect()
    try:
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def _row_to_session(row: sqlite3.Row) -> Job:
    agents_raw = json.loads(row["agents"]) if row["agents"] else []
    agents = []
    for a in agents_raw:
        # Ensure datetime fields are strings
        agents.append(AgentAssignment(**a))

    composition: Composition | None = None
    row_keys_early = set(row.keys())
    comp_raw = row["composition"] if "composition" in row_keys_early else None
    if comp_raw:
        # Try to parse as Composition; legacy rows written by an older build
        # (plan-based schema) won't validate — we drop the payload rather
        # than raising so old sessions stay visible in the history list.
        # They can still be deleted but not re-run.
        try:
            composition = Composition.model_validate_json(comp_raw)
        except Exception:
            session_id = row["id"]
            if session_id not in _legacy_composition_warned:
                _legacy_composition_warned.add(session_id)
                logger.debug(
                    "Session %s has a legacy payload that cannot be parsed as Composition; dropping",
                    session_id,
                )
            composition = None

    row_keys = set(row.keys())
    cancelled_at_raw = row["cancelled_at"] if "cancelled_at" in row_keys else None
    return Job(
        id=row["id"],
        user_id=row["user_id"],
        user_upn=row["user_upn"] if "user_upn" in row_keys else None,
        workspace_id=row["workspace_id"],
        task_description=row["task_description"],
        context=json.loads(row["context"]) if row["context"] else None,
        status=JobStatus(row["status"]),
        composition=composition,
        created_at=datetime.fromisoformat(row["created_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        cancelled_at=datetime.fromisoformat(cancelled_at_raw) if cancelled_at_raw else None,
        cancelled_by_user_id=row["cancelled_by_user_id"] if "cancelled_by_user_id" in row_keys else None,
        cancelled_by_upn=row["cancelled_by_upn"] if "cancelled_by_upn" in row_keys else None,
        agents=agents,
    )


# ── User Agent Config CRUD ──────────────────────────────────────────

def save_agent_config(config: UserAgentConfig) -> UserAgentConfig:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_agent_configs (id, user_id, user_upn, agent_template_id, access_levels, tool_integrations, runtime_schedule, custom_prompt_additions, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                config.id,
                config.user_id,
                config.user_upn,
                config.agent_template_id,
                json.dumps(config.access_levels),
                json.dumps(config.tool_integrations),
                config.runtime_schedule,
                config.custom_prompt_additions,
                config.created_at.isoformat(),
            ),
        )
        conn.commit()
        return config
    finally:
        conn.close()


def get_user_agent_configs(user_id: str) -> list[UserAgentConfig]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM user_agent_configs WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [_row_to_config(r) for r in rows]
    finally:
        conn.close()


def delete_agent_config(config_id: str) -> bool:
    conn = _connect()
    try:
        cursor = conn.execute("DELETE FROM user_agent_configs WHERE id = ?", (config_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_agent_config(config_id: str) -> UserAgentConfig | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM user_agent_configs WHERE id = ?", (config_id,)
        ).fetchone()
        return _row_to_config(row) if row else None
    finally:
        conn.close()


def _row_to_config(row: sqlite3.Row) -> UserAgentConfig:
    return UserAgentConfig(
        id=row["id"],
        user_id=row["user_id"],
        user_upn=row["user_upn"] if "user_upn" in row.keys() else None,
        agent_template_id=row["agent_template_id"],
        access_levels=json.loads(row["access_levels"]),
        tool_integrations=json.loads(row["tool_integrations"]),
        runtime_schedule=row["runtime_schedule"],
        custom_prompt_additions=row["custom_prompt_additions"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ── Audit Log ────────────────────────────────────────────────────────

def log_audit(
    session_id: str,
    agent_id: str | None,
    tool_name: str | None,
    tool_args: dict[str, Any] | None,
    result_summary: str,
    user_id: str | None = None,
    user_upn: str | None = None,
    *,
    success: bool | None = None,
) -> None:
    """Record an audited event.

    ``success`` is optional so existing callers (which only report on the
    happy path) stay source-compatible; the orchestrator explicitly passes
    ``True`` / ``False`` around each tool dispatch so post-mortem queries
    like ``SELECT … WHERE success=0`` become trivial.
    """
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO audit_log (session_id, agent_id, tool_name, tool_args, result_summary, timestamp, user_id, user_upn, success) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                agent_id,
                tool_name,
                json.dumps(tool_args) if tool_args else None,
                result_summary,
                datetime.now(UTC).isoformat(),
                user_id,
                user_upn,
                None if success is None else (1 if success else 0),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_audit_log(session_id: str) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE session_id = ? ORDER BY timestamp", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
