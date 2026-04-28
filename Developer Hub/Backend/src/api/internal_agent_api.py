"""Internal API endpoints for container-isolated agents.

These endpoints are called by agent containers running in Docker. They
are NOT exposed to external users — they're on the same internal Docker
network.

- ``POST /api/internal/tools/execute`` — proxy a tool call through the
  central ``tool_runtime.execute()`` chokepoint.
- ``POST /api/internal/slots/complete`` — report slot completion so the
  orchestrator can update session state.
- ``GET /api/internal/tools/schemas`` — fetch the OpenAI tool schemas
  so the agent container knows what tools are available.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal", tags=["internal-agent"])


class ToolExecuteRequest(BaseModel):
    session_id: str
    slot_id: str
    assignment_session_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolExecuteResponse(BaseModel):
    ok: bool
    output: str
    policy_decision: str


class SlotCompleteRequest(BaseModel):
    session_id: str
    slot_id: str
    assignment_session_id: str
    status: str  # success | partial | error
    summary: str = ""


@router.post("/tools/execute")
async def execute_tool(req: ToolExecuteRequest, request: Request) -> ToolExecuteResponse:
    """Proxy a tool call from an agent container through tool_runtime.

    The agent container sends the tool name and arguments; we run them
    through the full security chokepoint (policy registry, kill-switches,
    circuit breaker, caller-identity scrubbing, workspace pinning).
    """
    from services.agenthub import tool_runtime
    from services.agenthub.orchestrator_engine import get_orchestrator_engine

    engine = get_orchestrator_engine()

    # Find the active job execution
    execution = engine.get_job_execution(req.session_id)
    if execution is None:
        return ToolExecuteResponse(
            ok=False,
            output="TOOL_ERROR: session not found or not active",
            policy_decision="denied:session_not_found",
        )

    job = execution.job

    # Build CallerContext from the job's verified identity (never from
    # the agent container's request — that would let a compromised
    # container forge caller identity).
    ctx = tool_runtime.CallerContext(
        tenant_id=job.user_id,
        user_id=job.user_id,
        user_upn=job.user_upn,
        workspace_id=job.workspace_id,
        session_id=req.assignment_session_id,
    )

    # Find allowed tools for this slot
    assignment = None
    for a in job.agents:
        if a.session_id == req.assignment_session_id:
            assignment = a
            break

    run_id = None
    task_id = None
    task_title = None
    if execution.dynamic_mission_state is not None:
        for dynamic_run in execution.dynamic_mission_state.subagent_runs.values():
            if dynamic_run.agent_session_id == req.assignment_session_id:
                run_id = dynamic_run.id
                task_id = dynamic_run.task_id
                dynamic_task = execution.dynamic_mission_state.tasks.get(dynamic_run.task_id)
                task_title = dynamic_task.title if dynamic_task is not None else None
                break

    agent_id = assignment.agent_id if assignment is not None else req.slot_id
    agent_name = getattr(assignment, "role", None) or agent_id

    result = await tool_runtime.execute(
        tool_name=req.tool_name,
        arguments=req.arguments,
        ctx=tool_runtime.CallerContext(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            user_upn=ctx.user_upn,
            workspace_id=ctx.workspace_id,
            session_id=ctx.session_id,
            agent_id=agent_id,
            agent_name=agent_name,
            actor_role="generalist" if agent_id == "generalist" else "subagent",
            agent_session_id=req.assignment_session_id,
            run_id=run_id,
            task_id=task_id,
            task_title=task_title or agent_name,
            tool_call_id=f"container-{req.slot_id}",
        ),
        mcp_manager=engine.mcp_manager,
        mcp_tokens=execution.mcp_tokens,
    )

    # Emit tool call events on the session event bus
    execution.emit(
        "tool_call_started",
        agentId=req.assignment_session_id,
        callId=f"container-{req.slot_id}",
        toolName=req.tool_name,
    )
    execution.emit(
        "tool_call_ended",
        agentId=req.assignment_session_id,
        callId=f"container-{req.slot_id}",
        toolName=req.tool_name,
        status="ok" if result.ok else "error",
    )

    return ToolExecuteResponse(
        ok=result.ok,
        output=result.output,
        policy_decision=result.policy_decision,
    )


@router.post("/slots/complete")
async def report_slot_complete(req: SlotCompleteRequest) -> dict:
    """Receive a completion report from an agent container.

    The orchestrator updates the agent assignment status and emits
    the appropriate SSE events.
    """
    from domain.models.agent_models import AgentStatus
    from services.agenthub.orchestrator_engine import get_orchestrator_engine
    from services.agenthub.session_store import update_session

    engine = get_orchestrator_engine()

    execution = engine.get_job_execution(req.session_id)
    if execution is None:
        return {"ok": False, "error": "session not found"}

    job = execution.job
    for a in job.agents:
        if a.session_id == req.assignment_session_id:
            if req.status == "success":
                a.status = AgentStatus.COMPLETED
                a.current_step = "Completed"
            elif req.status == "error":
                a.status = AgentStatus.ERROR
                a.current_step = req.summary[:200] or "Error in container"
            else:
                a.status = AgentStatus.COMPLETED
                a.current_step = "Completed (partial)"
            break

    update_session(job)
    return {"ok": True}


@router.get("/tools/schemas")
async def get_tool_schemas(request: Request) -> list[dict]:
    """Return the OpenAI-format tool schemas for registered MCP tools.

    Agent containers call this at startup to discover what tools they
    can request.
    """
    from services.agenthub.orchestrator_engine import get_orchestrator_engine

    engine = get_orchestrator_engine()
    if engine.mcp_manager:
        return engine.mcp_manager.get_openai_tools_schema()
    return []
