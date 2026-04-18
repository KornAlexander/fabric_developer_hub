"""SQLite-backed persistence for AgentHub jobs, agent configs, and audit trail."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import UTC, datetime

from models.agent_models import (
    AgentAssignment,
    ExecutionPlan,
    Job,
    JobStatus,
    UserAgentConfig,
)

logger = logging.getLogger(__name__)

_DB_PATH: str | None = None
_SCHEMA_VERSION = 1

# Resolved once on first use. Precedence:
# 1. AGENTHUB_DB_PATH env var (set by .env / docker-compose).
# 2. ~/.config/<workload>/agenthub.db (matches ItemMetadataStore location).
# Do not persist state inside the source tree.


def _default_db_path() -> str:
    from constants.workload_constants import WorkloadConstants
    workload = WorkloadConstants.WORKLOAD_NAME.replace(" ", "_")
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config")
    db_dir = os.path.join(base, workload)
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "agenthub.db")


def _db_path() -> str:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = os.environ.get("AGENTHUB_DB_PATH") or _default_db_path()
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                task_description TEXT NOT NULL,
                context     TEXT,
                status      TEXT NOT NULL DEFAULT 'planned',
                plan        TEXT,
                created_at  TEXT NOT NULL,
                started_at  TEXT,
                completed_at TEXT,
                agents      TEXT NOT NULL DEFAULT '[]'
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

            CREATE TABLE IF NOT EXISTS user_agent_configs (
                id                  TEXT PRIMARY KEY,
                user_id             TEXT NOT NULL,
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
                job_id      TEXT NOT NULL,
                agent_id    TEXT,
                tool_name   TEXT,
                tool_args   TEXT,
                result_summary TEXT,
                timestamp   TEXT NOT NULL,
                user_id     TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_log(job_id);
            """
        )
        conn.commit()
        logger.info("AgentHub database initialized at %s", _db_path())
    finally:
        conn.close()


# ── Job CRUD ─────────────────────────────────────────────────────────

def create_job(job: Job) -> Job:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO jobs (id, user_id, workspace_id, task_description, context, status, plan, created_at, started_at, completed_at, agents) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                job.id,
                job.user_id,
                job.workspace_id,
                job.task_description,
                json.dumps(job.context) if job.context else None,
                job.status.value,
                job.plan.model_dump_json() if job.plan else None,
                job.created_at.isoformat(),
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                json.dumps([a.model_dump() for a in job.agents], default=str),
            ),
        )
        conn.commit()
        return job
    finally:
        conn.close()


def get_job(job_id: str) -> Job | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        return _row_to_job(row)
    finally:
        conn.close()


def list_jobs(user_id: str, status: str | None = None, limit: int = 50) -> list[Job]:
    conn = _connect()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [_row_to_job(r) for r in rows]
    finally:
        conn.close()


def update_job(job: Job) -> Job:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE jobs SET status=?, plan=?, started_at=?, completed_at=?, agents=? WHERE id=?",
            (
                job.status.value,
                job.plan.model_dump_json() if job.plan else None,
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                json.dumps([a.model_dump() for a in job.agents], default=str),
                job.id,
            ),
        )
        conn.commit()
        return job
    finally:
        conn.close()


def delete_job(job_id: str) -> bool:
    conn = _connect()
    try:
        cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def _row_to_job(row: sqlite3.Row) -> Job:
    agents_raw = json.loads(row["agents"]) if row["agents"] else []
    agents = []
    for a in agents_raw:
        # Ensure datetime fields are strings
        agents.append(AgentAssignment(**a))

    plan = None
    if row["plan"]:
        plan = ExecutionPlan.model_validate_json(row["plan"])

    return Job(
        id=row["id"],
        user_id=row["user_id"],
        workspace_id=row["workspace_id"],
        task_description=row["task_description"],
        context=json.loads(row["context"]) if row["context"] else None,
        status=JobStatus(row["status"]),
        plan=plan,
        created_at=datetime.fromisoformat(row["created_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        agents=agents,
    )


# ── User Agent Config CRUD ──────────────────────────────────────────

def save_agent_config(config: UserAgentConfig) -> UserAgentConfig:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_agent_configs (id, user_id, agent_template_id, access_levels, tool_integrations, runtime_schedule, custom_prompt_additions, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                config.id,
                config.user_id,
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


def _row_to_config(row: sqlite3.Row) -> UserAgentConfig:
    return UserAgentConfig(
        id=row["id"],
        user_id=row["user_id"],
        agent_template_id=row["agent_template_id"],
        access_levels=json.loads(row["access_levels"]),
        tool_integrations=json.loads(row["tool_integrations"]),
        runtime_schedule=row["runtime_schedule"],
        custom_prompt_additions=row["custom_prompt_additions"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ── Audit Log ────────────────────────────────────────────────────────

def log_audit(
    job_id: str,
    agent_id: str | None,
    tool_name: str | None,
    tool_args: dict | None,
    result_summary: str,
    user_id: str | None = None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO audit_log (job_id, agent_id, tool_name, tool_args, result_summary, timestamp, user_id) VALUES (?,?,?,?,?,?,?)",
            (
                job_id,
                agent_id,
                tool_name,
                json.dumps(tool_args) if tool_args else None,
                result_summary,
                datetime.now(UTC).isoformat(),
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_audit_log(job_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE job_id = ? ORDER BY timestamp", (job_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
