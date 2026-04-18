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

import pytest

from services.mcp.mcp_client_manager import MCPClientManager


def test_load_config_missing_file_returns_empty(tmp_path) -> None:
    """REGRESSION: missing config file must NOT raise; manager starts empty."""
    mgr = MCPClientManager(str(tmp_path / "nonexistent.json"))
    assert mgr.config == {"servers": {}}
    assert mgr.has_tools() is False


def test_load_config_resolves_variables(tmp_path) -> None:
    """${PYTHON}, ${SRC_DIR}, ${REPO_DIR} placeholders must be substituted."""
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
    assert server["command"].endswith("python") or server["command"].endswith("python3") \
        or "python" in server["command"]
    # ${SRC_DIR} → directory containing the config
    assert str(tmp_path) in server["args"][0]


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
