"""AgentHub REST API — jobs, agents, orchestration, SSE events."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
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
    ComposeRequest,
    CreateJobRequest,
    Job,
    JobStatus,
    RunSessionRequest,
    SendMessageRequest,
    UserAgentConfig,
)
from domain.models.authentication_models import AuthorizationContext
from domain.models.composition import CompositionError
from domain.catalogs.architectures import ARCHITECTURES
from services.agenthub import session_store, workspaces_cache
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
        logger.warning("Rejected invalid Fabric token: %s", exc)
        raise HTTPException(401, "Invalid Fabric token") from exc
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
    return {"items": items, "captured_at": captured_at}


# ── PBI Fixer proxy ──────────────────────────────────────────────────

class PbiFixerProxyRequest(BaseModel):
    """Forward an authenticated call to a Fabric / Power BI REST endpoint.

    The PBI Fixer iframe cannot acquire a Fabric- or Power BI-audience
    token directly (Fabric workload SDK only issues workload-audience
    tokens). This proxy does the OBO exchange server-side and forwards
    the call with the appropriate token, scoped per ``api`` field:

    - ``api="fabric"`` → token aud ``api.fabric.microsoft.com``
    - ``api="pbi"``    → token aud ``analysis.windows.net/powerbi/api``

    ``path`` is the URL portion **after** the API root, including a
    leading slash. Example: ``"/groups/{ws}/datasets/{ds}/tables"``.
    """

    api: str = Field(..., pattern="^(fabric|pbi)$")
    path: str
    method: str = Field("GET", pattern="^(GET|POST|PUT|PATCH|DELETE)$")
    body: dict | list | None = None


_FABRIC_API_ROOT = "https://api.fabric.microsoft.com/v1"
_PBI_API_ROOT = "https://api.powerbi.com/v1.0/myorg"


@router.post("/pbi-fixer/proxy")
async def pbi_fixer_proxy(
    payload: PbiFixerProxyRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Proxy a single Fabric / Power BI REST call using the user's OBO token."""
    user_id = _user_key_from_context(ctx)
    _rate_limit(user_id, "pbi_fixer_proxy")

    mcp_tokens = await _mcp_tokens(request)
    if not mcp_tokens:
        raise HTTPException(400, "Fabric token required")

    if payload.api == "fabric":
        token = mcp_tokens.get("FABRIC_API_TOKEN")
        root = _FABRIC_API_ROOT
    else:
        token = mcp_tokens.get("PBI_API_TOKEN") or mcp_tokens.get("FABRIC_API_TOKEN")
        root = _PBI_API_ROOT

    if not token:
        raise HTTPException(401, f"No OBO token available for api={payload.api}")

    if not payload.path.startswith("/"):
        raise HTTPException(400, "path must start with '/'")

    url = f"{root}{payload.path}"

    import httpx
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        try:
            resp = await client.request(
                payload.method,
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload.body if payload.body is not None else None,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Upstream request failed: {exc}") from exc

        # Some long-running Fabric ops (getDefinition for reports/models)
        # return 202 with a Location header pointing at an LRO. Follow it
        # transparently so the frontend doesn't need to know about polling.
        attempts = 0
        while resp.status_code == 202 and attempts < 30:
            attempts += 1
            location = resp.headers.get("Location") or resp.headers.get("Operation-Location")
            if not location:
                break
            try:
                retry_after = float(resp.headers.get("Retry-After", "2"))
            except ValueError:
                retry_after = 2.0
            await asyncio.sleep(min(retry_after, 5.0))
            try:
                resp = await client.get(
                    location,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"LRO poll failed: {exc}") from exc

        # Once the LRO completes, Fabric typically returns 200 on the
        # status URL but the body is the operation status — the actual
        # result lives at f"{location}/result". Fetch that if the body
        # looks like an LRO status (status: Succeeded). Surface
        # status: Failed as an HTTP error so the frontend can react —
        # without this, write operations like updateDefinition appear
        # to "succeed" (HTTP 200) while having actually failed inside
        # Fabric.
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
            try:
                maybe_status = resp.json()
            except Exception:
                maybe_status = None
            if isinstance(maybe_status, dict) and "status" in maybe_status:
                op_status = maybe_status.get("status")
                if op_status == "Failed":
                    err = maybe_status.get("error") or {}
                    err_code = err.get("errorCode", "")
                    err_msg = err.get("message", "")
                    raise HTTPException(
                        status_code=502,
                        detail=f"Fabric LRO failed [{err_code}]: {err_msg}",
                    )
                if (
                    op_status in ("Succeeded", "Completed")
                    and "result" not in maybe_status
                    and location
                ):
                    # Try fetching the result endpoint. Some write
                    # operations (e.g. updateDefinition) have no result
                    # body — Fabric responds 400 / OperationHasNoResult.
                    # Treat that as success and fall back to the status
                    # body so the call does not appear to fail.
                    try:
                        result_resp = await client.get(
                            f"{location.rstrip('/')}/result",
                            headers={"Authorization": f"Bearer {token}"},
                        )
                    except httpx.HTTPError as exc:
                        raise HTTPException(502, f"LRO result fetch failed: {exc}") from exc
                    if result_resp.status_code < 400:
                        resp = result_resp
                    else:
                        # 400 OperationHasNoResult or similar → keep
                        # the Succeeded status body as the response.
                        pass

    # Surface non-2xx as the same status so the frontend can react. Body
    # is forwarded verbatim (text) so callers see the raw API error.
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"{payload.method} {url}: {resp.text}",
        )

    # 204/empty body → return {}
    if not resp.content:
        return {}
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


# ── PBI Fixer: Translations (WS-G) ──────────────────────────────────
# Auto-translate proposal + apply endpoints. The propose endpoint
# generates translation candidates for a batch of source strings
# against one or more target cultures. The apply endpoint is scoped
# out for this pass — see WS-N — and returns 501 so the frontend can
# surface a clear "not yet enabled" message.


class TranslationSourceItem(BaseModel):
    objectType: str = Field(..., description="Table | Column | Measure | Hierarchy | Description")
    objectPath: str = Field(..., description="'Sales' or 'Sales[Amount]' etc.")
    sourceCaption: str = Field(..., description="Source string to translate")
    existingCaption: str | None = None


class TranslationProposeRequest(BaseModel):
    workspaceId: str
    datasetId: str
    targetCultures: list[str] = Field(..., min_length=1)
    sourceCulture: str | None = "en-US"
    sourceItems: list[TranslationSourceItem] = Field(default_factory=list)
    glossary: dict[str, str] | None = None


class TranslationProposalItem(BaseModel):
    objectType: str
    objectPath: str
    sourceCaption: str
    existingCaption: str | None = None
    proposedCaption: str
    proposedDescription: str | None = None


class TranslationProposeResponse(BaseModel):
    culture: str
    items: list[TranslationProposalItem]


# A tiny built-in glossary keeps propose output deterministic for tests
# and offline dev. Real LLM-backed translation is deferred to WS-N; the
# endpoint shape is stable so the client doesn't need to change.
_BUILTIN_GLOSSARY: dict[str, dict[str, str]] = {
    "de-DE": {
        "sales": "Umsatz", "revenue": "Umsatz", "product": "Produkt",
        "products": "Produkte", "customer": "Kunde", "customers": "Kunden",
        "order": "Bestellung", "orders": "Bestellungen", "date": "Datum",
        "amount": "Betrag", "total": "Summe", "quantity": "Menge",
        "name": "Name", "category": "Kategorie", "region": "Region",
        "country": "Land", "city": "Stadt", "year": "Jahr",
        "month": "Monat", "day": "Tag", "price": "Preis", "cost": "Kosten",
        "profit": "Gewinn", "store": "Filiale", "employee": "Mitarbeiter",
    },
    "fr-FR": {
        "sales": "Ventes", "revenue": "Chiffre d'affaires", "product": "Produit",
        "products": "Produits", "customer": "Client", "customers": "Clients",
        "order": "Commande", "orders": "Commandes", "date": "Date",
        "amount": "Montant", "total": "Total", "quantity": "Quantité",
        "name": "Nom", "category": "Catégorie", "region": "Région",
        "country": "Pays", "city": "Ville", "year": "Année",
        "month": "Mois", "day": "Jour", "price": "Prix", "cost": "Coût",
        "profit": "Bénéfice", "store": "Magasin", "employee": "Employé",
    },
    "es-ES": {
        "sales": "Ventas", "revenue": "Ingresos", "product": "Producto",
        "products": "Productos", "customer": "Cliente", "customers": "Clientes",
        "order": "Pedido", "orders": "Pedidos", "date": "Fecha",
        "amount": "Importe", "total": "Total", "quantity": "Cantidad",
        "name": "Nombre", "category": "Categoría", "region": "Región",
        "country": "País", "city": "Ciudad", "year": "Año",
        "month": "Mes", "day": "Día", "price": "Precio", "cost": "Coste",
        "profit": "Beneficio", "store": "Tienda", "employee": "Empleado",
    },
}


def _translate_word(word: str, culture: str, glossary: dict[str, str] | None) -> str:
    """Translate a single word using the user-supplied glossary first,
    then the built-in glossary. Falls back to the original word. Case
    sensitivity is preserved on the first character."""
    if not word:
        return word
    key = word.lower()
    # User-supplied glossary takes priority (already in target culture).
    if glossary and key in {k.lower() for k in glossary.keys()}:
        # Case-insensitive lookup
        for gk, gv in glossary.items():
            if gk.lower() == key:
                translated = gv
                break
        else:
            translated = word
    else:
        translated = _BUILTIN_GLOSSARY.get(culture, {}).get(key, word)
    # Preserve leading capitalization
    if word[:1].isupper():
        translated = translated[:1].upper() + translated[1:]
    return translated


def _translate_caption(caption: str, culture: str, glossary: dict[str, str] | None) -> str:
    """Split caption into tokens (keeping spaces / non-letter runs) and
    translate each word via the glossary chain."""
    import re
    # Split on word boundaries but preserve separators.
    parts = re.split(r"(\W+)", caption)
    return "".join(
        _translate_word(p, culture, glossary) if p.isalpha() else p
        for p in parts
    )


@router.post("/pbi-fixer/translations/propose", response_model=TranslationProposeResponse)
async def pbi_fixer_translations_propose(
    payload: TranslationProposeRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Generate a translation proposal for the given source items.

    For this pass, translation is done via a small deterministic
    glossary (see ``_BUILTIN_GLOSSARY``). The LLM-backed path described
    in WS-G will be wired later; the response shape is stable so the
    frontend review grid doesn't change when that lands.
    """
    user_id = _user_key_from_context(ctx)
    _rate_limit(user_id, "pbi_fixer_translations_propose")

    if not payload.sourceItems:
        raise HTTPException(400, "sourceItems is required — pass the model objects to translate")
    if len(payload.targetCultures) != 1:
        # Multi-culture is a UI affordance — backend translates one
        # culture per call to keep responses small and paginatable.
        raise HTTPException(400, "targetCultures must contain exactly one culture per call")

    culture = payload.targetCultures[0]
    glossary = payload.glossary or {}

    items: list[TranslationProposalItem] = []
    for src in payload.sourceItems:
        proposed = _translate_caption(src.sourceCaption, culture, glossary)
        items.append(TranslationProposalItem(
            objectType=src.objectType,
            objectPath=src.objectPath,
            sourceCaption=src.sourceCaption,
            existingCaption=src.existingCaption,
            proposedCaption=proposed,
        ))

    return TranslationProposeResponse(culture=culture, items=items)


class TranslationApplyRequest(BaseModel):
    workspaceId: str
    datasetId: str
    culture: str
    items: list[TranslationProposalItem]


@router.post("/pbi-fixer/translations/apply")
async def pbi_fixer_translations_apply(
    payload: TranslationApplyRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Apply accepted translations to a semantic model.

    Implementation strategy: round-trip the model TMDL definition via
    Fabric's ``getDefinition`` / ``updateDefinition`` REST. This avoids
    needing an XMLA / sempy-labs bridge while still producing the same
    ``ObjectTranslation`` rows that XMLA writes would produce. Steps:

    1. Pull the model definition (LRO).
    2. Find or create ``definition/cultures/<culture>.tmdl``.
    3. Parse the existing culture body, merge the requested items into
       its ``translations`` block (preserving any unrelated entries +
       linguisticMetadata), and re-serialise deterministically.
    4. POST ``updateDefinition`` with the patched parts (LRO).
    """
    import base64
    import httpx

    from services.agenthub.tmdl_translations import (
        ApplyItem,
        empty_culture,
        merge_items,
        parse_culture,
        serialize_culture,
    )

    user_id = _user_key_from_context(ctx)
    _rate_limit(user_id, "pbi_fixer_translations_apply")

    if not payload.items:
        raise HTTPException(400, "items is required")
    if not payload.culture or not re.match(r"^[a-zA-Z]{2,3}(-[A-Za-z0-9]+)*$", payload.culture):
        raise HTTPException(400, "culture must be a valid culture code, e.g. 'de-DE'")

    mcp_tokens = await _mcp_tokens(request)
    if not mcp_tokens:
        raise HTTPException(400, "Fabric token required")
    fabric_token = mcp_tokens.get("FABRIC_API_TOKEN")
    if not fabric_token:
        raise HTTPException(401, "No Fabric OBO token available")

    base = f"{_FABRIC_API_ROOT}/workspaces/{payload.workspaceId}/semanticModels/{payload.datasetId}"

    async def _follow_lro(client: httpx.AsyncClient, resp: httpx.Response) -> httpx.Response:
        attempts = 0
        while resp.status_code == 202 and attempts < 30:
            attempts += 1
            location = resp.headers.get("Location") or resp.headers.get("Operation-Location")
            if not location:
                break
            try:
                retry_after = float(resp.headers.get("Retry-After", "2"))
            except ValueError:
                retry_after = 2.0
            await asyncio.sleep(min(retry_after, 5.0))
            resp = await client.get(location, headers={"Authorization": f"Bearer {fabric_token}"})
        # If the LRO finished with a status body, fetch /result if the
        # operation succeeded; treat result-not-available as success.
        if (
            resp.status_code == 200
            and resp.headers.get("content-type", "").startswith("application/json")
        ):
            try:
                body = resp.json()
            except Exception:
                body = None
            if isinstance(body, dict) and body.get("status") == "Failed":
                err = body.get("error") or {}
                raise HTTPException(
                    502,
                    f"Fabric LRO failed [{err.get('errorCode', '')}]: {err.get('message', '')}",
                )
            if (
                isinstance(body, dict)
                and body.get("status") in ("Succeeded", "Completed")
                and "definition" not in body
            ):
                location = resp.url and str(resp.url) or location
                try:
                    rr = await client.get(
                        f"{location.rstrip('/')}/result",
                        headers={"Authorization": f"Bearer {fabric_token}"},
                    )
                    if rr.status_code < 400:
                        resp = rr
                except httpx.HTTPError:
                    pass
        return resp

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
        # 1. getDefinition (TMDL)
        try:
            resp = await client.post(
                f"{base}/getDefinition?format=TMDL",
                headers={
                    "Authorization": f"Bearer {fabric_token}",
                    "Content-Type": "application/json",
                },
                json={},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"getDefinition failed: {exc}") from exc
        resp = await _follow_lro(client, resp)
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, f"getDefinition: {resp.text}")
        try:
            def_body = resp.json()
        except Exception as exc:
            raise HTTPException(502, f"getDefinition returned non-JSON: {exc}") from exc

        parts = (def_body.get("definition") or {}).get("parts") or []
        if not parts:
            raise HTTPException(502, "Semantic model definition has no parts")

        culture_path = f"definition/cultures/{payload.culture}.tmdl"
        # 2. Find or create the culture part
        culture_part = next(
            (p for p in parts if p.get("path") == culture_path),
            None,
        )
        if culture_part is None:
            text = ""
            cm = empty_culture(payload.culture)
        else:
            try:
                text = base64.b64decode(culture_part["payload"]).decode("utf-8")
            except Exception as exc:
                raise HTTPException(
                    502, f"Failed to decode culture TMDL '{culture_path}': {exc}"
                ) from exc
            cm = parse_culture(text)
            if not cm.culture:
                cm.culture = payload.culture

        # 3. Merge items
        apply_items = [
            ApplyItem(
                object_type=it.objectType,
                object_path=it.objectPath,
                value=it.proposedCaption,
                proposed_description=it.proposedDescription,
            )
            for it in payload.items
        ]
        touched = merge_items(cm, apply_items)
        if touched == 0:
            raise HTTPException(400, "No items had a recognisable object_type / object_path")

        new_body = serialize_culture(cm)
        new_payload_b64 = base64.b64encode(new_body.encode("utf-8")).decode("ascii")

        new_parts = [{**p} for p in parts]
        if culture_part is None:
            new_parts.append(
                {"path": culture_path, "payload": new_payload_b64, "payloadType": "InlineBase64"}
            )
        else:
            for np in new_parts:
                if np.get("path") == culture_path:
                    np["payload"] = new_payload_b64
                    np["payloadType"] = np.get("payloadType") or "InlineBase64"
                    break

        # 4. updateDefinition
        try:
            up = await client.post(
                f"{base}/updateDefinition",
                headers={
                    "Authorization": f"Bearer {fabric_token}",
                    "Content-Type": "application/json",
                },
                json={"definition": {"parts": new_parts}},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"updateDefinition failed: {exc}") from exc
        up = await _follow_lro(client, up)
        if up.status_code >= 400:
            raise HTTPException(up.status_code, f"updateDefinition: {up.text}")

    return {
        "applied": touched,
        "culture": payload.culture,
        "createdCultureFile": culture_part is None,
    }


# ---------------------------------------------------------------------------
# WS-E v0.41 — generic Fixer apply endpoint
# ---------------------------------------------------------------------------


class FixerApplyRequest(BaseModel):
    workspaceId: str
    fixerId: str
    scanOnly: bool = True
    datasetId: str | None = None
    reportId: str | None = None


@router.post("/pbi-fixer/fixers/apply")
async def pbi_fixer_fixers_apply(
    payload: FixerApplyRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Run a TS-port PBI fixer against a semantic model or report.

    The handler registry lives in ``services.agenthub.pbi_fixer_handlers``
    and each handler mutates the TMDL or PBIR JSON parts in place. We
    follow the same Fabric ``getDefinition`` / ``updateDefinition``
    LRO round-trip as ``pbi_fixer_translations_apply`` (v0.40).
    """
    import httpx

    from services.agenthub.pbi_fixer_handlers import FIXER_HANDLERS

    user_id = _user_key_from_context(ctx)
    _rate_limit(user_id, "pbi_fixer_fixers_apply")

    fx = FIXER_HANDLERS.get(payload.fixerId)
    if not fx:
        raise HTTPException(404, f"Unknown fixerId: {payload.fixerId}")
    scope, handler = fx

    if scope == "sm" and not payload.datasetId:
        raise HTTPException(400, "datasetId required for semantic-model fixers")
    if scope == "report" and not payload.reportId:
        raise HTTPException(400, "reportId required for report fixers")

    mcp_tokens = await _mcp_tokens(request)
    if not mcp_tokens:
        raise HTTPException(400, "Fabric token required")
    fabric_token = mcp_tokens.get("FABRIC_API_TOKEN")
    if not fabric_token:
        raise HTTPException(401, "No Fabric OBO token available")

    if scope == "sm":
        base = (
            f"{_FABRIC_API_ROOT}/workspaces/{payload.workspaceId}"
            f"/semanticModels/{payload.datasetId}"
        )
        get_url = f"{base}/getDefinition?format=TMDL"
    else:
        base = (
            f"{_FABRIC_API_ROOT}/workspaces/{payload.workspaceId}"
            f"/reports/{payload.reportId}"
        )
        get_url = f"{base}/getDefinition"

    async def _follow_lro(client: httpx.AsyncClient, resp: httpx.Response) -> httpx.Response:
        attempts = 0
        while resp.status_code == 202 and attempts < 30:
            attempts += 1
            location = resp.headers.get("Location") or resp.headers.get("Operation-Location")
            if not location:
                break
            try:
                retry_after = float(resp.headers.get("Retry-After", "2"))
            except ValueError:
                retry_after = 2.0
            await asyncio.sleep(min(retry_after, 5.0))
            resp = await client.get(location, headers={"Authorization": f"Bearer {fabric_token}"})
        if (
            resp.status_code == 200
            and resp.headers.get("content-type", "").startswith("application/json")
        ):
            try:
                body = resp.json()
            except Exception:
                body = None
            if isinstance(body, dict) and body.get("status") == "Failed":
                err = body.get("error") or {}
                raise HTTPException(
                    502,
                    f"Fabric LRO failed [{err.get('errorCode', '')}]: {err.get('message', '')}",
                )
            if (
                isinstance(body, dict)
                and body.get("status") in ("Succeeded", "Completed")
                and "definition" not in body
            ):
                location = (resp.url and str(resp.url)) or location
                try:
                    rr = await client.get(
                        f"{location.rstrip('/')}/result",
                        headers={"Authorization": f"Bearer {fabric_token}"},
                    )
                    if rr.status_code < 400:
                        resp = rr
                except httpx.HTTPError:
                    pass
        return resp

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
        try:
            resp = await client.post(
                get_url,
                headers={
                    "Authorization": f"Bearer {fabric_token}",
                    "Content-Type": "application/json",
                },
                json={},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"getDefinition failed: {exc}") from exc
        resp = await _follow_lro(client, resp)
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, f"getDefinition: {resp.text}")
        try:
            def_body = resp.json()
        except Exception as exc:
            raise HTTPException(502, f"getDefinition returned non-JSON: {exc}") from exc

        parts = (def_body.get("definition") or {}).get("parts") or []
        if not parts:
            raise HTTPException(502, "Definition has no parts")

        try:
            result = handler(parts, payload.scanOnly)
        except Exception as exc:
            raise HTTPException(500, f"{payload.fixerId} handler failed: {exc}") from exc

        applied = False
        if not payload.scanOnly and result.findings:
            try:
                up = await client.post(
                    f"{base}/updateDefinition",
                    headers={
                        "Authorization": f"Bearer {fabric_token}",
                        "Content-Type": "application/json",
                    },
                    json={"definition": {"parts": result.parts}},
                )
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"updateDefinition failed: {exc}") from exc
            up = await _follow_lro(client, up)
            if up.status_code >= 400:
                raise HTTPException(up.status_code, f"updateDefinition: {up.text}")
            applied = True

    return {
        "fixerId": payload.fixerId,
        "scope": scope,
        "scanOnly": payload.scanOnly,
        "applied": applied,
        "findings": [
            {
                "objectPath": f.object_path,
                "detail": f.detail,
                "before": f.before,
                "after": f.after,
            }
            for f in result.findings
        ],
        "log": result.log,
    }


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
        "solo": "solo",
        "supervisor": "supervisor",
        "sequential": "sequential",
        "parallel": "supervisor",
        "router": "supervisor",
        "hierarchical": "hierarchical",
        "reflection": "supervisor",
        "mixed": "mixed",
        "network": "network",
        "debate": "network",
        "magentic": "hierarchical",
    }
    pattern = _PATTERN_MAP.get(comp.architecture, "supervisor")

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
        for slot in comp.slots
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
        for idx, slot in enumerate(comp.slots)
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
            "agentCount": len(comp.slots),
            "stepCount": len(comp.slots),
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


@router.post("/sessions")
async def create_session(
    req: CreateJobRequest,
    request: Request,
    ctx: AuthorizationContext | None = Depends(require_user),
):
    """Create a new session and compose an execution graph."""
    user_id = _user_key_from_context(ctx)
    user_upn = _user_upn_from_context(ctx)
    logger.info(
        "[AGENTHUB] Plan this → composing session (workspace=%s, task_chars=%d, attachments=%d)",
        req.workspace_id,
        len(req.task_description or ""),
        len(req.attachments or []),
    )
    _rate_limit(user_id, "create_session")
    # Compose only needs the Copilot token — MCP/Fabric OBO tokens are
    # not referenced anywhere in this handler and were previously awaited
    # for no reason (up to ~2s of OBO exchange on the hot path).
    copilot_token = await _copilot_token(request)

    try:
        composition = await get_orchestrator_engine().compose(
            task_description=req.task_description,
            workspace_id=req.workspace_id,
            copilot_token=copilot_token,
            attachments=req.attachments,
            model=req.model,
        )
    except CompositionError as e:
        # Spec: surface as structured 422 so the UI can render a
        # "Composition could not be generated" empty state rather than a
        # blank card.
        raise HTTPException(
            status_code=422,
            detail={"error": "composition_failed", "reason": e.reason, **e.details},
        ) from e

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
        "[AGENTHUB] Session %s composed — %s (%d slots)",
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
    if not execution:
        raise HTTPException(404, "No active execution for this session")

    last_event_id_raw = request.headers.get("last-event-id") or request.query_params.get("lastEventId")
    last_seq: int | None = None
    if last_event_id_raw:
        try:
            last_seq = int(last_event_id_raw)
        except ValueError:
            last_seq = None

    async def event_stream():
        # Always seed a new subscriber with a fresh snapshot so the UI
        # never renders a blank frame, even when resume replay is
        # available. The reducer dedupes on ``seq``.
        snapshot = {
            "type": "run_overview",
            "seq": execution._seq,  # noqa: SLF001 — accessor kept internal
            "sessionId": str(session_id),
            **execution.snapshot_run_overview(),
        }
        yield _format_sse_frame(snapshot)

        async for ev in execution.events(last_seq=last_seq):
            yield _format_sse_frame(ev)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    """Return the architecture catalog the composer chooses from — used
    by the UI to render the "Regenerate as …" picker and any future
    user-facing architecture explainer.
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
