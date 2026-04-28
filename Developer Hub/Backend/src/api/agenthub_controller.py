"""AgentHub REST API — jobs, agents, orchestration, SSE events."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from api import github_chat_controller
from domain.catalogs.architectures import ARCHITECTURES
from domain.constants.workload_scopes import WorkloadScopes
from domain.exceptions.exceptions import AuthenticationException
from domain.models.agent_models import (
    AddAgentRequest,
    AgentConfigRequest,
    ApprovePlanRequest,
    ComposeRequest,
    CreateJobRequest,
    Job,
    JobStatus,
    SendMessageRequest,
    UserAgentConfig,
)
from domain.models.authentication_models import AuthorizationContext
from domain.models.composition import AgentSlot, Budget, Composition, CompositionError
from services.agenthub import session_store, workspaces_cache, session_event_store
from services.agenthub.agent_registry import get_template, list_templates
from services.agenthub.attachments import classify_attachments
from services.agenthub.compose_models import rank_compose_models
from services.agenthub.download_tokens import consume_token, issue_token
from services.agenthub.orchestrator_engine import get_orchestrator_engine
from services.agenthub.rate_limit import RateLimitExceeded
from services.agenthub.rate_limit import acquire as rate_limit_acquire
from services.auth.authentication import get_authentication_service
from services.configuration_service import get_configuration_service
from services.correlation import set_session_id, set_user_id
from services.observability import bounded_text, collection_counts, stable_digest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["AgentHub"])


# Per-(user, workspace_id) cache of the workspace-preview payload.
# The cost is one Fabric /items + one /folders round-trip; we keep the
# result hot for a short window so repeated chip-clicks feel instant.
# Entries expire on read after TTL, and a LRU-ish sweep prevents
# unbounded growth on a long-lived process. The cached value now also
# carries the wall-clock ``captured_at`` ISO string so the UI and any
# historical consumer (session snapshot) can show "as-of HH:MM:SS".
_WORKSPACE_ITEMS_TTL_SEC = 60
_workspace_items_cache: dict[tuple[str, str], tuple[float, str, list[dict]]] = {}
_FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
_FABRIC_DEFINITION_COLLECTIONS = {
    "notebook": "notebooks",
    "report": "reports",
    "semanticmodel": "semanticModels",
}


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


def _public_token_error_reason(exc: Exception) -> str:
    """Return a user-safe auth failure reason.

    Keeps details actionable for the UI while avoiding raw stack traces.
    """
    raw = " ".join(str(exc).split())
    if not raw:
        return "token validation failed"
    lower = raw.lower()
    if "decoding token headers" in lower:
        return "token format is invalid (cannot decode JWT headers)"
    if "expired" in lower:
        return "token has expired"
    if "invalid audience" in lower or "audience" in lower:
        return "token audience is invalid"
    if "invalid issuer" in lower or "issuer" in lower:
        return "token issuer is invalid"
    if "missing claim" in lower:
        return "token is missing required claims"
    if len(raw) > 180:
        return raw[:179] + "…"
    return raw


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
    # EventSource (SSE) cannot set custom headers, so allow the Fabric
    # token to be supplied as a query parameter as well.
    if not fabric_token:
        fabric_token = (request.query_params.get("token") or "").strip()

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
        ctx = await auth_service.authenticate_data_plane_call(
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
        reason = _public_token_error_reason(exc)
        # In dev/test mode, soft-fail malformed tokens so the
        # standalone-for-E2E bootstrap (which uses synthetic tokens
        # not issued by Entra ID) can still drive Mission Control.
        if not config.is_production():
            logger.debug("Auth validation soft-failed in dev (malformed token): %s", reason)
            return None
        logger.warning("Rejected invalid Fabric token: %s", exc)
        raise HTTPException(401, f"Invalid Fabric token: {reason}") from exc
    except Exception as exc:
        if config.is_production():
            logger.exception("Auth validation error")
            raise HTTPException(401, "Invalid Fabric token") from exc
        logger.debug("Auth validation soft-failed in dev: %s", exc)
        return None

    # Bind the caller's stable identity so every subsequent log line in
    # this request (and any asyncio-task it spawns — contextvars
    # propagate through ``create_task`` / ``to_thread``) is tagged with
    # ``u:<oid8>``. Background orchestrator tasks inherit this.
    if ctx is not None:
        oid = ctx.object_id
        if oid:
            set_user_id(oid)
        else:
            for claim in ctx.claims:
                if claim.type in ("upn", "preferred_username", "email", "unique_name") and claim.value:
                    set_user_id(str(claim.value))
                    break
    return ctx


async def _require_user_with_correlation(
    request: Request,
) -> AuthorizationContext | None:
    """Deprecated — ``require_user`` now binds ``user_id`` itself."""
    return await require_user(request)


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
        logger.info(
            "[WORKSPACES] cache hit: user=%s count=%d",
            user_id[:12], len(cached),
        )
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


@router.get("/workspaces/{workspace_id}/items")
async def list_workspace_items(
    workspace_id: str,
    request: Request,
    refresh: bool = Query(False, description="Bypass the per-user cache"),
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """List items + folders in a Fabric workspace for the preview modal.

    Fetches ``fabric_list_items`` and ``fabric_list_folders`` in parallel
    and returns a unified, Fabric-UI-ordered list with folders first
    (alphabetical), then items (alphabetical). Result is cached per
    (user, workspace) for a short TTL so subsequent clicks on the same
    chip are instant. Pass ``?refresh=1`` to force a fresh fetch (used
    by the modal's manual Refresh button so stale caches don't hide
    items the user just created).
    """
    user_id = _user_key_from_context(ctx)
    _rate_limit(user_id, "list_workspace_items")

    mcp_tokens = await _mcp_tokens(request)
    if not mcp_tokens:
        raise HTTPException(400, "Fabric token required")

    items, captured_at = await _fetch_workspace_snapshot(
        user_id, workspace_id, mcp_tokens, use_cache=not refresh,
    )
    logger.info(
        "[WORKSPACE-ITEMS] workspace=%s items=%d cached=%s",
        workspace_id[:8], len(items), "no" if refresh else "maybe",
    )
    return {"items": items, "captured_at": captured_at}


def _definition_collection_for(item_type: str) -> str:
    collection = _FABRIC_DEFINITION_COLLECTIONS.get(item_type.replace(" ", "").lower())
    if not collection:
        raise HTTPException(400, f"Unsupported Fabric definition item type: {item_type}")
    return collection


@router.post("/workspaces/{workspace_id}/items/{item_type}/{item_id}/definition")
async def get_workspace_item_definition(
    workspace_id: str,
    item_type: str,
    item_id: str,
    request: Request,
    format: str | None = None,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Return a Fabric item definition using the backend OBO token path."""
    user_id = _user_key_from_context(ctx)
    _rate_limit(user_id, "list_workspace_items")

    mcp_tokens = await _mcp_tokens(request)
    fabric_api_token = (mcp_tokens or {}).get("FABRIC_API_TOKEN")
    if not fabric_api_token:
        raise HTTPException(400, "Fabric API token required")

    collection = _definition_collection_for(item_type)
    url = f"{_FABRIC_API_BASE}/workspaces/{workspace_id}/{collection}/{item_id}/getDefinition"
    if format:
        url = f"{url}?format={format}"
    headers = {"Authorization": f"Bearer {fabric_api_token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0)) as client:
        try:
            response = await client.post(url, headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Failed to request Fabric definition: {exc}") from exc

        if response.status_code == 200:
            return response.json()
        if response.status_code != 202:
            raise HTTPException(
                response.status_code if 400 <= response.status_code < 500 else 502,
                response.text or "Fabric definition request failed",
            )

        location = response.headers.get("Location")
        if not location:
            raise HTTPException(502, "Fabric returned 202 for getDefinition without a Location header")

        delay_sec = max(int(response.headers.get("Retry-After", "3") or "3"), 1)
        for _ in range(60):
            await asyncio.sleep(delay_sec)
            try:
                state_response = await client.get(location, headers=headers)
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"Failed to poll Fabric definition operation: {exc}") from exc
            if state_response.status_code not in {200, 202, 204}:
                raise HTTPException(
                    state_response.status_code if 400 <= state_response.status_code < 500 else 502,
                    state_response.text or "Fabric definition operation poll failed",
                )
            state = state_response.json() if state_response.status_code == 200 and state_response.text else {}
            status = str(state.get("status") or "").lower()
            if status in {"succeeded", "completed"}:
                result_response = await client.get(location.rstrip("/") + "/result", headers=headers)
                if result_response.status_code != 200:
                    raise HTTPException(
                        result_response.status_code if 400 <= result_response.status_code < 500 else 502,
                        result_response.text or "Fabric definition operation result failed",
                    )
                return result_response.json()
            if status in {"failed", "cancelled", "canceled"}:
                raise HTTPException(502, f"Fabric definition operation failed: {json.dumps(state)[:500]}")
            delay_sec = min(int(delay_sec * 1.5), 15)

    raise HTTPException(504, "Fabric definition operation timed out")


_POWERBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"


@router.post("/workspaces/{workspace_id}/semantic-models/{semantic_model_id}/query")
async def query_workspace_semantic_model(
    workspace_id: str,
    semantic_model_id: str,
    request: Request,
    payload: dict | None = Body(default=None),
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Run a DAX query against a Power BI semantic model using the backend OBO Power BI token."""
    user_id = _user_key_from_context(ctx)
    _rate_limit(user_id, "list_workspace_items")

    query_text = (payload or {}).get("query")
    if not isinstance(query_text, str) or not query_text.strip():
        raise HTTPException(400, "DAX query text required in body.query")

    mcp_tokens = await _mcp_tokens(request)
    powerbi_token = (mcp_tokens or {}).get("POWERBI_API_TOKEN")
    if not powerbi_token:
        raise HTTPException(400, "Power BI API token required")

    url = (
        f"{_POWERBI_API_BASE}/groups/{workspace_id}"
        f"/datasets/{semantic_model_id}/executeQueries"
    )
    headers = {"Authorization": f"Bearer {powerbi_token}", "Content-Type": "application/json"}
    body = {"queries": [{"query": query_text}], "serializerSettings": {"includeNulls": True}}

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        try:
            response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Failed to call Power BI executeQueries: {exc}") from exc

        if response.status_code != 200:
            raise HTTPException(
                response.status_code if 400 <= response.status_code < 500 else 502,
                response.text or "Power BI executeQueries failed",
            )

        data = response.json() if response.text else {}
        rows: list[dict] = []
        try:
            results = data.get("results") or []
            if results:
                tables = results[0].get("tables") or []
                if tables:
                    rows = tables[0].get("rows") or []
        except Exception:  # pragma: no cover - defensive
            rows = []
        return {"source": "powerbi_executeQueries", "rows": rows}


async def _fetch_workspace_snapshot(
    user_id: str,
    workspace_id: str,
    mcp_tokens: dict,
    *,
    use_cache: bool = True,
) -> tuple[list[dict], str]:
    """Return ``(items, captured_at_iso)`` for the workspace preview.

    Shared by the live endpoint and session creation (which snapshots
    the workspace state so the Session detail view can later replay
    "how the workspace looked back then"). A click-through in the UI
    should feel instant even though the Fabric API calls take ~1s, so
    we keep a short per-(user, workspace) TTL cache. Pass
    ``use_cache=False`` to force a fresh fetch.
    """
    cache_key = (user_id, workspace_id)
    now = time.time()
    if use_cache:
        cached = _workspace_items_cache.get(cache_key)
        if cached and (now - cached[0]) < _WORKSPACE_ITEMS_TTL_SEC:
            return cached[2], cached[1]

    if not github_chat_controller._mcp_manager:
        raise HTTPException(503, "MCP manager not available")

    mgr = github_chat_controller._mcp_manager

    async def _call_items() -> list[dict]:
        raw = await mgr.call_tool(
            "fabric_list_items",
            {"workspace_id": workspace_id},
            mcp_tokens,
            allowed_tools={"fabric_list_items"},
            workspace_id=workspace_id,
        )
        body = str(raw)
        try:
            data = json.loads(body)
        except Exception:
            if "HTTP 40" in body:
                raise HTTPException(400, body) from None
            raise HTTPException(502, body or "Failed to list items") from None
        return data if isinstance(data, list) else []

    async def _call_folders() -> list[dict]:
        try:
            raw = await mgr.call_tool(
                "fabric_list_folders",
                {"workspace_id": workspace_id},
                mcp_tokens,
                allowed_tools={"fabric_list_folders"},
                workspace_id=workspace_id,
            )
        except Exception:
            # Folders API may not be enabled on older capacities; degrade gracefully.
            return []
        body = str(raw)
        try:
            data = json.loads(body)
        except Exception:
            return []
        return data if isinstance(data, list) else []

    try:
        items_raw, folders_raw = await asyncio.gather(_call_items(), _call_folders())
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("fabric workspace-preview failed workspace=%s", workspace_id)
        raise HTTPException(502, f"Failed to list workspace items: {exc}") from exc

    # Folders are rendered first and are clickable to drill in.
    # ``parentFolderId`` is surfaced so the UI can render nested folders
    # only inside their parent. Items carry ``folderId`` for the same
    # filter on the item side. ``webUrl`` lets the UI render a "View
    # details" link that escapes the iframe sandbox via
    # ``workloadClient.navigation.openBrowserTab``.
    def _owner_of(row: dict) -> str | None:
        # Fabric's /items endpoint doesn't guarantee an owner field; we
        # surface whichever hint is present so the UI can fall back to
        # blank when it's missing. ``createdBy`` is the most common
        # shape (a dict with ``displayName`` / ``userPrincipalName``);
        # some item types return a raw string.
        for key in ("createdBy", "lastModifiedBy", "ownerName"):
            val = row.get(key)
            if isinstance(val, dict):
                name = val.get("displayName") or val.get("userPrincipalName")
                if name:
                    return str(name)
            elif isinstance(val, str) and val:
                return val
        return None

    folders = [
        {
            "id": f.get("id"),
            "name": f.get("displayName") or f.get("id"),
            "type": "Folder",
            "parentFolderId": f.get("parentFolderId"),
            "owner": _owner_of(f),
        }
        for f in folders_raw if f.get("id")
    ]
    items = [
        {
            "id": it.get("id"),
            "name": it.get("displayName") or it.get("id"),
            "type": it.get("type") or "Unknown",
            "folderId": it.get("folderId"),
            "webUrl": it.get("webUrl"),
            "owner": _owner_of(it),
        }
        for it in items_raw if it.get("id")
    ]
    folders.sort(key=lambda x: str(x["name"]).lower())
    items.sort(key=lambda x: (str(x["type"]).lower(), str(x["name"]).lower()))
    merged = folders + items
    captured_at = datetime.now(UTC).isoformat()

    _workspace_items_cache[cache_key] = (now, captured_at, merged)
    # Opportunistic TTL sweep — drop entries older than 10×TTL so the
    # cache doesn't grow unbounded on long-lived processes.
    if len(_workspace_items_cache) > 256:
        cutoff = now - (_WORKSPACE_ITEMS_TTL_SEC * 10)
        stale = [k for k, (t, _, _) in _workspace_items_cache.items() if t < cutoff]
        for k in stale:
            _workspace_items_cache.pop(k, None)

    return merged, captured_at


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
    serialised = [a.model_dump() for a in attachments]
    # Tag each attachment with a user-facing classification (documentation /
    # suspicious / clean). This is purely for UI presentation — the runtime
    # defense is the structural fence added by process_attachments.
    findings = classify_attachments(serialised)
    finding_by_name = {f["name"]: f for f in findings}
    for a in serialised:
        name = str(a.get("name") or a.get("filename") or "attachment")
        f = finding_by_name.get(name)
        if f is not None:
            a["classification"] = {
                "severity": f["severity"],
                "category": f["category"],
                "markerCount": f["markerCount"],
                "hasHighConfidence": f["hasHighConfidence"],
                "documentLike": f["documentLike"],
                "message": f["message"],
            }
    new_ctx["prompt_attachments"] = serialised
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


def _composition_to_plan_view(comp) -> dict:
    """Project a ``Composition`` into the legacy ``plan`` view-model so the
    existing frontend (OrchestratorPage / DashboardPage / SessionDetailPage /
    OrchCanvas / TeamPanel) keeps rendering without changes.

    This is a *view* only — the source of truth is the Composition itself,
    which is serialized alongside the plan in the wire payload. New UI
    code should read ``job.composition``; legacy code reads ``job.plan``.
    """
    # Map Composition architecture → legacy TeamPattern. Non-driver values
    # fall back to supervisor (which the canvas renders as a hub-and-spoke).
    _PATTERN_MAP = {
        "dynamic": "solo",
        "solo": "solo",
        "supervisor": "supervisor",
        "sequential": "sequential",
        "hierarchical": "hierarchical",
        "reflection": "supervisor",
        "mixed": "mixed",
        "network": "network",
    }
    pattern = _PATTERN_MAP.get(comp.architecture, "supervisor")
    internal_agent_ids = {"orchestrator", "generalist"}
    visible_slots = [
        slot for slot in comp.slots
        if slot.agent_id not in internal_agent_ids and slot.id not in internal_agent_ids
    ]
    visible_slot_ids = {slot.id for slot in visible_slots}

    def _agent_label(agent_id: str) -> str:
        """Render a human-friendly name for a slot's agent.

        The canvas and team strip both show this directly. Falls back to
        the raw id only when the template registry doesn't know the agent
        (e.g. a composition referencing a retired template).
        """
        tpl = get_template(agent_id)
        if tpl is None:
            return agent_id
        # Prefer display_name ("FabricDataEngineer") over name
        # ("fabric-data-engineer"). Both fields exist on AgentTemplate.
        return tpl.display_name or tpl.name or agent_id

    def _all_skills_for(agent_id: str, selected_ids: set[str]) -> list[str]:
        """Return every skill the agent declares, ordered with
        selected skills first. Drives the frontend "+N" overflow chip
        so users can see the agent's full capability surface without
        leaving the Review page."""
        tpl = get_template(agent_id)
        if tpl is None:
            return []
        selected_first: list[str] = []
        rest: list[str] = []
        seen: set[str] = set()
        for sk in tpl.skills:
            name = getattr(sk, "name", None) or getattr(sk, "id", None)
            if not name or name in seen:
                continue
            seen.add(name)
            sk_id = getattr(sk, "id", None) or name
            (selected_first if sk_id in selected_ids else rest).append(name)
        return selected_first + rest

    nodes = [
        {
            "id": slot.id,
            "agent": _agent_label(slot.agent_id),
            "role": slot.role,
            "status": "planned",
            # Skills selected by the orchestrator for this slot —
            # rendered as the primary pills on the graph node. Order
            # is treated as "most useful first" by the frontend.
            "skills": [s.name for s in slot.skills],
            # Full list of declared skills for the slot's agent (selected
            # first) — powers the "+N" overflow popover.
            "allSkills": _all_skills_for(
                slot.agent_id,
                {s.id for s in slot.skills},
            ),
        }
        for slot in visible_slots
    ]
    _KIND_MAP = {"delegate": "delegate", "peer": "peer", "report": "report",
                 "handoff": "peer", "critique": "report"}
    edges = [
        {
            "from": h.from_,
            "to": h.to,
            "kind": _KIND_MAP.get(h.kind, "peer"),
        }
        for h in comp.handoffs
        if h.from_ in visible_slot_ids and h.to in visible_slot_ids
    ]

    steps = [
        {
            "id": slot.id,
            "order": idx + 1,
            "title": slot.role,
            "description": f"{slot.role} — {', '.join(s.name for s in slot.skills)}"
                            if slot.skills else slot.role,
            # Legacy fields the frontend reads defensively; minimal shapes
            # that won't crash PlanStep-typed code paths.
            "action": "execute",
            "target": {
                "itemType": "session",
                "displayName": slot.role,
                "workspaceId": "",
            },
            "inputs": [],
            "dependsOn": [],
            "rationale": "",
            "risk": "low",
            "reversible": True,
        }
        for idx, slot in enumerate(visible_slots)
    ]

    return {
        "jobId": comp.session_id,
        "summary": comp.headline,
        "title": comp.headline,
        "subtitle": comp.subtitle,
        "assumptions": [],
        "prerequisites": [],
        "steps": steps,
        "workspaceItems": [],
        "noAction": [],
        "conflicts": [],
        "clarificationsNeeded": [],
        "footer": {
            "agentCount": len(visible_slots),
            "stepCount": len(visible_slots),
            "approvalPoints": 0,
            "executionBlocked": False,
        },
        "team": {
            "pattern": pattern,
            "nodes": nodes,
            "edges": edges,
        },
    }


def _serialize_job(job: Job) -> dict:
    """Marshal a :class:`Job` to the UI wire shape.

    Job-level fields stay snake_case (the existing frontend reads them as
    ``task_description`` / ``workspace_id`` / etc.), but the nested
    ``composition`` uses camelCase aliases. We serialize them separately
    and stitch.
    """
    data = job.model_dump(mode="json")
    if job.composition is not None:
        data["composition"] = job.composition.model_dump(mode="json", by_alias=True)
        # Legacy ``plan`` view-model so the existing frontend components
        # keep rendering unchanged during the composition cutover.
        data["plan"] = _composition_to_plan_view(job.composition)
    return data


def _job_observability_summary(job: Job) -> dict:
    """Compact support summary for session list/load/status logs."""
    status = job.status.value if hasattr(job.status, "value") else str(job.status)
    composition = job.composition
    agent_statuses = [
        a.status.value if hasattr(a.status, "value") else str(a.status)
        for a in (job.agents or [])
    ]
    phase_count = sum(len(a.phases or []) for a in (job.agents or []))
    action_count = sum(len(a.actions or []) for a in (job.agents or []))
    return {
        "id": job.id,
        "status": status,
        "workspaceId": job.workspace_id,
        "architecture": composition.architecture if composition else None,
        "slotCount": len(composition.slots) if composition else 0,
        "agentCount": len(job.agents or []),
        "agentStatuses": dict(sorted({value: agent_statuses.count(value) for value in set(agent_statuses)}.items())),
        "phaseCount": phase_count,
        "actionCount": action_count,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "completedAt": job.completed_at.isoformat() if job.completed_at else None,
    }


def _job_observability_digest(job: Job) -> str:
    composition = job.composition
    return stable_digest({
        "id": job.id,
        "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "workspaceId": job.workspace_id,
        "task": bounded_text(job.task_description, max_chars=120),
        "composition": {
            "architecture": composition.architecture if composition else None,
            "slots": [
                {"id": slot.id, "agentId": slot.agent_id, "role": slot.role}
                for slot in (composition.slots if composition else [])
            ],
        },
        "agents": [
            {
                "agentId": agent.agent_id,
                "sessionId": agent.session_id,
                "role": agent.role,
                "status": agent.status.value if hasattr(agent.status, "value") else str(agent.status),
                "phaseCount": len(agent.phases or []),
                "actionCount": len(agent.actions or []),
            }
            for agent in (job.agents or [])
        ],
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "completedAt": job.completed_at.isoformat() if job.completed_at else None,
    })


def _dynamic_seed_composition(
    *,
    task_description: str,
    require_approvals: bool,
) -> Composition:
    """Build the default dynamic mission seed without an LLM round trip."""
    session_id = str(uuid.uuid4())
    return Composition(
        session_id=session_id,
        task=task_description,
        architecture="dynamic",
        rationale=(
            "Start immediately with the hidden generalist mission controller. "
            "The live runtime will inspect context, use MCP tools when safe, and "
            "delegate structured follow-up work to specialists as needed."
        ),
        headline="Dynamic mission",
        subtitle="A generalist controller starts now and delegates specialist work on demand.",
        slots=[
            AgentSlot(
                id="generalist",
                agent_id="generalist",
                role="Generalist mission controller",
                skills=[],
            )
        ],
        handoffs=[],
        entrypoint_slot_id="generalist",
        budget=Budget(
            max_turns=20,
            max_tool_calls=100,
            max_wallclock_s=600,
            require_approvals=require_approvals,
        ),
    )


@router.post("/sessions")
async def create_session(
    req: CreateJobRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Create a new dynamic session without blocking on planning."""
    user_id = _user_key_from_context(ctx)
    user_upn = _user_upn_from_context(ctx)
    logger.info(
        "[AGENTHUB] Start mission → seeding dynamic session (user=%s, workspace=%s, attachments=%d, model=%s)",
        user_upn or user_id[:8],
        req.workspace_id,
        len(req.attachments or []),
        req.model or "default",
    )
    logger.info(
        "[AGENTHUB] task: %.500s%s",
        req.task_description,
        " [TRUNCATED]" if len(req.task_description) > 500 else "",
    )
    if req.attachments:
        for i, att in enumerate(req.attachments):
            logger.info(
                "[AGENTHUB] attachment[%d]: %s (kind=%s, %d chars)",
                i, att.name, att.kind, len(att.content),
            )
    _rate_limit(user_id, "create_session")

    ctx_dict = req.context or {}
    require_approvals = True
    if isinstance(ctx_dict, dict) and "require_approvals" in ctx_dict:
        require_approvals = bool(ctx_dict.get("require_approvals"))
    composition = _dynamic_seed_composition(
        task_description=req.task_description,
        require_approvals=require_approvals,
    )

    persisted_context = _persist_context_with_attachments(req.context, req.attachments)

    # Note: the legacy code captured a Fabric workspace snapshot here so
    # the Session Detail page could later show "as-of HH:MM:SS". With
    # the composition pipeline we don't need it for planning, and it
    # added an extra Fabric round-trip to the hot path. Drop it — any
    # Session Detail view that wants a snapshot can fetch it lazily.

    job = Job(
        id=composition.session_id,
        user_id=user_id,
        user_upn=user_upn,
        workspace_id=req.workspace_id,
        task_description=req.task_description,
        context=persisted_context,
        status=JobStatus.PLANNED,
        composition=composition,
    )
    session_store.create_session(job)
    # Bind session id so downstream log lines in this handler (and any
    # asyncio tasks spawned from it) carry the s:<id> tag.
    set_session_id(job.id)
    logger.info(
        "[AGENTHUB] Session %s seeded — %s (%d slots)",
        job.id, composition.architecture, len(composition.slots),
    )
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
    summaries = [_job_observability_summary(job) for job in jobs]
    logger.info(
        "[SESSIONS] list user=%s filter=%s count=%d limit=%d offset=%d statuses=%s arch=%s digest=%s",
        user_id[:12],
        status or "all",
        len(jobs),
        limit,
        offset,
        collection_counts(summaries, "status"),
        collection_counts(summaries, "architecture"),
        stable_digest(summaries),
    )
    return [_strip_attachment_content(_serialize_job(j)) for j in jobs]


@router.get("/sessions/summary")
async def sessions_summary(
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Return aggregate session counts for the caller.

    Designed for dashboard KPIs: one lightweight grouped query instead of
    materializing all session rows client-side just to compute totals.
    """
    user_id = _user_key_from_context(ctx)
    summary = session_store.summarize_sessions(user_id)
    logger.info(
        "[SESSIONS] summary: user=%s total=%d active=%d history=%d",
        user_id[:12],
        summary.get("total", 0),
        summary.get("active_total", 0),
        summary.get("history_total", 0),
    )
    return summary


@router.get("/sessions/{session_id}/status")
async def get_session_status(
    session_id: UUID,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Return the minimal persisted run status needed by recovery polling."""
    job = _ensure_owner(session_store.get_session(str(session_id)), _user_key_from_context(ctx))
    summary = _job_observability_summary(job)
    digest = _job_observability_digest(job)
    logger.info(
        "[SESSION] status id=%s status=%s agents=%d phases=%d actions=%d digest=%s",
        str(session_id)[:8],
        summary.get("status"),
        summary.get("agentCount", 0),
        summary.get("phaseCount", 0),
        summary.get("actionCount", 0),
        digest,
    )
    return {**summary, "digest": digest}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: UUID,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    job = _ensure_owner(session_store.get_session(str(session_id)), _user_key_from_context(ctx))
    summary = _job_observability_summary(job)
    digest = _job_observability_digest(job)
    logger.debug(
        "[SESSION] load id=%s status=%s arch=%s slots=%d agents=%d phases=%d actions=%d digest=%s task=%s",
        str(session_id)[:8],
        summary.get("status"),
        summary.get("architecture") or "-",
        summary.get("slotCount", 0),
        summary.get("agentCount", 0),
        summary.get("phaseCount", 0),
        summary.get("actionCount", 0),
        digest,
        bounded_text(job.task_description, max_chars=120),
    )
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


@router.post("/sessions/{session_id}/agents")
async def add_agent_to_session(
    session_id: UUID,
    req: AddAgentRequest,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Attach a new agent to a running session on demand.

    Implements the runtime-side half of the Orchestrator's
    ``team-orchestration`` skill. Callable by the session owner when
    the execution surfaces a missing capability (e.g. realising
    mid-run that a ``fabric-admin`` is needed to create a workspace).
    Returns the newly-created ``AgentAssignment`` so the UI can
    render it optimistically before the ``agent_added`` SSE frame
    arrives.
    """
    user_key = _user_key_from_context(ctx)
    _rate_limit(user_key, "add_agent")
    _ensure_owner(session_store.get_session(str(session_id)), user_key)
    assignment = await get_orchestrator_engine().add_agent_to_job(
        str(session_id),
        agent_id=req.agent_id,
        role=req.role,
        goal=req.goal,
    )
    if assignment is None:
        raise HTTPException(
            404,
            "Session not running, stopping, or agent id unknown",
        )
    return {
        "status": "attached",
        "agent": assignment.model_dump(mode="json", by_alias=True),
    }


@router.get("/sessions/{session_id}/events.json")
async def session_events_inspect(
    session_id: UUID,
    types: str | None = None,
    limit: int = 500,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Return the recorded ring of mission events as JSON.

    This is the inspection counterpart to the SSE endpoint and is intended
    for e2e tests and developer tooling that need to assert on specific
    event types (e.g. ``verifier_verdict``) without holding an SSE
    connection open. Events are returned in emit order; ``types`` filters
    to a CSV of event-type names.

    When the live in-memory ``_JobExecution`` is no longer available
    (mission completed, backend restart, ring rotated), the response is
    served from the persisted ``session_events`` table so the live log can
    be reconstructed exactly when the user re-opens the session later.
    """
    _ensure_owner(session_store.get_session(str(session_id)), _user_key_from_context(ctx))
    type_filter: set[str] | None = None
    if types:
        type_filter = {t.strip() for t in types.split(",") if t.strip()}
    execution = get_orchestrator_engine().get_job_execution(str(session_id))
    if execution is not None:
        ring = list(execution._ring)  # type: ignore[attr-defined]
        if type_filter:
            ring = [ev for ev in ring if ev.get("type") in type_filter]
        if limit and limit > 0:
            ring = ring[-limit:]
        return {
            "sessionId": str(session_id),
            "source": "live",
            "count": len(ring),
            "events": ring,
        }
    persisted = session_event_store.load_events(
        str(session_id),
        types=type_filter,
        limit=limit if limit and limit > 0 else None,
    )
    return {
        "sessionId": str(session_id),
        "source": "persisted",
        "count": len(persisted),
        "events": persisted,
    }


@router.get("/sessions/{session_id}/events")
async def session_events_sse(
    session_id: UUID,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """SSE stream of real-time session events.

    Supports ``Last-Event-ID`` resume: on reconnect the browser's
    EventSource replays the last received ``seq`` via the standard
    header, and we replay any events that landed while the client was
    disconnected from the per-session ring buffer. If the buffer has
    already rotated past that point (long disconnect), we emit a fresh
    ``run_overview`` snapshot and start streaming live from there —
    the client's reducer is idempotent on ``seq`` so duplicate-safe.
    """
    _ensure_owner(session_store.get_session(str(session_id)), _user_key_from_context(ctx))
    execution = get_orchestrator_engine().get_job_execution(str(session_id))

    last_event_id_raw = request.headers.get("last-event-id") or request.query_params.get("lastEventId")
    last_seq: int | None = None
    if last_event_id_raw:
        try:
            last_seq = int(last_event_id_raw)
        except ValueError:
            last_seq = None

    if execution is None:
        # No live execution (mission completed, backend restarted, or
        # ring rotated). Reload the persisted event log from SQLite so
        # re-opening the session shows the full live log instead of an
        # empty trace, then close the stream. The frontend reducer is
        # idempotent on ``seq`` so this is duplicate-safe.
        persisted = session_event_store.load_events(
            str(session_id),
            after_seq=last_seq,
        )
        logger.info(
            "[SSE] replay-only session=%s persisted_events=%d (no live execution)",
            str(session_id)[:8],
            len(persisted),
        )

        async def replay_stream():
            for ev in persisted:
                yield _format_sse_frame(ev)

        return StreamingResponse(
            replay_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Content-Encoding": "identity",
            },
        )

    logger.info(
        "[SSE] subscribe session=%s user=%s last_seq=%s has_auth=%s has_fabric=%s",
        str(session_id)[:8],
        _user_key_from_context(ctx)[:12],
        str(last_seq) if last_seq is not None else "none",
        bool(request.headers.get("Authorization")),
        bool(request.headers.get("X-Fabric-Token")),
    )

    async def event_stream():
        # Always seed a new subscriber with a fresh snapshot so the UI
        # never renders a blank frame, even when resume replay is
        # available. The reducer dedupes on ``seq``.
        snapshot = {
            "type": "run_overview",
            "seq": _sse_snapshot_seq(last_seq),
            "sessionId": str(session_id),
            **execution.snapshot_run_overview(),
        }
        yield _format_sse_frame(snapshot)

        async for ev in execution.events(last_seq=last_seq):
            yield _format_sse_frame(ev)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            # Bypass GZipMiddleware — gzip compression buffers small SSE
            # frames and prevents real-time delivery to the browser.
            "Content-Encoding": "identity",
        },
    )


def _sse_snapshot_seq(last_seq: int | None) -> int:
    """Return a snapshot cursor that does not suppress replayed events."""
    return last_seq if last_seq is not None else 0


def _format_sse_frame(ev: dict) -> str:
    """Serialise ``ev`` as an SSE frame, including a monotonic ``id:``
    field when the event carries a ``seq`` so the browser's native
    EventSource can use it as ``Last-Event-ID`` on reconnect.

    Heartbeats deliberately omit the ``id:`` line so they don't
    clobber the resume cursor while the run is idle.
    """
    body = json.dumps(ev, default=str)
    seq = ev.get("seq")
    if seq is not None:
        return f"id: {seq}\ndata: {body}\n\n"
    return f"data: {body}\n\n"


# P7 · Mission Control — debug-only endpoint that returns the
# per-session ring buffer for post-mortem inspection. Gated on
# ``Application.Debug`` so it never ships enabled in production.


@router.post("/sessions/{session_id}/debug/snapshot")
async def debug_session_snapshot(
    session_id: UUID,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    config = get_configuration_service()
    if not config.is_debug():
        raise HTTPException(404, "Not found")
    _ensure_owner(session_store.get_session(str(session_id)), _user_key_from_context(ctx))
    execution = get_orchestrator_engine().get_job_execution(str(session_id))
    if not execution:
        raise HTTPException(404, "No active execution for this session")
    return {
        "seq": execution._seq,  # noqa: SLF001 — debug-only accessor
        "events": list(execution._ring),  # noqa: SLF001
        "runOverview": execution.snapshot_run_overview(),
    }


class _MakeStreamingSessionRequest(BaseModel):
    frames: int = 5
    interval_ms: int = 400


@router.post("/_test/make-streaming-session")
async def make_streaming_session(req: _MakeStreamingSessionRequest):
    """Debug-only — create a fake in-memory session and emit ``frames``
    log_line events spaced ``interval_ms`` apart in the background.

    Consumers of ``GET /api/sessions/{id}/events`` can then verify the
    SSE stream arrives in real time (not buffered). Gated on
    ``Application.Debug`` so it never ships enabled in production.
    """
    import asyncio
    import uuid

    from domain.models.agent_models import Job, JobStatus
    from services.agenthub import session_store as _store
    from services.agenthub.orchestrator_engine import (
        _JobExecution,
    )
    from services.agenthub.orchestrator_engine import (
        get_orchestrator_engine as _engine,
    )

    config = get_configuration_service()
    if not config.is_debug():
        raise HTTPException(404, "Not found")

    session_id = str(uuid.uuid4())
    job = Job(
        id=session_id,
        user_id=_DEV_USER_KEY,
        task_description="SSE streaming smoke test",
        workspace_id="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    _store.create_session(job)
    execution = _JobExecution(job, copilot_token="", mcp_tokens=None)
    _engine()._active_jobs[session_id] = execution

    async def _emit():
        await asyncio.sleep(0.15)  # give the caller time to subscribe
        for i in range(req.frames):
            # ``message`` is the field the frontend reducer reads for
            # log_line events (see mission/missionReducer.ts). ``level``
            # is required so the UI renders the row with the right icon.
            execution.emit(
                "log_line",
                message=f"streaming-test event #{i}",
                level="info",
            )
            await asyncio.sleep(req.interval_ms / 1000)
        execution.emit("job_complete", reason="streaming test done")

    asyncio.create_task(_emit())
    return {"session_id": session_id}


@router.post("/_test/make-mission-fixture")
async def make_mission_fixture():
    """Debug-only — create a fixture mission with a Generalist + 3 specialists,
    handoff lines, structured changes, and per-agent log lines. Used by the
    Mission Control visual-regression Playwright test to iterate on layout
    without running the full live Fabric pipeline.

    Gated on ``Application.Debug`` so it never ships in production.
    Returns ``{session_id}`` for the frontend to navigate to.
    """
    from domain.models.agent_models import Job, JobStatus
    from services.agenthub import session_store as _store
    from services.agenthub.orchestrator_engine import (
        _JobExecution,
        get_orchestrator_engine as _engine,
    )

    config = get_configuration_service()
    if not config.is_debug():
        raise HTTPException(404, "Not found")

    session_id = str(uuid.uuid4())
    job = Job(
        id=session_id,
        user_id=_DEV_USER_KEY,
        task_description="Create an end to end solution (ingestion, transformation, "
                         "semantic modelling and a report) which shows all Fabric items.",
        workspace_id="test",
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    _store.create_session(job)
    execution = _JobExecution(job, copilot_token="", mcp_tokens=None)
    _engine()._active_jobs[session_id] = execution

    generalist_id = "agent-generalist"
    data_eng_id = "agent-data-engineer"
    admin_id = "agent-admin"
    modeler_id = "agent-modeler"

    composition = {
        "architecture": "dynamic",
        "task": job.task_description,
        "slots": [
            {"id": generalist_id, "agentId": generalist_id, "role": "Generalist",
             "skills": [], "status": "active"},
            {"id": data_eng_id, "agentId": data_eng_id, "role": "Fabric Data Engineer",
             "skills": [], "status": "active"},
            {"id": admin_id, "agentId": admin_id, "role": "Fabric Admin",
             "skills": [], "status": "active"},
            {"id": modeler_id, "agentId": modeler_id, "role": "Modeler",
             "skills": [], "status": "active"},
        ],
        "handoffs": [
            {"from": generalist_id, "to": data_eng_id, "kind": "delegate"},
            {"from": generalist_id, "to": admin_id, "kind": "delegate"},
            {"from": generalist_id, "to": modeler_id, "kind": "delegate"},
        ],
    }

    async def _emit_fixture():
        await asyncio.sleep(0.15)
        execution.emit("composition_ready", composition=composition)

        agents = [
            (generalist_id, "AgentHub Generalist", "Generalist"),
            (data_eng_id, "Fabric Data Engineer", "Fabric Data Engineer"),
            (admin_id, "Fabric Admin", "Fabric Admin"),
            (modeler_id, "Modeler", "Modeler"),
        ]
        for agent_id, agent_name, role in agents:
            execution.emit("slot_progress", slotId=agent_id, agentId=agent_id,
                           agentName=agent_name, role=role, status="running")
            execution.emit("agent_status", agentId=agent_id, agentName=agent_name,
                           role=role, status="running")
            await asyncio.sleep(0.05)

        logs_per_agent = {
            generalist_id: [
                "Parallel discovery is running across three specialists.",
                "Capacity, certification, access, and evidence actions are tracked.",
                "Detailed logs are available without changing the run state.",
            ],
            data_eng_id: [
                "Workspace and item scan is active.",
                "Pipeline and lakehouse dependencies are being mapped.",
                "Evidence artifact is being produced.",
            ],
            admin_id: [
                "Capacity and permissions checks are active.",
                "Legacy access and F2 utilization are under review.",
                "Approval-bound changes are being drafted.",
            ],
            modeler_id: [
                "Semantic model checks are active.",
                "FinanceModel certification path is being prepared.",
                "Report readiness is being tracked.",
            ],
        }
        for agent_id, messages in logs_per_agent.items():
            agent_name = next(a[1] for a in agents if a[0] == agent_id)
            for msg in messages:
                execution.emit("log_line", agentId=agent_id, agentName=agent_name,
                               level="info", message=msg)
                await asyncio.sleep(0.02)

        # Structured ChangeRecorded events (ChangeKind: created|updated|deleted|important_action)
        changes = [
            {"recordId": str(uuid.uuid4()), "kind": "created", "status": "pending",
             "agentId": data_eng_id, "agentName": "Fabric Data Engineer",
             "targetName": "dependency_map.json", "targetType": "Artifact",
             "targetScope": "file", "summary": "Producing the dependency evidence artifact.",
             "toolName": "fabric_lakehouse_dependency_scan", "ts": datetime.now(UTC).isoformat()},
            {"recordId": str(uuid.uuid4()), "kind": "updated", "status": "pending",
             "agentId": modeler_id, "agentName": "Modeler",
             "targetName": "FinanceModel certification", "targetType": "SemanticModel",
             "targetScope": "item", "summary": "Missing certified label found; update path is being prepared.",
             "toolName": "powerbi_certify_model", "ts": datetime.now(UTC).isoformat()},
            {"recordId": str(uuid.uuid4()), "kind": "deleted", "status": "pending",
             "agentId": admin_id, "agentName": "Fabric Admin",
             "targetName": "ServiceAcc_Old access", "targetType": "Access",
             "targetScope": "access", "summary": "Legacy access detected and marked for removal review.",
             "toolName": "fabric_admin_remove_access", "ts": datetime.now(UTC).isoformat()},
            {"recordId": str(uuid.uuid4()), "kind": "important_action", "status": "pending",
             "agentId": admin_id, "agentName": "Fabric Admin",
             "targetName": "Capacity F2 → F4", "targetType": "Capacity",
             "targetScope": "execution", "summary": "Two-hour capacity scale window is being drafted.",
             "toolName": "fabric_admin_scale_capacity", "ts": datetime.now(UTC).isoformat()},
            {"recordId": str(uuid.uuid4()), "kind": "important_action", "status": "pending",
             "agentId": admin_id, "agentName": "Fabric Admin",
             "targetName": "Require MFA for new group members", "targetType": "TenantSetting",
             "targetScope": "settings", "summary": "Tenant setting change is being prepared for approval.",
             "toolName": "fabric_admin_set_tenant_setting", "ts": datetime.now(UTC).isoformat()},
        ]
        for change in changes:
            execution.emit("change_recorded", **change)
            await asyncio.sleep(0.03)

        execution.emit("artifact_added", artifactId=str(uuid.uuid4()),
                       agentId=data_eng_id, kind="json",
                       name="dependency_map.json", state="draft")

    asyncio.create_task(_emit_fixture())
    return {"session_id": session_id}


# ── Orchestration endpoints ──────────────────────────────────────────

@router.get("/orchestrate/compose-models")
async def list_compose_models(
    request: Request,
):
    """Return the user's Copilot catalog ranked for the Compose step.

    Wraps ``/api/github/models`` with a task-fit ranking so the UI can
    render the "Plan this" model picker pre-sorted best-first, with
    short reasons and latency hints. Entries unsuitable for composition
    (embeddings, TTS, legacy) are filtered out.

    Auth: uses the caller's GitHub token (Authorization header) to hit
    the Copilot models API — the same auth path as ``/api/github/models``.
    Does NOT require the Fabric OBO token because it has no Fabric
    side-effects; requiring it here caused 401s for clients that only
    ship the GitHub Bearer token on this call.
    """
    try:
        catalog = await github_chat_controller.list_models(request)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("[COMPOSE-MODELS] catalog fetch failed: %s", e)
        # Don't 500 the UI — return an empty catalog and let the
        # frontend fall back to "default" without a picker.
        return {"models": [], "default": None}
    raw_models = catalog.get("models", []) if isinstance(catalog, dict) else []
    ranked = rank_compose_models(raw_models)
    default_id = ranked[0]["id"] if ranked else None
    logger.info(
        "[COMPOSE-MODELS] catalog=%d models, ranked=%d, default=%s",
        len(raw_models), len(ranked), default_id,
    )
    return {"models": ranked, "default": default_id}


@router.post("/orchestrate/compose")
async def compose_endpoint(
    req: ComposeRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Compose an execution graph without creating a session.

    Callers that want to persist the result should instead POST to
    ``/api/sessions`` which composes-and-stores in a single round-trip.
    This endpoint exists for preview flows (e.g. a "what would this
    look like?" affordance in the compose form).
    """
    _rate_limit(_user_key_from_context(ctx), "compose")
    copilot_token = await _copilot_token(request)
    try:
        composition = await get_orchestrator_engine().compose(
            task_description=req.task_description,
            workspace_id=req.workspace_id,
            copilot_token=copilot_token,
            attachments=req.attachments,
            preferred_architecture=req.preferred_architecture,
            require_approvals=req.require_approvals,
            branch_out=req.branch_out,
            model=req.model,
        )
    except CompositionError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "composition_failed", "reason": e.reason, **e.details},
        ) from e
    return composition.model_dump(mode="json", by_alias=True)


@router.post("/sessions/{session_id}/run")
async def run_session(
    session_id: str,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Start executing an already-composed session."""
    user_key = _user_key_from_context(ctx)
    _rate_limit(user_key, "run_session")
    job = _ensure_owner(session_store.get_session(session_id), user_key)
    if job.status != JobStatus.PLANNED:
        raise HTTPException(400, f"Session is {job.status.value}, not planned")
    if job.composition is None:
        raise HTTPException(400, "Session has no composition")

    comp = job.composition
    slot_summary = " → ".join(
        f"{s.agent_id}({s.id})" for s in comp.slots
    )
    logger.info(
        "[AGENTHUB] Run session %s: arch=%s slots=[%s] budget=%d/%d/%ds",
        session_id, comp.architecture, slot_summary,
        comp.budget.max_turns, comp.budget.max_tool_calls, comp.budget.max_wallclock_s,
    )

    job.status = JobStatus.APPROVED
    session_store.update_session(job)

    copilot_token = await _copilot_token(request)
    mcp_tokens = await _mcp_tokens(request)
    await get_orchestrator_engine().start_job(job, copilot_token, mcp_tokens)
    return {"status": "running", "session_id": job.id}


@router.post("/orchestrate/reject")
async def reject_composition(
    req: ApprovePlanRequest,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Mark a composed session as cancelled without running it."""
    job = _ensure_owner(session_store.get_session(req.session_id), _user_key_from_context(ctx))
    job.status = JobStatus.CANCELLED
    job.completed_at = datetime.now(UTC)
    session_store.update_session(job)
    return {"status": "rejected"}


# ── Catalog endpoints ────────────────────────────────────────────────

@router.get("/catalogs/architectures")
async def list_architectures():
    """Return the dynamic mission strategy catalog.

    Fixed architecture choices are no longer part of the default UI;
    this endpoint remains for clients that still fetch the catalog.
    """
    return [
        {
            "id": a.id,
            "name": a.name,
            "headline": a.headline,
            "description": a.description,
            "pickWhen": a.pick_when,
            "watchFor": a.watch_for,
            "fabricUseCases": a.fabric_use_cases,
            "hasDriver": a.has_driver,
        }
        for a in ARCHITECTURES
    ]


@router.get("/catalogs/agents")
async def list_agents_with_skills():
    """Return the agent catalog with attached skills — the surface the
    composer picks from. Alias of ``/agents`` plus the skills array,
    exposed under ``/catalogs`` so the UI can fetch "everything the
    composer sees" in one call.
    """
    return [t.model_dump(mode="json") for t in list_templates()]


# ── P4 · Mission Control — mid-run approval resolution. Client posts
# the chosen recovery action; the orchestrator engine consumes it via
# its user-message queue.

class ResolveApprovalRequest(BaseModel):
    session_id: str
    approval_id: str
    action: str  # "approve" | "decline" | "request_alternative" | "edit_input"
    reason: str | None = None


@router.post("/sessions/{session_id}/approvals/{approval_id}")
async def resolve_approval(
    session_id: str,
    approval_id: str,
    req: ResolveApprovalRequest,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    user_key = _user_key_from_context(ctx)
    _rate_limit(user_key, "approve_plan")
    job = _ensure_owner(session_store.get_session(session_id), user_key)
    execution = get_orchestrator_engine().get_job_execution(session_id)
    if not execution:
        raise HTTPException(404, "No active execution for this session")

    # Record the resolution on the job context for audit.
    log_ctx = dict(job.context or {})
    approvals = list(log_ctx.get("approval_log") or [])
    approvals.append({
        "approval_id": approval_id,
        "action": req.action,
        "reason": req.reason,
        "resolved_at": datetime.now(UTC).isoformat(),
        "by": user_key,
    })
    log_ctx["approval_log"] = approvals
    job.context = log_ctx
    session_store.update_session(job)

    # Emit resolution event — the orchestrator engine listens for these
    # on its user-message queue to un-pause the corresponding step.
    try:
        execution.emit(
            "approval.resolved",
            approvalId=approval_id,
            action=req.action,
            reason=req.reason,
        )
    except Exception:
        pass
    return {"status": "ok", "action": req.action}


# ── Agent template & config endpoints ────────────────────────────────

@router.get("/agents")
async def list_agent_templates():
    return [t.model_dump() for t in list_templates()]


@router.get("/agents/{agent_id}")
async def get_agent_template(agent_id: str):
    t = get_template(agent_id)
    if not t or t.is_internal:
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
