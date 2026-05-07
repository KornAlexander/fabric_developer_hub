"""HTTP front door for a mission-scoped MCP runtime container.

The orchestrator starts one container running this module for each mission when
container isolation is enabled. The backend remains the policy/identity gate;
this service owns MCP server discovery and per-request MCP subprocess dispatch
for exactly one session.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from services.mcp.mcp_client_manager import MCPClientManager

logger = logging.getLogger(__name__)

app = FastAPI(title="AgentHub Mission MCP Runtime", version="1.0")
_manager: MCPClientManager | None = None


class ToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    tokens: dict[str, str] | None = None
    allowed_tools: list[str] | None = None
    workspace_id: str | None = None
    execution_context: dict[str, Any] | None = None


class ToolCallResponse(BaseModel):
    output: str
    latency_breakdown_ms: dict[str, int] = Field(default_factory=dict)


def _require_manager() -> MCPClientManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="MCP runtime is not initialized")
    return _manager


def _require_runtime_token(token: str | None) -> None:
    expected = os.environ.get("MCP_RUNTIME_AUTH_TOKEN")
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="MCP runtime token is invalid")


@app.on_event("startup")
async def startup() -> None:
    global _manager
    config_path = os.environ.get("MCP_CONFIG_PATH", "/app/src/mcp_servers.json")
    session_id = os.environ.get("AGENTHUB_MCP_RUNTIME_SESSION_ID", "-")
    logger.info("[MISSION_MCP] starting session=%s config=%s", session_id, config_path)
    manager = MCPClientManager(config_path)
    await manager.discover_tools()
    if not manager.has_tools():
        raise RuntimeError("Mission MCP runtime discovered zero tools")
    _manager = manager
    logger.info(
        "[MISSION_MCP] ready session=%s servers=%d tools=%d",
        session_id,
        len(manager.config.get("servers", {})),
        len(manager.tools),
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    manager = _require_manager()
    return {
        "ok": True,
        "tool_count": len(manager.tools),
        "tool_names": sorted(manager.tools),
    }


@app.get("/tools/schemas")
async def schemas(x_agenthub_mcp_token: str | None = Header(default=None)) -> list[dict[str, Any]]:
    _require_runtime_token(x_agenthub_mcp_token)
    return _require_manager().get_openai_tools_schema()


@app.post("/tools/call")
async def call_tool(
    req: ToolCallRequest,
    x_agenthub_mcp_token: str | None = Header(default=None),
) -> ToolCallResponse:
    _require_runtime_token(x_agenthub_mcp_token)
    manager = _require_manager()
    try:
        result = await manager.call_tool_with_metrics(
            req.tool_name,
            req.arguments,
            req.tokens,
            allowed_tools=set(req.allowed_tools) if req.allowed_tools is not None else None,
            workspace_id=req.workspace_id,
            execution_context=req.execution_context,
        )
    except Exception as exc:
        logger.exception("[MISSION_MCP] tool call failed: %s", req.tool_name)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return ToolCallResponse(
        output=result.output,
        latency_breakdown_ms=result.latency_breakdown_ms,
    )


if __name__ == "__main__":
    port = int(os.environ.get("MCP_RUNTIME_PORT", "8765"))
    uvicorn.run(
        "services.mcp.mission_runtime_service:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("MCP_RUNTIME_LOG_LEVEL", "info"),
    )