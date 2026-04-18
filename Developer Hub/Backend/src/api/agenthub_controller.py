"""AgentHub REST API — jobs, agents, orchestration, SSE events."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from domain.models.agent_models import (
    AgentConfigRequest,
    ApprovePlanRequest,
    CreateJobRequest,
    GeneratePlanRequest,
    Job,
    JobStatus,
    SendMessageRequest,
    UserAgentConfig,
)
from services.agenthub import session_store
from services.agenthub.agent_registry import get_template, list_templates
from services.agenthub.orchestrator_engine import get_orchestrator_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["AgentHub"])


# ── Helpers ──────────────────────────────────────────────────────────

def _user_id_from_request(request: Request) -> str:
    """Extract a user identifier.  Falls back to 'anonymous'."""
    # In production this comes from the decoded Fabric/OBO token.
    # For now extract from Authorization header hash.
    auth = request.headers.get("Authorization", "")
    if auth:
        return f"user-{abs(hash(auth)) % 100000:05d}"
    return "anonymous"


async def _copilot_token(request: Request) -> str:
    """Get a Copilot token from the GitHub auth header."""
    from api.github_chat_controller import _extract_github_token, _get_copilot_token
    github_token = _extract_github_token(request)
    return await _get_copilot_token(github_token)


async def _mcp_tokens(request: Request) -> dict | None:
    """OBO exchange if Fabric token is present."""
    fabric_header = request.headers.get("X-Fabric-Token", "")
    fabric_token = fabric_header.removeprefix("Bearer ").strip() or None
    if not fabric_token:
        return None
    from api.github_chat_controller import _acquire_mcp_tokens
    return await _acquire_mcp_tokens(fabric_token)


# ── Workspace listing ────────────────────────────────────────────────

@router.get("/workspaces")
async def list_workspaces(request: Request):
    """List Fabric workspaces the user has access to (for workspace selector)."""
    mcp_tokens = await _mcp_tokens(request)
    if not mcp_tokens:
        raise HTTPException(400, "Fabric token required to list workspaces")

    from api.github_chat_controller import _mcp_manager
    if not _mcp_manager:
        raise HTTPException(503, "MCP manager not available")

    try:
        import json
        result = await _mcp_manager.call_tool("fabric_list_workspaces", {}, mcp_tokens)
        workspaces = json.loads(str(result))
        return [{"id": w.get("id"), "name": w.get("displayName", w.get("id"))} for w in workspaces]
    except HTTPException:
        raise
    except Exception as e:
        # Don't echo the underlying exception message back to the user — it can
        # leak internal SDK details. Log full traceback server-side.
        logger.exception("Failed to list workspaces")
        raise HTTPException(500, "Failed to list workspaces") from e


# ── Session endpoints ────────────────────────────────────────────────

@router.post("/sessions")
async def create_session(req: CreateJobRequest, request: Request):
    """Create a new session and generate an execution plan."""
    user_id = _user_id_from_request(request)
    copilot_token = await _copilot_token(request)

    plan = await get_orchestrator_engine().generate_plan(
        req.task_description, req.workspace_id, copilot_token, req.context,
    )

    job = Job(
        id=plan.job_id,
        user_id=user_id,
        workspace_id=req.workspace_id,
        task_description=req.task_description,
        context=req.context,
        status=JobStatus.PLANNED,
        plan=plan,
    )
    session_store.create_session(job)
    logger.info("[AGENTHUB] Session %s created — plan: %s", job.id, plan.summary[:100])
    return job.model_dump(mode="json")


@router.get("/sessions")
async def list_sessions(request: Request, status: str | None = None, limit: int = 50):
    user_id = _user_id_from_request(request)
    jobs = session_store.list_sessions(user_id, status=status, limit=limit)
    return [j.model_dump(mode="json") for j in jobs]


@router.get("/sessions/{session_id}")
async def get_session(session_id: UUID):
    job = session_store.get_session(str(session_id))
    if not job:
        raise HTTPException(404, "Session not found")
    return job.model_dump(mode="json")


@router.delete("/sessions/{session_id}")
async def cancel_or_delete_session(session_id: UUID):
    session_id_str = str(session_id)
    job = session_store.get_session(session_id_str)
    if not job:
        raise HTTPException(404, "Session not found")

    if job.status == JobStatus.RUNNING:
        get_orchestrator_engine().cancel_job(session_id_str)
        job.status = JobStatus.CANCELLED
        job.completed_at = datetime.now(UTC)
        session_store.update_session(job)
        return {"status": "cancelled"}
    else:
        session_store.delete_session(session_id_str)
        return {"status": "deleted"}


@router.post("/sessions/{session_id}/message")
async def send_message_to_session(session_id: UUID, req: SendMessageRequest):
    ok = get_orchestrator_engine().inject_message(str(session_id), req.message, req.target_agent_id)
    if not ok:
        raise HTTPException(404, "Session not running or not found")
    return {"status": "sent"}


@router.get("/sessions/{session_id}/events")
async def session_events_sse(session_id: UUID):
    """SSE stream of real-time session events."""
    execution = get_orchestrator_engine().get_job_execution(str(session_id))
    if not execution:
        raise HTTPException(404, "No active execution for this session")

    async def event_stream():
        async for ev in execution.events():
            yield f"data: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Orchestration endpoints ──────────────────────────────────────────

@router.post("/orchestrate/plan")
async def generate_plan_endpoint(req: GeneratePlanRequest, request: Request):
    """Generate an execution plan without creating a session."""
    copilot_token = await _copilot_token(request)
    plan = await get_orchestrator_engine().generate_plan(
        req.task_description, req.workspace_id, copilot_token, req.context,
    )
    return plan.model_dump(mode="json")


@router.post("/orchestrate/approve")
async def approve_plan(req: ApprovePlanRequest, request: Request):
    """Approve a planned session and start execution."""
    job = session_store.get_session(req.session_id)
    if not job:
        raise HTTPException(404, "Session not found")
    if job.status != JobStatus.PLANNED:
        raise HTTPException(400, f"Session is {job.status.value}, not planned")

    job.status = JobStatus.APPROVED
    session_store.update_session(job)

    copilot_token = await _copilot_token(request)
    mcp_tokens = await _mcp_tokens(request)

    await get_orchestrator_engine().start_job(job, copilot_token, mcp_tokens)
    return {"status": "running", "session_id": job.id}


@router.post("/orchestrate/reject")
async def reject_plan(req: ApprovePlanRequest):
    job = session_store.get_session(req.session_id)
    if not job:
        raise HTTPException(404, "Session not found")
    job.status = JobStatus.CANCELLED
    job.completed_at = datetime.now(UTC)
    session_store.update_session(job)
    return {"status": "rejected"}


# ── Agent template & config endpoints ────────────────────────────────

@router.get("/agents")
async def list_agent_templates():
    return [t.model_dump() for t in list_templates()]


@router.get("/agents/{agent_id}")
async def get_agent_template(agent_id: str):
    t = get_template(agent_id)
    if not t:
        raise HTTPException(404, "Agent template not found")
    return t.model_dump()


@router.post("/agents/configure")
async def configure_agent(req: AgentConfigRequest, request: Request):
    user_id = _user_id_from_request(request)
    config = UserAgentConfig(
        id=str(uuid.uuid4()),
        user_id=user_id,
        agent_template_id=req.agent_template_id,
        access_levels=req.access_levels,
        tool_integrations=req.tool_integrations,
        runtime_schedule=req.runtime_schedule,
        custom_prompt_additions=req.custom_prompt_additions,
    )
    session_store.save_agent_config(config)
    return config.model_dump(mode="json")


@router.get("/agents/my")
async def my_agents(request: Request):
    user_id = _user_id_from_request(request)
    configs = session_store.get_user_agent_configs(user_id)
    # Enrich with template info
    result = []
    for c in configs:
        t = get_template(c.agent_template_id)
        result.append({
            **c.model_dump(mode="json"),
            "template": t.model_dump() if t else None,
        })
    return result


@router.delete("/agents/my/{config_id}")
async def delete_my_agent(config_id: UUID):
    ok = session_store.delete_agent_config(str(config_id))
    if not ok:
        raise HTTPException(404, "Config not found")
    return {"status": "deleted"}


# ── Audit ─────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/audit")
async def get_audit(session_id: UUID):
    return session_store.get_audit_log(str(session_id))
