"""Smoke tests for ``services.agenthub.orchestrator_engine``.

Full LLM/MCP integration is out of scope, but the synchronous helpers and
state-management surface are testable in isolation:
  * ``OrchestratorEngine.__init__`` + ``configure``
  * ``cancel_job`` / ``inject_message`` / ``get_job_execution`` against
    the ``_active_jobs`` registry
  * The stateless ``_detect_action_from_tool`` helper (one parametrized
    sweep covers all 9 ``fabric_*`` tool name branches)
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from domain.models.agent_models import (
    AgentAction,
    AgentAssignment,
    AgentStatus,
    Job,
    JobStatus,
)
from domain.models.composition import AgentSlot, Composition
from domain.models.dynamic_orchestration import MissionBrief, MissionState, MissionStatus, SubagentRun, TaskNode
from services.agenthub import orchestrator_engine as oe
from services.agenthub.orchestrator_engine import (
    OrchestratorEngine,
    _apply_single_created_folder_id,
    _detect_action_from_tool,
    _extract_dynamic_followups,
    _get_blocking_issue,
    _has_successful_required_creation,
    _infer_single_created_folder_id,
    _is_transient_llm_exception,
    _JobExecution,
    _limit_creation_tools_for_goal,
    _major_issue_level,
    _parse_agent_output,
    _required_creation_tool_for_goal,
    _required_creation_tools_for_goal,
    _tool_result_major_issue_level,
)


def test_engine_init_and_configure() -> None:
    engine = OrchestratorEngine()
    assert engine.mcp_manager is None
    assert engine.copilot_token_fn is None
    assert engine.acquire_mcp_tokens_fn is None
    assert engine._active_jobs == {}

    mcp = MagicMock()
    cop = MagicMock()
    obo = MagicMock()
    engine.configure(mcp, cop, obo)
    assert engine.mcp_manager is mcp
    assert engine.copilot_token_fn is cop
    assert engine.acquire_mcp_tokens_fn is obo


@pytest.mark.asyncio
async def test_attach_mission_mcp_runtime_uses_global_manager_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTHUB_MCP_RUNTIME", "off")
    global_manager = MagicMock()
    engine = OrchestratorEngine(mcp_manager=global_manager)
    execution = _JobExecution(_make_job(), copilot_token="t", mcp_tokens=None)

    await engine._attach_mission_mcp_runtime(execution)

    assert execution.mcp_manager is global_manager
    assert execution.mcp_runtime is None


@pytest.mark.asyncio
async def test_attach_mission_mcp_runtime_starts_session_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.mcp import mission_runtime_manager

    monkeypatch.setenv("AGENTHUB_MCP_RUNTIME", "container")
    source_manager = MagicMock()
    runtime = SimpleNamespace(close=AsyncMock())
    calls: list[tuple[str, object]] = []

    async def fake_start(session_id: str, manager: object) -> object:
        calls.append((session_id, manager))
        return runtime

    monkeypatch.setattr(mission_runtime_manager, "start_mission_mcp_runtime", fake_start)
    engine = OrchestratorEngine(mcp_manager=source_manager)
    execution = _JobExecution(_make_job("mission-1"), copilot_token="t", mcp_tokens=None)

    await engine._attach_mission_mcp_runtime(execution)

    assert calls == [("mission-1", source_manager)]
    assert execution.mcp_manager is runtime
    assert execution.mcp_runtime is runtime


@pytest.mark.asyncio
async def test_attach_mission_mcp_runtime_hard_fails_when_container_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.mcp import mission_runtime_manager

    monkeypatch.setenv("AGENTHUB_MCP_RUNTIME", "container")

    async def fake_start(session_id: str, manager: object) -> object:
        raise RuntimeError("mission MCP runtime refused to start")

    monkeypatch.setattr(mission_runtime_manager, "start_mission_mcp_runtime", fake_start)
    engine = OrchestratorEngine(mcp_manager=MagicMock())
    execution = _JobExecution(_make_job("mission-fail"), copilot_token="t", mcp_tokens=None)

    with pytest.raises(RuntimeError, match="refused to start"):
        await engine._attach_mission_mcp_runtime(execution)

    assert execution.mcp_manager is None
    assert execution.mcp_runtime is None


@pytest.mark.asyncio
async def test_job_execution_closes_mission_mcp_runtime() -> None:
    execution = _JobExecution(_make_job("mission-cleanup"), copilot_token="t", mcp_tokens=None)
    runtime = SimpleNamespace(close=AsyncMock())
    execution.mcp_runtime = runtime

    await execution.close_mcp_runtime()

    runtime.close.assert_awaited_once()
    assert execution.mcp_runtime is None


def test_event_payload_summary_keeps_generalist_context_and_agent_receipt_details() -> None:
    summary = oe._event_payload_summary(
        "agent_context_received",
        {
            "runId": "run-1",
            "taskId": "task-1",
            "agentId": "admin",
            "agentName": "Admin",
            "agentSessionId": "agent-session-1",
            "contextDigest": "ctx-123",
            "goalDigest": "goal-123",
            "toolScopeCount": 4,
            "upstreamResultCount": 2,
            "specialistCatalogCount": 3,
            "steeringPreview": "Inspect the workspace and do not mutate artifacts.",
        },
    )

    assert summary["runId"] == "run-1"
    assert summary["taskId"] == "task-1"
    assert summary["agentId"] == "admin"
    assert summary["agentSessionId"] == "agent-session-1"
    assert summary["contextDigest"] == "ctx-123"
    assert summary["goalDigest"] == "goal-123"
    assert summary["steeringPreview"] == "Inspect the workspace and do not mutate artifacts."


def test_task_failed_is_high_level_for_public_traceability() -> None:
    assert oe._event_log_category(
        "task_failed",
        {"taskId": "verify", "reason": "verification loop exceeded"},
    ) == "high_level"


def test_intervention_payload_summaries_keep_reconstructable_reasons() -> None:
    abandoned = oe._event_payload_summary(
        "subagent_abandoned",
        {
            "runId": "run-stuck",
            "taskId": "repair",
            "replacementTaskId": "repair-retry-1",
            "reason": "Repeated tool loop continued after steering.",
        },
    )
    failed = oe._event_payload_summary(
        "task_failed",
        {
            "taskId": "verify",
            "reason": "verification feedback loop exceeded no-progress limit",
            "message": "Fabric verification is not converging after repeated repair attempts.",
        },
    )

    assert abandoned["replacementTaskId"] == "repair-retry-1"
    assert abandoned["reason"] == "Repeated tool loop continued after steering."
    assert failed["reason"] == "verification feedback loop exceeded no-progress limit"
    assert "not converging" in failed["message"]


def test_generalist_direct_work_is_high_level_and_summarized() -> None:
    payload = {
        "runId": "run-generalist",
        "taskId": "generalist",
        "agentId": "generalist",
        "taskTitle": "Generalist mission controller",
        "reason": "Generalist chose to handle routing directly.",
        "toolScopeCount": 4,
        "contextDigest": "ctx-123",
    }

    assert oe._event_log_category("generalist_direct_work", payload) == "high_level"
    summary = oe._event_payload_summary("generalist_direct_work", payload)
    assert summary["taskTitle"] == "Generalist mission controller"
    assert summary["reason"] == "Generalist chose to handle routing directly."
    assert summary["contextDigest"] == "ctx-123"


def _make_job(job_id: str = "j-1") -> Job:
    return Job(
        id=job_id, user_id="u-1", workspace_id="ws-1",
        task_description="t", context={}, status=JobStatus.RUNNING,
    )


def _make_composition(session_id: str) -> Composition:
    return Composition(
        sessionId=session_id,
        task="Inspect workspace inventory",
        architecture="dynamic",
        rationale="Use the mission controller.",
        headline="Workspace inventory",
        subtitle="Inspect and summarize items.",
        entrypointSlotId="generalist",
        slots=[AgentSlot(id="generalist", agentId="generalist", role="Mission controller")],
        handoffs=[],
    )


def test_pi_subagents_projection_emits_native_status_control_and_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTHUB_PI_OBSERVABILITY", "pi-subagents")
    job = _make_job("session-pi")
    job.agents.append(
        AgentAssignment(
            agent_id="fabric-data-engineer",
            session_id="assignment-1",
            role="FabricDataEngineer",
            goal="Create workspace inventory solution",
        )
    )
    execution = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    mission = MissionState(
        brief=MissionBrief(session_id=job.id, goal="Create workspace inventory solution", workspace_id="ws-1"),
    )
    mission.tasks["task-1"] = TaskNode(
        id="task-1",
        title="Create inventory solution",
        objective="Build a Fabric workspace inventory solution.",
    )
    mission.subagent_runs["run-1"] = SubagentRun(
        id="run-1",
        task_id="task-1",
        agent_id="fabric-data-engineer",
        agent_session_id="assignment-1",
    )
    execution.dynamic_mission_state = mission

    started = execution.emit(
        "tool_call_started",
        agentId="assignment-1",
        agentName="FabricDataEngineer",
        callId="call-1",
        toolName="fabric_create_workspace_inventory_solution",
    )
    execution.emit(
        "slot_progress",
        slotId="assignment-1",
        agentId="assignment-1",
        agentName="FabricDataEngineer",
        status="done",
        reason="verified",
    )

    projected = [event for event in execution._ring if event["type"].startswith("pi.subagents.")]
    assert [event["type"] for event in projected] == [
        "pi.subagents.status",
        "pi.subagents.control",
        "pi.subagents.status",
        "pi.subagents.result",
    ]
    assert all(event["runId"] == "run-1" for event in projected)
    assert all(event["taskId"] == "task-1" for event in projected)
    assert projected[0]["currentTool"] == "fabric_create_workspace_inventory_solution"
    assert projected[0]["sourceEventSeq"] == started["seq"]
    assert projected[1]["controlType"] == "active_long_running"
    assert projected[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_dynamic_job_emits_startup_events_before_runtime_attach_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.mcp import mission_runtime_manager

    monkeypatch.setenv("AGENTHUB_MCP_RUNTIME", "container")
    monkeypatch.setattr(oe, "update_session", lambda _job: None)

    async def fake_start(session_id: str, manager: object) -> object:
        raise RuntimeError("mission MCP runtime exited with code 137")

    monkeypatch.setattr(mission_runtime_manager, "start_mission_mcp_runtime", fake_start)
    emitted: list[dict] = []
    original_emit = oe._JobExecution.emit

    def recording_emit(self: oe._JobExecution, event_type: str, **kwargs):
        emitted.append({"type": event_type, **kwargs})
        return original_emit(self, event_type, **kwargs)

    monkeypatch.setattr(oe._JobExecution, "emit", recording_emit)

    job = _make_job("runtime-start-fail")
    job.composition = _make_composition(job.id)
    engine = OrchestratorEngine(mcp_manager=MagicMock())

    result = await engine._start_dynamic_job(job, copilot_token="t", mcp_tokens=None)

    assert result == job.id
    assert job.status == JobStatus.FAILED
    assert job.id not in engine._active_jobs
    event_types = [event["type"] for event in emitted]
    assert event_types[:4] == ["composition_ready", "run_overview", "slot_progress", "log_line"]
    assert any(event["type"] == "log_line" and "Preparing the isolated tool runtime" in event["message"] for event in emitted)
    assert any(event["type"] == "agent_error" and "not a problem with your prompt" in event["error"] for event in emitted)
    failed_event = next(event for event in emitted if event["type"] == "job_failed")
    assert failed_event["status"] == "failed"
    assert "runtime/startup error" in failed_event["reason"]


@pytest.mark.asyncio
async def test_dynamic_monitor_completes_after_recovered_assignment_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recovered dynamic missions should not finish as job_failed because of stale slot errors."""
    job = _make_job("dynamic-recovered")
    job.agents.append(
        AgentAssignment(
            agent_id="fabric-verifier",
            session_id="failed-verifier-slot",
            role="Verify user-facing Fabric deliverables",
            goal="verify",
            status=AgentStatus.ERROR,
            current_step="Container exited with code 255",
        )
    )
    execution = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    execution.dynamic_mission_state = MissionState(
        brief=MissionBrief(session_id=job.id, goal="g", workspace_id=job.workspace_id),
        status=MissionStatus.COMPLETED,
    )
    emitted: list[str] = []
    monkeypatch.setattr(execution, "emit", lambda event_type, **_: emitted.append(event_type))
    monkeypatch.setattr(execution, "close_mcp_runtime", AsyncMock())
    engine = OrchestratorEngine()
    engine._active_jobs[job.id] = execution

    await engine._monitor_job(execution)

    assert job.status == JobStatus.COMPLETED
    assert emitted[-1] == "job_complete"
    assert job.id not in engine._active_jobs


def test_cancel_job_unknown_returns_false() -> None:
    engine = OrchestratorEngine()
    assert engine.cancel_job("missing") is False


def test_cancel_job_known_marks_cancelled_and_cancels_tasks() -> None:
    engine = OrchestratorEngine()
    job = _make_job()
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    fake_task = MagicMock()
    exe.tasks.append(fake_task)
    engine._active_jobs[job.id] = exe

    assert engine.cancel_job(job.id) is True
    assert exe.cancelled is True
    fake_task.cancel.assert_called_once()


def test_inject_message_unknown_job_returns_false() -> None:
    engine = OrchestratorEngine()
    assert engine.inject_message("missing", "hi") is None


@pytest.mark.asyncio
async def test_inject_message_routes_to_specific_session() -> None:

    engine = OrchestratorEngine()
    job = _make_job()
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    q = asyncio.Queue()
    exe.user_message_queues["agent-session-1"] = q
    engine._active_jobs[job.id] = exe

    result = engine.inject_message(job.id, "hello", "agent-session-1")
    assert result is not None
    assert result["status"] == "queued"
    assert result["targetMode"] == "agent"
    queued = q.get_nowait()
    assert queued.message == "hello"
    assert queued.target_agent_session_id == "agent-session-1"
    assert exe._ring[-1]["type"] == "user_message_queued"
    assert exe._ring[-1]["messagePreview"] == "hello"


@pytest.mark.asyncio
async def test_inject_message_broadcasts_when_no_target() -> None:

    engine = OrchestratorEngine()
    job = _make_job()
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    q1 = asyncio.Queue()
    q2 = asyncio.Queue()
    exe.user_message_queues["a"] = q1
    exe.user_message_queues["b"] = q2
    engine._active_jobs[job.id] = exe

    result = engine.inject_message(job.id, "broadcast")
    assert result is not None
    assert result["targetMode"] == "broadcast"
    assert q1.get_nowait().message == "broadcast"
    assert q2.get_nowait().message == "broadcast"
    assert [event["type"] for event in exe._ring[-2:]] == ["user_message_queued", "user_message_broadcast"]


@pytest.mark.asyncio
async def test_inject_message_interrupt_is_deferred_to_safe_boundary() -> None:

    engine = OrchestratorEngine()
    job = _make_job()
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    q = asyncio.Queue()
    exe.user_message_queues["agent-session-1"] = q
    engine._active_jobs[job.id] = exe

    result = engine.inject_message(job.id, "stop and use the existing lakehouse", "agent-session-1", mode="interrupt")
    assert result is not None
    assert result["mode"] == "interrupt"
    assert q.get_nowait().mode == "interrupt"
    assert [event["type"] for event in exe._ring[:3]] == [
        "turn_interrupt_requested",
        "turn_interrupt_deferred",
        "user_message_queued",
    ]


def test_get_job_execution() -> None:
    engine = OrchestratorEngine()
    assert engine.get_job_execution("missing") is None
    job = _make_job()
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    engine._active_jobs[job.id] = exe
    assert engine.get_job_execution(job.id) is exe


def test_copilot_headers_shape() -> None:
    h = oe._copilot_headers("tok-1")
    assert h["Authorization"] == "Bearer tok-1"
    assert h["Copilot-Integration-Id"] == "vscode-chat"
    assert h["Content-Type"] == "application/json"


class _SchemaMcpManager:
    def __init__(self, tool_names: set[str]) -> None:
        self.tool_names = tool_names

    def get_openai_tools_schema(self) -> list[dict]:
        return [
            {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
            for name in sorted(self.tool_names)
        ]


def test_generalist_wildcard_resolves_to_bootstrap_tools_under_model_limit() -> None:
    tool_names = {f"extra_tool_{idx}" for idx in range(154)} | set(oe._GENERALIST_BOOTSTRAP_TOOLS) | {
        "fabric_create_item",
        "fabric_delete_item",
    }

    resolved = oe._resolve_wildcard_tool_scope("generalist", _SchemaMcpManager(tool_names))

    assert len(resolved) <= oe.MODEL_TOOL_SCHEMA_LIMIT
    assert resolved <= oe._GENERALIST_BOOTSTRAP_TOOLS
    assert "fabric_list_items" in resolved
    assert "fabric_create_folder" in resolved
    assert "fabric_create_item" not in resolved
    assert "fabric_delete_item" not in resolved
    assert "extra_tool_1" not in resolved


def test_specialist_wildcard_resolves_to_catalog_tools_not_full_mcp_fleet() -> None:
    template = oe.get_template("fabric-data-engineer")
    assert template is not None
    catalog_tools = set(template.available_tools)
    tool_names = catalog_tools | {f"extra_tool_{idx}" for idx in range(154)}

    resolved = oe._resolve_wildcard_tool_scope("fabric-data-engineer", _SchemaMcpManager(tool_names))

    assert len(resolved) <= oe.MODEL_TOOL_SCHEMA_LIMIT
    assert resolved <= catalog_tools
    assert set(template.available_tools[:oe.MODEL_TOOL_SCHEMA_LIMIT]) <= resolved
    assert "extra_tool_1" not in resolved


@pytest.mark.asyncio
async def test_add_agent_to_job_unknown_job_returns_none() -> None:
    """Orchestrator's team-orchestration capability: attaching a new
    agent to a job that doesn't exist in ``_active_jobs`` is a no-op
    that returns ``None`` (not an exception)."""
    engine = OrchestratorEngine()
    result = await engine.add_agent_to_job(
        "missing-job", agent_id="fabric-admin", role="Admin",
    )
    assert result is None


@pytest.mark.asyncio
async def test_add_agent_to_job_unknown_agent_returns_none() -> None:
    """Catalog protection: unknown ``agent_id`` is dropped rather than
    crashing the running job."""
    engine = OrchestratorEngine()
    job = _make_job()
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    engine._active_jobs[job.id] = exe
    result = await engine.add_agent_to_job(
        job.id, agent_id="does-not-exist", role="Ghost",
    )
    assert result is None
    assert job.agents == []  # no assignment leaked in


@pytest.mark.asyncio
async def test_add_agent_to_job_when_cancelling_returns_none() -> None:
    """A job that is stopping (cancel_event set) must refuse new
    attachments — otherwise the new agent would race against
    ``_monitor_job`` completion."""
    engine = OrchestratorEngine()
    job = _make_job()
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    exe.cancel_event.set()
    engine._active_jobs[job.id] = exe
    result = await engine.add_agent_to_job(
        job.id, agent_id="fabric-admin", role="Admin",
    )
    assert result is None


@pytest.mark.asyncio
async def test_driver_exception_marks_job_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oe, "update_session", lambda _job: None)

    engine = OrchestratorEngine()
    job = _make_job("j-driver")
    assignment = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-driver",
        role="Admin",
        goal="Run driver",
        status=AgentStatus.QUEUED,
    )
    job.agents.append(assignment)
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    exe._test_events = []
    original_emit = exe.emit

    def recording_emit(event_type: str, **kwargs):
        exe._test_events.append({"type": event_type, **kwargs})
        original_emit(event_type, **kwargs)

    exe.emit = recording_emit

    class _FailingDriver:
        async def run(self, **_kwargs):
            raise RuntimeError("boom")

    task = asyncio.create_task(
        engine._run_driver(
            exe,
            _FailingDriver(),
            runner=SimpleNamespace(),
            budget=SimpleNamespace(),
        )
    )
    exe.tasks.append(task)

    await engine._monitor_job(exe)

    assert assignment.status == AgentStatus.ERROR
    assert assignment.current_step and "driver failed" in assignment.current_step
    assert job.status == JobStatus.FAILED
    assert any(e["type"] == "slot_progress" and e["status"] == "failed" for e in exe._test_events)
    assert any(e["type"] == "job_failed" for e in exe._test_events)


@pytest.mark.asyncio
async def test_monitor_spawns_recovery_agent_and_waits_for_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(oe, "update_session", lambda _job: None)

    engine = OrchestratorEngine()
    job = _make_job("j-recover")
    failed = AgentAssignment(
        agent_id="fabric-data-engineer",
        session_id="agent-failed",
        role="Build lakehouse",
        goal="Create the lakehouse and notebook artifacts",
        status=AgentStatus.ERROR,
        current_step="LLM error: timeout while creating lakehouse artifacts",
    )
    peer = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-peer",
        role="Admin validation",
        goal="Validate workspace state",
        status=AgentStatus.RUNNING,
    )
    job.agents.extend([failed, peer])
    exe = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    exe._test_events = []
    original_emit = exe.emit

    def recording_emit(event_type: str, **kwargs):
        exe._test_events.append({"type": event_type, **kwargs})
        original_emit(event_type, **kwargs)

    exe.emit = recording_emit
    engine._active_jobs[job.id] = exe

    async def peer_task():
        await asyncio.sleep(0.01)
        peer.status = AgentStatus.COMPLETED

    async def fake_run_agent(execution, assignment, template, user_q, *, allowed_tools=None):
        del user_q, allowed_tools
        assignment.status = AgentStatus.COMPLETED
        assignment.current_step = "Completed"
        execution.emit(
            "agent_status",
            agentId=assignment.session_id,
            agentName=template.display_name,
            status="completed",
            currentStep="Completed",
        )
        execution.emit(
            "slot_progress",
            slotId=assignment.session_id,
            agentId=assignment.session_id,
            status="done",
            agentName=template.display_name,
        )

    monkeypatch.setattr(engine, "_run_agent", fake_run_agent)
    exe.tasks.append(asyncio.create_task(peer_task()))

    await engine._monitor_job(exe)

    recovery_agents = [a for a in job.agents if getattr(a, "_recovery_for", None) == failed.session_id]
    assert len(recovery_agents) == 1
    assert recovery_agents[0].status == AgentStatus.COMPLETED
    assert getattr(failed, "_recovery_status", None) == "recovered"
    assert job.status == JobStatus.COMPLETED
    assert any(e["type"] == "agent_added" for e in exe._test_events)
    assert any(
        e["type"] == "log_line" and "spawned fabric-data-engineer" in e.get("message", "")
        for e in exe._test_events
    )
    assert any(e["type"] == "job_complete" for e in exe._test_events)


@pytest.mark.asyncio
async def test_monitor_requests_user_action_for_capacity_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(oe, "update_session", lambda _job: None)

    engine = OrchestratorEngine()
    job = _make_job("j-user-action")
    failed = AgentAssignment(
        agent_id="fabric-data-engineer",
        session_id="agent-capacity",
        role="Create Fabric items",
        goal="Create lakehouse items",
        status=AgentStatus.ERROR,
        current_step="fabric_create_item failed with 404 CapacityNotActive",
    )
    job.agents.append(failed)
    exe = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    exe._test_events = []
    original_emit = exe.emit

    def recording_emit(event_type: str, **kwargs):
        exe._test_events.append({"type": event_type, **kwargs})
        original_emit(event_type, **kwargs)

    exe.emit = recording_emit
    engine._active_jobs[job.id] = exe

    await engine._monitor_job(exe)

    assert getattr(failed, "_recovery_status", None) == "requires_user"
    assert job.status == JobStatus.FAILED
    approval = next(e for e in exe._test_events if e["type"] == "approval_required")
    assert approval["approvalId"].startswith("recovery-")
    assert "retry_after_fix" in approval["recoveryActions"]
    assert any(
        e["type"] == "log_line" and "user action required" in e.get("message", "")
        for e in exe._test_events
    )


@pytest.mark.asyncio
async def test_monitor_stops_remaining_work_for_policy_boundary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(oe, "update_session", lambda _job: None)

    engine = OrchestratorEngine()
    job = _make_job("j-stop")
    failed = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-policy",
        role="Admin guardrail",
        goal="Validate workspace policy",
        status=AgentStatus.ERROR,
        current_step="Cross-workspace policy violation blocked by tool policy",
    )
    running = AgentAssignment(
        agent_id="fabric-data-engineer",
        session_id="agent-running",
        role="Builder still running",
        goal="Build artifacts",
        status=AgentStatus.RUNNING,
    )
    job.agents.extend([failed, running])
    exe = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    exe._test_events = []
    original_emit = exe.emit

    def recording_emit(event_type: str, **kwargs):
        exe._test_events.append({"type": event_type, **kwargs})
        original_emit(event_type, **kwargs)

    exe.emit = recording_emit
    engine._active_jobs[job.id] = exe

    never = asyncio.Event()

    async def running_task():
        await never.wait()

    exe.tasks.append(asyncio.create_task(running_task()))

    await asyncio.wait_for(engine._monitor_job(exe), timeout=1.0)

    assert getattr(failed, "_recovery_status", None) == "stopped"
    assert running.status == AgentStatus.ERROR
    assert running.current_step and "Stopped by orchestrator" in running.current_step
    assert job.status == JobStatus.FAILED
    assert any(
        e["type"] == "log_line" and "stopping remaining work" in e.get("message", "")
        for e in exe._test_events
    )


def test_apply_single_created_folder_id_replaces_placeholder() -> None:
    job = _make_job("j-folder-apply")
    folder_agent = AgentAssignment(agent_id="a", session_id="s", role="folder", goal="folder")
    folder_agent.actions.append(
        AgentAction(
            id="action-folder",
            action_type="Created",
            entity_name="Run Folder",
            entity_type="Folder",
            fabric_item_id="folder-1",
        )
    )
    job.agents.append(folder_agent)
    tool_args = {"display_name": "Notebook", "folderId": "assigned folder"}

    applied_folder_id, supplied_folder_id = _apply_single_created_folder_id(tool_args, job)

    assert applied_folder_id == "folder-1"
    assert supplied_folder_id == "assigned folder"
    assert tool_args["folder_id"] == "folder-1"
    assert "folderId" not in tool_args


def test_required_creation_tool_detects_create_roles() -> None:
    assert _required_creation_tool_for_goal(
        "Create an end to end solution with ingestion, transformation, semantic modelling and a report "
        "which shows all Fabric items I have access to in a visualization. Work in folder tmp_20260426193000."
    ) == "fabric_create_workspace_inventory_solution"
    assert _required_creation_tool_for_goal(
        "Task: call fabric_create_folder\nYour role: Coordinating lead creates folder."
    ) == "fabric_create_folder"
    assert _required_creation_tool_for_goal(
        "Task: call fabric_create_item\nYour role: Creates the Lakehouse item shell."
    ) == "fabric_create_item"
    assert _required_creation_tool_for_goal(
        "Task: call fabric_create_folder first, then fabric_create_item with folder_id\n"
        "Your role: Create the Lakehouse item shell in the assigned folder."
    ) == "fabric_create_item"
    assert _required_creation_tool_for_goal(
        "Task: call fabric_create_folder first, then fabric_create_item with folder_id\n"
        "Your role: Coordinate the audit, create the folder, and assign item creation tasks."
    ) == "fabric_create_folder"
    assert _required_creation_tools_for_goal(
        "Task: call fabric_create_folder first, then fabric_create_item with folder_id\n"
        "Your role: Create folder and notebook item in the workspace."
    ) == ("fabric_create_folder", "fabric_create_item")
    assert _required_creation_tool_for_goal(
        "Task: call fabric_create_item\nYour role: Audits item inventory."
    ) is None


def test_required_creation_tool_detects_explicit_owner_clauses() -> None:
    mixed_task = (
        "Task: call fabric_create_folder first, then fabric_create_item with folder_id. "
        "Use explicit creation ownership: the top-level coordinator creates the run folder; "
        "the diagnostics lead creates the Lakehouse; "
        "the remediation lead creates the remediation Notebook; "
        "the review actor creates the review Notebook. "
        "All other workers verify or critique only and must not create folders or extra items."
    )

    assert _required_creation_tool_for_goal(
        f"{mixed_task}\nYour slot id: coordinator.\nYour role: Top-level coordinator managing sub-teams."
    ) == "fabric_create_folder"
    assert _required_creation_tool_for_goal(
        f"{mixed_task}\nYour slot id: diagnostics-lead.\nYour role: Lead diagnostics sub-team for inventory."
    ) == "fabric_create_item"
    assert _required_creation_tool_for_goal(
        f"{mixed_task}\nYour slot id: review-actor.\nYour role: Draft the playbook text."
    ) == "fabric_create_item"
    assert _required_creation_tool_for_goal(
        f"{mixed_task}\nYour slot id: diagnostics-worker-1.\nYour role: Perform diagnostics on inventory."
    ) is None


def test_explicit_creation_ownership_limits_write_tools_to_owner_role() -> None:
    task = (
        "Task: call fabric_create_folder first, then fabric_create_item with folder_id. "
        "Use exactly this fixed order: first an architect creates the run folder; "
        "then a modeler creates the Lakehouse item; "
        "then a data engineer verifies the outputs and must not create folders or extra items."
    )
    all_write_tools = {
        "fabric_create_folder",
        "fabric_create_item",
        "fabric_create_directory",
        "fabric_write_file",
        "fabric_list_items",
    }

    architect_goal = f"{task}\nYour slot id: architect.\nYour role: Draft the contract."
    architect_required = _required_creation_tools_for_goal(architect_goal)
    assert _limit_creation_tools_for_goal(architect_goal, all_write_tools, architect_required) == {
        "fabric_create_folder",
        "fabric_list_items",
    }

    modeler_goal = f"{task}\nYour slot id: modeler.\nYour role: Build the model handoff."
    modeler_required = _required_creation_tools_for_goal(modeler_goal)
    assert _limit_creation_tools_for_goal(modeler_goal, all_write_tools, modeler_required) == {
        "fabric_create_item",
        "fabric_list_items",
    }

    verifier_goal = f"{task}\nYour slot id: verifier.\nYour role: Verify the outputs."
    verifier_required = _required_creation_tools_for_goal(verifier_goal)
    assert _limit_creation_tools_for_goal(verifier_goal, all_write_tools, verifier_required) == {
        "fabric_list_items",
    }


def test_creation_ownership_after_hard_requirements_limits_write_tools() -> None:
    task = (
        "Task: run a peer-network Fabric review.\n\n"
        "Hard creation requirements for this E2E run:\n"
        "- First call fabric_create_folder with the exact run folder name.\n"
        "- Use the returned folder id for every fabric_create_item call.\n"
        "- Create exactly one Notebook inside that folder; do not create extra items.\n\n"
        "Use explicit peer duties while preserving the peer-network debate: "
        "the data engineer peer creates the run folder, "
        "the modeler peer creates the decision Notebook inside that folder, "
        "and the admin peer verifies it by listing workspace items before the group returns its recommendation."
    )
    all_write_tools = {
        "fabric_create_folder",
        "fabric_create_item",
        "fabric_create_directory",
        "fabric_write_file",
        "fabric_list_items",
    }

    folder_owner_goal = f"{task}\nYour slot id: peer-data-engineer.\nYour role: Creates the run folder."
    folder_required = _required_creation_tools_for_goal(folder_owner_goal)
    assert _limit_creation_tools_for_goal(folder_owner_goal, all_write_tools, folder_required) == {
        "fabric_create_folder",
        "fabric_list_items",
    }

    item_owner_goal = f"{task}\nYour slot id: peer-modeler.\nYour role: Creates the decision Notebook."
    item_required = _required_creation_tools_for_goal(item_owner_goal)
    assert _limit_creation_tools_for_goal(item_owner_goal, all_write_tools, item_required) == {
        "fabric_create_item",
        "fabric_list_items",
    }

    verifier_goal = f"{task}\nYour slot id: peer-admin.\nYour role: Verifies the created items."
    verifier_required = _required_creation_tools_for_goal(verifier_goal)
    assert _limit_creation_tools_for_goal(verifier_goal, all_write_tools, verifier_required) == {
        "fabric_list_items",
    }


def test_mandatory_verifier_goal_does_not_force_inventory_creation_tool() -> None:
    goal = (
        "Title: Verify user-facing Fabric deliverables\n"
        "Objective: Independently verify the produced user-facing Fabric/Power BI deliverables before the mission can finish. "
        "Call browser_verify_visual_render against every Report/Dashboard webUrl below.\n"
        "Original goal:\n"
        "Create an end to end solution (ingestion, transformation, semantic modelling and a report) which shows all Fabric items "
        "I have access to in a nice appropriate visualization."
    )

    required = _required_creation_tools_for_goal(goal)

    assert required == ()
    assert _limit_creation_tools_for_goal(
        goal,
        {"fabric_create_workspace_inventory_solution", "fabric_list_items", "browser_verify_visual_render"},
        required,
    ) == {"fabric_list_items", "browser_verify_visual_render"}


def test_required_inventory_creation_ignores_verifier_tool_catalog_mentions() -> None:
    goal = (
        "MISSION GOAL: Create an end to end solution with ingestion, transformation, semantic modelling "
        "and a report which shows all Fabric items I have access to in a visualization. "
        "Work in folder tmp_20260428103231.\n"
        "DYNAMIC TASK: use fabric_create_workspace_inventory_solution directly as the single "
        "purpose-built implementation tool.\n"
        "Available specialist catalog includes browser_verify_visual_render for later verifier tasks."
    )

    assert _required_creation_tools_for_goal(goal) == ("fabric_create_workspace_inventory_solution",)


def test_limit_tools_for_model_caps_and_keeps_required_tool_first() -> None:
    required_tool = "fabric_create_workspace_inventory_solution"
    tools = [
        {"type": "function", "function": {"name": f"generic_tool_{index}", "parameters": {}}}
        for index in range(20)
    ] + [{"type": "function", "function": {"name": required_tool, "parameters": {}}}]

    selected = oe._limit_tools_for_model(
        tools,
        goal="Create a Fabric item inventory semantic model and report visualization.",
        required_tool_names=(required_tool,),
        limit=5,
    )

    assert len(selected) == 5
    assert selected[0]["function"]["name"] == required_tool


def test_specialist_goal_with_verifier_in_catalog_still_requires_creation_tool() -> None:
    """Regression: a producer goal that merely lists FabricVerifier in its
    SPECIALIST CATALOG must NOT be treated as verifier-only. Otherwise the
    required-creation gate is silently skipped and the producer can ship a
    text-only ACTION marker without ever calling the build tool."""
    goal = (
        "MISSION GOAL:\n"
        "Create an end to end solution (ingestion, transformation, semantic modelling and a report) "
        "which shows all Fabric items I have access to in a nice appropriate visualization.\n"
        "DYNAMIC TASK:\n"
        "Title: Create end-to-end solution for Fabric item visualization\n"
        "Objective: Design and implement an end-to-end solution to visualize all Fabric items "
        "the user has access to. This includes ingestion, transformation, semantic modeling, and a report.\n"
        "SPECIALIST CATALOG:\n"
        "- fabric-verifier (FabricVerifier): Fabric acceptance verifier - checks created items, "
        "data, semantic models, and report visuals against the original task.\n"
        "- fabric-data-engineer (FabricDataEngineer): Fabric builder - creates items, etc.\n"
    )

    from services.agenthub.orchestrator_engine import _is_verification_only_goal

    assert _is_verification_only_goal(goal) is False
    assert _required_creation_tools_for_goal(goal) == ("fabric_create_workspace_inventory_solution",)


def test_extract_dynamic_followups_handles_long_block_split_across_phase_details() -> None:
    """Regression: previously phase.details[-5:] truncated the JSON and the
    parser missed the entire DYNAMIC_RESULT block, so the generalist's
    delegation never produced specialist tasks."""
    from domain.models.agent_models import AgentAssignment, AgentStatus, ReasoningPhase

    raw = (
        "PHASE_START: 1 | Delegate Fabric Inventory Solution Creation\n"
        "ACTION: Delegating to specialist.\n"
        "DYNAMIC_RESULT_START\n"
        "{\n"
        '  "followupTasks": [\n'
        "    {\n"
        '      "title": "Create Fabric Inventory Solution",\n'
        '      "objective": "End-to-end ingestion, transformation, semantic model and report.",\n'
        '      "candidateAgentIds": ["fabric-data-engineer"],\n'
        '      "delegationReason": "Owns inventory tool.",\n'
        '      "parallelismSafe": false\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "DYNAMIC_RESULT_END\n"
        "DECISION: Handing off to fabric-data-engineer."
    )
    phase = ReasoningPhase(phase_number=1, title="Round 1", description="")
    phase.details = [line.strip() for line in raw.splitlines() if line.strip()]
    assignment = AgentAssignment(
        session_id="s-1", agent_id="generalist", role="generalist", goal="g",
        status=AgentStatus.RUNNING,
    )
    assignment.phases.append(phase)

    followups = _extract_dynamic_followups(assignment)

    assert len(followups) == 1
    assert followups[0].candidate_agent_ids == ["fabric-data-engineer"]
    assert followups[0].title == "Create Fabric Inventory Solution"


def test_extract_dynamic_followups_clips_overlong_objective() -> None:
    from domain.models.agent_models import AgentAssignment, AgentStatus, ReasoningPhase

    raw = {
        "followupTasks": [
            {
                "title": "Verify",
                "objective": "x" * 5000,
                "candidateAgentIds": ["fabric-verifier"],
            }
        ]
    }
    phase = ReasoningPhase(phase_number=1, title="Round 1", description="")
    phase.details = ["DYNAMIC_RESULT_START", json.dumps(raw), "DYNAMIC_RESULT_END"]
    assignment = AgentAssignment(
        session_id="s-1", agent_id="generalist", role="generalist", goal="g",
        status=AgentStatus.RUNNING,
    )
    assignment.phases.append(phase)

    followups = _extract_dynamic_followups(assignment)

    assert len(followups) == 1
    assert len(followups[0].objective) <= 4000


def test_creation_ownership_with_network_handoffs_limits_write_tools() -> None:
    task = (
        "Task: Within only workspace ws-1, run a bounded peer-network review with no top-level supervisor.\n\n"
        "Hard creation requirements for this E2E run:\n"
        "- First call fabric_create_folder in workspace ws-1 with display_name \"E2E network run\".\n"
        "- Use the returned folder id as folder_id for every fabric_create_item call.\n"
        "- Create exactly these Fabric item shells inside that folder; do not create them at workspace root and do not create extra items:\n"
        "1. call fabric_create_item with item_type \"Notebook\", display_name \"E2E_network_Decision_Notebook\", "
        "description \"peer-network decision record shell\", and folder_id set to the folder id returned by fabric_create_folder.\n"
        "- Leave the folder and items in the workspace until E2E verification finishes; do not delete them during the run.\n\n"
        "Use explicit peer duties while preserving the peer-network debate: "
        "the data engineer peer creates the run folder, "
        "the modeler peer creates the decision Notebook inside that folder, "
        "and the admin peer verifies it by listing workspace items before the group returns its recommendation."
    )
    all_write_tools = {
        "fabric_create_folder",
        "fabric_create_item",
        "fabric_create_directory",
        "fabric_write_file",
        "fabric_list_items",
    }

    folder_owner_goal = f"{task}\nYour slot id: peer-data-engineer.\nYour role: Creates the run folder and contributes to the peer-network debate."
    folder_required = _required_creation_tools_for_goal(folder_owner_goal)
    assert folder_required == ("fabric_create_folder",)
    assert _limit_creation_tools_for_goal(folder_owner_goal, all_write_tools, folder_required) == {
        "fabric_create_folder",
        "fabric_list_items",
    }

    item_owner_goal = (
        "[UPSTREAM HANDOFF from Creates the run folder and contributes to the peer-network debate. (peer-data-engineer)]\n"
        "Status: partial\n"
        "Summary: Completed\n"
        "Artifacts: E2E network run (Folder, id=folder-1)\n"
        "  folder_id: folder-1\n"
        "  folder_name: E2E network run\n"
        "[END UPSTREAM HANDOFF]\n\n"
        f"{task}\nYour slot id: peer-modeler.\nYour role: Creates the decision Notebook and contributes to the peer-network debate."
    )
    item_required = _required_creation_tools_for_goal(item_owner_goal)
    assert item_required == ("fabric_create_item",)
    assert _limit_creation_tools_for_goal(item_owner_goal, all_write_tools, item_required) == {
        "fabric_create_item",
        "fabric_list_items",
    }

    verifier_goal = (
        "[UPSTREAM HANDOFF from Creates the decision Notebook and contributes to the peer-network debate. (peer-modeler)]\n"
        "Status: completed\n"
        "Summary: Created decision Notebook\n"
        "[END UPSTREAM HANDOFF]\n\n"
        f"{task}\nYour slot id: peer-admin.\nYour role: Verifies the created items and contributes to the peer-network debate."
    )
    verifier_required = _required_creation_tools_for_goal(verifier_goal)
    assert verifier_required == ()
    assert _limit_creation_tools_for_goal(verifier_goal, all_write_tools, verifier_required) == {
        "fabric_list_items",
    }


def test_creation_ownership_with_sequential_source_references_limits_write_tools() -> None:
    task = (
        "Task: execute a strict sequential handoff. "
        "Use exactly this fixed order: first an architect creates the run folder and drafts the logical contract; "
        "then a modeler creates the Lakehouse item from that contract; "
        "then a data engineer creates the Notebook item from the modeler's output.\n\n"
        "Hard creation requirements for this E2E run:\n"
        "- First call fabric_create_folder in workspace ws-1 with display_name \"E2E sequential run\".\n"
        "- Use the returned folder id as folder_id for every fabric_create_item call.\n"
        "- Create exactly these Fabric item shells inside that folder; do not create extra items:\n"
        "1. call fabric_create_item with item_type \"Lakehouse\", display_name \"E2E_sequential_Lakehouse\", "
        "description \"lakehouse shell\", and folder_id set to the folder id returned by fabric_create_folder.\n"
        "2. call fabric_create_item with item_type \"Notebook\", display_name \"E2E_sequential_Notebook\", "
        "description \"notebook shell\", and folder_id set to the folder id returned by fabric_create_folder."
    )
    all_write_tools = {
        "fabric_create_folder",
        "fabric_create_item",
        "fabric_create_directory",
        "fabric_write_file",
        "fabric_list_items",
    }

    architect_goal = f"{task}\nYour slot id: architect.\nYour role: Drafts the logical improvement contract and creates the run folder."
    architect_required = _required_creation_tools_for_goal(architect_goal)
    assert architect_required == ("fabric_create_folder",)

    modeler_goal = (
        "[UPSTREAM HANDOFF from Drafts the logical improvement contract and creates the run folder. (architect)]\n"
        "Status: partial\n"
        "Artifacts: E2E sequential run (Folder, id=folder-1)\n"
        "[END UPSTREAM HANDOFF]\n\n"
        f"{task}\nYour slot id: modeler.\nYour role: Creates the Lakehouse item from the architect's contract."
    )
    modeler_required = _required_creation_tools_for_goal(modeler_goal)
    assert modeler_required == ("fabric_create_item",)
    assert _limit_creation_tools_for_goal(modeler_goal, all_write_tools, modeler_required) == {
        "fabric_create_item",
        "fabric_list_items",
    }

    data_engineer_goal = (
        "[UPSTREAM HANDOFF from Creates the Lakehouse item from the architect's contract. (modeler)]\n"
        "Status: partial\n"
        "Artifacts: E2E_sequential_Lakehouse (Lakehouse, id=lakehouse-1)\n"
        "[END UPSTREAM HANDOFF]\n\n"
        f"{task}\nYour slot id: data-engineer.\nYour role: Creates the Notebook item from the modeler's output."
    )
    data_engineer_required = _required_creation_tools_for_goal(data_engineer_goal)
    assert data_engineer_required == ("fabric_create_item",)
    assert _limit_creation_tools_for_goal(data_engineer_goal, all_write_tools, data_engineer_required) == {
        "fabric_create_item",
        "fabric_list_items",
    }


def test_dynamic_task_goal_detects_sequential_creation_owner() -> None:
    goal = (
        "MISSION GOAL:\n"
        "Within only workspace ws-1, execute a strict sequential handoff. "
        "Use exactly this fixed order: first an architect creates the run folder and drafts the logical contract; "
        "then a modeler creates the Lakehouse item from that contract; "
        "then a data engineer creates the Notebook item from the modeler's output.\n\n"
        "Hard creation requirements for this E2E run:\n"
        "- First call fabric_create_folder in workspace ws-1 with display_name \"E2E sequential run\".\n"
        "- Use the returned folder id as folder_id for every fabric_create_item call.\n\n"
        "DYNAMIC TASK:\n"
        "Title: Drafts the logical improvement contract and creates the run folder.\n"
        "Objective: Execute composition slot architect for mission: create the required folder and item shells.\n"
        "Task id: architect\n"
        "Workspace id: ws-1\n"
    )

    assert _required_creation_tools_for_goal(goal) == ("fabric_create_folder",)

    all_write_tools = {
        "fabric_create_folder",
        "fabric_create_item",
        "fabric_create_directory",
        "fabric_write_file",
        "fabric_list_items",
    }
    assert _limit_creation_tools_for_goal(goal, all_write_tools, _required_creation_tools_for_goal(goal)) == {
        "fabric_create_folder",
        "fabric_list_items",
    }


def test_parse_agent_output_ignores_text_only_mutating_action() -> None:
    job = _make_job("j-action")
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    assignment = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-action",
        role="Admin",
        goal="Create an item",
    )
    template = SimpleNamespace(display_name="FabricAdmin")

    _parse_agent_output(
        "ACTION: Created | ENTITY: fake_lakehouse | TYPE: Lakehouse",
        assignment,
        exe,
        template,
    )

    assert assignment.actions == []
    assert any(
        ev.get("type") == "log_line" and "Ignoring text-only ACTION" in str(ev.get("message", ""))
        for ev in exe._ring
    )


# ── _detect_action_from_tool sweep ──────────────────────────────────


@pytest.mark.parametrize("tool,args,result,expected_type,expected_action", [
    # Successful actions
    ("fabric_create_item", {"display_name": "LH", "item_type": "Lakehouse"}, "ok",
     "Lakehouse", "Created"),
    ("fabric_create_folder", {"display_name": "Run Folder"}, "ok",
     "Folder", "Created"),
    ("fabric_write_file", {"file_path": "Files/x"}, "ok",
     "File", "Modified"),
    ("fabric_delete_file", {"file_path": "Files/x"}, "ok",
     "File", "Deleted"),
    ("fabric_delete_item", {"item_id": "i-1"}, "ok",
     "Item", "Deleted"),
    ("fabric_create_directory", {"directory_path": "Files/d"}, "ok",
     "Directory", "Created"),
    ("fabric_list_workspaces", {}, "[...]",
     "Workspace", "Queried"),
    ("fabric_list_items", {"workspace_id": "ws-12345678", "item_type": "Lakehouse"}, "ok",
     "Items", "Queried"),
    ("fabric_list_files", {"path": "Files/"}, "ok",
     "Files", "Queried"),
    ("fabric_read_file", {"file_path": "Files/x"}, "ok",
     "File", "Read"),
])
def test_detect_action_success_paths(
    tool: str, args: dict, result: str, expected_type: str, expected_action: str,
) -> None:
    action = _detect_action_from_tool(tool, args, result)
    assert action is not None
    assert action.entity_type == expected_type
    assert action.action_type == expected_action


@pytest.mark.parametrize("tool,args,result", [
    ("fabric_create_item", {"display_name": "LH", "item_type": "Lakehouse"},
     "Error: 403 Forbidden"),
    ("fabric_write_file", {"file_path": "Files/x"}, "Error: Unauthorized"),
    ("fabric_delete_file", {"file_path": "Files/x"}, "Error: Not Found"),
    ("fabric_delete_item", {"item_id": "i-1"}, "Failed: bad request"),
])
def test_detect_action_error_paths_mark_as_failed(
    tool: str, args: dict, result: str,
) -> None:
    """REGRESSION: error markers in the tool result must flip the action_type
    to "Failed" instead of misleadingly marking destructive actions as
    successful."""
    action = _detect_action_from_tool(tool, args, result)
    assert action is not None
    assert action.action_type == "Failed"


def test_detect_action_unknown_tool_returns_none() -> None:
    assert _detect_action_from_tool("unknown_tool", {}, "result") is None


@pytest.mark.parametrize("text", [
    "CapacityNotActive",
    "workspace capacity is inactive",
    "Cannot proceed until capacity is enabled",
])
def test_major_issue_level_detects_blocking_capacity_signals(text: str) -> None:
    assert _major_issue_level(text) == "error"


@pytest.mark.parametrize("text", [
    "Quota exceeded for this workload",
    "request throttled by service",
])
def test_major_issue_level_detects_capacity_warning_signals(text: str) -> None:
    assert _major_issue_level(text) == "warn"


def test_tool_result_major_issue_level_ignores_successful_structured_diagnostics() -> None:
        tool_result = """
<<<UNTRUSTED_TOOL_OUTPUT_BEGIN>>>
tool=fabric_diagnose_workspace_artifacts
{
    "status": "ok",
    "workspaceId": "workspace-1",
    "refreshHistory": [
        {"status": "Failed", "message": "source Delta table does not exist from a prior attempt"}
    ]
}
<<<UNTRUSTED_TOOL_OUTPUT_END>>>
"""

        assert _tool_result_major_issue_level(tool_result) is None


def test_tool_result_major_issue_level_detects_unstructured_blocker() -> None:
        assert _tool_result_major_issue_level("Semantic model refresh failed: 0xC14700C7") == "error"


def test_tool_result_requires_diagnostics_for_partial_refresh_failure() -> None:
    rt_result = SimpleNamespace(ok=True, policy_decision="allowed")
    tool_result = json.dumps({
        "status": "partial",
        "workspaceId": "workspace-1",
        "semanticModelId": "model-1",
        "errors": ["Semantic model refresh FAILED: 0xC14700C7 access permissions"],
    })

    needs_diagnostics, reason = oe._tool_result_requires_diagnostics(rt_result, tool_result)

    assert needs_diagnostics is True
    assert reason == "structured_status:partial"


def test_tool_result_does_not_require_diagnostics_for_transient_browser_capture_miss() -> None:
    rt_result = SimpleNamespace(ok=True, policy_decision="allowed")
    tool_result = json.dumps({
        "status": "failed",
        "errorCode": "EXPECTED_TEXT_NOT_VISIBLE",
        "reason": "expected text was not visible in the rendered page: 'Fabric Items'",
        "finalUrl": "https://app.powerbi.com/groups/workspace-1/reports/report-1",
        "bodyTextSample": "Power BI Report",
        "screenshotBytes": 1534,
    })

    needs_diagnostics, reason = oe._tool_result_requires_diagnostics(rt_result, tool_result)

    assert needs_diagnostics is False
    assert reason == ""


def test_tool_result_still_requires_diagnostics_for_browser_access_error() -> None:
    rt_result = SimpleNamespace(ok=True, policy_decision="allowed")
    tool_result = json.dumps({
        "status": "failed",
        "errorCode": "EXPECTED_TEXT_NOT_VISIBLE",
        "reason": "expected text was not visible because you don't have access to this report",
        "finalUrl": "https://app.powerbi.com/groups/workspace-1/reports/report-1",
    })

    needs_diagnostics, reason = oe._tool_result_requires_diagnostics(rt_result, tool_result)

    assert needs_diagnostics is True
    assert reason == "structured_status:failed"


def test_diagnostic_directive_uses_workspace_folder_and_item_context() -> None:
    directive = oe._diagnostic_directive(
        "fabric_create_workspace_inventory_solution",
        {"workspace_id": "workspace-1"},
        json.dumps({
            "status": "partial",
            "workspaceId": "workspace-1",
            "folderId": "folder-1",
            "semanticModelId": "model-1",
            "lakehouseId": "lakehouse-1",
            "errors": ["Direct Lake identity risk: owner mismatch"],
        }),
        "structured_status:partial",
    )

    assert "DIAGNOSTIC CHECKPOINT REQUIRED BEFORE RETRY" in directive
    assert "fabric_diagnose_workspace_artifacts" in directive
    assert '"workspace_id": "workspace-1"' in directive
    assert '"folder_id": "folder-1"' in directive
    assert "owner/permission" in directive


@pytest.mark.parametrize("exc", [
    httpx.ReadTimeout("slow"),
    httpx.ConnectError("dns failed"),
    RuntimeError("Temporary failure in name resolution"),
    RuntimeError("request timed out"),
])
def test_transient_llm_exception_detection(exc: Exception) -> None:
    assert _is_transient_llm_exception(exc) is True


def test_transient_llm_exception_detection_rejects_regular_errors() -> None:
    assert _is_transient_llm_exception(ValueError("bad request shape")) is False


def test_detect_action_capacity_not_active_marks_failed() -> None:
    action = _detect_action_from_tool(
        "fabric_create_item",
        {"display_name": "LH", "item_type": "Lakehouse"},
        "Operation failed: CapacityNotActive",
    )
    assert action is not None
    assert action.action_type == "Failed"


@pytest.mark.parametrize("tool,args,expected_type", [
    ("fabric_create_item", {"display_name": "LH", "item_type": "Lakehouse"}, "Lakehouse"),
    ("fabric_create_folder", {"display_name": "Run Folder"}, "Folder"),
])
def test_detect_action_existing_conflict_is_idempotent_success(
    tool: str,
    args: dict,
    expected_type: str,
) -> None:
    action = _detect_action_from_tool(
        tool,
        args,
        "Error creating item: 409 — ItemDisplayNameAlreadyInUse: item already exists",
    )

    assert action is not None
    assert action.action_type == "Created"
    assert action.entity_type == expected_type


def test_has_successful_required_creation_matches_tool_action_type() -> None:
    assignment = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-create",
        role="Creator",
        goal="Create",
    )
    assignment.actions.append(
        AgentAction(
            id="a-1",
            action_type="Created",
            entity_name="Run Folder",
            entity_type="Folder",
        )
    )

    assert _has_successful_required_creation(assignment, "fabric_create_folder") is True
    assert _has_successful_required_creation(assignment, "fabric_create_item") is False


def test_has_successful_required_creation_matches_inventory_solution() -> None:
    assignment = AgentAssignment(
        agent_id="generalist",
        session_id="agent-create",
        role="Generalist",
        goal="Create Fabric inventory solution",
    )
    assignment.actions.append(
        AgentAction(
            id="a-1",
            action_type="Created",
            entity_name="tmp_20260426193000",
            entity_type="WorkspaceInventorySolution",
            details=(
                '{"status":"created",'
                '"semanticModelDataValidation":{"status":"queryable"},'
                '"reportRenderValidation":{"status":"rendered"}}'
            ),
        )
    )

    assert _has_successful_required_creation(assignment, "fabric_create_workspace_inventory_solution") is True


def test_has_successful_required_creation_trusts_created_inventory_status() -> None:
    assignment = AgentAssignment(
        agent_id="generalist",
        session_id="agent-create",
        role="Generalist",
        goal="Create Fabric inventory solution",
    )
    assignment.actions.append(
        AgentAction(
            id="a-1",
            action_type="Created",
            entity_name="tmp_20260426193000",
            entity_type="WorkspaceInventorySolution",
            details='{"status":"created","semanticModelDataValidation":{"status":"queryable"}}',
        )
    )

    assert _has_successful_required_creation(assignment, "fabric_create_workspace_inventory_solution") is True


def test_detect_action_from_inventory_solution_response() -> None:
    action = _detect_action_from_tool(
        "fabric_create_workspace_inventory_solution",
        {"folder_name": "tmp_20260426193000"},
        (
            '{"status":"created","folderId":"folder-1","folderName":"tmp_20260426193000",'
            '"sourceItemCount":12,"semanticModelDataValidation":{"status":"queryable"},'
            '"reportRenderValidation":{"status":"rendered"}}'
        ),
    )

    assert action is not None
    assert action.action_type == "Created"
    assert action.entity_type == "WorkspaceInventorySolution"
    assert action.entity_name == "tmp_20260426193000"
    assert action.fabric_item_id == "folder-1"


def test_detect_action_from_inventory_solution_allows_empty_errors_array() -> None:
        action = _detect_action_from_tool(
                "fabric_create_workspace_inventory_solution",
                {"folder_name": "tmp_20260426202213"},
                """
<<<UNTRUSTED_TOOL_OUTPUT_BEGIN>>> tool=fabric_create_workspace_inventory_solution
{
    "status": "created",
    "folderId": "folder-1",
    "folderName": "tmp_20260426202213",
    "sourceItemCount": 167,
    "semanticModelDataValidation": {"status": "queryable"},
    "reportRenderValidation": {"status": "rendered"},
    "errors": [],
    "warnings": ["Lakehouse creation was rejected; report populated from live Fabric inventory data."]
}
<<<UNTRUSTED_TOOL_OUTPUT_END>>>
""",
        )

        assert action is not None
        assert action.action_type == "Created"
        assert action.entity_type == "WorkspaceInventorySolution"
        assert action.fabric_item_id == "folder-1"


def test_detect_action_from_inventory_solution_keeps_compact_valid_details() -> None:
    action = _detect_action_from_tool(
        "fabric_create_workspace_inventory_solution",
        {"folder_name": "tmp_20260427185815"},
        json.dumps({
            "status": "created",
            "workspaceId": "workspace-1",
            "folderId": "folder-1",
            "folderName": "tmp_20260427185815",
            "sourceItemCount": 144,
            "sourceWorkspaceCount": 12,
            "dataSource": "lakehouse_delta_tables",
            "notebookWritesEnabled": True,
            "persistentDataWritten": True,
            "persistentDataStore": {"type": "Lakehouse", "id": "lakehouse-1", "written": True},
            "notebookExecution": {"status": "Completed", "exitValue": "OK"},
            "semanticModelId": "model-1",
            "reportId": "report-1",
            "semanticModelDataValidation": {"status": "queryable", "rowCount": 144},
            "reportRenderValidation": {"status": "rendered", "via": "powerbi_exportTo_pdf"},
            "createdItems": [{"type": "Report", "id": "report-1"}],
            "progress": [{"step": f"step-{idx}", "blob": "x" * 200} for idx in range(60)],
            "errors": [],
            "warnings": [],
        }),
    )
    assignment = AgentAssignment(
        agent_id="generalist",
        session_id="agent-create",
        role="Generalist",
        goal="Create Fabric inventory solution",
    )
    assignment.actions.append(action)

    assert action is not None
    assert action.action_type == "Created"
    details = json.loads(action.details or "{}")
    assert details["persistentDataWritten"] is True
    assert details["persistentDataStore"]["id"] == "lakehouse-1"
    assert details["notebookExecution"]["status"] == "Completed"
    assert details["createdItems"] == [{"type": "Report", "id": "report-1"}]
    assert len(action.details or "") < 4000
    assert _has_successful_required_creation(
        assignment,
        "fabric_create_workspace_inventory_solution",
    ) is True


def test_detect_action_from_inventory_solution_trusts_created_status_without_nested_blocks() -> None:
    action = _detect_action_from_tool(
        "fabric_create_workspace_inventory_solution",
        {"folder_name": "tmp_20260426193000"},
        (
            '{"status":"created","folderId":"folder-1","folderName":"tmp_20260426193000",'
            '"semanticModelDataValidation":{"status":"queryable"},"errors":[]}'
        ),
    )

    assert action is not None
    assert action.action_type == "Created"
    assert action.entity_type == "WorkspaceInventorySolution"


def test_detect_action_from_create_response_captures_fabric_identity() -> None:
    action = _detect_action_from_tool(
        "fabric_create_item",
        {"display_name": "LH", "item_type": "Lakehouse"},
        '{"id":"item-1","displayName":"LH","type":"Lakehouse","webUrl":"https://example.test/item"}',
    )

    assert action is not None
    assert action.fabric_item_id == "item-1"
    assert action.web_url == "https://example.test/item"


def test_detect_action_from_wrapped_create_response_captures_fabric_identity() -> None:
    action = _detect_action_from_tool(
        "fabric_create_folder",
        {"display_name": "Run Folder"},
        """
<<<UNTRUSTED_TOOL_OUTPUT_BEGIN>>> tool=fabric_create_folder
{"id":"folder-1","displayName":"Run Folder"}
<<<UNTRUSTED_TOOL_OUTPUT_END>>>
""",
    )

    assert action is not None
    assert action.fabric_item_id == "folder-1"


def test_infer_single_created_folder_id_requires_exactly_one_folder() -> None:
    job = _make_job("j-folder")
    first = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-folder-1",
        role="Create folder",
        goal="Create folder",
    )
    first.actions.append(
        AgentAction(
            id="a-folder-1",
            action_type="Created",
            entity_name="Run Folder",
            entity_type="Folder",
            fabric_item_id="folder-1",
        )
    )
    job.agents.append(first)

    assert _infer_single_created_folder_id(job) == "folder-1"

    second = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-folder-2",
        role="Create folder",
        goal="Create folder",
    )
    second.actions.append(
        AgentAction(
            id="a-folder-2",
            action_type="Created",
            entity_name="Other Folder",
            entity_type="Folder",
            fabric_item_id="folder-2",
        )
    )
    job.agents.append(second)

    assert _infer_single_created_folder_id(job) is None


def test_parse_agent_output_marks_blocking_issue_for_capacity_decision() -> None:
    job = _make_job("j-parse")
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    assignment = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-1",
        role="Admin",
        goal="Inspect workspace",
    )
    template = SimpleNamespace(display_name="FabricAdmin")

    _parse_agent_output(
        "DECISION: Inspection failed due to an inactive capacity issue (CapacityNotActive).",
        assignment,
        exe,
        template,
    )

    assert _get_blocking_issue(assignment) is not None
    assert any(
        ev.get("type") == "slot_progress" and ev.get("status") == "failed"
        for ev in exe._ring
    )


@pytest.mark.asyncio
async def test_run_agent_marks_error_when_final_decision_contains_capacity_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = OrchestratorEngine()
    engine.mcp_manager = SimpleNamespace(get_openai_tools_schema=lambda: [])

    # Avoid DB writes in unit scope.
    monkeypatch.setattr(oe, "update_session", lambda _job: None)

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "Workspace inventory review complete. "
                                "Lakehouse inspection cannot proceed because "
                                "workspace capacity is inactive (CapacityNotActive)."
                            ),
                            "tool_calls": [],
                        }
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def post(self, *args, **kwargs):
            del args, kwargs
            return _FakeResponse()

    monkeypatch.setattr(oe.httpx, "AsyncClient", _FakeAsyncClient)

    job = _make_job("j-final")
    exe = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    assignment = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-final",
        role="Admin",
        goal="Review workspace prerequisites",
    )
    template = SimpleNamespace(
        name="FabricAdmin",
        display_name="FabricAdmin",
        system_prompt="You are an admin agent.",
        available_tools=[],
    )
    user_q: asyncio.Queue = asyncio.Queue()

    await engine._run_agent(exe, assignment, template, user_q, allowed_tools=set())

    assert assignment.status == AgentStatus.ERROR
    assert _get_blocking_issue(assignment) is not None
    assert any(
        ev.get("type") == "agent_error" and "Blocking issue:" in str(ev.get("error", ""))
        for ev in exe._ring
    )


@pytest.mark.asyncio
async def test_run_agent_completes_creation_role_after_required_tool_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = OrchestratorEngine()
    engine.mcp_manager = SimpleNamespace(
        get_openai_tools_schema=lambda: [
            {
                "type": "function",
                "function": {"name": "fabric_create_folder", "parameters": {}},
            }
        ]
    )
    monkeypatch.setattr(oe, "update_session", lambda _job: None)

    post_calls = []

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "fabric_create_folder",
                                        "arguments": '{"display_name":"Run Folder","workspace_id":"ws-1"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def post(self, *args, **kwargs):
            post_calls.append({"args": args, "kwargs": kwargs})
            return _FakeResponse()

    async def _fake_execute(**_kwargs):
        return SimpleNamespace(output="ok", policy_decision="allowed", ok=True)

    monkeypatch.setattr(oe.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr("services.agenthub.tool_runtime.execute", _fake_execute)

    job = _make_job("j-create")
    exe = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    assignment = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-create",
        role="Create folder",
        goal="Task: call fabric_create_folder\nYour role: Coordinating lead creates folder.",
    )
    template = SimpleNamespace(
        name="FabricAdmin",
        display_name="FabricAdmin",
        system_prompt="You are an admin agent.",
        available_tools=[],
    )

    await engine._run_agent(exe, assignment, template, asyncio.Queue(), allowed_tools=set())

    assert assignment.status == AgentStatus.COMPLETED
    assert assignment.actions[0].action_type == "Created"
    assert assignment.actions[0].entity_type == "Folder"
    assert len(post_calls) == 1
    assert post_calls[0]["kwargs"]["json"]["tool_choice"] == {
        "type": "function",
        "function": {"name": "fabric_create_folder"},
    }
    assert any(
        ev.get("type") == "slot_progress" and ev.get("status") == "done"
        for ev in exe._ring
    )


@pytest.mark.asyncio
async def test_run_agent_caps_model_tool_schema_body_and_preserves_required_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required_tool = "fabric_create_workspace_inventory_solution"
    tool_names = [f"generic_tool_{index}" for index in range(245)] + [required_tool]
    engine = OrchestratorEngine()
    engine.mcp_manager = SimpleNamespace(
        get_openai_tools_schema=lambda: [
            {"type": "function", "function": {"name": tool_name, "parameters": {}}}
            for tool_name in tool_names
        ]
    )
    monkeypatch.setattr(oe, "update_session", lambda _job: None)

    post_bodies = []

    class _FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, call_number: int):
            self.call_number = call_number

        def json(self):
            if self.call_number == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-inventory",
                                        "function": {
                                            "name": required_tool,
                                            "arguments": '{"workspace_id":"ws-1","folder_name":"tmp_schema_cap"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            return {
                "choices": [
                    {
                        "message": {
                            "content": "DECISION: Inventory solution created and verified.",
                            "tool_calls": [],
                        }
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def post(self, *args, **kwargs):
            del args
            post_bodies.append(kwargs["json"])
            return _FakeResponse(len(post_bodies))

    async def _fake_execute(**kwargs):
        assert kwargs["tool_name"] == required_tool
        return SimpleNamespace(
            output=json.dumps({"status": "created", "folderId": "folder-1", "folderName": "tmp_schema_cap"}),
            policy_decision="allowed",
            ok=True,
        )

    monkeypatch.setattr(oe.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr("services.agenthub.tool_runtime.execute", _fake_execute)

    job = _make_job("j-schema-cap")
    assignment = AgentAssignment(
        agent_id="fabric-data-engineer",
        session_id="agent-schema-cap",
        role="Create Fabric inventory visualization solution",
        goal=(
            "Create an end to end solution (ingestion, transformation, semantic modelling and a report) "
            "which shows all Fabric items I have access to in a nice appropriate visualization. "
            "Work in folder tmp_schema_cap."
        ),
    )
    job.agents.append(assignment)
    exe = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    template = SimpleNamespace(
        name="FabricDataEngineer",
        display_name="FabricDataEngineer",
        system_prompt="You are a Fabric data engineer.",
        available_tools=tool_names,
    )

    await engine._run_agent(exe, assignment, template, asyncio.Queue(), allowed_tools=set(tool_names))

    assert len(post_bodies[0]["tools"]) == 1
    assert post_bodies[0]["tools"][0]["function"]["name"] == required_tool
    assert post_bodies[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": required_tool},
    }
    assert assignment.status == AgentStatus.COMPLETED
    assert assignment.actions[0].entity_type == "WorkspaceInventorySolution"


@pytest.mark.asyncio
async def test_run_agent_injects_prior_folder_id_for_item_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = OrchestratorEngine()
    engine.mcp_manager = SimpleNamespace(
        get_openai_tools_schema=lambda: [
            {
                "type": "function",
                "function": {"name": "fabric_create_item", "parameters": {}},
            }
        ]
    )
    monkeypatch.setattr(oe, "update_session", lambda _job: None)

    captured_args = []

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "fabric_create_item",
                                        "arguments": (
                                            '{"display_name":"LH","workspace_id":"ws-1",'
                                            '"item_type":"Lakehouse"}'
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def post(self, *args, **kwargs):
            del args, kwargs
            return _FakeResponse()

    async def _fake_execute(**kwargs):
        captured_args.append(kwargs["arguments"])
        return SimpleNamespace(
            output='{"id":"item-1","displayName":"LH","type":"Lakehouse"}',
            policy_decision="allowed",
            ok=True,
        )

    monkeypatch.setattr(oe.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr("services.agenthub.tool_runtime.execute", _fake_execute)

    job = _make_job("j-create-item")
    folder_agent = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-folder",
        role="Create folder",
        goal="Create folder",
        status=AgentStatus.COMPLETED,
    )
    folder_agent.actions.append(
        AgentAction(
            id="action-folder",
            action_type="Created",
            entity_name="Run Folder",
            entity_type="Folder",
            fabric_item_id="folder-1",
        )
    )
    assignment = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-item",
        role="Create Lakehouse item",
        goal="Task: call fabric_create_item inside the folder\nYour role: Create the Lakehouse item.",
    )
    job.agents.extend([folder_agent, assignment])
    exe = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    template = SimpleNamespace(
        name="FabricAdmin",
        display_name="FabricAdmin",
        system_prompt="You are an admin agent.",
        available_tools=["fabric_create_item"],
    )

    await engine._run_agent(exe, assignment, template, asyncio.Queue(), allowed_tools={"fabric_create_item"})

    assert captured_args[0]["folder_id"] == "folder-1"
    assert assignment.status == AgentStatus.COMPLETED
    assert assignment.actions[0].fabric_item_id == "item-1"


@pytest.mark.asyncio
async def test_run_agent_completes_folder_then_item_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = OrchestratorEngine()
    engine.mcp_manager = SimpleNamespace(
        get_openai_tools_schema=lambda: [
            {
                "type": "function",
                "function": {"name": "fabric_create_folder", "parameters": {}},
            },
            {
                "type": "function",
                "function": {"name": "fabric_create_item", "parameters": {}},
            },
        ]
    )
    monkeypatch.setattr(oe, "update_session", lambda _job: None)

    post_bodies = []
    captured_args = []

    class _FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, tool_name: str, arguments: str) -> None:
            self._tool_name = tool_name
            self._arguments = arguments

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": f"call-{len(post_bodies)}",
                                    "function": {
                                        "name": self._tool_name,
                                        "arguments": self._arguments,
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def post(self, *args, **kwargs):
            del args
            post_bodies.append(kwargs["json"])
            if len(post_bodies) == 1:
                return _FakeResponse(
                    "fabric_create_folder",
                    '{"display_name":"Run Folder","workspace_id":"ws-1"}',
                )
            return _FakeResponse(
                "fabric_create_item",
                (
                    '{"display_name":"Notebook","workspace_id":"ws-1",'
                    '"item_type":"Notebook","folder_id":"assigned folder"}'
                ),
            )

    async def _fake_execute(**kwargs):
        captured_args.append(kwargs["arguments"])
        tool_name = kwargs["tool_name"]
        if tool_name == "fabric_create_folder":
            return SimpleNamespace(
                output='{"id":"folder-1","displayName":"Run Folder"}',
                policy_decision="allowed",
                ok=True,
            )
        return SimpleNamespace(
            output='{"id":"item-1","displayName":"Notebook","type":"Notebook"}',
            policy_decision="allowed",
            ok=True,
        )

    monkeypatch.setattr(oe.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr("services.agenthub.tool_runtime.execute", _fake_execute)

    job = _make_job("j-create-both")
    assignment = AgentAssignment(
        agent_id="fabric-admin",
        session_id="agent-both",
        role="Create folder and notebook item",
        goal=(
            "Task: call fabric_create_folder first, then fabric_create_item with folder_id\n"
            "Your role: Create folder and notebook item in the workspace."
        ),
    )
    job.agents.append(assignment)
    exe = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    template = SimpleNamespace(
        name="FabricAdmin",
        display_name="FabricAdmin",
        system_prompt="You are an admin agent.",
        available_tools=[],
    )

    await engine._run_agent(exe, assignment, template, asyncio.Queue(), allowed_tools=set())

    assert [body["tool_choice"]["function"]["name"] for body in post_bodies] == [
        "fabric_create_folder",
        "fabric_create_item",
    ]
    assert captured_args[1]["folder_id"] == "folder-1"
    assert assignment.status == AgentStatus.COMPLETED
    assert [action.entity_type for action in assignment.actions] == ["Folder", "Notebook"]
