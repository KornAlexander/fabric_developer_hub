from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mcp_servers.stdio_jsonrpc_filter import is_jsonrpc_line


def test_is_jsonrpc_line_accepts_protocol_frames() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    assert is_jsonrpc_line(json.dumps(payload).encode("utf-8") + b"\n")


def test_is_jsonrpc_line_rejects_banners_and_plain_json() -> None:
    assert not is_jsonrpc_line(b"Detected platform: linux, architecture: arm64\n")
    assert not is_jsonrpc_line(b'{"level":"info","message":"startup"}\n')


def test_wrapper_moves_non_jsonrpc_stdout_to_stderr(tmp_path) -> None:
    child = tmp_path / "noisy_child.py"
    child.write_text(
        "import json\n"
        "import sys\n"
        "print('Detected platform: linux, architecture: arm64')\n"
        "print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {'ok': True}}))\n"
        "print('Using @microsoft/powerbi-modeling-mcp version: 0.5.0-beta.4')\n"
        "print('child stderr line', file=sys.stderr)\n",
        encoding="utf-8",
    )

    wrapper = Path(__file__).resolve().parents[3] / "src" / "mcp_servers" / "stdio_jsonrpc_filter.py"
    result = subprocess.run(
        [sys.executable, str(wrapper), "--", sys.executable, str(child)],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0
    stdout_lines = result.stdout.splitlines()
    assert stdout_lines == ['{"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}']
    assert "Detected platform: linux, architecture: arm64" in result.stderr
    assert "Using @microsoft/powerbi-modeling-mcp version: 0.5.0-beta.4" in result.stderr
    assert "child stderr line" in result.stderr
