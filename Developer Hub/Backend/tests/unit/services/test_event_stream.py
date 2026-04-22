"""Tests for the mission-control event stream additions on
``_JobExecution`` — monotonic ``seq``, bounded ring buffer, and
``Last-Event-ID`` replay via ``events(last_seq=...)``.
"""
from __future__ import annotations

import asyncio

import pytest

from domain.models.agent_models import Job, JobStatus
from services.agenthub.orchestrator_engine import _JobExecution


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

    snap = exe.snapshot_run_overview()
    assert snap["job"]["id"] == "j-1"
    assert snap["activeAgentId"] == "s1"
    assert len(snap["artifacts"]) == 1
    assert snap["artifacts"][0]["name"] == "Bronze"
    assert len(snap["slotProgress"]) == 1
    assert snap["slotProgress"][0]["status"] == "running"


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
