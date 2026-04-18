"""Smoke tests for MCP-server modules.

These trivially-cheap tests catch the entire class of bugs where an MCP
server module fails to import (e.g. wrong ``FastMCP`` constructor signature,
syntax errors, missing imports) — exactly the failure mode that shipped
``semantic_link`` as DOA before the Phase-5 refactor.
"""
from __future__ import annotations


def test_fabric_module_imports() -> None:
    from mcp_servers import fabric

    assert fabric.mcp is not None


def test_semantic_link_module_imports() -> None:
    from mcp_servers import semantic_link

    assert semantic_link.mcp is not None


def test_fabric_tool_count() -> None:
    """Lock the public tool surface so accidental removals are caught."""
    from mcp_servers import fabric

    tool_names = sorted(n for n in dir(fabric) if n.startswith("fabric_"))
    assert len(tool_names) == 9, f"expected 9 fabric_* tools, got {tool_names}"
    assert "fabric_list_workspaces" in tool_names
    assert "fabric_list_files" in tool_names
    assert "fabric_read_file" in tool_names


def test_semantic_link_tool_count() -> None:
    """Lock the sl_* tool surface — a regression from the FastMCP-signature
    bug would manifest as zero tools registered (because the module crashed
    on import)."""
    from mcp_servers import semantic_link

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
