"""Smoke tests for ``services.mcp.mcp_client_manager``.

Subprocess-spawning code is hard to test end-to-end, but we can exercise:
  * Config loading + variable resolution (``${PYTHON}``, ``${SRC_DIR}``, ``${REPO_DIR}``)
  * Missing-config graceful fallback
  * ``has_tools`` state
  * ``get_openai_tools_schema`` shape conversion
  * ``_clean_schema`` transformations (anyOf null-stripping, title removal)
  * ``call_tool`` argument validation (unknown tool → ValueError)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from services.mcp.mcp_client_manager import MCPClientManager


def test_load_config_missing_file_returns_empty(tmp_path) -> None:
    """REGRESSION: missing config file must NOT raise; manager starts empty."""
    mgr = MCPClientManager(str(tmp_path / "nonexistent.json"))
    assert mgr.config == {"servers": {}}
    assert mgr.has_tools() is False


def test_load_config_resolves_variables(tmp_path) -> None:
    """${PYTHON}, ${SRC_DIR}, ${REPO_DIR} placeholders must be substituted."""
    # The pruning step requires the script path to exist on disk.
    (tmp_path / "fabric.py").write_text("# stub")

    cfg = {
        "servers": {
            "fabric": {
                "command": "${PYTHON}",
                "args": ["${SRC_DIR}/fabric.py", "--repo", "${REPO_DIR}"],
                "env": {},
            },
        },
    }
    cfg_path = tmp_path / "mcp_servers.json"
    cfg_path.write_text(json.dumps(cfg))

    mgr = MCPClientManager(str(cfg_path))
    server = mgr.config["servers"]["fabric"]
    # ${PYTHON} → sys.executable (absolute path)
    assert server["command"] == sys.executable
    # ${SRC_DIR} → directory containing the config
    assert server["args"][0].startswith(str(tmp_path))
    assert server["args"][0].endswith("/fabric.py")
    # ${REPO_DIR} must be substituted (no leftover placeholder)
    assert "${REPO_DIR}" not in server["args"][2]


def test_load_config_resolves_variables_on_shallow_path(tmp_path, monkeypatch) -> None:
    """REGRESSION: ``parents[4]`` used to crash with IndexError when the
    config file lived at a shallow path (e.g. ``/app/src/mcp_servers.json``
    in Docker). Resolution must succeed regardless of path depth.

    Strategy: clear MCP_REPO_DIR so the env-var override doesn't mask the
    walk-up path, and place the config in a 2-deep tmp tree.
    """
    monkeypatch.delenv("MCP_REPO_DIR", raising=False)

    shallow = tmp_path / "src"
    shallow.mkdir()
    cfg_path = shallow / "mcp_servers.json"
    cfg_path.write_text(json.dumps({
        "servers": {
            "x": {"command": "${PYTHON}", "args": ["${REPO_DIR}/script.py"]},
        },
    }))

    # Should not raise.
    mgr = MCPClientManager(str(cfg_path))
    # Server is pruned because the resolved script doesn't exist on disk —
    # but config load itself must not crash.
    assert "${REPO_DIR}" not in str(mgr.config)


def test_resolve_repo_dir_env_override_wins(tmp_path, monkeypatch) -> None:
    """``MCP_REPO_DIR`` env var takes precedence over filesystem walk-up."""
    monkeypatch.setenv("MCP_REPO_DIR", "/explicit/repo/root")
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text("{}")
    assert MCPClientManager._resolve_repo_dir(str(cfg_path)) == "/explicit/repo/root"


def test_resolve_repo_dir_finds_pyproject_marker(tmp_path, monkeypatch) -> None:
    """Walk-up should locate a directory containing ``pyproject.toml``."""
    monkeypatch.delenv("MCP_REPO_DIR", raising=False)
    repo = tmp_path / "myrepo"
    (repo / "deep" / "src").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("")
    cfg = repo / "deep" / "src" / "cfg.json"
    cfg.write_text("{}")
    assert MCPClientManager._resolve_repo_dir(str(cfg)) == str(repo.resolve())


def test_prune_missing_servers_drops_nonexistent_scripts(tmp_path, monkeypatch) -> None:
    """REGRESSION: a server whose script doesn't exist (e.g. host-only path
    referenced by ${REPO_DIR}/... but absent inside a container) must be
    silently dropped, not retained as a poisoned entry."""
    monkeypatch.delenv("MCP_REPO_DIR", raising=False)
    real_script = tmp_path / "real.py"
    real_script.write_text("# hi")

    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({
        "servers": {
            "real": {"command": "x", "args": [str(real_script)]},
            "missing": {"command": "x", "args": ["/does/not/exist.py"]},
        },
    }))

    mgr = MCPClientManager(str(cfg_path))
    assert "real" in mgr.config["servers"]
    assert "missing" not in mgr.config["servers"]


def test_clean_schema_removes_title() -> None:
    schema = {
        "type": "object",
        "title": "MyTool",
        "properties": {
            "x": {"type": "string", "title": "X param"},
        },
    }
    cleaned = MCPClientManager._clean_schema(schema)
    assert "title" not in cleaned
    assert "title" not in cleaned["properties"]["x"]
    assert cleaned["required"] == []


def test_clean_schema_collapses_anyof_with_null() -> None:
    """REGRESSION: OpenAI doesn't support ``anyOf``. The Pydantic-emitted
    ``anyOf: [{type: string}, {type: null}]`` for ``Optional[str]`` must be
    flattened to ``{type: string}``."""
    schema = {
        "type": "object",
        "properties": {
            "name": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Optional name",
            },
        },
    }
    cleaned = MCPClientManager._clean_schema(schema)
    name_def = cleaned["properties"]["name"]
    assert "anyOf" not in name_def
    assert name_def["type"] == "string"
    assert name_def["description"] == "Optional name"


def test_clean_schema_preserves_required() -> None:
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }
    cleaned = MCPClientManager._clean_schema(schema)
    assert cleaned["required"] == ["x"]


def test_get_openai_tools_schema_format(tmp_path) -> None:
    """The OpenAI function-calling format must wrap each tool as
    ``{type: function, function: {name, description, parameters}}``."""
    mgr = MCPClientManager(str(tmp_path / "missing.json"))
    mgr.tools = {
        "fabric_list_workspaces": {
            "name": "fabric_list_workspaces",
            "description": "List workspaces",
            "inputSchema": {"type": "object", "properties": {}},
        },
    }

    schemas = mgr.get_openai_tools_schema()
    assert len(schemas) == 1
    s = schemas[0]
    assert s["type"] == "function"
    assert s["function"]["name"] == "fabric_list_workspaces"
    assert s["function"]["description"] == "List workspaces"
    assert s["function"]["parameters"]["type"] == "object"


@pytest.mark.asyncio
async def test_call_tool_unknown_raises(tmp_path) -> None:
    mgr = MCPClientManager(str(tmp_path / "missing.json"))
    with pytest.raises(ValueError, match="Unknown tool"):
        await mgr.call_tool("does_not_exist", {})
