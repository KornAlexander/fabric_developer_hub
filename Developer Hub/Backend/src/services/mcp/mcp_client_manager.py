"""MCP Client Manager — manages MCP server lifecycles and tool routing.

Per the design doc (§3.2, §7.6):
- Tool schemas are discovered once at startup via a temporary server instance.
- Each tool call spawns a fresh per-request server process (no shared state).
- Fabric tokens are injected via environment variables, never tool arguments.
"""

import asyncio
import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

# Timeout for individual tool calls (seconds)
TOOL_CALL_TIMEOUT = 30

# Env-var allowlist for spawned MCP subprocesses. The backend's full
# ``os.environ`` carries secrets (ClientSecret, DB paths, anything an
# operator configured) that the MCP server has no reason to see. We copy
# only the entries every Python/OS-level child process actually needs, plus
# anything the MCP config itself declared in ``env``, plus the per-request
# auth tokens. Everything else (secrets, unrelated config) is dropped.
_BASE_ENV_ALLOWLIST: frozenset[str] = frozenset({
    # OS / interpreter baseline
    "PATH", "HOME", "TMP", "TEMP", "TMPDIR", "USER", "LOGNAME",
    "LANG", "LC_ALL", "LC_CTYPE",
    "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
    # TLS / CA certs (httpx / requests in the MCP servers need these)
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    # Proxy config for outbound HTTP — MCP servers make real API calls
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
    # Our own knobs that MCP code reads
    "MCP_REPO_DIR",
})

# Tool-argument safety limits. These are defense-in-depth; individual MCP
# servers should still validate, but the manager is the last gate before we
# send a crafted argument to an external server.
_MAX_ARG_STRING_LEN = 64_000
_MAX_ARG_DEPTH = 8

# Arg keys that are interpreted as filesystem / OneLake paths by our MCP
# servers. Any value under these keys is checked for traversal attempts.
_PATH_ARG_KEYS: frozenset[str] = frozenset({
    "file_path", "directory_path", "path", "source_path", "target_path",
    "destination_path", "src_path", "dst_path",
})

# Arg keys that must match the Job's declared workspace_id when it is
# provided. Prevents a prompt-injected tool call from pivoting the agent to
# a DIFFERENT workspace the user happens to also have rights to.
_WORKSPACE_ARG_KEYS: frozenset[str] = frozenset({
    "workspace_id", "workspaceId",
})


class ToolPolicyViolation(RuntimeError):
    """Raised when a tool call fails a pre-dispatch policy check.

    Distinct from ``ValueError`` so callers (orchestrator) can surface it as
    a security event instead of a generic failure.
    """


def _validate_tool_arguments(
    tool_name: str,
    arguments: dict,
    *,
    workspace_id: str | None,
) -> None:
    """Run policy checks on a pending tool call. Raises on violation.

    Checks (in order):

    1. Argument structure is not pathologically deep / large — bounded
       recursion and string length caps so a crafted dict can't OOM us.
    2. If the Job carries a ``workspace_id`` and the tool's arguments also
       do, they must match. This prevents lateral movement to another
       workspace the user has rights to.
    3. Path-like arg keys may not contain ``..``, null bytes, backslashes on
       POSIX layouts, or start with a leading ``/`` (absolute paths) —
       OneLake is *not* a filesystem, absolute paths would escape the
       lakehouse root and backslashes are only legitimate on Windows.
    """

    # (1) structural bounds
    def _walk(value, depth: int) -> None:
        if depth > _MAX_ARG_DEPTH:
            raise ToolPolicyViolation(
                f"Tool {tool_name}: argument nesting exceeds {_MAX_ARG_DEPTH}"
            )
        if isinstance(value, str):
            if len(value) > _MAX_ARG_STRING_LEN:
                raise ToolPolicyViolation(
                    f"Tool {tool_name}: argument value exceeds "
                    f"{_MAX_ARG_STRING_LEN} chars"
                )
            return
        if isinstance(value, dict):
            for v in value.values():
                _walk(v, depth + 1)
            return
        if isinstance(value, list):
            for v in value:
                _walk(v, depth + 1)

    _walk(arguments, 0)

    # (2) workspace binding
    if workspace_id:
        for key in _WORKSPACE_ARG_KEYS:
            if key in arguments:
                got = str(arguments[key] or "").strip().lower()
                want = workspace_id.strip().lower()
                if got and got != want:
                    raise ToolPolicyViolation(
                        f"Tool {tool_name}: {key}={got!r} does not match the "
                        f"session's workspace {want!r}. Cross-workspace tool "
                        f"calls are blocked as a prompt-injection defense."
                    )

    # (3) path traversal
    for key in _PATH_ARG_KEYS:
        if key not in arguments:
            continue
        val = arguments[key]
        if not isinstance(val, str):
            continue
        if "\x00" in val:
            raise ToolPolicyViolation(
                f"Tool {tool_name}: {key} contains a null byte"
            )
        if ".." in val.split("/") or ".." in val.split("\\"):
            raise ToolPolicyViolation(
                f"Tool {tool_name}: {key}={val!r} contains a parent-directory "
                f"traversal segment ('..')"
            )
        if val.startswith("/") or (len(val) >= 2 and val[1] == ":"):
            raise ToolPolicyViolation(
                f"Tool {tool_name}: {key}={val!r} is an absolute path; only "
                f"lakehouse-relative paths are accepted"
            )
        if "\\" in val:
            # OneLake / Fabric APIs use POSIX separators; a backslash
            # usually indicates a hand-crafted Windows-style injection.
            raise ToolPolicyViolation(
                f"Tool {tool_name}: {key}={val!r} contains a backslash; use "
                f"forward slashes for lakehouse paths"
            )


class MCPClientManager:
    """Manages MCP server processes and client connections."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self.tools: dict[str, dict] = {}           # tool_name → tool metadata dict
        self.tool_server_map: dict[str, str] = {}   # tool_name → server_name

    def _load_config(self, config_path: str) -> dict:
        path = Path(config_path)
        if not path.exists():
            logger.warning("MCP config not found at %s, starting with no servers", config_path)
            return {"servers": {}}
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        # Resolve template variables in the config
        self._resolve_variables(raw, config_path)
        # Drop servers whose entrypoint script doesn't exist on disk so that
        # missing optional MCPs (e.g. host-only ${REPO_DIR}/... in Docker)
        # degrade gracefully instead of failing tool discovery.
        self._prune_missing_servers(raw)
        return raw

    @staticmethod
    def _resolve_repo_dir(config_path: str) -> str:
        """Resolve ${REPO_DIR} robustly across Docker / local layouts.

        Precedence:
          1. ``MCP_REPO_DIR`` env var (explicit override — preferred in containers).
          2. Walk up from ``config_path`` looking for a project marker
             (``.git`` directory or ``pyproject.toml``).
          3. Fall back to the immediate parent of the config file.

        Notably this does NOT use ``Path(config_path).parents[N]`` with a
        hard-coded ``N`` — that crashed with IndexError on shallow Docker
        paths like ``/app/src/mcp_servers.json``.
        """
        override = os.environ.get("MCP_REPO_DIR")
        if override:
            return override

        path = Path(config_path).resolve()
        for candidate in path.parents:
            if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
                return str(candidate)

        # No marker found — fall back to the directory containing the config.
        return str(path.parent)

    def _resolve_variables(self, config: dict, config_path: str) -> None:
        """Replace ${PYTHON}, ${SRC_DIR}, and ${REPO_DIR} placeholders in server configs."""
        src_dir = str(Path(config_path).parent)
        repo_dir = self._resolve_repo_dir(config_path)
        python_exe = sys.executable

        def _sub(val: str) -> str:
            return (val
                    .replace("${PYTHON}", python_exe)
                    .replace("${SRC_DIR}", src_dir)
                    .replace("${REPO_DIR}", repo_dir))

        for server_config in config.get("servers", {}).values():
            if "command" in server_config:
                server_config["command"] = _sub(server_config["command"])
            if "args" in server_config:
                server_config["args"] = [_sub(arg) for arg in server_config["args"]]

    @staticmethod
    def _prune_missing_servers(config: dict) -> None:
        """Drop servers whose first arg (the script path) does not exist.

        Keeps the manager usable when some MCPs are only available in the
        host dev layout (e.g. ``${REPO_DIR}/mcp/...``) but not inside a
        container that only ships ``src/``.
        """
        servers = config.get("servers", {})
        for name in list(servers.keys()):
            args = servers[name].get("args") or []
            if args and not Path(args[0]).exists():
                logger.warning(
                    "MCP server %s: script %s not found on disk; skipping",
                    name, args[0],
                )
                del servers[name]

    def has_tools(self) -> bool:
        return len(self.tools) > 0

    async def discover_tools(self):
        """Start a temporary instance of each server to discover tool schemas.

        Called once at startup. The temporary instances are shut down after discovery.
        """
        for name, server_config in self.config.get("servers", {}).items():
            if not server_config.get("command"):
                logger.warning("Server %s has no command, skipping", name)
                continue
            try:
                logger.info("Discovering tools from MCP server: %s", name)
                session, stack = await self._start_server(server_config, env_override={})
                try:
                    result = await session.list_tools()
                    for tool in result.tools:
                        self.tools[tool.name] = {
                            "name": tool.name,
                            "description": tool.description or "",
                            "inputSchema": tool.inputSchema or {"type": "object", "properties": {}},
                        }
                        self.tool_server_map[tool.name] = name
                        logger.info("  Discovered tool: %s", tool.name)
                finally:
                    await stack.aclose()
                logger.info("MCP server %s: discovered %d tools", name, len([
                    t for t, s in self.tool_server_map.items() if s == name
                ]))
            except Exception as e:
                logger.error("Failed to discover tools from MCP server %s: %s", name, e)

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict,
        tokens: dict[str, str] | None = None,
        *,
        allowed_tools: set[str] | frozenset[str] | None = None,
        workspace_id: str | None = None,
    ) -> str:
        """Execute a tool call via a per-request MCP server instance.

        Spawns a fresh process, calls the tool, kills the process.

        Args:
            tool_name: The MCP tool name to call.
            arguments: Tool arguments dict.
            tokens: Env-var-name → token-value mapping injected into the process.
                    E.g. {"FABRIC_API_TOKEN": "eyJ...", "ONELAKE_TOKEN": "eyJ..."}
            allowed_tools: If provided, the tool call is rejected unless
                ``tool_name`` is in this set. Enforced here (not just when
                building the schema the LLM sees) so a prompt-injected or
                hallucinated tool call cannot bypass the per-agent policy.
            workspace_id: Optional Job-level workspace id the tool call is
                bound to. Any ``workspace_id``/``workspaceId`` argument that
                disagrees with this value is rejected as a cross-workspace
                pivot attempt.

        Raises:
            ToolPolicyViolation: policy check failed before dispatch.
            ValueError: ``tool_name`` is not a known tool.
            TimeoutError: the call exceeded ``TOOL_CALL_TIMEOUT``.
        """
        if tool_name not in self.tool_server_map:
            raise ValueError(f"Unknown tool: {tool_name}")

        if allowed_tools is not None and tool_name not in allowed_tools:
            # This is the authoritative gate — the schema filter given to
            # the LLM is advisory. Treat a hit here as a security event.
            raise ToolPolicyViolation(
                f"Tool {tool_name!r} is not permitted for this agent. "
                f"Allow-list: {sorted(allowed_tools)}"
            )

        _validate_tool_arguments(
            tool_name, arguments or {}, workspace_id=workspace_id,
        )

        server_name = self.tool_server_map[tool_name]
        server_config = self.config["servers"][server_name]

        # Build per-request env with auth tokens
        env_override = dict(server_config.get("env", {}))
        if server_config.get("requires_auth") and tokens:
            env_override.update(tokens)

        session, stack = await self._start_server(server_config, env_override=env_override)
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments),
                timeout=TOOL_CALL_TIMEOUT,
            )
            # Flatten MCP result content into a string
            parts = []
            for content in result.content:
                if hasattr(content, "text"):
                    parts.append(content.text)
                else:
                    parts.append(str(content))
            return "\n".join(parts)
        except TimeoutError as e:
            raise TimeoutError(f"Tool {tool_name} timed out after {TOOL_CALL_TIMEOUT}s") from e
        finally:
            await stack.aclose()

    def get_openai_tools_schema(self) -> list[dict]:
        """Convert MCP tool schemas to OpenAI function-calling format."""
        result = []
        for tool in self.tools.values():
            params = self._clean_schema(tool["inputSchema"])
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": params,
                },
            })
        return result

    @staticmethod
    def _clean_schema(schema: dict) -> dict:
        """Strip non-OpenAI fields from MCP JSON schemas.

        Removes title, converts anyOf[type, null] → type with nullable hint,
        ensures required is present.
        """
        cleaned = dict(schema)
        cleaned.pop("title", None)
        if "required" not in cleaned:
            cleaned["required"] = []
        props = cleaned.get("properties", {})
        clean_props = {}
        for prop_name, prop_def in props.items():
            p = dict(prop_def)
            p.pop("title", None)
            # Convert anyOf[{type: X}, {type: null}] → {type: X} (OpenAI doesn't support anyOf)
            if "anyOf" in p:
                non_null = [t for t in p["anyOf"] if t.get("type") != "null"]
                if non_null:
                    merged = {**non_null[0], "description": p.get("description", "")}
                    merged.pop("title", None)
                    p = merged
                else:
                    p.pop("anyOf")
            clean_props[prop_name] = p
        cleaned["properties"] = clean_props
        return cleaned

    async def _start_server(
        self,
        server_config: dict,
        env_override: dict,
    ) -> tuple[ClientSession, AsyncExitStack]:
        """Start an MCP server process and return (session, stack).

        Caller is responsible for calling `await stack.aclose()` to shut down the process.

        Security: the child process receives an **allow-listed** slice of
        the backend's environment (PATH / locale / TLS-cert / proxy vars
        only) plus whatever the MCP server config and per-request
        ``env_override`` explicitly declare. The backend's full ``os.environ``
        — which includes ClientSecret, DB paths, and other operator config —
        is never passed to the child. This shrinks the blast radius of any
        future compromise of an MCP server (first-party or third-party).
        """
        # Start from a sanitized base copy of our own env
        merged_env = {
            k: v for k, v in os.environ.items() if k in _BASE_ENV_ALLOWLIST
        }
        # Layer in the MCP server's own declared env (from mcp_servers.json),
        # then the per-request overrides (auth tokens). Later writers win.
        merged_env.update(server_config.get("env", {}))
        merged_env.update(env_override)

        server_params = StdioServerParameters(
            command=server_config["command"],
            args=server_config.get("args", []),
            env=merged_env,
        )

        stack = AsyncExitStack()
        try:
            transport = await stack.enter_async_context(stdio_client(server_params))
            read, write = transport
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            return session, stack
        except Exception:
            await stack.aclose()
            raise
