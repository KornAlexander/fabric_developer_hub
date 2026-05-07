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

import asyncio
import base64
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.mcp.mcp_client_manager import MCPClientManager


def _empty_config(tmp_path) -> str:
    cfg_path = tmp_path / "mcp_servers.json"
    cfg_path.write_text(json.dumps({"servers": {}}))
    return str(cfg_path)


def _fake_jwt(claims: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def encode(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(claims)}.sig"


def test_powerbi_design_uses_static_startup_discovery() -> None:
    """Third-party Power BI design MCP startup is too slow for mission health.

    Mission-scoped MCP sidecars must not spawn this FastMCP server while the
    user waits for the run to start; the known allowlisted tools are registered
    statically and real server startup happens only if a tool is actually used.
    """
    backend_root = Path(__file__).resolve().parents[3]
    config_path = backend_root / "src" / "mcp_servers.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    server = config["servers"]["powerbi-design"]

    assert server["skip_startup_discovery"] is True
    assert server["allow_static_discovery_fallback"] is True
    assert set(server["static_tools"]) == set(server["tool_allowlist"])
    assert "update_report_definition" in server["static_tools"]


def test_package_backed_servers_use_static_startup_discovery() -> None:
    """Package-backed MCP servers must not gate mission sidecar health.

    These servers install or hydrate packages on first run, which can exceed
    the sidecar health deadline. The tool catalog is stable, so AgentHub can
    expose it statically and start the real MCP process only on tool use.
    """
    backend_root = Path(__file__).resolve().parents[3]
    config_path = backend_root / "src" / "mcp_servers.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    for server_name in ("azure-mcp-guidance", "fabric-docs", "microsoft-docs", "time"):
        server = config["servers"][server_name]
        static_names = [tool["name"] for tool in server["static_tools"]]

        assert server["skip_startup_discovery"] is True
        assert server["allow_static_discovery_fallback"] is True
        assert static_names == server["tool_allowlist"]

    assert config["servers"]["microsoft-docs"]["transport"] == "streamable_http"
    assert config["servers"]["microsoft-docs"]["url"] == "https://learn.microsoft.com/api/mcp"
    azure_args = config["servers"]["azure-mcp-guidance"]["args"]
    assert azure_args.count("--tool") == 2
    assert "get_azure_bestpractices_get" in azure_args
    assert "get_azure_bestpractices_ai_app" in azure_args
    assert "--read-only" in azure_args


def test_load_config_missing_file_raises(tmp_path) -> None:
    """Missing MCP config is fatal — AgentHub must not run tool-less."""
    with pytest.raises(FileNotFoundError, match="MCP config not found"):
        MCPClientManager(str(tmp_path / "nonexistent.json"))


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
    (shallow / "script.py").write_text("# stub")
    cfg_path = shallow / "mcp_servers.json"
    cfg_path.write_text(json.dumps({
        "servers": {
            "x": {"command": "${PYTHON}", "args": ["${REPO_DIR}/script.py"]},
        },
    }))

    # Should not raise.
    mgr = MCPClientManager(str(cfg_path))
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


def test_validate_local_server_paths_rejects_nonexistent_scripts(tmp_path, monkeypatch) -> None:
    """A configured server whose script doesn't exist is a fatal deploy bug."""
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

    with pytest.raises(RuntimeError, match="MCP server path validation failed") as exc:
        MCPClientManager(str(cfg_path))
    assert "missing" in str(exc.value)
    assert "/does/not/exist.py" in str(exc.value)


def test_validate_local_server_paths_rejects_nonexistent_cwd(tmp_path) -> None:
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({
        "servers": {
            "powerbi-design": {
                "command": sys.executable,
                "args": ["-m", "src.server.mcp_server"],
                "cwd": str(tmp_path / "missing"),
            },
        },
    }))

    with pytest.raises(RuntimeError, match="cwd .* not found"):
        MCPClientManager(str(cfg_path))


def test_unavailable_servers_merges_pruned_and_failed(tmp_path) -> None:
    """``unavailable_servers()`` exposes the combined set of MCP servers
    that can't serve tools this deploy, used by the capability validator."""
    mgr = MCPClientManager(_empty_config(tmp_path))
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
    mgr = MCPClientManager(_empty_config(tmp_path))
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
    mgr = MCPClientManager(_empty_config(tmp_path))
    with pytest.raises(ValueError, match="Unknown tool"):
        await mgr.call_tool("does_not_exist", {})


@pytest.mark.asyncio
async def test_call_tool_with_metrics_breaks_out_startup_and_execution(tmp_path) -> None:
    class FakeStack:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    fake_stack = FakeStack()
    fake_session = SimpleNamespace(
        call_tool=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text="hello")]))
    )
    mgr = _mgr_with_fake_tool(tmp_path, "fabric_list_items")
    mgr._start_server = AsyncMock(return_value=(fake_session, fake_stack))  # type: ignore[method-assign]

    result = await mgr.call_tool_with_metrics(
        "fabric_list_items",
        {"workspace_id": "ws-1"},
        allowed_tools={"fabric_list_items"},
        workspace_id="ws-1",
    )

    assert result.output == "hello"
    assert set(result.latency_breakdown_ms) >= {
        "mcpProcessStartupMs",
        "mcpToolExecutionMs",
        "mcpDispatchTotalMs",
    }
    assert all(value >= 0 for value in result.latency_breakdown_ms.values())
    assert fake_stack.closed is True


@pytest.mark.asyncio
async def test_start_server_rejects_unknown_transport(tmp_path) -> None:
    """Unknown ``transport`` values must fail fast with a clear error
    rather than silently falling back to stdio."""
    mgr = MCPClientManager(_empty_config(tmp_path))
    with pytest.raises(ValueError, match="Unsupported MCP transport"):
        await mgr._start_server(
            {"transport": "ftp", "command": "x"},
            env_override={},
        )


@pytest.mark.asyncio
async def test_start_http_server_requires_url(tmp_path) -> None:
    """Streamable-HTTP server config must declare a ``url``."""
    mgr = MCPClientManager(_empty_config(tmp_path))
    with pytest.raises(ValueError, match="missing required 'url'"):
        await mgr._start_server(
            {"transport": "streamable_http"},
            env_override={},
        )


@pytest.mark.asyncio
async def test_start_http_server_requires_auth_token(tmp_path) -> None:
    """Authenticated HTTP transport should fail before connecting when
    no per-request Fabric token is available."""
    mgr = MCPClientManager(_empty_config(tmp_path))
    with pytest.raises(RuntimeError, match="requires auth token"):
        await mgr._start_server(
            {
                "transport": "streamable_http",
                "url": "https://example.invalid/mcp",
                "requires_auth": True,
            },
            env_override={},
        )


def test_http_headers_include_bearer_token_and_correlation() -> None:
    headers = MCPClientManager._http_headers_for_server(
        {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "requires_auth": True,
            "auth_token_env": "FABRIC_API_TOKEN",
            "headers": {"X-Static": "yes"},
        },
        {
            "FABRIC_API_TOKEN": "token-123",
            "AGENTHUB_REQUEST_ID": "req-1",
            "AGENTHUB_SESSION_ID": "sess-1",
        },
    )

    assert headers["Authorization"] == "Bearer token-123"
    assert headers["X-Static"] == "yes"
    assert headers["X-AgentHub-Request-ID"] == "req-1"
    assert headers["X-AgentHub-Session-ID"] == "sess-1"


def test_register_static_tools_adds_http_fallback_schema(tmp_path) -> None:
    mgr = MCPClientManager(_empty_config(tmp_path))

    added = mgr._register_static_tools(
        "fabric-remote-core",
        {
            "tool_allowlist": ["list_workspaces"],
            "static_tools": [
                {"name": "list_workspaces", "description": "List workspaces"},
                {"name": "delete_workspace", "description": "Delete workspace"},
            ],
        },
    )

    assert added == 1
    assert mgr.tool_server_map["list_workspaces"] == "fabric-remote-core"
    assert mgr.tools["list_workspaces"]["inputSchema"]["additionalProperties"] is True


@pytest.mark.asyncio
async def test_discover_tools_uses_static_tools_after_http_auth_failure(tmp_path, caplog) -> None:
    cfg_path = tmp_path / "mcp_servers.json"
    cfg_path.write_text(json.dumps({
        "servers": {
            "fabric-remote-core": {
                "transport": "streamable_http",
                "url": "https://example.invalid/mcp",
                "requires_auth": True,
                "static_tools": [
                    {"name": "list_workspaces", "description": "List workspaces"},
                ],
            },
        },
    }))
    mgr = MCPClientManager(str(cfg_path))

    caplog.set_level(logging.WARNING)
    await mgr.discover_tools()

    assert "fabric-remote-core" not in mgr.failed_servers
    assert mgr.tool_server_map["list_workspaces"] == "fabric-remote-core"
    assert "auth token unavailable during startup discovery" in caplog.text
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.asyncio
async def test_discover_tools_uses_static_tools_after_opted_in_timeout(tmp_path, caplog) -> None:
    cfg_path = tmp_path / "mcp_servers.json"
    cfg_path.write_text(json.dumps({
        "servers": {
            "slow-stdio": {
                "command": "fake-command",
                "discovery_timeout_seconds": 0.01,
                "allow_static_discovery_fallback": True,
                "static_tools": [
                    {"name": "slow_tool", "description": "Known tool"},
                ],
            },
        },
    }))
    mgr = MCPClientManager(str(cfg_path))

    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(60)

    mgr._start_server = AsyncMock(side_effect=never_finishes)  # type: ignore[method-assign]

    caplog.set_level(logging.WARNING)
    await mgr.discover_tools()

    assert "slow-stdio" not in mgr.failed_servers
    assert mgr.tool_server_map["slow_tool"] == "slow-stdio"
    assert "discovery timed out after 0.01s" in caplog.text
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.asyncio
async def test_discover_tools_can_use_static_tools_without_startup_spawn(tmp_path, caplog) -> None:
    cfg_path = tmp_path / "mcp_servers.json"
    cfg_path.write_text(json.dumps({
        "servers": {
            "static-stdio": {
                "command": "fake-command",
                "skip_startup_discovery": True,
                "static_tools": [
                    {"name": "static_tool", "description": "Known startup tool"},
                ],
            },
        },
    }))
    mgr = MCPClientManager(str(cfg_path))
    mgr._start_server = AsyncMock()  # type: ignore[method-assign]

    caplog.set_level(logging.WARNING)
    await mgr.discover_tools()

    mgr._start_server.assert_not_called()  # type: ignore[attr-defined]
    assert "static-stdio" not in mgr.failed_servers
    assert mgr.tool_server_map["static_tool"] == "static-stdio"
    assert "startup discovery skipped by config" in caplog.text
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_qualified_name_for_known_tool(tmp_path) -> None:
    """``qualified_name`` renders ``server::tool`` for discovered tools
    so logs and error messages stay unambiguous."""
    mgr = MCPClientManager(_empty_config(tmp_path))
    mgr.tool_server_map = {"t1": "server-a"}
    assert mgr.qualified_name("t1") == "server-a::t1"


def test_qualified_name_for_undiscovered_tool(tmp_path) -> None:
    mgr = MCPClientManager(_empty_config(tmp_path))
    assert mgr.qualified_name("nope") == "<undiscovered>::nope"


def test_timeout_for_tool_uses_long_window_for_known_long_running_tools(monkeypatch) -> None:
    from services.mcp import mcp_client_manager as mod

    monkeypatch.setattr(mod, "TOOL_CALL_TIMEOUT", 30)
    monkeypatch.setattr(mod, "LONG_RUNNING_TOOL_CALL_TIMEOUT", 120)
    monkeypatch.setattr(mod, "_TOOL_TIMEOUT_OVERRIDES", {
        "fabric_create_workspace_inventory_solution": 120,
        "fabric_verify_report_renderable": 120,
        "fabric_verify_workspace_inventory_solution": 120,
    })

    assert mod._timeout_for_tool("fabric_list_items") == 30
    assert mod._timeout_for_tool("fabric_create_workspace_inventory_solution") == 120
    assert mod._timeout_for_tool("fabric_verify_report_renderable") == 120
    assert mod._timeout_for_tool("fabric_verify_workspace_inventory_solution") == 120
    assert mod._timeout_for_tool("run_shell_command") == 120


# ── Security: tool policy enforcement ───────────────────────────────
#
# These tests exercise the pre-dispatch gate that hardens the tool surface
# against prompt injection. They deliberately use synthetic
# ``tool_server_map`` entries so we never actually spawn a subprocess.


def _mgr_with_fake_tool(tmp_path, tool_name: str = "fabric_write_file"):
    mgr = MCPClientManager(_empty_config(tmp_path))
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


@pytest.mark.asyncio
async def test_call_tool_rejects_app_only_token_for_fabric_mutation(tmp_path) -> None:
    from services.mcp.mcp_client_manager import ToolPolicyViolation

    mgr = _mgr_with_fake_tool(tmp_path, "create_item")
    mgr.config["servers"] = {"fabric": {"command": "x", "args": [], "requires_auth": True}}

    with pytest.raises(ToolPolicyViolation, match="application/service-principal token"):
        await mgr.call_tool(
            "create_item",
            {"workspace_id": "workspace-1", "displayName": "Model", "type": "SemanticModel"},
            tokens={
                "FABRIC_API_TOKEN": _fake_jwt({
                    "idtyp": "app",
                    "roles": ["Item.ReadWrite.All"],
                    "appid": "app-1",
                    "app_displayname": "Fabric ClawHub",
                })
            },
            allowed_tools={"create_item"},
        )


def test_mutation_token_guard_allows_delegated_user_token() -> None:
    from services.mcp.mcp_client_manager import _validate_delegated_token_for_mutation

    _validate_delegated_token_for_mutation(
        "create_item",
        {"requires_auth": True, "auth_token_env": "FABRIC_API_TOKEN"},
        {
            "FABRIC_API_TOKEN": _fake_jwt({
                "scp": "Item.ReadWrite.All Dataset.ReadWrite.All",
                "name": "Lukasz Obst",
                "oid": "user-1",
            })
        },
    )


def test_mutation_token_guard_covers_definition_publish() -> None:
    from services.mcp.mcp_client_manager import ToolPolicyViolation, _validate_delegated_token_for_mutation

    with pytest.raises(ToolPolicyViolation, match="fabric_definition_publish"):
        _validate_delegated_token_for_mutation(
            "fabric_definition_publish",
            {"requires_auth": True, "auth_token_env": "FABRIC_API_TOKEN"},
            {
                "FABRIC_API_TOKEN": _fake_jwt({
                    "idtyp": "app",
                    "roles": ["Item.ReadWrite.All"],
                    "appid": "app-1",
                    "app_displayname": "Fabric ClawHub",
                })
            },
        )


def test_mutation_token_guard_checks_onelake_token_for_file_writes() -> None:
    from services.mcp.mcp_client_manager import ToolPolicyViolation, _validate_delegated_token_for_mutation

    with pytest.raises(ToolPolicyViolation, match="ONELAKE_TOKEN"):
        _validate_delegated_token_for_mutation(
            "fabric_write_file",
            {"requires_auth": True},
            {
                "FABRIC_API_TOKEN": _fake_jwt({"scp": "Item.ReadWrite.All", "oid": "user-1"}),
                "ONELAKE_TOKEN": _fake_jwt({"idtyp": "app", "roles": ["Storage.BlobDataContributor"], "appid": "app-1"}),
            },
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


def test_execution_context_env_maps_actor_metadata() -> None:
    from services.mcp.mcp_client_manager import _execution_context_env

    env = _execution_context_env({
        "actor_role": "subagent",
        "agent_id": "fabric-builder",
        "agent_name": "Fabric Builder\nInjected line",
        "agent_session_id": "agent-session-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "task_title": "Build inventory solution",
        "tool_call_id": "call-1",
        "ignored": "value",
    })

    assert env == {
        "AGENTHUB_ACTOR_ROLE": "subagent",
        "AGENTHUB_AGENT_ID": "fabric-builder",
        "AGENTHUB_AGENT_NAME": "Fabric Builder Injected line",
        "AGENTHUB_AGENT_SESSION_ID": "agent-session-1",
        "AGENTHUB_RUN_ID": "run-1",
        "AGENTHUB_TASK_ID": "task-1",
        "AGENTHUB_TASK_TITLE": "Build inventory solution",
        "AGENTHUB_TOOL_CALL_ID": "call-1",
    }

