"""Tests for the mission-control event stream additions on
``_JobExecution`` — monotonic ``seq``, bounded ring buffer, and
``Last-Event-ID`` replay via ``events(last_seq=...)``.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import services.agenthub.orchestrator_engine as oe
from domain.models.agent_models import Job, JobStatus
from services.agenthub.orchestrator_engine import (
    _JobExecution,
    _change_record_from_tool,
    _detect_action_from_tool,
)


def _exe() -> _JobExecution:
    job = Job(
        id="j-1", user_id="u-1", workspace_id="ws-1",
        task_description="t", context={}, status=JobStatus.RUNNING,
    )
    return _JobExecution(job, copilot_token="t", mcp_tokens=None)


def test_emit_stamps_monotonic_seq_and_session() -> None:
    exe = _exe()
    exe.emit("agent_status", agentId="a")
    exe.emit("slot_progress", slotId="a", status="running", agentId="a")
    exe.emit("log_line", message="hi")

    assert len(exe._ring) == 3
    seqs = [ev["seq"] for ev in exe._ring]
    assert seqs == [1, 2, 3]
    assert all(ev["sessionId"] == "j-1" for ev in exe._ring)
    assert all("ts" in ev for ev in exe._ring)


def test_emit_classifies_public_log_categories() -> None:
    exe = _exe()
    exe.emit("mission_seeded", taskCount=2)
    exe.emit("agent_decision", agentId="a", phaseNumber=1, decision="Inspect workspace")
    exe.emit("task_created", task={"id": "t1", "title": "Inspect workspace"})
    exe.emit("subagent_result", result={"status": "completed", "summary": "done"})
    exe.emit("action", action={"type": "create", "target": "Report"})
    exe.emit("tool_call_started", agentId="a", callId="c1", toolName="fabric_list_items")
    exe.emit("log_line", level="warn", message="Retrying model call", tags=["llm_retry"])

    assert [event["logCategory"] for event in exe._ring] == [
        "high_level",
        "detailed",
        "high_level",
        "high_level",
        "high_level",
        "diagnostic",
        "diagnostic",
    ]


def test_emit_suppresses_trace_events_from_public_buffers() -> None:
    exe = _exe()
    exe.emit("resource_lock_acquired", key="workspace:1", mode="write", runId="r1")
    exe.emit("log_line", level="info", message="Internal state", tags=["trace"])
    exe.emit("log_line", level="info", message="Visible progress")

    assert [event["message"] for event in exe._ring] == ["Visible progress"]
    assert exe.event_queue.qsize() == 1
    assert len(exe._trace_ring) == 2
    assert all(event["logCategory"] == "trace" for event in exe._trace_ring)


def test_emit_writes_backend_mission_trace_and_audit(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe = _exe()
    audit_calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(oe, "log_audit", lambda *args, **kwargs: audit_calls.append((args, kwargs)))

    with caplog.at_level(logging.INFO, logger="services.agenthub.orchestrator_engine"):
        exe.emit(
            "generalist_state_decision",
            runId="run-1",
            taskId="task-1",
            agentId="generalist",
            summary="Merged specialist feedback into the plan.",
            rationale="Generalist reviewed output and selected verifier handoff.",
        )

    assert exe._ring[-1]["logCategory"] == "high_level"
    trace_records = [record for record in caplog.records if "[MISSION_TRACE:j-1" in record.getMessage()]
    assert trace_records
    assert "generalist_state_decision" in trace_records[-1].getMessage()
    assert "Merged specialist feedback" in trace_records[-1].getMessage()

    assert audit_calls
    args, kwargs = audit_calls[-1]
    assert args[0] == "j-1"
    assert args[2] == "mission_event:generalist_state_decision"
    assert args[4].startswith("generalist_state_decision: Merged specialist feedback")
    assert kwargs["log_category"] == "high_level"


def test_trace_events_do_not_enter_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _exe()
    audit_calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(oe, "log_audit", lambda *args, **kwargs: audit_calls.append((args, kwargs)))

    exe.emit("resource_lock_acquired", key="workspace:1", mode="write", runId="r1")

    assert exe._ring == []
    assert len(exe._trace_ring) == 1
    assert audit_calls == []


def test_trust_and_diagnostic_events_are_categorized_and_summarized() -> None:
    exe = _exe()

    exe.emit(
        "diagnostic_baseline_captured",
        agentId="engineer",
        toolName="fabric_write_file",
        baselineCount=2,
        summary="Captured report diagnostics before write.",
    )
    exe.emit(
        "diagnostic_new_issues",
        agentId="engineer",
        toolName="fabric_write_file",
        newIssueCount=1,
        summary="Post-write validation found a new issue.",
        issues=[{"severity": "error", "code": "SchemaViolation", "message": "Missing visual position."}],
    )
    exe.emit(
        "mcp_server_approval_required",
        serverId="workspace-powerbi-tools",
        source="workspace",
        toolsPreview=["read_model", "publish_model", "delete_model"],
        risk="Workspace MCP tools can modify Fabric items.",
    )
    exe.emit(
        "runtime_config_refreshed",
        configVersion="cfg-2026-04-30",
        summary="Runtime policy cache refreshed.",
    )

    assert [event["logCategory"] for event in exe._ring] == [
        "diagnostic",
        "high_level",
        "high_level",
        "diagnostic",
    ]
    baseline, new_issue, approval, runtime_refresh = exe._ring
    assert baseline["payloadSummary"]["baselineCount"] == 2
    assert new_issue["payloadSummary"]["newIssueCount"] == 1
    assert new_issue["payloadSummary"]["issuePreview"] == ["Missing visual position."]
    assert approval["payloadSummary"]["serverId"] == "workspace-powerbi-tools"
    assert approval["payloadSummary"]["toolsPreview"] == ["read_model", "publish_model", "delete_model"]
    assert approval["payloadSummary"]["risk"] == "Workspace MCP tools can modify Fabric items."
    assert runtime_refresh["payloadSummary"]["configVersion"] == "cfg-2026-04-30"
    assert runtime_refresh["payloadSummary"]["summary"] == "Runtime policy cache refreshed."


def test_emit_persists_public_trust_events_but_not_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    exe = _exe()
    persisted: list[dict] = []
    monkeypatch.setattr(oe.session_event_store, "append_event", lambda _session_id, payload: persisted.append(payload))

    exe.emit(
        "mcp_server_approval_required",
        serverId="workspace-powerbi-tools",
        risk="Workspace MCP tools can modify Fabric items.",
    )
    exe.emit("resource_lock_acquired", key="workspace:1", mode="write", runId="r1")

    assert [event["type"] for event in persisted] == ["mcp_server_approval_required"]
    assert persisted[0]["logCategory"] == "high_level"
    assert exe._trace_ring[-1]["type"] == "resource_lock_acquired"


def test_ring_buffer_bounded_at_max() -> None:
    exe = _exe()
    for i in range(_JobExecution.EVENT_BUFFER_MAX + 20):
        exe.emit("log_line", message=str(i))
    assert len(exe._ring) == _JobExecution.EVENT_BUFFER_MAX
    # Oldest dropped → first retained event's seq is 21.
    assert exe._ring[0]["seq"] == 21
    assert exe._ring[-1]["seq"] == _JobExecution.EVENT_BUFFER_MAX + 20


def test_replay_since_returns_events_strictly_after_last_seq() -> None:
    exe = _exe()
    for i in range(5):
        exe.emit("log_line", message=str(i))
    replay = exe.replay_since(2)
    assert [ev["seq"] for ev in replay] == [3, 4, 5]


def test_replay_since_empty_when_last_seq_exceeds_latest() -> None:
    exe = _exe()
    exe.emit("log_line", message="only")
    assert exe.replay_since(99) == []


@pytest.mark.asyncio
async def test_events_iterator_replays_then_streams_live() -> None:
    exe = _exe()
    exe.emit("log_line", message="a")
    exe.emit("log_line", message="b")
    # A later live event produced after the subscriber attaches.
    exe.emit("log_line", message="c")
    # Terminal so the generator returns cleanly.
    exe.emit("job_complete", jobId="j-1", status="completed", totalDuration="0s")

    collected: list[dict] = []
    async for ev in exe.events(last_seq=1):
        collected.append(ev)
        if ev["type"] == "job_complete":
            break
    seqs = [ev["seq"] for ev in collected]
    assert seqs == [2, 3, 4]


def test_snapshot_run_overview_shape() -> None:
    exe = _exe()
    exe.emit("slot_progress", slotId="s1", agentId="s1", status="running",
              activeAgentId="s1")
    exe.emit("artifact_added", artifactId="x", agentId="s1",
              kind="Lakehouse", name="Bronze", state="written")
    exe.emit("change_recorded", recordId="c1", kind="created", status="applied",
              targetName="Bronze", targetType="Lakehouse", targetScope="item",
              summary="Created Lakehouse Bronze.", toolName="fabric_create_item",
              agentId="s1", agentName="Fabric Data Engineer")

    snap = exe.snapshot_run_overview()
    assert snap["job"]["id"] == "j-1"
    assert snap["activeAgentId"] == "s1"
    assert len(snap["artifacts"]) == 1
    assert snap["artifacts"][0]["name"] == "Bronze"
    assert len(snap["changes"]) == 1
    assert snap["changes"][0]["recordId"] == "c1"
    assert snap["changes"][0]["kind"] == "created"
    assert "logCategory" not in snap["changes"][0]
    assert len(snap["slotProgress"]) == 1
    assert snap["slotProgress"][0]["status"] == "running"


def test_change_record_from_tool_accepts_successful_writes_only() -> None:
    created_action = _detect_action_from_tool(
        "fabric_create_item",
        {"display_name": "Bronze", "item_type": "Lakehouse"},
        '{"id":"lh-1","webUrl":"https://fabric/items/lh-1"}',
    )
    assert created_action is not None
    created = _change_record_from_tool(
        "fabric_create_item",
        {"display_name": "Bronze", "item_type": "Lakehouse"},
        "created",
        created_action,
    )

    assert created is not None
    assert created["kind"] == "created"
    assert created["status"] == "applied"
    assert created["targetName"] == "Bronze"
    assert created["targetType"] == "Lakehouse"
    assert created["targetId"] == "lh-1"

    read_action = _detect_action_from_tool(
        "fabric_read_file",
        {"file_path": "Files/input.csv"},
        "contents",
    )
    assert read_action is not None
    assert _change_record_from_tool(
        "fabric_read_file",
        {"file_path": "Files/input.csv"},
        "contents",
        read_action,
    ) is None


def test_change_record_from_tool_captures_important_non_item_writes() -> None:
    change = _change_record_from_tool(
        "sl_run_item_job",
        {"item_id": "pipeline-1", "job_type": "Pipeline"},
        "started",
        None,
    )

    assert change is not None
    assert change["kind"] == "important_action"
    assert change["status"] == "applied"
    assert change["targetScope"] == "execution"
    assert change["toolName"] == "sl_run_item_job"


def test_change_record_from_tool_excludes_failed_writes() -> None:
    failed_action = _detect_action_from_tool(
        "fabric_create_directory",
        {"directory_path": "Files/new-folder"},
        "Error creating directory: 403 Forbidden",
    )

    assert failed_action is not None
    assert failed_action.action_type == "Failed"
    assert _change_record_from_tool(
        "fabric_create_directory",
        {"directory_path": "Files/new-folder"},
        "Error creating directory: 403 Forbidden",
        failed_action,
    ) is None


@pytest.mark.asyncio
async def test_cancel_event_set_by_cancel_path() -> None:
    # Sanity: the new cancel_event Event attribute exists and is
    # wired through the OrchestratorEngine.cancel_job path.
    from services.agenthub.orchestrator_engine import OrchestratorEngine
    engine = OrchestratorEngine()
    exe = _exe()
    engine._active_jobs[exe.job.id] = exe

    async def runner():
        await exe.cancel_event.wait()

    task = asyncio.create_task(runner())
    await asyncio.sleep(0)
    assert not task.done()
    engine.cancel_job(exe.job.id)
    await asyncio.wait_for(task, timeout=0.5)
    assert exe.cancelled is True
    assert exe.cancel_event.is_set()
