"""Unit tests for the first-party Code Interpreter MCP server."""
from __future__ import annotations

import json

import pytest

from mcp_servers.code_interpreter import _run_python_code_impl


@pytest.mark.asyncio
async def test_run_python_code_returns_stdout_and_result() -> None:
    raw = await _run_python_code_impl(
        "print('hello')\nresult = sum([1, 2, 3])",
        timeout_seconds=2,
    )
    body = json.loads(raw)

    assert body["ok"] is True
    assert body["stdout"] == "hello\n"
    assert body["result"] == "6"
    assert body["limits"]["filesystem"] == "temporary working directory only"


@pytest.mark.asyncio
async def test_run_python_code_exposes_json_input_as_data() -> None:
    raw = await _run_python_code_impl(
        "result = data['values'][0] + data['values'][1]",
        input_data='{"values": [10, 7]}',
        timeout_seconds=2,
    )
    body = json.loads(raw)

    assert body["ok"] is True
    assert body["result"] == "17"


@pytest.mark.asyncio
async def test_run_python_code_denies_unsafe_imports() -> None:
    raw = await _run_python_code_impl("import os\nresult = os.getcwd()", timeout_seconds=2)
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "SafetyError"
    assert "not allowed" in body["error"]


@pytest.mark.asyncio
async def test_run_python_code_denies_dunder_escape() -> None:
    raw = await _run_python_code_impl("result = (1).__class__", timeout_seconds=2)
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "SafetyError"
    assert "dunder" in body["error"]


@pytest.mark.asyncio
async def test_run_python_code_times_out() -> None:
    raw = await _run_python_code_impl("while True:\n    pass", timeout_seconds=1)
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] in {"TimeoutError", "InterpreterProtocolError"}
