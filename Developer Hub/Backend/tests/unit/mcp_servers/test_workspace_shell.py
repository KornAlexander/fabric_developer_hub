"""Unit tests for the controlled workspace shell MCP server."""
from __future__ import annotations

import json

import pytest

from mcp_servers import workspace_shell as shell


@pytest.mark.asyncio
async def test_run_shell_command_allows_python_version() -> None:
    raw = await shell._run_shell_command_impl("python --version", timeout_seconds=5)
    body = json.loads(raw)

    assert body["ok"] is True
    assert body["exitCode"] == 0
    assert "Python" in body["stdout"] or "Python" in body["stderr"]
    assert body["limits"]["shell"] is False


@pytest.mark.asyncio
async def test_run_shell_command_rejects_control_operator() -> None:
    raw = await shell.run_shell_command("python --version && echo nope")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "ShellPolicyError"
    assert "control operators" in body["error"]


@pytest.mark.asyncio
async def test_run_shell_command_rejects_unapproved_command() -> None:
    raw = await shell.run_shell_command("curl https://example.com")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "ShellPolicyError"
    assert "not in the workspace shell allowlist" in body["error"]


@pytest.mark.asyncio
async def test_run_shell_command_rejects_workspace_escape() -> None:
    raw = await shell.run_shell_command("python --version", working_directory="../")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "ShellPolicyError"
    assert "workspace shell root" in body["error"]


@pytest.mark.asyncio
async def test_run_shell_command_rejects_secret_like_path() -> None:
    raw = await shell.run_shell_command("rg CLIENT_SECRET .env")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "ShellPolicyError"
    assert "secret-like" in body["error"]


@pytest.mark.asyncio
async def test_run_shell_command_rejects_absolute_path_argument() -> None:
    raw = await shell.run_shell_command("rg token /tmp")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "ShellPolicyError"
    assert "absolute" in body["error"]


@pytest.mark.asyncio
async def test_run_shell_command_rejects_parent_path_argument() -> None:
    raw = await shell.run_shell_command("rg token ../Frontend")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "ShellPolicyError"
    assert "parent-directory" in body["error"]


def test_authorize_maps_python_to_current_interpreter() -> None:
    argv = shell._authorize(["python", "-m", "pytest", "tests"])

    assert argv[0].endswith("python") or "python" in argv[0]
    assert argv[1:] == ["-m", "pytest", "tests"]


def test_authorize_rejects_python_arbitrary_code() -> None:
    with pytest.raises(shell.ShellPolicyError, match="python is limited"):
        shell._authorize(["python", "-c", "print('nope')"])
