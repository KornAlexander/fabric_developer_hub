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
    # Pruned server is recorded so the capability validator can classify
    # missing-tool findings as ops issues (WARNING) rather than catalog
    # bugs (ERROR).
    assert "missing" in mgr.pruned_servers
    assert "/does/not/exist.py" in mgr.pruned_servers["missing"]


def test_unavailable_servers_merges_pruned_and_failed(tmp_path) -> None:
    """``unavailable_servers()`` exposes the combined set of MCP servers
    that can't serve tools this deploy, used by the capability validator."""
    mgr = MCPClientManager(str(tmp_path / "nonexistent.json"))
    mgr.pruned_servers["pbi-fixer"] = "script missing"
    mgr.failed_servers["fabric-docs"] = "npx not found"
    unavailable = mgr.unavailable_servers()
    assert unavailable == {
        "pbi-fixer": "script missing",
        "fabric-docs": "npx not found",
    }


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


@pytest.mark.asyncio
async def test_start_server_rejects_unknown_transport(tmp_path) -> None:
    """Unknown ``transport`` values must fail fast with a clear error
    rather than silently falling back to stdio."""
    mgr = MCPClientManager(str(tmp_path / "missing.json"))
    with pytest.raises(ValueError, match="Unsupported MCP transport"):
        await mgr._start_server(
            {"transport": "ftp", "command": "x"},
            env_override={},
        )


@pytest.mark.asyncio
async def test_start_http_server_requires_url(tmp_path) -> None:
    """Streamable-HTTP server config must declare a ``url``."""
    mgr = MCPClientManager(str(tmp_path / "missing.json"))
    with pytest.raises(ValueError, match="missing required 'url'"):
        await mgr._start_server(
            {"transport": "streamable_http"},
            env_override={},
        )


@pytest.mark.asyncio
async def test_start_http_server_is_scaffold_only(tmp_path) -> None:
    """HTTP transport is scaffolded but not wired. Attempting to use
    it must raise NotImplementedError with a pointer to the next step
    (upstream SP auth)."""
    mgr = MCPClientManager(str(tmp_path / "missing.json"))
    with pytest.raises(NotImplementedError, match="Service Principal auth"):
        await mgr._start_server(
            {"transport": "streamable_http", "url": "https://example.invalid/mcp"},
            env_override={},
        )


def test_qualified_name_for_known_tool(tmp_path) -> None:
    """``qualified_name`` renders ``server::tool`` for discovered tools
    so logs and error messages stay unambiguous."""
    mgr = MCPClientManager(str(tmp_path / "missing.json"))
    mgr.tool_server_map = {"t1": "server-a"}
    assert mgr.qualified_name("t1") == "server-a::t1"


def test_qualified_name_for_undiscovered_tool(tmp_path) -> None:
    mgr = MCPClientManager(str(tmp_path / "missing.json"))
    assert mgr.qualified_name("nope") == "<undiscovered>::nope"


# ── Security: tool policy enforcement ───────────────────────────────
#
# These tests exercise the pre-dispatch gate that hardens the tool surface
# against prompt injection. They deliberately use synthetic
# ``tool_server_map`` entries so we never actually spawn a subprocess.


def _mgr_with_fake_tool(tmp_path, tool_name: str = "fabric_write_file"):
    mgr = MCPClientManager(str(tmp_path / "missing.json"))
    mgr.tool_server_map[tool_name] = "fabric"
    mgr.config["servers"] = {"fabric": {"command": "x", "args": []}}
    return mgr


@pytest.mark.asyncio
async def test_call_tool_rejects_tool_outside_allowlist(tmp_path) -> None:
    """A prompt-injected / hallucinated tool call must be rejected at the
    manager even if the tool itself is registered with the manager."""
    from services.mcp.mcp_client_manager import ToolPolicyViolation
    mgr = _mgr_with_fake_tool(tmp_path, "fabric_delete_item")
    with pytest.raises(ToolPolicyViolation, match="not permitted"):
        await mgr.call_tool(
            "fabric_delete_item",
            {"item_id": "abc"},
            allowed_tools={"fabric_list_items"},
        )


@pytest.mark.asyncio
async def test_call_tool_rejects_cross_workspace_pivot(tmp_path) -> None:
    """Policy must reject a tool call whose ``workspace_id`` disagrees with
    the Job's pinned workspace — preventing prompt-injection pivots to a
    different workspace the user also has access to."""
    from services.mcp.mcp_client_manager import ToolPolicyViolation
    mgr = _mgr_with_fake_tool(tmp_path, "fabric_list_items")
    with pytest.raises(ToolPolicyViolation, match="does not match"):
        await mgr.call_tool(
            "fabric_list_items",
            {"workspace_id": "11111111-1111-1111-1111-111111111111"},
            allowed_tools={"fabric_list_items"},
            workspace_id="22222222-2222-2222-2222-222222222222",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_path",
    [
        "../../etc/passwd",
        "foo/../../../escape",
        "/absolute/leak",
        "C:\\windows\\passwd",
        "mixed\\slashes",
        "has\x00null",
    ],
)
async def test_call_tool_rejects_path_traversal(tmp_path, bad_path: str) -> None:
    """Path-like args must reject traversal, absolute paths, backslashes,
    and null bytes — the classic filesystem-abuse vectors an injected
    prompt could slip into a tool call."""
    from services.mcp.mcp_client_manager import ToolPolicyViolation
    mgr = _mgr_with_fake_tool(tmp_path, "fabric_write_file")
    with pytest.raises(ToolPolicyViolation):
        await mgr.call_tool(
            "fabric_write_file",
            {"file_path": bad_path, "content": "x"},
            allowed_tools={"fabric_write_file"},
        )


@pytest.mark.asyncio
async def test_call_tool_rejects_oversized_argument(tmp_path) -> None:
    """Arg-structure guards cap string length to prevent a crafted dict
    from exhausting downstream JSON encoders or LLM context windows."""
    from services.mcp.mcp_client_manager import (
        _MAX_ARG_STRING_LEN,
        ToolPolicyViolation,
    )
    mgr = _mgr_with_fake_tool(tmp_path, "fabric_write_file")
    with pytest.raises(ToolPolicyViolation, match="exceeds"):
        await mgr.call_tool(
            "fabric_write_file",
            {"file_path": "ok.txt", "content": "a" * (_MAX_ARG_STRING_LEN + 1)},
            allowed_tools={"fabric_write_file"},
        )


def test_env_allowlist_excludes_secrets(monkeypatch) -> None:
    """The MCP subprocess env must NOT inherit arbitrary backend env vars
    like ClientSecret — only the explicit allow-list + server-declared env
    + per-request tokens are forwarded."""
    from services.mcp.mcp_client_manager import _BASE_ENV_ALLOWLIST
    # Secrets an operator might have configured on the backend — none of
    # these should ever be in the allow-list.
    forbidden = {
        "ClientSecret", "CLIENT_SECRET", "AZURE_CLIENT_SECRET",
        "AAD_CLIENT_SECRET", "DATABASE_URL", "AGENTHUB_DB_PATH",
        "GITHUB_TOKEN", "FABRIC_API_TOKEN_STATIC",
    }
    for name in forbidden:
        assert name not in _BASE_ENV_ALLOWLIST, (
            f"{name} must not be in the MCP subprocess env allow-list"
        )
    # Sanity: the allow-list does include the essentials.
    for name in ("PATH", "HOME", "SSL_CERT_FILE"):
        assert name in _BASE_ENV_ALLOWLIST

