"""Subprocess runner for the Code Interpreter MCP tool.

This file intentionally uses only the Python standard library so it can be
started with ``python -I -S`` from the MCP server. It reads a JSON request from
stdin, validates the submitted code with a conservative AST guard, executes it
with a small builtin/module allowlist, and writes one JSON response to stdout.
"""
from __future__ import annotations

import ast
import contextlib
import importlib
import io
import json
import math
import os
import resource
import signal
import sys
import traceback
from types import MappingProxyType
from typing import Any

SAFE_MODULES: frozenset[str] = frozenset({
    "collections",
    "csv",
    "datetime",
    "decimal",
    "fractions",
    "functools",
    "itertools",
    "json",
    "math",
    "operator",
    "random",
    "re",
    "statistics",
    "string",
    "textwrap",
})

BANNED_NAMES: frozenset[str] = frozenset({
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "memoryview",
    "open",
    "setattr",
    "vars",
    "__import__",
})

SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "pow": pow,
    "print": print,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
}


class SafetyError(ValueError):
    """Raised when submitted code violates the interpreter policy."""


class SafetyVisitor(ast.NodeVisitor):
    """Conservative AST checks for the sandbox profile."""

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr.startswith("__"):
            raise SafetyError("dunder attribute access is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id.startswith("__") or node.id in BANNED_NAMES:
            raise SafetyError(f"name {node.id!r} is not allowed")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self._check_module(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level:
            raise SafetyError("relative imports are not allowed")
        self._check_module(node.module or "")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        raise SafetyError("global statements are not allowed")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:  # noqa: N802
        raise SafetyError("nonlocal statements are not allowed")

    @staticmethod
    def _check_module(module_name: str) -> None:
        root = module_name.split(".", 1)[0]
        if root not in SAFE_MODULES:
            raise SafetyError(f"module {module_name!r} is not allowed")


def _safe_import(
    name: str,
    globals_: dict[str, Any] | None = None,
    locals_: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> Any:
    del globals_, locals_, fromlist
    if level:
        raise ImportError("relative imports are not allowed")
    root = name.split(".", 1)[0]
    if root not in SAFE_MODULES:
        raise ImportError(f"module {name!r} is not allowed")
    return importlib.import_module(name)


def _set_limits(timeout_seconds: int, memory_mb: int) -> None:
    try:
        cpu_seconds = max(1, min(timeout_seconds, 10))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    except Exception:
        pass
    try:
        memory_bytes = max(64, min(memory_mb, 512)) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    except Exception:
        pass


def _timeout_handler(signum: int, frame: object) -> None:
    del signum, frame
    raise TimeoutError("code execution timed out")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... truncated at {limit} chars"


def _build_globals(input_data: str | None) -> dict[str, Any]:
    builtins = dict(SAFE_BUILTINS)
    builtins["__import__"] = _safe_import

    parsed_input: Any = None
    if input_data:
        try:
            parsed_input = json.loads(input_data)
        except json.JSONDecodeError:
            parsed_input = None

    return {
        "__builtins__": MappingProxyType(builtins),
        "input_data": input_data,
        "data": parsed_input,
        "math": math,
    }


def _execute(request: dict[str, Any]) -> dict[str, Any]:
    code = str(request.get("code") or "")
    input_data = request.get("input_data")
    timeout_seconds = int(request.get("timeout_seconds") or 5)
    memory_mb = int(request.get("memory_mb") or 256)
    output_limit = int(request.get("output_limit") or 20_000)

    if not code.strip():
        raise SafetyError("code is empty")

    tree = ast.parse(code, mode="exec")
    SafetyVisitor().visit(tree)
    compiled = compile(tree, "<agenthub-code-interpreter>", "exec")

    _set_limits(timeout_seconds, memory_mb)
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(max(1, min(timeout_seconds, 10)))

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    execution_globals = _build_globals(input_data)
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            exec(compiled, execution_globals, execution_globals)  # noqa: S102
    finally:
        signal.alarm(0)

    result = execution_globals.get("result")
    return {
        "ok": True,
        "stdout": _truncate(stdout_buffer.getvalue(), output_limit),
        "stderr": _truncate(stderr_buffer.getvalue(), output_limit),
        "result": None if result is None else _truncate(repr(result), output_limit),
    }


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
        response = _execute(request)
    except Exception as exc:  # return structured tool error, not a raw traceback
        response = {
            "ok": False,
            "errorType": type(exc).__name__,
            "error": str(exc),
            "traceback": "".join(traceback.format_exception_only(type(exc), exc)).strip(),
        }

    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    os.chdir(os.getcwd())
    raise SystemExit(main())
