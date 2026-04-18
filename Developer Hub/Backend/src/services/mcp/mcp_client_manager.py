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
        return raw

    def _resolve_variables(self, config: dict, config_path: str):
        """Replace ${PYTHON}, ${SRC_DIR}, and ${REPO_DIR} placeholders in server configs."""
        src_dir = str(Path(config_path).parent)
        # REPO_DIR = repo root (fabric_agenthub/), 4 levels above src/
        repo_dir = str(Path(config_path).parents[4])
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
    ) -> str:
        """Execute a tool call via a per-request MCP server instance.

        Spawns a fresh process, calls the tool, kills the process.

        Args:
            tool_name: The MCP tool name to call.
            arguments: Tool arguments dict.
            tokens: Env-var-name → token-value mapping injected into the process.
                    E.g. {"FABRIC_API_TOKEN": "eyJ...", "ONELAKE_TOKEN": "eyJ..."}
        """
        if tool_name not in self.tool_server_map:
            raise ValueError(f"Unknown tool: {tool_name}")

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
        """
        # Merge base OS env with server-specific env and overrides
        merged_env = dict(os.environ)
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
