"""Smoke tests for MCP-server modules.

These trivially-cheap tests catch the entire class of bugs where an MCP
server module fails to import (e.g. wrong ``FastMCP`` constructor signature,
syntax errors, missing imports) — exactly the failure mode that shipped
``semantic_link`` as DOA before the Phase-5 refactor.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mcp_servers import fabric, semantic_link


def test_fabric_module_imports() -> None:

    assert fabric.mcp is not None


def test_semantic_link_module_imports() -> None:

    assert semantic_link.mcp is not None


def test_fabric_tool_count() -> None:
    """Lock the public tool surface so accidental removals are caught."""

    tool_names = sorted(n for n in dir(fabric) if n.startswith("fabric_"))
    assert len(tool_names) == 10, f"expected 10 fabric_* tools, got {tool_names}"
    assert "fabric_list_workspaces" in tool_names
    assert "fabric_list_files" in tool_names
    assert "fabric_read_file" in tool_names
    assert "fabric_create_directory" in tool_names


def test_semantic_link_tool_count() -> None:
    """Lock the sl_* tool surface — a regression from the FastMCP-signature
    bug would manifest as zero tools registered (because the module crashed
    on import)."""

    tool_names = sorted(n for n in dir(semantic_link) if n.startswith("sl_"))
    assert len(tool_names) == 43, f"expected 43 sl_* tools, got {len(tool_names)}"
    # Spot-check a tool from each major section
    for required in (
        "sl_evaluate_dax",
        "sl_list_semantic_models",
        "sl_refresh_semantic_model",
        "sl_list_reports",
        "sl_list_lakehouses",
        "sl_list_workspace_users",
        "sl_get_git_status",
        "sl_admin_list_workspaces",
    ):
        assert required in tool_names, f"missing {required}"


def test_mcp_server_scripts_import_when_spawned_as_subprocess() -> None:
    """REGRESSION: MCPClientManager spawns each server as a standalone
    script (``python /app/src/mcp_servers/fabric.py``). In that mode
    ``sys.path[0]`` is the script's own directory, NOT ``src/`` — so a
    sibling import like ``from mcp_servers._common import ...`` raises
    ``ModuleNotFoundError`` unless the script first inserts ``src/`` on
    ``sys.path``. This test reproduces the exact spawn invocation.
    """

    src_dir = Path(__file__).resolve().parents[3] / "src"
    for script in ("fabric.py", "semantic_link.py"):
        script_path = src_dir / "mcp_servers" / script
        # Run with --help-style probe: import-only by patching FastMCP.run
        # would require monkey-patching at subprocess level. Instead, use
        # ``-c`` to import the module without invoking ``mcp.run()``.
        result = subprocess.run(
            [sys.executable, "-c",
             f"import runpy; "
             f"import sys; sys.path.insert(0, {str(script_path.parent)!r}); "
             # Read the file and exec only its top-level imports + module
             # body — but stop before mcp.run(). Easiest: import the file
             # via importlib at its real path WITHOUT ``src/`` on path.
             f"import importlib.util; "
             f"spec = importlib.util.spec_from_file_location('mcp_server_under_test', {str(script_path)!r}); "
             f"mod = importlib.util.module_from_spec(spec); "
             f"spec.loader.exec_module(mod); "
             f"print('OK')"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, (
            f"{script} failed to import as a standalone script:\n"
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
        assert "OK" in result.stdout
