"""AgentHub Code Interpreter MCP server.

The exposed tool is intentionally narrow: run small Python snippets for
calculation, JSON/CSV shaping, schema exploration, and validation. It is not a
terminal, does not receive backend secrets, executes in a temporary directory,
and delegates actual code execution to a subprocess with AST checks, resource
limits, and a small builtin/module allowlist.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("code-interpreter", log_level="WARNING")

_RUNNER = Path(__file__).with_name("code_interpreter_runner.py")
_MAX_CODE_CHARS = int(os.environ.get("CODE_INTERPRETER_MAX_CODE_CHARS", "12000"))
_MAX_INPUT_CHARS = int(os.environ.get("CODE_INTERPRETER_MAX_INPUT_CHARS", "50000"))
_MAX_OUTPUT_CHARS = int(os.environ.get("CODE_INTERPRETER_MAX_OUTPUT_CHARS", "20000"))
_MAX_TIMEOUT_SECONDS = int(os.environ.get("CODE_INTERPRETER_MAX_TIMEOUT_SECONDS", "10"))
_MEMORY_MB = int(os.environ.get("CODE_INTERPRETER_MEMORY_MB", "256"))


def _bounded_timeout(value: int | None) -> int:
    try:
        requested = int(value or 5)
    except (TypeError, ValueError):
        requested = 5
    return max(1, min(requested, _MAX_TIMEOUT_SECONDS))


def _reject_oversized(label: str, value: str | None, limit: int) -> None:
    if value is not None and len(value) > limit:
        raise ValueError(f"{label} exceeds {limit} characters")


async def _run_python_code_impl(
    code: str,
    input_data: str | None = None,
    timeout_seconds: int | None = 5,
) -> str:
    _reject_oversized("code", code, _MAX_CODE_CHARS)
    _reject_oversized("input_data", input_data, _MAX_INPUT_CHARS)
    timeout = _bounded_timeout(timeout_seconds)
    request = {
        "code": code,
        "input_data": input_data,
        "timeout_seconds": timeout,
        "memory_mb": _MEMORY_MB,
        "output_limit": _MAX_OUTPUT_CHARS,
    }

    with tempfile.TemporaryDirectory(prefix="agenthub-code-") as workdir:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-S",
            str(_RUNNER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONIOENCODING": "utf-8",
                "TMPDIR": workdir,
            },
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(request).encode("utf-8")),
                timeout=timeout + 2,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return json.dumps({
                "ok": False,
                "errorType": "TimeoutError",
                "error": f"code execution exceeded {timeout} seconds",
                "stdout": "",
                "stderr": "",
            }, indent=2)

    stderr_text = stderr.decode("utf-8", errors="replace")[:_MAX_OUTPUT_CHARS]
    stdout_text = stdout.decode("utf-8", errors="replace")
    if process.returncode != 0 and not stdout_text.strip():
        error_type = "TimeoutError" if (process.returncode or 0) < 0 else "InterpreterProcessError"
        payload = {
            "ok": False,
            "errorType": error_type,
            "error": f"runner exited with status {process.returncode}",
            "stdout": "",
        }
    else:
        try:
            payload = json.loads(stdout_text or "{}")
        except json.JSONDecodeError:
            payload = {
                "ok": False,
                "errorType": "InterpreterProtocolError",
                "error": "runner did not return valid JSON",
                "stdout": stdout_text[:_MAX_OUTPUT_CHARS],
            }
    if "ok" not in payload:
        payload = {
            "ok": False,
            "errorType": "InterpreterProtocolError",
            "error": "runner returned an incomplete response",
            "stdout": stdout_text[:_MAX_OUTPUT_CHARS],
        }

    if stderr_text:
        payload["runnerStderr"] = stderr_text
    payload["limits"] = {
        "timeoutSeconds": timeout,
        "memoryMb": _MEMORY_MB,
        "network": "not exposed by interpreter policy",
        "filesystem": "temporary working directory only",
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


@mcp.tool()
async def run_python_code(
    code: str,
    input_data: str | None = None,
    timeout_seconds: int | None = 5,
) -> str:
    """Run a small Python snippet in the AgentHub sandbox.

    Use this for calculations, JSON/CSV transformations, lightweight data
    inspection, and validating small algorithms. The interpreter exposes
    ``input_data`` as a string and ``data`` as parsed JSON when possible.
    Assign a value to ``result`` or print to stdout to return information.

    Args:
        code: Python source code to execute.
        input_data: Optional text/JSON payload available as ``input_data``;
            if valid JSON, it is also available as ``data``.
        timeout_seconds: Requested execution timeout, clamped to the server
            maximum.
    """
    try:
        return await _run_python_code_impl(code, input_data, timeout_seconds)
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "errorType": type(exc).__name__,
            "error": str(exc),
        }, indent=2)


if __name__ == "__main__":
    mcp.run()
