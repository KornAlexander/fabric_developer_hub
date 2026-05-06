"""Run a stdio MCP child while keeping stdout JSON-RPC-only.

Some third-party MCP packages print startup banners to stdout before they
start speaking JSON-RPC. The MCP stdio client treats every stdout line as a
protocol frame, so those banners produce noisy parse errors. This wrapper
forwards valid JSON-RPC lines to stdout and moves all other child stdout to
stderr, where regular process logs belong.
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
from collections.abc import Sequence
from typing import BinaryIO


def is_jsonrpc_line(line: bytes) -> bool:
    stripped = line.lstrip()
    if not stripped.startswith(b"{"):
        return False
    try:
        payload = json.loads(stripped.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("jsonrpc") == "2.0"


def _copy_stdin(child_stdin: BinaryIO) -> None:
    try:
        for chunk in iter(lambda: sys.stdin.buffer.read(65536), b""):
            child_stdin.write(chunk)
            child_stdin.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            child_stdin.close()
        except OSError:
            pass


def _filter_stdout(child_stdout: BinaryIO) -> None:
    for line in iter(child_stdout.readline, b""):
        if is_jsonrpc_line(line):
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        else:
            sys.stderr.buffer.write(line)
            sys.stderr.buffer.flush()


def _copy_stderr(child_stderr: BinaryIO) -> None:
    for line in iter(child_stderr.readline, b""):
        sys.stderr.buffer.write(line)
        sys.stderr.buffer.flush()


def run(command: Sequence[str]) -> int:
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def terminate_child(signum: int, _frame) -> None:
        if process.poll() is None:
            process.terminate()

    previous_sigterm = signal.signal(signal.SIGTERM, terminate_child)
    previous_sigint = signal.signal(signal.SIGINT, terminate_child)
    try:
        threads = [
            threading.Thread(target=_copy_stdin, args=(process.stdin,), daemon=True),
            threading.Thread(target=_filter_stdout, args=(process.stdout,), daemon=True),
            threading.Thread(target=_copy_stderr, args=(process.stderr,), daemon=True),
        ]
        for thread in threads:
            thread.start()
        return process.wait()
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        print("usage: stdio_jsonrpc_filter.py -- <command> [args...]", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
