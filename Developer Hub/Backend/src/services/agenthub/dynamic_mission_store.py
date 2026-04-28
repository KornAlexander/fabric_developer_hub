"""SQLite persistence for dynamic mission orchestration state."""

from __future__ import annotations

import json
import sqlite3
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
from services.agenthub._db import connect as _connect


def init_schema(conn: sqlite3.Connection | None = None) -> None:
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
            """
        )
        if owns_connection:
            active_conn.commit()
    finally:
        if owns_connection:
            active_conn.close()


def save_mission_state(state: MissionState) -> MissionState:
    init_schema()
    conn = _connect()
    try:
        with conn:
            session_id = state.brief.session_id
            conn.execute("DELETE FROM dynamic_missions WHERE session_id = ?", (session_id,))
            conn.execute(
                "INSERT INTO dynamic_missions "
                "(session_id, status, brief, blackboard, replans_used, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    state.status.value,
                    state.brief.model_dump_json(by_alias=True),
                    json.dumps(state.blackboard, default=str),
                    state.replans_used,
                    state.created_at.isoformat(),
                    state.updated_at.isoformat(),
                ),
            )
            for task in state.tasks.values():
                conn.execute(
                    "INSERT INTO dynamic_task_nodes (session_id, task_id, status, payload) VALUES (?, ?, ?, ?)",
                    (session_id, task.id, task.status.value, task.model_dump_json(by_alias=True)),
                )
            for run in state.subagent_runs.values():
                conn.execute(
                    "INSERT INTO dynamic_subagent_runs "
                    "(session_id, run_id, task_id, agent_id, status, payload) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        run.id,
                        run.task_id,
                        run.agent_id,
                        run.status.value,
                        run.model_dump_json(by_alias=True),
                    ),
                )
            for result in state.results.values():
                conn.execute(
                    "INSERT INTO dynamic_agent_results "
                    "(session_id, result_id, run_id, task_id, status, payload) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        result.id,
                        result.run_id,
                        result.task_id,
                        result.status.value,
                        result.model_dump_json(by_alias=True),
                    ),
                )
            for lock in state.resource_locks.values():
                conn.execute(
                    "INSERT INTO dynamic_resource_locks (session_id, key, mode, payload) VALUES (?, ?, ?, ?)",
                    (session_id, lock.key, lock.mode.value, lock.model_dump_json(by_alias=True)),
                )
            for decision in state.decisions:
                conn.execute(
                    "INSERT INTO dynamic_orchestrator_decisions "
                    "(session_id, decision_id, type, task_id, target_run_id, created_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        decision.id,
                        decision.type.value,
                        decision.task_id,
                        decision.target_run_id,
                        decision.created_at.isoformat(),
                        decision.model_dump_json(by_alias=True),
                    ),
                )
        return state
    finally:
        conn.close()


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
        return cursor.rowcount > 0
    finally:
        conn.close()


def _from_iso(value: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(value)
