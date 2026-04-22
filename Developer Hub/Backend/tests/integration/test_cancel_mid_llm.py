"""P4 · Mission Control — cancel-mid-LLM integration test.

When the user hits Terminate while an agent is blocked on a slow
LLM round, the stream must end quickly — not 30 seconds later when
the httpx client times out. ``_run_agent`` races
``client.post(...)`` against ``execution.cancel_event.wait()`` and
raises ``CancelledError`` if the event wins.

This test monkeypatches ``httpx.AsyncClient`` so ``.post(...)`` blocks
indefinitely, starts ``_run_agent`` as a task, sets ``cancel_event``
after a short delay, and asserts the task finishes (via
``CancelledError``) in well under the 60 s client timeout.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from domain.models.agent_models import (
    AgentAssignment,
    AgentStatus,
    AgentTemplate,
    AgentCategory,
    Job,
    JobStatus,
)
from services.agenthub import orchestrator_engine as oe


class _BlockingClient:
    """An ``httpx.AsyncClient`` stand-in whose ``.post`` never returns.

    Implements just enough of the async-context-manager surface the
    orchestrator touches inside ``_run_agent`` so we don't need the
    real network stack. The blocking future makes the cancel race
    the only way out.
    """

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *_args, **_kwargs):
        await asyncio.Event().wait()  # blocks forever


def _job() -> Job:
    return Job(
        id="job-cancel-1", user_id="u", workspace_id="ws",
        task_description="cancel me", context={}, status=JobStatus.RUNNING,
    )


def _template() -> AgentTemplate:
    return AgentTemplate(
        id="tmpl-1", name="tester", display_name="Tester",
        category=AgentCategory.ENGINEERING, description="x",
        system_prompt="you are a test", available_tools=[],
    )


def _assignment() -> AgentAssignment:
    return AgentAssignment(
        agent_id="tmpl-1", session_id="sess-1", role="tester",
        status=AgentStatus.QUEUED, goal="do the thing",
    )


@pytest.mark.asyncio
async def test_run_agent_cancels_mid_llm_without_timeout(monkeypatch):
    """Setting ``cancel_event`` during a blocked ``client.post`` must
    tear the agent loop down within one event-loop turn — not when
    the 60 s httpx timeout elapses.
    """
    # Replace the real httpx client with one that never resolves ``.post``.
    monkeypatch.setattr(oe.httpx, "AsyncClient", _BlockingClient)
    # No-op session persistence — avoids the sqlite fixture setup the
    # full session_store requires. The cancel race is orthogonal.
    monkeypatch.setattr(oe, "update_session", lambda *a, **k: None)

    engine = oe.OrchestratorEngine()
    engine.configure(
        mcp_manager=MagicMock(),
        copilot_token_fn=lambda: "fake-token",
        acquire_mcp_tokens_fn=lambda *a, **k: None,
    )

    job = _job()
    execution = oe._JobExecution(job, copilot_token="fake", mcp_tokens=None)
    engine._active_jobs[job.id] = execution

    assignment = _assignment()
    job.agents = [assignment]
    template = _template()

    user_queue: asyncio.Queue = asyncio.Queue()
    execution.user_message_queues[assignment.session_id] = user_queue

    # Kick off the agent loop. It will walk through setup, emit the
    # initial slot_progress / agent_status events, hit the ``async
    # with httpx.AsyncClient(...)`` block, and park on ``client.post``.
    task = asyncio.create_task(engine._run_agent(
        execution, assignment, template, user_queue,
    ))

    # Give it a beat to get into the LLM call. 100 ms is plenty — the
    # setup before ``client.post`` is all synchronous emit() calls.
    await asyncio.sleep(0.2)
    assert not task.done(), "agent task should still be running"

    # Trigger the cancel. This is exactly what ``cancel_job`` would
    # do via the HTTP cancel endpoint.
    started = time.monotonic()
    execution.cancel_event.set()

    # The agent task should exit within a second. Give it a generous
    # budget (2 s) so CI jitter doesn't flake us; the real budget is
    # sub-10 ms on a hot loop.
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.CancelledError:
        # Normal exit from the cancel race.
        pass
    except Exception:
        # _run_agent catches CancelledError internally and returns.
        pass
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"cancel took {elapsed:.2f}s (expected <2s, network timeout is 60s)"
    assert task.done()


@pytest.mark.asyncio
async def test_cancel_job_sets_cancel_event(monkeypatch):
    """``OrchestratorEngine.cancel_job`` must both mark the execution
    cancelled *and* flip ``cancel_event`` so any in-flight LLM race
    unblocks immediately.
    """
    engine = oe.OrchestratorEngine()
    job = _job()
    execution = oe._JobExecution(job, copilot_token="t", mcp_tokens=None)
    engine._active_jobs[job.id] = execution

    assert not execution.cancel_event.is_set()
    ok = engine.cancel_job(job.id)
    assert ok is True
    assert execution.cancelled is True
    assert execution.cancel_event.is_set()
