"""End-to-end CRUD tests for ``services.agenthub.session_store`` against a
temporary SQLite database.

Strategy: monkeypatch ``AGENTHUB_DB_PATH`` to a tmp file before importing
``init_db``; reset the module-level ``_DB_PATH`` cache between tests.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain.models.agent_models import (
    AgentAssignment,
    AgentStatus,
    Job,
    JobStatus,
    UserAgentConfig,
)
from domain.models.plan import PlanStep, PlanTarget, Plan
from services.agenthub import _db, session_store
import json as _json


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Each test gets a fresh sqlite file."""
    db_path = tmp_path / "agenthub.db"
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(db_path))
    monkeypatch.setattr(_db, "_DB_PATH", None)
    session_store.init_db()
    yield
    monkeypatch.setattr(_db, "_DB_PATH", None)


def _make_job(job_id: str = "j-1", user_id: str = "u-1",
              status: JobStatus = JobStatus.PLANNED) -> Job:
    return Job(
        id=job_id,
        user_id=user_id,
        workspace_id="ws-1",
        task_description="do a thing",
        context={"k": "v"},
        status=status,
    )


# ── Job CRUD ─────────────────────────────────────────────────────────


def test_create_and_get_job_roundtrip() -> None:
    job = _make_job()
    job.plan = Plan(
        job_id=job.id,
        summary="plan summary",
        steps=[
            PlanStep(
                id="s1",
                order=1,
                title="Create Bronze lakehouse",
                action="create",
                target=PlanTarget(
                    item_type="Lakehouse",
                    display_name="lh_bronze",
                    workspace_id=job.workspace_id,
                ),
                rationale="Bronze layer is missing in destination workspace.",
            )
        ],
    )
    session_store.create_session(job)

    fetched = session_store.get_session(job.id)
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.user_id == job.user_id
    assert fetched.context == {"k": "v"}
    assert fetched.status == JobStatus.PLANNED
    assert fetched.plan is not None
    assert fetched.plan.summary == "plan summary"
    assert len(fetched.plan.steps) == 1
    assert fetched.plan.steps[0].target.display_name == "lh_bronze"


def test_get_job_returns_none_for_missing_id() -> None:
    assert session_store.get_session("nope") is None


def test_list_jobs_filters_by_user_and_status() -> None:
    session_store.create_session(_make_job("a", "alice", JobStatus.PLANNED))
    session_store.create_session(_make_job("b", "alice", JobStatus.RUNNING))
    session_store.create_session(_make_job("c", "bob", JobStatus.RUNNING))

    alice_jobs = session_store.list_sessions("alice")
    assert {j.id for j in alice_jobs} == {"a", "b"}

    alice_running = session_store.list_sessions("alice", status="running")
    assert {j.id for j in alice_running} == {"b"}

    bob_jobs = session_store.list_sessions("bob")
    assert {j.id for j in bob_jobs} == {"c"}


def test_list_jobs_orders_by_created_at_desc() -> None:
    """Newer jobs first."""
    older = _make_job("old", "u")
    older.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    newer = _make_job("new", "u")
    newer.created_at = datetime(2030, 1, 1, tzinfo=UTC)
    session_store.create_session(older)
    session_store.create_session(newer)

    listed = session_store.list_sessions("u")
    assert [j.id for j in listed] == ["new", "old"]


def test_list_jobs_respects_limit() -> None:
    for i in range(5):
        session_store.create_session(_make_job(f"j{i}", "u"))
    assert len(session_store.list_sessions("u", limit=3)) == 3


def test_update_job_persists_status_and_agents() -> None:
    job = _make_job()
    session_store.create_session(job)

    job.status = JobStatus.RUNNING
    job.started_at = datetime.now(UTC)
    job.agents.append(AgentAssignment(
        agent_id="t-1", session_id="s-1", role="r", status=AgentStatus.QUEUED, goal="g",
    ))
    session_store.update_session(job)

    fetched = session_store.get_session(job.id)
    assert fetched.status == JobStatus.RUNNING
    assert fetched.started_at is not None
    assert len(fetched.agents) == 1
    assert fetched.agents[0].agent_id == "t-1"


def test_delete_job_returns_true_when_deleted_false_when_missing() -> None:
    session_store.create_session(_make_job("x"))
    assert session_store.delete_session("x") is True
    assert session_store.get_session("x") is None
    assert session_store.delete_session("x") is False


# ── User Agent Config CRUD ──────────────────────────────────────────


def _make_config(cfg_id: str = "c-1", user_id: str = "u-1") -> UserAgentConfig:
    return UserAgentConfig(
        id=cfg_id,
        user_id=user_id,
        agent_template_id="tpl-data-engineer",
        access_levels={"workspace": True},
        tool_integrations={"github": True},
        runtime_schedule="0 9 * * *",
        custom_prompt_additions="be concise",
    )


def test_save_and_get_user_agent_configs() -> None:
    session_store.save_agent_config(_make_config("c1"))
    session_store.save_agent_config(_make_config("c2"))
    session_store.save_agent_config(_make_config("c3", user_id="other"))

    configs = session_store.get_user_agent_configs("u-1")
    assert {c.id for c in configs} == {"c1", "c2"}
    assert all(c.access_levels == {"workspace": True} for c in configs)


def test_save_agent_config_replaces_on_same_id() -> None:
    """``INSERT OR REPLACE`` should overwrite on conflict."""
    cfg = _make_config()
    session_store.save_agent_config(cfg)

    cfg.custom_prompt_additions = "updated"
    session_store.save_agent_config(cfg)

    configs = session_store.get_user_agent_configs("u-1")
    assert len(configs) == 1
    assert configs[0].custom_prompt_additions == "updated"


def test_delete_agent_config_returns_true_only_when_existed() -> None:
    session_store.save_agent_config(_make_config("c-del"))
    assert session_store.delete_agent_config("c-del") is True
    assert session_store.delete_agent_config("c-del") is False


# ── Audit Log ───────────────────────────────────────────────────────


def test_log_audit_and_retrieve_in_chronological_order() -> None:
    session_store.log_audit(
        "j-1", "agent-1", "fabric_list_workspaces", {}, "ok", user_id="u-1",
    )
    session_store.log_audit(
        "j-1", "agent-1", "fabric_create_item", {"display_name": "lh"}, "created", user_id="u-1",
    )
    session_store.log_audit("j-2", None, None, None, "unrelated")

    rows = session_store.get_audit_log("j-1")
    assert len(rows) == 2
    assert [r["tool_name"] for r in rows] == ["fabric_list_workspaces", "fabric_create_item"]
    # tool_args is JSON-serialised
    assert _json.loads(rows[1]["tool_args"]) == {"display_name": "lh"}


def test_get_audit_log_empty_when_no_entries() -> None:
    assert session_store.get_audit_log("never-logged") == []
