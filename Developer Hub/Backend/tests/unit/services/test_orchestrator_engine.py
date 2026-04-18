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

from unittest.mock import MagicMock

import pytest

from domain.models.agent_models import Job, JobStatus
from services.agenthub import orchestrator_engine as oe
from services.agenthub.orchestrator_engine import (
    OrchestratorEngine,
    _detect_action_from_tool,
    _JobExecution,
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


def _make_job(job_id: str = "j-1") -> Job:
    return Job(
        id=job_id, user_id="u-1", workspace_id="ws-1",
        task_description="t", context={}, status=JobStatus.RUNNING,
    )


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
    assert engine.inject_message("missing", "hi") is False


@pytest.mark.asyncio
async def test_inject_message_routes_to_specific_session() -> None:
    import asyncio

    engine = OrchestratorEngine()
    job = _make_job()
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    q = asyncio.Queue()
    exe.user_message_queues["agent-session-1"] = q
    engine._active_jobs[job.id] = exe

    assert engine.inject_message(job.id, "hello", "agent-session-1") is True
    assert q.get_nowait() == "hello"


@pytest.mark.asyncio
async def test_inject_message_broadcasts_when_no_target() -> None:
    import asyncio

    engine = OrchestratorEngine()
    job = _make_job()
    exe = _JobExecution(job, copilot_token="t", mcp_tokens=None)
    q1 = asyncio.Queue()
    q2 = asyncio.Queue()
    exe.user_message_queues["a"] = q1
    exe.user_message_queues["b"] = q2
    engine._active_jobs[job.id] = exe

    assert engine.inject_message(job.id, "broadcast") is True
    assert q1.get_nowait() == "broadcast"
    assert q2.get_nowait() == "broadcast"


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


# ── _detect_action_from_tool sweep ──────────────────────────────────


@pytest.mark.parametrize("tool,args,result,expected_type,expected_action", [
    # Successful actions
    ("fabric_create_item", {"display_name": "LH", "item_type": "Lakehouse"}, "ok",
     "Lakehouse", "Created"),
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
