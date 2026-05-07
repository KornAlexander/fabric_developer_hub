from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import internal_agent_api
from domain.models.agent_models import AgentAssignment, Job, JobStatus
from domain.models.dynamic_orchestration import MissionBrief, MissionState, SubagentRun, TaskNode
from services.agenthub import orchestrator_engine as oe
from services.agenthub import tool_runtime
from services.agenthub.orchestrator_engine import OrchestratorEngine, _JobExecution


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(internal_agent_api.router)
    return app


def _job(job_id: str = "session-1") -> Job:
    job = Job(
        id=job_id,
        user_id="user-1",
        user_upn="user@example.com",
        workspace_id="ws-1",
        task_description="do work",
        context={},
        status=JobStatus.RUNNING,
    )
    job.agents.append(
        AgentAssignment(
            agent_id="fabric-data-engineer",
            session_id="assignment-1",
            role="Engineer",
            goal="Use tools",
        )
    )
    return job


def test_tool_schemas_come_from_session_mcp_runtime(monkeypatch) -> None:
    manager = SimpleNamespace(
        get_openai_tools_schema=lambda: [
            {"type": "function", "function": {"name": "mission_tool"}}
        ]
    )
    engine = OrchestratorEngine(mcp_manager=SimpleNamespace(get_openai_tools_schema=lambda: []))
    job = _job()
    execution = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    execution.mcp_manager = manager
    engine._active_jobs[job.id] = execution
    monkeypatch.setattr(oe, "get_orchestrator_engine", lambda: engine)

    client = TestClient(_app())
    response = client.get(
        "/api/internal/tools/schemas",
        params={"session_id": job.id, "assignment_session_id": "assignment-1"},
    )

    assert response.status_code == 200
    assert response.json()[0]["function"]["name"] == "mission_tool"


def test_tool_schemas_reject_foreign_assignment(monkeypatch) -> None:
    manager = SimpleNamespace(
        get_openai_tools_schema=lambda: [
            {"type": "function", "function": {"name": "mission_tool"}}
        ]
    )
    engine = OrchestratorEngine(mcp_manager=SimpleNamespace(get_openai_tools_schema=lambda: []))
    job = _job()
    execution = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    execution.mcp_manager = manager
    engine._active_jobs[job.id] = execution
    monkeypatch.setattr(oe, "get_orchestrator_engine", lambda: engine)

    client = TestClient(_app())
    response = client.get(
        "/api/internal/tools/schemas",
        params={"session_id": job.id, "assignment_session_id": "other-assignment"},
    )

    assert response.status_code == 403


def test_execute_tool_uses_session_mcp_runtime(monkeypatch) -> None:
    manager = SimpleNamespace(name="mission-manager")
    engine = OrchestratorEngine(mcp_manager=SimpleNamespace(name="global-manager"))
    job = _job()
    execution = _JobExecution(job, copilot_token="tok", mcp_tokens={"FABRIC_API_TOKEN": "t"})
    execution.mcp_manager = manager
    engine._active_jobs[job.id] = execution
    monkeypatch.setattr(oe, "get_orchestrator_engine", lambda: engine)

    captured: dict[str, object] = {}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return tool_runtime.ToolResult(
            ok=True,
            output="ok",
            policy_decision="allowed",
            tool_name=kwargs["tool_name"],
            arg_hash="hash",
            latency_ms=123,
            latency_breakdown_ms={
                "backendPolicyMs": 4,
                "sidecarHttpMs": 20,
                "mcpProcessStartupMs": 30,
                "mcpToolExecutionMs": 60,
                "backendTotalMs": 123,
            },
        )

    monkeypatch.setattr(tool_runtime, "execute", fake_execute)

    client = TestClient(_app())
    response = client.post(
        "/api/internal/tools/execute",
        json={
            "session_id": job.id,
            "slot_id": "slot-1",
            "assignment_session_id": "assignment-1",
            "tool_name": "fabric_list_workspaces",
            "arguments": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["latency_ms"] == 123
    assert response.json()["latency_breakdown_ms"]["mcpToolExecutionMs"] == 60
    assert captured["mcp_manager"] is manager
    ended = [event for event in execution._ring if event["type"] == "tool_call_ended"][-1]
    assert ended["durationMs"] == 123
    assert ended["latencyBreakdownMs"]["sidecarHttpMs"] == 20


def test_execute_tool_rejects_foreign_assignment(monkeypatch) -> None:
    manager = SimpleNamespace(name="mission-manager")
    engine = OrchestratorEngine(mcp_manager=SimpleNamespace(name="global-manager"))
    job = _job()
    execution = _JobExecution(job, copilot_token="tok", mcp_tokens={"FABRIC_API_TOKEN": "t"})
    execution.mcp_manager = manager
    engine._active_jobs[job.id] = execution
    monkeypatch.setattr(oe, "get_orchestrator_engine", lambda: engine)

    client = TestClient(_app())
    response = client.post(
        "/api/internal/tools/execute",
        json={
            "session_id": job.id,
            "slot_id": "slot-1",
            "assignment_session_id": "other-assignment",
            "tool_name": "fabric_list_workspaces",
            "arguments": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["policy_decision"] == "denied:assignment_not_in_session"


def test_emit_internal_pi_event_stamps_assignment_context(monkeypatch) -> None:
    engine = OrchestratorEngine(mcp_manager=SimpleNamespace(get_openai_tools_schema=lambda: []))
    job = _job()
    execution = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    engine._active_jobs[job.id] = execution
    monkeypatch.setattr(oe, "get_orchestrator_engine", lambda: engine)

    client = TestClient(_app())
    response = client.post(
        "/api/internal/events/emit",
        json={
            "session_id": job.id,
            "slot_id": "slot-1",
            "assignment_session_id": "assignment-1",
            "type": "pi.subagents.status",
            "payload": {"runId": "run-1", "mode": "single", "state": "running", "summary": "live status"},
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    event = execution._ring[-1]
    assert event["type"] == "pi.subagents.status"
    assert event["agentId"] == "assignment-1"
    assert event["agentName"] == "Engineer"
    assert event["runId"] == "run-1"
    assert event["extension"]["packageName"] == "pi-subagents"


def test_emit_internal_pi_event_overrides_container_run_fallback_with_dynamic_context(monkeypatch) -> None:
    engine = OrchestratorEngine(mcp_manager=SimpleNamespace(get_openai_tools_schema=lambda: []))
    job = _job()
    execution = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    mission = MissionState(
        brief=MissionBrief(session_id=job.id, goal="do work", workspace_id="ws-1"),
    )
    mission.tasks["task-1"] = TaskNode(id="task-1", title="Native Pi task", objective="stream observability")
    mission.subagent_runs["run-dynamic"] = SubagentRun(
        id="run-dynamic",
        task_id="task-1",
        agent_id="fabric-data-engineer",
        agent_session_id="assignment-1",
    )
    execution.dynamic_mission_state = mission
    engine._active_jobs[job.id] = execution
    monkeypatch.setattr(oe, "get_orchestrator_engine", lambda: engine)

    client = TestClient(_app())
    response = client.post(
        "/api/internal/events/emit",
        json={
            "session_id": job.id,
            "slot_id": "slot-1",
            "assignment_session_id": "assignment-1",
            "type": "pi.subagents.status",
            "payload": {"runId": "assignment-1", "mode": "single", "state": "running", "summary": "live status"},
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    event = execution._ring[-1]
    assert event["runId"] == "run-dynamic"
    assert event["taskId"] == "task-1"
    assert event["taskTitle"] == "Native Pi task"


def test_emit_internal_event_rejects_non_pi_events(monkeypatch) -> None:
    engine = OrchestratorEngine(mcp_manager=SimpleNamespace(get_openai_tools_schema=lambda: []))
    job = _job()
    execution = _JobExecution(job, copilot_token="tok", mcp_tokens=None)
    engine._active_jobs[job.id] = execution
    monkeypatch.setattr(oe, "get_orchestrator_engine", lambda: engine)

    client = TestClient(_app())
    response = client.post(
        "/api/internal/events/emit",
        json={
            "session_id": job.id,
            "slot_id": "slot-1",
            "assignment_session_id": "assignment-1",
            "type": "tool_call_started",
            "payload": {"toolName": "fabric_list_workspaces"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "only pi.* events are accepted"}
    assert not execution._ring