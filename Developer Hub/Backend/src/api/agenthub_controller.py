"""AgentHub REST API — jobs, agents, orchestration, SSE events."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from api import github_chat_controller
from domain.constants.workload_scopes import WorkloadScopes
from domain.exceptions.exceptions import AuthenticationException
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
from domain.models.authentication_models import AuthorizationContext
from domain.models.plan import PlanValidationError
from services.agenthub import session_store, workspaces_cache
from services.agenthub.agent_registry import get_template, list_templates
from services.agenthub.download_tokens import consume_token, issue_token
from services.agenthub.orchestrator_engine import get_orchestrator_engine
from services.agenthub.rate_limit import RateLimitExceeded
from services.agenthub.rate_limit import acquire as rate_limit_acquire
from services.auth.authentication import get_authentication_service
from services.configuration_service import get_configuration_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["AgentHub"])


# ── Auth helpers ─────────────────────────────────────────────────────

_DEV_USER_KEY = "anonymous"


def _user_key_from_context(ctx: AuthorizationContext | None) -> str:
    """Derive a stable, immutable user key from a **validated** auth context.

    Prefers ``oid`` (Entra object id, never changes for a given user) so
    session ownership is stable across UPN renames. Falls back to a UPN-like
    claim only when ``oid`` is absent. In developer/test environments where
    no auth context is present, returns a single shared dev user key.
    """
    if ctx is None:
        return _DEV_USER_KEY
    oid = ctx.object_id
    if oid:
        return f"oid:{oid}"
    for claim in ctx.claims:
        if claim.type in ("upn", "preferred_username", "email", "unique_name"):
            val = claim.value
            if val:
                return str(val).lower()
    return _DEV_USER_KEY


def _user_upn_from_context(ctx: AuthorizationContext | None) -> str | None:
    """Extract a human-readable UPN / email from the auth context.

    Stored alongside the oid-based ``user_id`` purely for human inspection
    of DB rows and logs. Never used as an identity key (UPNs can change;
    ``oid`` cannot).
    """
    if ctx is None:
        return None
    for claim_type in ("upn", "preferred_username", "email", "unique_name"):
        for claim in ctx.claims:
            if claim.type == claim_type and claim.value:
                return str(claim.value)
    return None


async def require_user(request: Request) -> AuthorizationContext | None:
    """Authenticate the caller via the Fabric bearer token.

    In production the Fabric JWT is fully validated (JWKS signature, audience,
    issuer, expiry, allowed scopes) by
    ``AuthenticationService.authenticate_data_plane_call``. Validation failures
    return 401 to the caller.

    In a developer/test environment (``Application.Debug`` is ``true`` **and**
    the auth service is not configured) this returns ``None`` so routes can
    degrade to the shared anonymous dev user. Session-scoped routes still
    enforce ownership against the key derived from the returned context.
    """
    fabric_header = request.headers.get("X-Fabric-Token", "")
    fabric_token = fabric_header.removeprefix("Bearer ").strip()
    if not fabric_token:
        fabric_header = request.headers.get("Authorization", "")
        fabric_token = fabric_header.removeprefix("Bearer ").strip()

    config = get_configuration_service()

    if not fabric_token:
        if config.is_production():
            raise HTTPException(401, "Missing Fabric bearer token")
        return None

    try:
        auth_service = get_authentication_service()
    except Exception as exc:
        if config.is_production():
            logger.error("Auth service unavailable in production: %s", exc)
            raise HTTPException(503, "Auth service unavailable") from exc
        return None

    try:
        return await auth_service.authenticate_data_plane_call(
            f"Bearer {fabric_token}",
            allowed_scopes=[
                WorkloadScopes.AGENTHUB_READ_ALL,
                WorkloadScopes.AGENTHUB_READ_WRITE_ALL,
                # Legacy aliases — see workload_scopes.py.
                WorkloadScopes.ITEM1_READ_ALL,
                WorkloadScopes.ITEM1_READ_WRITE_ALL,
            ],
        )
    except AuthenticationException as exc:
        logger.warning("Rejected invalid Fabric token: %s", exc)
        raise HTTPException(401, "Invalid Fabric token") from exc
    except Exception as exc:
        if config.is_production():
            logger.exception("Auth validation error")
            raise HTTPException(401, "Invalid Fabric token") from exc
        logger.debug("Auth validation soft-failed in dev: %s", exc)
        return None


def _ensure_owner(job: Job | None, user_key: str) -> Job:
    """Return ``job`` if it exists and is owned by the caller; else raise 404.

    Always returns a uniform 404 (never 403) so a caller cannot probe the
    existence of other users' sessions by observing status-code differences.
    """
    if not job or job.user_id != user_key:
        raise HTTPException(404, "Session not found")
    return job


def _rate_limit(user_key: str, action: str) -> None:
    """Apply the per-user token bucket for an expensive action.

    Raises ``HTTPException(429)`` with a ``Retry-After`` header when the
    bucket is empty. Silently returns for the anonymous dev user so local
    tests and dev-mode exploration are not throttled.
    """
    try:
        rate_limit_acquire(user_key, action)
    except RateLimitExceeded as exc:
        # Round up so callers never see fractional retry values.
        retry_after = max(1, int(exc.retry_after + 0.5))
        raise HTTPException(
            status_code=429,
            detail=f"Too many {action} requests; retry in {retry_after}s",
            headers={"Retry-After": str(retry_after)},
        ) from exc


async def _copilot_token(request: Request) -> str:
    """Get a Copilot token from the GitHub auth header."""
    github_token = github_chat_controller._extract_github_token(request)
    return await github_chat_controller._get_copilot_token(github_token)


async def _mcp_tokens(request: Request) -> dict | None:
    """OBO exchange if Fabric token is present."""
    fabric_header = request.headers.get("X-Fabric-Token", "")
    fabric_token = fabric_header.removeprefix("Bearer ").strip() or None
    if not fabric_token:
        return None
    return await github_chat_controller._acquire_mcp_tokens(fabric_token)


# ── Workspace listing ────────────────────────────────────────────────

async def _fetch_and_reconcile_workspaces(user_id: str, mcp_tokens: dict, user_upn: str | None = None) -> list[dict]:
    """Call Fabric, normalize, and reconcile the per-user workspace cache."""
    if not github_chat_controller._mcp_manager:
        raise HTTPException(503, "MCP manager not available")
    result = await github_chat_controller._mcp_manager.call_tool(
        "fabric_list_workspaces",
        {},
        mcp_tokens,
        # No workspace_id binding here — this call discovers the list of
        # workspaces available to the caller. No path args either. The
        # policy validator is still invoked and is a no-op for empty args.
        allowed_tools={"fabric_list_workspaces"},
    )
    raw = json.loads(str(result))
    fresh = [
        {"id": w.get("id"), "name": w.get("displayName", w.get("id"))}
        for w in raw if w.get("id")
    ]
    workspaces_cache.reconcile(user_id, fresh, user_upn=user_upn)
    return fresh


async def _probe_git_status_one(
    user_id: str, workspace_id: str, mcp_tokens: dict,
) -> None:
    """Probe one workspace's git connection and persist the result.

    Never raises — errors are swallowed so one bad workspace does not
    abort the batch. A 404 from Fabric means "not git-connected" and is
    stored as ``git_connected=False``.
    """
    try:
        mgr = github_chat_controller._mcp_manager
        if mgr is None:
            # MCP not initialised — treat as not-git-connected (same outcome as a 404).
            workspaces_cache.update_git_status(user_id, workspace_id, connected=False)
            return
        raw = await mgr.call_tool(
            "sl_get_git_connection",
            {"workspace_id": workspace_id},
            mcp_tokens,
            allowed_tools={"sl_get_git_connection"},
            workspace_id=workspace_id,
        )
        body = str(raw)
        # mcp tool surfaces non-200 as a plaintext "HTTP 404: …" string.
        # Anything that is not valid JSON means the workspace is not
        # git-connected (or the call was denied) — treat as False.
        try:
            data = json.loads(body)
        except Exception:
            workspaces_cache.update_git_status(user_id, workspace_id, connected=False)
            return
        # Fabric response shape varies but typically contains a
        # ``gitProviderDetails`` and a ``gitConnectionState``.
        details = data.get("gitProviderDetails") or {}
        provider = details.get("gitProviderType") or data.get("gitProviderType")
        branch = details.get("branchName") or data.get("branchName")
        repo = (
            details.get("repositoryName")
            or details.get("directoryName")
            or data.get("repositoryName")
        )
        # A non-connected workspace returns an empty ``gitProviderDetails``
        # (or gitConnectionState == "NotConnected"). Treat those as False.
        state = data.get("gitConnectionState") or ""
        connected = bool(details) and state != "NotConnected"
        workspaces_cache.update_git_status(
            user_id, workspace_id,
            connected=connected, provider=provider,
            branch=branch, repo_name=repo,
        )
    except Exception:
        logger.debug(
            "git status probe failed for workspace=%s (non-fatal)",
            workspace_id, exc_info=True,
        )


async def _probe_git_status_batch(
    user_id: str, workspace_ids: list[str], mcp_tokens: dict, max_concurrency: int = 5,
) -> None:
    """Probe git status for many workspaces with bounded concurrency.

    Fabric's per-workspace ``git/connection`` endpoint is cheap on the
    server side, but opening 50 connections at once against a tenant that
    may have per-user quotas is unkind. A small semaphore keeps the probe
    polite.
    """
    sem = asyncio.Semaphore(max_concurrency)

    async def _run(wid: str) -> None:
        async with sem:
            await _probe_git_status_one(user_id, wid, mcp_tokens)

    await asyncio.gather(*(_run(w) for w in workspace_ids), return_exceptions=True)


async def _background_refresh_workspaces(user_id: str, fabric_token: str, user_upn: str | None = None) -> None:
    """Best-effort background refresh — never raises into the request.

    Does the OBO exchange itself so the /preload request handler can return
    immediately without blocking the event loop on MSAL. After the list
    reconcile, probes git-connection status for any workspace whose
    cached git status is missing or stale.
    """
    try:
        mcp_tokens = await github_chat_controller._acquire_mcp_tokens(fabric_token)
        if not mcp_tokens:
            return
        # Reconcile the workspace cache — side-effect only; the returned
        # list is not needed here because we re-read the cache below to
        # pick up git-status columns that the reconcile helper also updates.
        await _fetch_and_reconcile_workspaces(user_id, mcp_tokens, user_upn=user_upn)
        # Probe git status for every workspace whose cached entry is stale
        # or missing. We re-read the cache so the probe list reflects the
        # state AFTER reconcile (e.g. new rows).
        cached, _ = workspaces_cache.get_cached(user_id)
        to_probe = [
            w.workspace_id for w in cached
            if not workspaces_cache.git_status_is_fresh(w.git_checked_at)
        ]
        if to_probe:
            logger.info(
                "Probing git status for %d workspace(s) for user=%s",
                len(to_probe), user_id,
            )
            await _probe_git_status_batch(user_id, to_probe, mcp_tokens)
    except Exception:
        logger.warning("Background workspace refresh failed for user=%s", user_id, exc_info=True)


@router.get("/workspaces")
async def list_workspaces(
    request: Request,
    background_tasks: BackgroundTasks,
    refresh: bool = Query(False, description="Force a synchronous refresh from Fabric."),
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """List Fabric workspaces for the workspace selector.

    Returns the cached list when fresh (TTL = workspaces_cache.CACHE_TTL).
    When stale, fetches from Fabric and reconciles the cache (insert / update
    / delete). Pass ``?refresh=true`` to force a synchronous refresh.

    Each workspace entry includes git-integration fields
    (``git_connected``, ``git_provider``, ``git_branch``, ``git_repo_name``)
    so the frontend can filter "branch-out source" candidates to only
    git-connected workspaces. These are populated asynchronously by a
    background probe — unprobed workspaces have ``git_connected=None``.
    """
    def _serialize(rows):
        return [
            {
                "id": w.workspace_id,
                "name": w.workspace_name,
                "git_connected": w.git_connected,
                "git_provider": w.git_provider,
                "git_branch": w.git_branch,
                "git_repo_name": w.git_repo_name,
            }
            for w in rows
        ]

    user_id = _user_key_from_context(ctx)
    user_upn = _user_upn_from_context(ctx)

    cached, newest = workspaces_cache.get_cached(user_id)
    cache_fresh = workspaces_cache.is_fresh(newest)

    # Cached and not asked to refresh → serve cache without doing OBO.
    if cached and cache_fresh and not refresh:
        logger.debug("workspaces cache hit user=%s count=%d", user_id, len(cached))
        # Kick off a background git-status probe for any workspaces that
        # are unprobed or whose git status has aged out. Only do this when
        # a Fabric token is already attached (no OBO latency on cache hit).
        stale_git = [
            w.workspace_id for w in cached
            if not workspaces_cache.git_status_is_fresh(w.git_checked_at)
        ]
        if stale_git:
            fabric_token = request.headers.get("x-fabric-token") or request.headers.get("x-ms-workload-resource-token")
            if fabric_token:
                async def _bg_probe_only(uid: str, tok: str, wids: list[str]) -> None:
                    try:
                        tokens = await github_chat_controller._acquire_mcp_tokens(tok)
                        if tokens:
                            await _probe_git_status_batch(uid, wids, tokens)
                    except Exception:
                        logger.debug("background git probe failed", exc_info=True)
                background_tasks.add_task(_bg_probe_only, user_id, fabric_token, stale_git)
        return {
            "workspaces": _serialize(cached),
            "cached_at": newest.isoformat() if newest else None,
            "source": "cache",
        }

    # Cache miss / stale / forced refresh — now we need a Fabric token.
    mcp_tokens = await _mcp_tokens(request)
    if not mcp_tokens:
        if cached:
            # Serve stale cache rather than fail when token is missing.
            return {
                "workspaces": _serialize(cached),
                "cached_at": newest.isoformat() if newest else None,
                "source": "stale-cache",
            }
        raise HTTPException(400, "Fabric token required to list workspaces")

    try:
        await _fetch_and_reconcile_workspaces(user_id, mcp_tokens, user_upn=user_upn)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to list workspaces")
        if cached:
            return {
                "workspaces": _serialize(cached),
                "cached_at": newest.isoformat() if newest else None,
                "source": "stale-cache",
            }
        raise HTTPException(500, "Failed to list workspaces") from e

    # Re-read from cache so the response picks up any pre-existing git
    # fields for workspaces that survived reconcile. Fire a background
    # git-status probe for unprobed/stale entries.
    refreshed, _ = workspaces_cache.get_cached(user_id)
    to_probe = [
        w.workspace_id for w in refreshed
        if not workspaces_cache.git_status_is_fresh(w.git_checked_at)
    ]
    if to_probe:
        background_tasks.add_task(
            _probe_git_status_batch, user_id, to_probe, mcp_tokens,
        )

    return {
        "workspaces": _serialize(refreshed),
        "cached_at": datetime.now(UTC).isoformat(),
        "source": "refreshed",
    }


@router.post("/workspaces/preload")
async def preload_workspaces(
    request: Request,
    background_tasks: BackgroundTasks,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Schedule a background refresh of the user's workspaces.

    Called by the frontend right after GitHub auth so the workspace selector
    is instant on the user's first navigation. Returns immediately — the
    OBO exchange happens inside the background task so the event loop is
    not blocked on MSAL during page load.
    """
    user_id = _user_key_from_context(ctx)
    user_upn = _user_upn_from_context(ctx)
    fabric_header = request.headers.get("X-Fabric-Token", "")
    fabric_token = fabric_header.removeprefix("Bearer ").strip()
    if not fabric_token:
        # Without a Fabric token we can't fetch — ignore quietly so the call
        # is safe to make unconditionally from the frontend.
        return {"status": "skipped", "reason": "no fabric token"}
    background_tasks.add_task(_background_refresh_workspaces, user_id, fabric_token, user_upn)
    return {"status": "scheduled"}


class CreateWorkspaceRequest(BaseModel):
    """Body for ``POST /api/workspaces`` — create a workspace on the fly."""

    display_name: str = Field(
        min_length=1, max_length=200,
        description="Human-readable name for the new workspace.",
    )
    description: str | None = Field(default=None, max_length=500)
    capacity_id: str | None = Field(
        default=None,
        description="Optional capacity UUID. If omitted, the workspace "
        "is created without a capacity attached.",
    )


@router.post("/workspaces")
async def create_workspace(
    req: CreateWorkspaceRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Create a new Fabric workspace and add it to the caller's cache.

    The user's Fabric token (via OBO) authorizes the creation. On success
    the local workspace cache is updated so the next ``GET /workspaces``
    call returns the new workspace immediately without waiting for the
    reconcile TTL.
    """
    user_id = _user_key_from_context(ctx)
    _rate_limit(user_id, "create_session")  # reuse the conservative budget
    mcp_tokens = await _mcp_tokens(request)
    if not mcp_tokens:
        raise HTTPException(400, "Fabric token required to create a workspace")
    if not github_chat_controller._mcp_manager:
        raise HTTPException(503, "MCP manager not available")

    args: dict = {"display_name": req.display_name.strip()}
    if req.description:
        args["description"] = req.description.strip()
    if req.capacity_id:
        args["capacity_id"] = req.capacity_id.strip()

    try:
        raw = await github_chat_controller._mcp_manager.call_tool(
            "fabric_create_workspace",
            args,
            mcp_tokens,
            allowed_tools={"fabric_create_workspace"},
        )
    except Exception as exc:
        logger.exception("fabric_create_workspace failed")
        raise HTTPException(502, f"Failed to create workspace: {exc}") from exc

    body = str(raw)
    try:
        data = json.loads(body)
    except Exception as e:
        # MCP surfaces non-2xx as plaintext; propagate as a 400/502.
        logger.warning("fabric_create_workspace returned non-JSON: %s", body[:200])
        if "HTTP 40" in body:
            raise HTTPException(400, body) from e
        raise HTTPException(502, body or "Workspace creation failed") from e

    workspace_id = data.get("id")
    display_name = data.get("displayName") or req.display_name
    if not workspace_id:
        raise HTTPException(502, f"Fabric returned no workspace id: {body}")

    # Insert the new workspace into the local cache so the dropdown sees
    # it immediately. reconcile() is additive — it only removes rows
    # that are NOT in ``fresh``, so we include the full current cache
    # plus the new row.
    cached, _ = workspaces_cache.get_cached(user_id)
    fresh = [{"id": w.workspace_id, "name": w.workspace_name} for w in cached]
    if workspace_id not in {w["id"] for w in fresh}:
        fresh.append({"id": workspace_id, "name": display_name})
    user_upn = _user_upn_from_context(ctx)
    workspaces_cache.reconcile(user_id, fresh, user_upn=user_upn)

    return {
        "id": workspace_id,
        "name": display_name,
        "git_connected": False,
        "git_provider": None,
        "git_branch": None,
        "git_repo_name": None,
    }


# ── Session endpoints ────────────────────────────────────────────────

def _persist_context_with_attachments(
    context: dict | None,
    attachments: list | None,
) -> dict | None:
    """Fold prompt attachments into the persisted session context.

    We keep full ``content`` so a later "Recent prompts" click can fully
    restore the compose form (including file bytes). The list endpoint
    strips this content back out before sending it over the wire so the
    Recent-prompts list itself stays cheap; a single click then fetches the
    one session with full attachments via ``GET /api/sessions/{id}``.
    """
    if not attachments:
        return context
    new_ctx = dict(context) if context else {}
    new_ctx["prompt_attachments"] = [a.model_dump() for a in attachments]
    return new_ctx


def _strip_attachment_content(job_dump: dict) -> dict:
    """Return a copy of ``job_dump`` where ``prompt_attachments[*].content``
    is dropped. Used by the list endpoint to keep the response small — the
    frontend fetches full bytes on demand via ``GET /api/sessions/{id}``.
    """
    ctx = job_dump.get("context")
    if not isinstance(ctx, dict):
        return job_dump
    attachments = ctx.get("prompt_attachments")
    if not isinstance(attachments, list):
        return job_dump
    lite = [
        {k: v for k, v in a.items() if k != "content"} if isinstance(a, dict) else a
        for a in attachments
    ]
    new_ctx = dict(ctx)
    new_ctx["prompt_attachments"] = lite
    new_dump = dict(job_dump)
    new_dump["context"] = new_ctx
    return new_dump


def _serialize_job(job: Job) -> dict:
    """Marshal a :class:`Job` to the UI wire shape.

    Job-level fields stay snake_case (the existing frontend reads them as
    ``task_description`` / ``workspace_id`` / etc.), but the nested
    ``plan`` uses camelCase aliases. We serialize
    them separately and stitch.
    """
    data = job.model_dump(mode="json")
    if job.plan is not None:
        data["plan"] = job.plan.model_dump(mode="json", by_alias=True)
    return data


@router.post("/sessions")
async def create_session(
    req: CreateJobRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Create a new session and generate an execution plan."""
    user_id = _user_key_from_context(ctx)
    user_upn = _user_upn_from_context(ctx)
    _rate_limit(user_id, "create_session")
    copilot_token = await _copilot_token(request)
    mcp_tokens = await _mcp_tokens(request)

    try:
        plan = await get_orchestrator_engine().generate_plan(
            req.task_description, req.workspace_id, copilot_token, req.context,
            attachments=req.attachments,
            mcp_tokens=mcp_tokens,
        )
    except PlanValidationError as e:
        # Spec §3.2: surface as structured 422 so the UI renders the
        # "Plan could not be generated" empty state rather than a blank
        # card from a silent fallback.
        raise HTTPException(
            status_code=422,
            detail={"error": "plan_validation_failed", "reason": e.reason, **e.details},
        ) from e

    persisted_context = _persist_context_with_attachments(req.context, req.attachments)

    job = Job(
        id=plan.job_id,
        user_id=user_id,
        user_upn=user_upn,
        workspace_id=req.workspace_id,
        task_description=req.task_description,
        context=persisted_context,
        status=JobStatus.PLANNED,
        plan=plan,
    )
    session_store.create_session(job)
    logger.info("[AGENTHUB] Session %s created — plan: %s", job.id, plan.summary[:100])
    return _serialize_job(job)


@router.get("/sessions")
async def list_sessions(
    request: Request,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """List sessions owned by the caller, newest first.

    Supports ``limit`` + ``offset`` for the Recent-prompts infinite-scroll UI.
    Attachment bytes are stripped from the response; callers that need full
    attachment content should fetch the individual session via
    ``GET /api/sessions/{id}``.
    """
    user_id = _user_key_from_context(ctx)
    jobs = session_store.list_sessions(user_id, status=status, limit=limit, offset=offset)
    return [_strip_attachment_content(_serialize_job(j)) for j in jobs]


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: UUID,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    job = _ensure_owner(session_store.get_session(str(session_id)), _user_key_from_context(ctx))
    return _serialize_job(job)


@router.delete("/sessions/{session_id}")
async def cancel_session(
    session_id: UUID,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Cancel a session.

    The old behaviour was "cancel if running, else delete". The record is
    now always preserved so the Recent-Sessions list can show a stopped
    session along with who cancelled it and when. Already-terminal
    sessions (completed / failed / previously cancelled) are returned
    unchanged.
    """
    session_id_str = str(session_id)
    user_key = _user_key_from_context(ctx)
    job = _ensure_owner(session_store.get_session(session_id_str), user_key)

    # Already terminal — nothing to do. We still return 200 so the UI's
    # optimistic update doesn't have to special-case double-clicks.
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        return {"status": job.status.value, "already_terminal": True}

    # Stop any live execution first. Safe to call even if the job isn't
    # actually running — the engine no-ops for unknown ids.
    if job.status == JobStatus.RUNNING:
        try:
            get_orchestrator_engine().cancel_job(session_id_str)
        except Exception:
            logger.warning("cancel_job raised for %s", session_id_str, exc_info=True)

    now = datetime.now(UTC)
    job.status = JobStatus.CANCELLED
    job.completed_at = now
    job.cancelled_at = now
    job.cancelled_by_user_id = user_key
    job.cancelled_by_upn = _user_upn_from_context(ctx)
    session_store.update_session(job)
    return {"status": "cancelled"}


@router.post("/sessions/{session_id}/message")
async def send_message_to_session(
    session_id: UUID,
    req: SendMessageRequest,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    user_key = _user_key_from_context(ctx)
    _rate_limit(user_key, "send_message")
    _ensure_owner(session_store.get_session(str(session_id)), user_key)
    ok = get_orchestrator_engine().inject_message(str(session_id), req.message, req.target_agent_id)
    if not ok:
        raise HTTPException(404, "Session not running or not found")
    return {"status": "sent"}


@router.get("/sessions/{session_id}/events")
async def session_events_sse(
    session_id: UUID,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """SSE stream of real-time session events."""
    _ensure_owner(session_store.get_session(str(session_id)), _user_key_from_context(ctx))
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
async def generate_plan_endpoint(
    req: GeneratePlanRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Generate an execution plan without creating a session."""
    _rate_limit(_user_key_from_context(ctx), "generate_plan")
    copilot_token = await _copilot_token(request)
    mcp_tokens = await _mcp_tokens(request)
    try:
        plan = await get_orchestrator_engine().generate_plan(
            req.task_description, req.workspace_id, copilot_token, req.context,
            attachments=req.attachments,
            mcp_tokens=mcp_tokens,
        )
    except PlanValidationError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "plan_validation_failed", "reason": e.reason, **e.details},
        ) from e
    return plan.model_dump(mode="json", by_alias=True)


@router.post("/orchestrate/approve")
async def approve_plan(
    req: ApprovePlanRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Approve a planned session and start execution."""
    user_key = _user_key_from_context(ctx)
    _rate_limit(user_key, "approve_plan")
    job = _ensure_owner(session_store.get_session(req.session_id), user_key)
    if job.status != JobStatus.PLANNED:
        raise HTTPException(400, f"Session is {job.status.value}, not planned")

    job.status = JobStatus.APPROVED
    session_store.update_session(job)

    copilot_token = await _copilot_token(request)
    mcp_tokens = await _mcp_tokens(request)

    await get_orchestrator_engine().start_job(job, copilot_token, mcp_tokens)
    return {"status": "running", "session_id": job.id}


@router.post("/orchestrate/reject")
async def reject_plan(
    req: ApprovePlanRequest,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    job = _ensure_owner(session_store.get_session(req.session_id), _user_key_from_context(ctx))
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
async def configure_agent(
    req: AgentConfigRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    user_id = _user_key_from_context(ctx)
    user_upn = _user_upn_from_context(ctx)
    config = UserAgentConfig(
        id=str(uuid.uuid4()),
        user_id=user_id,
        user_upn=user_upn,
        agent_template_id=req.agent_template_id,
        access_levels=req.access_levels,
        tool_integrations=req.tool_integrations,
        runtime_schedule=req.runtime_schedule,
        custom_prompt_additions=req.custom_prompt_additions,
    )
    session_store.save_agent_config(config)
    return config.model_dump(mode="json")


@router.get("/agents/my")
async def my_agents(
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    user_id = _user_key_from_context(ctx)
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
async def delete_my_agent(
    config_id: UUID,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    user_id = _user_key_from_context(ctx)
    existing = session_store.get_agent_config(str(config_id))
    if not existing or existing.user_id != user_id:
        raise HTTPException(404, "Config not found")
    ok = session_store.delete_agent_config(str(config_id))
    if not ok:
        raise HTTPException(404, "Config not found")
    return {"status": "deleted"}


# ── Audit ─────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/audit")
async def get_audit(
    session_id: UUID,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    _ensure_owner(session_store.get_session(str(session_id)), _user_key_from_context(ctx))
    return session_store.get_audit_log(str(session_id))


# ── Attachment download (sandbox-safe) ────────────────────────────────
#
# Fabric embeds our workload in a cross-origin iframe sandbox that lacks
# ``allow-downloads`` / ``allow-popups``, so every in-frame save path
# (``<a download>``, ``window.open``, ``showSaveFilePicker``) is blocked
# by the browser. The only escape is
# ``workloadClient.navigation.openBrowserTab({url})`` — which opens an
# http(s) URL in a fresh top-level tab *outside* the sandbox.
#
# So the frontend POSTs attachment bytes here (authenticated), gets a
# short-lived one-shot URL back, and calls ``openBrowserTab`` on it.
# The new tab hits the GET endpoint, receives the bytes with
# ``Content-Disposition: attachment``, and the browser triggers its
# native Save flow. The bytes leave server memory on first GET.


class _AttachmentDownloadRequest(BaseModel):
    """Client payload for minting a single-use download URL."""

    name: str = Field(min_length=1, max_length=256)
    mime: str = Field(default="application/octet-stream", max_length=128)
    # Content cap matches the inline-attachment limit elsewhere in the
    # model layer (see ``agent_models._MAX_ATTACHMENT_CONTENT_LEN``).
    content: str = Field(max_length=14 * 1024 * 1024)


@router.post("/attachments/download-token")
async def mint_attachment_download_token(
    req: _AttachmentDownloadRequest,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Mint a short-lived download token for the given attachment bytes.

    Returns ``{token, url}``. The ``url`` is relative to the workload
    backend; the frontend joins it against its own ``WORKLOAD_BE_URL``
    before calling ``openBrowserTab``.
    """
    user_id = _user_key_from_context(ctx)
    _rate_limit(user_id, "attachment_download")
    try:
        token = await issue_token(req.name, req.mime, req.content)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid attachment content: {exc}") from exc
    return {"token": token, "url": f"/api/attachments/download/{token}"}


@router.get("/attachments/download/{token}")
async def serve_attachment_download(token: str):
    """Serve the bytes behind ``token`` as a one-shot download.

    Intentionally unauthenticated: the token *is* the capability. It is
    consumed on first GET, so a replay won't succeed. 404 for unknown /
    expired / already-consumed tokens — the same code path.
    """
    entry = await consume_token(token)
    if entry is None:
        raise HTTPException(404, "Download token is invalid or expired")

    # Quote the filename per RFC 6266 so non-ASCII names survive the
    # round trip through Save-As.
    from urllib.parse import quote
    safe_name = quote(entry.name, safe="")
    disposition = f'attachment; filename="{entry.name}"; filename*=UTF-8\'\'{safe_name}'

    return Response(
        content=entry.content,
        media_type=entry.mime,
        headers={
            "Content-Disposition": disposition,
            # Prevent any intermediary from caching the bytes — they
            # represent a single user's one-shot download.
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
