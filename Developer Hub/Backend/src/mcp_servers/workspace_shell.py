"""Controlled workspace shell MCP server.

This is intentionally not a general terminal. It executes a narrow set of
development/read commands under a pinned workspace root, without ``shell=True``
and with a sanitized environment, timeout, and output caps.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from pathlib import Path, PurePath

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("workspace-shell", log_level="WARNING")


def _default_root() -> Path:
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        candidate = Path(sys.argv[1]).resolve()
        if candidate.exists() and candidate.is_dir():
            return candidate
    return Path.cwd().resolve()


_ROOT = _default_root()
_MAX_COMMAND_CHARS = int(os.environ.get("WORKSPACE_SHELL_MAX_COMMAND_CHARS", "4000"))
_MAX_OUTPUT_CHARS = int(os.environ.get("WORKSPACE_SHELL_MAX_OUTPUT_CHARS", "24000"))
_MAX_TIMEOUT_SECONDS = int(os.environ.get("WORKSPACE_SHELL_MAX_TIMEOUT_SECONDS", "90"))

_CONTROL_TOKENS = ("&&", "||", ";", "|", ">", "<", "`", "$(", "\n", "\r")
_DENY_PATH_PARTS = {".data", ".git", ".venv", "node_modules", "__pycache__"}
_DENY_ARG_FRAGMENTS = (".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".pem", ".pfx", ".key")

_PYTHON_MODULES = {"pytest", "ruff", "mypy", "py_compile", "compileall"}
_NPM_COMMANDS = {"test", "run", "list", "ls"}
_NPM_RUN_PREFIXES = ("test", "lint", "typecheck", "build", "check", "e2e")
_NPX_COMMANDS = {"playwright", "vitest", "tsc", "eslint"}
_GIT_COMMANDS = {"status", "diff", "log", "show", "grep", "ls-files", "branch"}
_SIMPLE_READ_COMMANDS = {"rg", "ls", "pwd", "head", "tail", "wc"}


class ShellPolicyError(ValueError):
    """Raised when a shell command violates the workspace shell profile."""


def _truncate(value: str) -> str:
    if len(value) <= _MAX_OUTPUT_CHARS:
        return value
    return value[:_MAX_OUTPUT_CHARS] + f"\n... truncated at {_MAX_OUTPUT_CHARS} chars"


def _bounded_timeout(value: int | None) -> int:
    try:
        requested = int(value or 30)
    except (TypeError, ValueError):
        requested = 30
    return max(1, min(requested, _MAX_TIMEOUT_SECONDS))


def _resolve_cwd(working_directory: str | None) -> Path:
    if not working_directory:
        return _ROOT
    if "\x00" in working_directory:
        raise ShellPolicyError("working_directory contains a null byte")
    candidate = (_ROOT / working_directory).resolve()
    try:
        candidate.relative_to(_ROOT)
    except ValueError as exc:
        raise ShellPolicyError("working_directory must stay under the workspace shell root") from exc
    if any(part in _DENY_PATH_PARTS for part in candidate.relative_to(_ROOT).parts):
        raise ShellPolicyError("working_directory points at a blocked internal directory")
    if not candidate.exists() or not candidate.is_dir():
        raise ShellPolicyError("working_directory does not exist or is not a directory")
    return candidate


def _parse_command(command: str) -> list[str]:
    if not command or not command.strip():
        raise ShellPolicyError("command is empty")
    if len(command) > _MAX_COMMAND_CHARS:
        raise ShellPolicyError(f"command exceeds {_MAX_COMMAND_CHARS} characters")
    if any(token in command for token in _CONTROL_TOKENS):
        raise ShellPolicyError("shell control operators, pipes, redirects, and command substitution are not allowed")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ShellPolicyError(f"command could not be parsed: {exc}") from exc
    if not argv:
        raise ShellPolicyError("command is empty")
    for arg in argv:
        lowered = arg.lower()
        if any(fragment in lowered for fragment in _DENY_ARG_FRAGMENTS):
            raise ShellPolicyError("command references a blocked secret-like path")
        if arg.startswith(("/", "~")):
            raise ShellPolicyError("absolute and home-relative path arguments are not allowed")
        if ".." in PurePath(arg).parts:
            raise ShellPolicyError("parent-directory path arguments are not allowed")
    return argv


def _script_name_allowed(name: str) -> bool:
    normalized = name.strip().lower()
    return bool(normalized) and any(
        normalized == prefix or normalized.startswith(f"{prefix}:") or normalized.startswith(f"{prefix}-")
        for prefix in _NPM_RUN_PREFIXES
    )


def _authorize(argv: list[str]) -> list[str]:
    executable = Path(argv[0]).name
    if executable in {"python", "python3"}:
        if len(argv) >= 3 and argv[1] == "-m" and argv[2] in _PYTHON_MODULES:
            return [sys.executable, *argv[1:]]
        if len(argv) == 2 and argv[1] in {"--version", "-V"}:
            return [sys.executable, argv[1]]
        raise ShellPolicyError("python is limited to -m pytest/ruff/mypy/py_compile/compileall or --version")

    if executable in {"pytest", "ruff", "mypy"}:
        return argv

    if executable == "npm":
        if len(argv) < 2 or argv[1] not in _NPM_COMMANDS:
            raise ShellPolicyError("npm is limited to test, run, list, and ls")
        if argv[1] == "run" and (len(argv) < 3 or not _script_name_allowed(argv[2])):
            raise ShellPolicyError("npm run is limited to test/lint/typecheck/build/check/e2e scripts")
        return argv

    if executable == "npx":
        if len(argv) < 2 or argv[1] not in _NPX_COMMANDS:
            raise ShellPolicyError("npx is limited to playwright, vitest, tsc, and eslint")
        return argv

    if executable == "node":
        if len(argv) == 2 and argv[1] in {"--version", "-v"}:
            return argv
        raise ShellPolicyError("node is limited to --version")

    if executable == "git":
        if len(argv) < 2 or argv[1] not in _GIT_COMMANDS:
            raise ShellPolicyError("git is limited to status, diff, log, show, grep, ls-files, and branch")
        return argv

    if executable in _SIMPLE_READ_COMMANDS:
        if any(arg in {"--hidden", "--no-ignore"} for arg in argv[1:]):
            raise ShellPolicyError("hidden/ignored file traversal flags are not allowed")
        return argv

    raise ShellPolicyError(f"command {executable!r} is not in the workspace shell allowlist")


def _env(cwd: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", str(cwd)),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "CI": "1",
    }
    return env


async def _run_shell_command_impl(
    command: str,
    working_directory: str | None = None,
    timeout_seconds: int | None = 30,
) -> str:
    cwd = _resolve_cwd(working_directory)
    argv = _authorize(_parse_command(command))
    timeout = _bounded_timeout(timeout_seconds)

    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=_env(cwd),
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return json.dumps({
            "ok": False,
            "exitCode": None,
            "errorType": "TimeoutError",
            "error": f"command exceeded {timeout} seconds",
            "stdout": "",
            "stderr": "",
            "cwd": str(cwd.relative_to(_ROOT) or "."),
            "limits": {"timeoutSeconds": timeout, "root": str(_ROOT)},
        }, indent=2)

    return json.dumps({
        "ok": process.returncode == 0,
        "exitCode": process.returncode,
        "command": argv,
        "cwd": str(cwd.relative_to(_ROOT) or "."),
        "stdout": _truncate(stdout.decode("utf-8", errors="replace")),
        "stderr": _truncate(stderr.decode("utf-8", errors="replace")),
        "limits": {"timeoutSeconds": timeout, "root": str(_ROOT), "shell": False},
    }, indent=2, ensure_ascii=False)


@mcp.tool()
async def run_shell_command(
    command: str,
    working_directory: str | None = None,
    timeout_seconds: int | None = 30,
) -> str:
    """Run an allowlisted development shell command under the backend workspace root.

    Use this for checks that need the project environment: pytest, ruff, npm
    test/build scripts, git read-only inspection, and simple read commands. It
    is not an interactive terminal and does not support pipes, redirects,
    command substitution, installers, or arbitrary interpreters.
    """
    try:
        return await _run_shell_command_impl(command, working_directory, timeout_seconds)
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "errorType": type(exc).__name__,
            "error": str(exc),
            "limits": {"root": str(_ROOT), "shell": False},
        }, indent=2)


if __name__ == "__main__":
    mcp.run()
