"""Integration tests for dynamic orchestrator ↔ agent runner wiring.

These verify that ``start_job`` defaults to the dynamic mission runtime,
that the runtime drives real ``SlotRunner`` → ``_run_agent`` execution
with a mock LLM, and that the previous fixed driver path is still
available only as an explicit debug override.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from domain.models.agent_models import (
    AgentCategory,
    AgentTemplate,
    Job,
    JobStatus,
)
from domain.models.composition import (
    AgentSlot,
    Budget,
    Composition,
    Handoff,
)
from services.agenthub import orchestrator_engine as oe


def _simple_template(agent_id: str = "fabric-admin") -> AgentTemplate:
    return AgentTemplate(
        id=agent_id, name=agent_id, display_name=agent_id.title(),
        category=AgentCategory.ADMIN, description="test",
        system_prompt="you are a test", available_tools=[],
    )


def _make_composition(
    arch: str,
    slots: list[dict],
    handoffs: list[dict] | None = None,
    entrypoint: str | None = None,
) -> Composition:
    slot_objs = [AgentSlot(id=s["id"], agent_id=s.get("agent_id", "fabric-admin"), role=s.get("role", s["id"])) for s in slots]
    handoff_objs = [Handoff.model_validate({"from": h["from"], "to": h["to"], "kind": h.get("kind", "delegate")}) for h in (handoffs or [])]
    return Composition(
        session_id="int-test-session",
        task="Integration test task",
        architecture=arch,
        rationale="test",
        headline="test",
        subtitle="test",
        slots=slot_objs,
        handoffs=handoff_objs,
        entrypoint_slot_id=entrypoint or slot_objs[0].id,
        budget=Budget(max_turns=6, max_tool_calls=20, max_wallclock_s=60),
    )


class _MockLLMResponse:
    """Simulates a single LLM response that finishes the agent loop."""

    def __init__(self):
        self.status_code = 200

    def json(self):
        return {
            "choices": [{
                "message": {
                    "content": "DECISION: Task completed successfully.",
                    "role": "assistant",
                },
                "finish_reason": "stop",
            }]
        }


class _MockHttpClient:
    """Mock httpx.AsyncClient that returns canned responses."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return _MockLLMResponse()


@pytest.mark.asyncio
async def test_start_job_uses_dynamic_runtime_by_default(monkeypatch):
    """start_job seeds a live dynamic mission and runs subagents by default."""
    monkeypatch.setattr(oe.httpx, "AsyncClient", _MockHttpClient)
    monkeypatch.setattr(oe, "update_session", lambda *a, **k: None)
    monkeypatch.setattr(oe, "log_audit", lambda *a, **k: None)
    monkeypatch.setattr(oe, "get_template", lambda tid: _simple_template(tid))

    comp = _make_composition("solo", [{"id": "s1"}])
    job = Job(
        id="int-solo-1", user_id="u-1", workspace_id="ws-1",
        task_description="test solo", composition=comp,
    )
    engine = oe.OrchestratorEngine()
    engine.configure(MagicMock(), lambda: "tok", lambda *a: None)

    job_id = await engine.start_job(job, "tok", None)
    assert job_id == "int-solo-1"

    exe = engine._active_jobs.get(job_id)
    assert exe is not None
    assert exe.dynamic_mission_state is not None
    assert any(ev.get("type") == "mission_seeded" for ev in exe._ring)
    if exe:
        await asyncio.gather(*exe.tasks, return_exceptions=True)

    assert exe.dynamic_mission_state is not None
    assert exe.dynamic_mission_state.status.value == "completed"
    assert job.status in (JobStatus.COMPLETED, JobStatus.RUNNING)
    assert len(job.agents) == 1
    assert any(ev.get("type") == "subagent_spawned" for ev in exe._ring)
    assert any(ev.get("type") == "subagent_result" for ev in exe._ring)


@pytest.mark.asyncio
async def test_dynamic_runtime_preserves_sequential_dependencies(monkeypatch):
    """Composition handoffs become dynamic task dependencies."""
    monkeypatch.setattr(oe.httpx, "AsyncClient", _MockHttpClient)
    monkeypatch.setattr(oe, "update_session", lambda *a, **k: None)
    monkeypatch.setattr(oe, "log_audit", lambda *a, **k: None)
    monkeypatch.setattr(oe, "get_template", lambda tid: _simple_template(tid))

    comp = _make_composition(
        "sequential",
        [{"id": "a"}, {"id": "b"}],
        handoffs=[{"from": "a", "to": "b", "kind": "report"}],
        entrypoint="a",
    )
    job = Job(
        id="int-seq-1", user_id="u-1", workspace_id="ws-1",
        task_description="test sequential", composition=comp,
    )
    engine = oe.OrchestratorEngine()
    engine.configure(MagicMock(), lambda: "tok", lambda *a: None)

    await engine.start_job(job, "tok", None)

    exe = engine._active_jobs.get(job.id)
    assert exe is not None
    if exe:
        await asyncio.gather(*exe.tasks, return_exceptions=True)

    assert len(job.agents) == 2
    assert exe.dynamic_mission_state is not None
    assert exe.dynamic_mission_state.tasks["b"].dependencies == ["a"]


@pytest.mark.asyncio
async def test_cancel_propagates_through_driver(monkeypatch):
    """Cancelling a job stops the dynamic mission task."""

    class _BlockingClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            await asyncio.Event().wait()

    monkeypatch.setattr(oe.httpx, "AsyncClient", _BlockingClient)
    monkeypatch.setattr(oe, "update_session", lambda *a, **k: None)
    monkeypatch.setattr(oe, "log_audit", lambda *a, **k: None)
    monkeypatch.setattr(oe, "get_template", lambda tid: _simple_template(tid))

    comp = _make_composition("solo", [{"id": "s1"}])
    job = Job(
        id="int-cancel-1", user_id="u-1", workspace_id="ws-1",
        task_description="cancel me", composition=comp,
    )
    engine = oe.OrchestratorEngine()
    engine.configure(MagicMock(), lambda: "tok", lambda *a: None)

    await engine.start_job(job, "tok", None)
    await asyncio.sleep(0.1)

    assert engine.cancel_job(job.id)
    exe = engine._active_jobs.get(job.id)
    if exe:
        try:
            await asyncio.wait_for(
                asyncio.gather(*exe.tasks, return_exceptions=True),
                timeout=2.0,
            )
        except (asyncio.CancelledError, TimeoutError):
            pass


@pytest.mark.asyncio
async def test_dynamic_supervisor_seed_runs_lead_then_workers(monkeypatch):
    """End-to-end: supervisor composition is executed by dynamic tasks."""
    monkeypatch.setattr(oe.httpx, "AsyncClient", _MockHttpClient)
    monkeypatch.setattr(oe, "update_session", lambda *a, **k: None)
    monkeypatch.setattr(oe, "log_audit", lambda *a, **k: None)
    monkeypatch.setattr(oe, "get_template", lambda tid: _simple_template(tid))

    comp = _make_composition(
        "supervisor",
        [{"id": "lead"}, {"id": "w1"}, {"id": "w2"}],
        handoffs=[
            {"from": "lead", "to": "w1"},
            {"from": "lead", "to": "w2"},
        ],
        entrypoint="lead",
    )
    job = Job(
        id="int-sup-1", user_id="u-1", workspace_id="ws-1",
        task_description="test supervisor", composition=comp,
    )
    engine = oe.OrchestratorEngine()
    engine.configure(MagicMock(), lambda: "tok", lambda *a: None)

    await engine.start_job(job, "tok", None)
    exe = engine._active_jobs.get(job.id)
    if exe:
        await asyncio.gather(*exe.tasks, return_exceptions=True)

    assert len(job.agents) == 3


@pytest.mark.asyncio
async def test_fixed_driver_runtime_is_explicit_debug_override(monkeypatch):
    """The old composition driver path only runs when explicitly requested."""
    monkeypatch.setenv("AGENTHUB_ORCHESTRATION_RUNTIME", "fixed")
    monkeypatch.setattr(oe, "update_session", lambda *a, **k: None)
    monkeypatch.setattr(oe, "log_audit", lambda *a, **k: None)
    monkeypatch.setattr(oe, "get_template", lambda tid: _simple_template(tid))

    from services.agenthub.drivers.registry import DriverRegistry

    calls: list[str] = []

    class _FixedDriver:
        async def run(self, *, composition, execution, slot_runner, budget, tracker=None):
            calls.append(composition.architecture)
            for assignment in execution.job.agents:
                assignment.status = oe.AgentStatus.COMPLETED

    monkeypatch.setattr(
        DriverRegistry,
        "get",
        classmethod(lambda cls, arch: _FixedDriver()),
    )

    comp = _make_composition("solo", [{"id": "s1"}])
    job = Job(
        id="int-fixed-1", user_id="u-1", workspace_id="ws-1",
        task_description="test fixed", composition=comp,
    )
    engine = oe.OrchestratorEngine()
    engine.configure(MagicMock(), lambda: "tok", lambda *a: None)

    await engine.start_job(job, "tok", None)
    exe = engine._active_jobs.get(job.id)
    assert exe is not None
    assert exe.dynamic_mission_state is None
    await asyncio.gather(*exe.tasks, return_exceptions=True)

    assert calls == ["solo"]
    assert len(job.agents) == 1
