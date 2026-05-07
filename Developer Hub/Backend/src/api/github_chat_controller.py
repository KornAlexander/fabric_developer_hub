"""GitHub Copilot API integration via Device Flow authentication.

Handles:
- GitHub OAuth Device Flow (no popups needed in sandboxed iframes)
- Exchanging GitHub tokens for short-lived Copilot API tokens
- Listing available models from the Copilot API (Claude, GPT, Gemini, etc.)
- Proxying chat completions to the Copilot API
- Agentic tool-call loop with OBO-authenticated Fabric/OneLake tokens

Authorization-header contract (TWO-TOKEN MODEL — important):
- ``POST /api/github/device-code`` and ``POST /api/github/poll-token``
  are UNAUTHENTICATED (Device Flow bootstrap).
- Every other ``/api/github/*`` endpoint expects the GitHub OAuth access
  token in the standard ``Authorization: Bearer <github_token>`` header.
  That token is NOT an Entra ID token and MUST NOT be used to call Fabric
  REST, OneLake, or anything that trusts AAD issuer/audience.
- When Copilot tool calls need Fabric/OneLake access (OBO), the Fabric
  workload token is passed alongside in the ``X-Fabric-Token`` header.
  It is validated by ``AuthenticationService`` and used only for OBO
  exchange; user claims from this token are the authoritative tenant/oid.
  LLM-supplied ``tenantId``/``workspaceId``/``userId`` arguments NEVER
  authorize; tool_runtime strips and overrides them from the verified
  ``CallerContext`` built from this token's claims.
- GitHub tokens MUST NOT reach Copilot/MCP tool arguments, prompts, or
  logs. Copilot tokens are short-lived and cached in-process keyed by
  SHA-256(github_token).
"""

import asyncio
import hashlib
import json
import logging
import time

import httpx
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from jose import jwt
from pydantic import BaseModel

from domain.models.authentication_models import AuthorizationContext
from services.auth.authentication import get_authentication_service
from services.http_client import get_http_client_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github", tags=["GitHub Chat"])

# GitHub OAuth endpoints
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

# The Copilot GitHub App client ID — required for Device Flow to get
# tokens that work with the copilot_internal API. This is the same
# client ID used by VS Code, Neovim, JetBrains, and all open-source
# Copilot integrations (copilot.el, aider, etc.).
COPILOT_GITHUB_APP_CLIENT_ID = "Iv1.b507a08c87ecfe98"

# Copilot API endpoints
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_API_BASE = "https://api.githubcopilot.com"

# Cache for Copilot tokens: github_token_hash -> (copilot_token, expires_at)
# Bounded to 1000 entries, TTL 25 min (tokens last ~30 min, refresh early)
_copilot_token_cache: TTLCache = TTLCache(maxsize=1000, ttl=1500)

# Cache for MCP tokens (Fabric API + OneLake) keyed by SHA-256 of the
# user's Fabric workload token. The underlying Entra tokens live ~1 h so
# caching for 5 min is safe and massively reduces OBO calls during the
# workspace preload fan-out (~20 concurrent requests per page load).
# Bounded to 500 entries to survive many concurrent users without
# unbounded memory growth.
_mcp_token_cache: TTLCache = TTLCache(maxsize=500, ttl=300)
# In-flight dedup: when N parallel callers arrive with the same fabric
# token and there is no cached entry, only the first one runs the OBO;
# the rest await the shared future. Avoids the thundering-herd of 20
# MSAL calls at page load even when the MSAL in-memory cache is cold.
_mcp_token_inflight: dict[str, asyncio.Future[dict[str, str] | None]] = {}


def _shared_client() -> httpx.AsyncClient:
    """Return the process-wide pooled ``httpx.AsyncClient``.

    We used to spin up a new ``async with httpx.AsyncClient(...)`` for every
    GitHub/Copilot call. That bypasses connection pooling, triggers a full
    TLS handshake per request, and leaks TCP sockets under load. The
    ``HttpClientService`` singleton is owned by ``ServiceRegistry`` and
    lives for the app's lifetime; per-call timeouts are passed explicitly
    at each call site to preserve the prior per-endpoint behavior.
    """
    return get_http_client_service().raw_client


async def _get_copilot_token(github_token: str) -> str:
    """Exchange a GitHub access token for a short-lived Copilot API token.

    Copilot tokens expire after ~30 minutes. We cache them to avoid
    re-exchanging on every request.
    """
    # SHA-256 prevents cache collisions (Python's built-in ``hash()`` is
    # only 64-bit and randomized per interpreter, which makes it unsuitable
    # as a cache key for security-sensitive material like access tokens).
    cache_key = hashlib.sha256(github_token.encode("utf-8")).hexdigest()

    # Check cache
    if cache_key in _copilot_token_cache:
        token, expires_at = _copilot_token_cache[cache_key]
        if time.time() < expires_at - 60:  # refresh 60s before expiry
            return token

    resp = await _shared_client().get(
        COPILOT_TOKEN_URL,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/json",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.25.0",
        },
        timeout=15.0,
    )

    if resp.status_code == 401:
        _copilot_token_cache.pop(cache_key, None)
        raise HTTPException(status_code=401, detail="GitHub token invalid or expired. Please sign in again.")

    if resp.status_code == 403:
        raise HTTPException(
            status_code=403,
            detail="No GitHub Copilot subscription found. You need an active Copilot Individual, Business, or Enterprise subscription."
        )
    if resp.status_code != 200:
        logger.error("Copilot token exchange failed (%d): %s", resp.status_code, resp.text[:300])
        raise HTTPException(status_code=502, detail=f"Copilot token exchange failed: {resp.status_code}")

    data = resp.json()
    copilot_token = data.get("token")
    expires_at = data.get("expires_at", 0)

    if not copilot_token:
        raise HTTPException(status_code=502, detail="No token in Copilot response")

    # Cache it
    _copilot_token_cache[cache_key] = (copilot_token, expires_at)
    logger.info("Obtained Copilot token, expires_at=%s", expires_at)

    return copilot_token


def _extract_github_token(request: Request) -> str:
    """Extract the GitHub token from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    if auth_header.startswith("token "):
        return auth_header[6:]
    if auth_header:
        return auth_header
    raise HTTPException(status_code=401, detail="Missing Authorization header with GitHub token")


# --- Pydantic Models ---

class DeviceCodeResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    # GitHub also returns a ``verification_uri_complete`` URL that has
    # the user code pre-embedded. Opening *that* URL sends the user
    # straight to the Authorize screen (one click) instead of a blank
    # prompt where they must paste the code. The field is optional
    # because older GitHub apps historically didn't return it.
    verification_uri_complete: str | None = None
    expires_in: int
    interval: int


class PollTokenRequest(BaseModel):
    device_code: str


class PollTokenResponse(BaseModel):
    status: str  # "pending", "complete", "expired", "error"
    access_token: str | None = None
    error: str | None = None
    github_user: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = True
    max_tokens: int | None = 4096
    temperature: float | None = 0.7
    tools_enabled: bool = True


# --- MCP Manager (set during app startup) ---

from services.mcp.mcp_client_manager import MCPClientManager

_mcp_manager: MCPClientManager | None = None


def set_mcp_manager(manager: MCPClientManager) -> None:
    """Called from main.py at startup to inject the MCPClientManager."""
    global _mcp_manager
    _mcp_manager = manager


# --- Endpoints ---

@router.post("/device-code", response_model=DeviceCodeResponse)
async def start_device_flow():
    """Start GitHub Device Flow — returns a user code for github.com/login/device."""

    resp = await _shared_client().post(
        GITHUB_DEVICE_CODE_URL,
        data={"client_id": COPILOT_GITHUB_APP_CLIENT_ID, "scope": ""},
        headers={"Accept": "application/json"},
    )

    if resp.status_code != 200:
        logger.error("GitHub device code request failed: %s", resp.text)
        raise HTTPException(status_code=502, detail="Failed to start GitHub device flow")

    data = resp.json()
    return DeviceCodeResponse(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        verification_uri_complete=data.get("verification_uri_complete"),
        expires_in=data["expires_in"],
        interval=data["interval"],
    )


@router.post("/poll-token", response_model=PollTokenResponse)
async def poll_token(req: PollTokenRequest):
    """Poll GitHub for the access token after user enters the device code."""

    client = _shared_client()
    resp = await client.post(
        GITHUB_TOKEN_URL,
        data={
            "client_id": COPILOT_GITHUB_APP_CLIENT_ID,
            "device_code": req.device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        headers={"Accept": "application/json"},
    )

    if resp.status_code != 200:
        return PollTokenResponse(status="error", error="GitHub token endpoint returned non-200")

    data = resp.json()

    if "error" in data:
        error = data["error"]
        if error in ("authorization_pending", "slow_down"):
            return PollTokenResponse(status="pending")
        elif error == "expired_token":
            return PollTokenResponse(status="expired", error="Device code expired")
        elif error == "access_denied":
            return PollTokenResponse(status="error", error="User denied access")
        else:
            return PollTokenResponse(status="error", error=data.get("error_description", error))

    access_token = data.get("access_token")
    if not access_token:
        return PollTokenResponse(status="error", error="No access token in response")

    # Fetch GitHub username
    github_user = None
    user_resp = await client.get(
        GITHUB_USER_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    if user_resp.status_code == 200:
        github_user = user_resp.json().get("login")

    return PollTokenResponse(status="complete", access_token=access_token, github_user=github_user)


@router.get("/models")
async def list_models(request: Request):
    """List available models from the Copilot API.

    Returns the full catalog including Claude, GPT, Gemini, etc.
    """
    github_token = _extract_github_token(request)
    copilot_token = await _get_copilot_token(github_token)

    resp = await _shared_client().get(
        f"{COPILOT_API_BASE}/models",
        headers={
            "Authorization": f"Bearer {copilot_token}",
            "Accept": "application/json",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.25.0",
        },
        timeout=15.0,
    )

    if resp.status_code != 200:
        logger.error("Copilot models request failed (%d): %s", resp.status_code, resp.text[:300])
        raise HTTPException(status_code=resp.status_code, detail=f"Copilot API error: {resp.text[:200]}")

    data = resp.json()
    model_list = data.get("data", data.get("models", data if isinstance(data, list) else []))

    models = []
    for m in model_list:
        mid = m.get("id") or m.get("name", "unknown")
        models.append({
            "id": mid,
            "name": m.get("name") or mid,
            "publisher": m.get("owned_by") or m.get("publisher", ""),
            "version": m.get("version", ""),
            "capabilities": m.get("capabilities", {}),
        })

    return {"models": models}


@router.post("/chat/completions")
async def chat_completions(chat_req: ChatRequest, request: Request):
    """Chat completions with optional agentic tool execution via MCP."""
    logger.info("[CHAT] ──── New chat request ────")
    logger.info("[CHAT] Model: %s, messages: %d, tools_enabled: %s, stream: %s",
                chat_req.model, len(chat_req.messages), chat_req.tools_enabled, chat_req.stream)
    for i, m in enumerate(chat_req.messages):
        logger.info("[CHAT] Message[%d] role=%s content='%s'", i, m.role, m.content[:150])

    github_token = _extract_github_token(request)
    copilot_token = await _get_copilot_token(github_token)

    # Extract optional Fabric token for MCP tool execution
    fabric_header = request.headers.get("X-Fabric-Token", "")
    fabric_token = fabric_header.removeprefix("Bearer ").strip() or None
    logger.info("[CHAT] Fabric token present: %s", bool(fabric_token))

    has_manager = _mcp_manager is not None
    has_tools = _mcp_manager.has_tools() if _mcp_manager is not None else False
    use_tools = chat_req.tools_enabled and has_manager and has_tools
    logger.info("[CHAT] MCP manager: %s, has_tools: %s, tools_enabled: %s → use_tools: %s",
                has_manager, has_tools, chat_req.tools_enabled, use_tools)

    # Exchange the workload token for properly-scoped tokens via OBO
    mcp_tokens = None
    caller_ctx = None
    if use_tools and fabric_token:
        mcp_tokens = await _acquire_mcp_tokens(fabric_token)
        logger.info("[CHAT] OBO tokens acquired: %s",
                    list(mcp_tokens.keys()) if mcp_tokens else "None")
        # Build a CallerContext from the VERIFIED fabric JWT claims. The
        # tool runtime uses this to strip any LLM-supplied tenant/user/oid
        # arguments and to key kill-switches. workspace_id is left None in
        # the plain-chat path because the user may be asking about any
        # workspace they have access to; the OBO token itself bounds data
        # access to the caller's permissions.
        try:
            from services.agenthub.tool_runtime import CallerContext
            unverified = jwt.get_unverified_claims(fabric_token)
            tid = unverified.get("tid") or ""
            oid = unverified.get("oid") or ""
            upn = unverified.get("upn") or unverified.get("preferred_username")
            if tid and oid:
                caller_ctx = CallerContext(
                    tenant_id=tid, user_id=oid, user_upn=upn,
                    workspace_id=None, session_id=f"chat-{oid[:8]}",
                )
        except Exception:
            logger.warning("[CHAT] Could not build CallerContext from fabric token", exc_info=True)

    if use_tools:
        # Agentic mode: tool-call loop then stream final response
        return StreamingResponse(
            _stream_agentic_chat(copilot_token, chat_req, mcp_tokens, caller_ctx),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    else:
        # Passthrough mode (existing behavior — no tools)
        body = {
            "model": chat_req.model,
            "messages": [{"role": m.role, "content": m.content} for m in chat_req.messages],
            "max_tokens": chat_req.max_tokens,
            "temperature": chat_req.temperature,
            "stream": chat_req.stream,
        }
        copilot_headers = _copilot_headers(copilot_token)

        if chat_req.stream:
            return StreamingResponse(
                _stream_chat(copilot_headers, body),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        else:
            resp = await _shared_client().post(
                f"{COPILOT_API_BASE}/chat/completions",
                json=body,
                headers=copilot_headers,
                timeout=60.0,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])
            return resp.json()


# --- Copilot API helpers ---

MAX_TOOL_ROUNDS = 10
AGENTIC_REQUEST_TIMEOUT = 300  # 5 minutes total

# Scopes for OBO token exchange
_FABRIC_API_SCOPES = ["https://api.fabric.microsoft.com/.default"]
_POWERBI_API_SCOPES = [
    "https://analysis.windows.net/powerbi/api/Dataset.ReadWrite.All",
    "https://analysis.windows.net/powerbi/api/Report.ReadWrite.All",
]
_ONELAKE_SCOPES = ["https://storage.azure.com/.default"]
# Azure Management and Microsoft Graph are acquired best-effort so diagnostic
# agents can triage capacity, RBAC, Entra identity, and surrounding Azure
# resources. Failures remain non-fatal; Fabric-only missions can proceed with
# the Fabric / Power BI / OneLake tokens.
_AZURE_MANAGEMENT_SCOPES = ["https://management.azure.com/.default"]
_PBI_SCOPES = ["https://analysis.windows.net/powerbi/api/.default"]
_GRAPH_API_SCOPES = ["https://graph.microsoft.com/.default"]

# Model to fall back to for tool-calling when the primary model doesn't support it
TOOL_CALLING_FALLBACK_MODEL = "gpt-4o"

SYSTEM_PROMPT = (
    "You are AgentHub, an AI assistant integrated into a Microsoft Fabric workload. "
    "You have access to tools that can interact with the user's Microsoft Fabric environment. "
    "When the user asks you to perform an action or retrieve information from Fabric, "
    "use the available tools. Always confirm destructive actions before executing them. "
    "IMPORTANT: All tool parameters named 'workspace_id' and 'item_id' require GUIDs (UUIDs), "
    "NOT display names. Use fabric_list_workspaces to discover workspace GUIDs, "
    "and fabric_list_items to discover item GUIDs. The user's current workspace and item "
    "IDs are provided in the system context — use those directly when the user says "
    "'this workspace' or 'current workspace'.\n\n"
    "ITEM CARDS: When referencing specific Fabric items in your response, render them as "
    "clickable cards using this exact syntax: [[item:WORKSPACE_ID|ITEM_ID|DISPLAY_NAME|ITEM_TYPE|WEB_URL]]\n"
    "Example: [[item:8bdca8af-1db1-4fd8-9564-0c98b4dbdffc|a325c1d9-53b5-4be9-8f80-06b7079ae289|My Lakehouse|Lakehouse|https://app.powerbi.com/groups/...]]\n"
    "Valid ITEM_TYPE values: Lakehouse, Notebook, Report, Dashboard, DataPipeline, SQLEndpoint, "
    "Warehouse, SemanticModel, Eventstream, KQLDatabase.\n"
    "When a tool result includes 'webUrl', always copy that exact URL into the marker instead of reconstructing it.\n"
    "Use item cards when listing items, showing newly created items, or when the user asks "
    "to 'show me' an item. Place each card on its own line."
)


async def _acquire_mcp_tokens(fabric_token: str) -> dict[str, str] | None:
    """Exchange the user's workload token for Fabric API, Power BI API, and OneLake tokens via OBO.

    Wraps ``_do_acquire_mcp_tokens`` with a TTL cache + in-flight dedup
    so the 20-ish parallel workspace preload requests that fire on page
    load collapse into a single OBO exchange. MSAL's in-memory cache
    already makes repeat exchanges cheap at the network layer, but we
    still paid decode/authority/log overhead and spammed the log
    ~20×/page. The cache lives 5 min; the underlying Entra tokens live
    ~1 h, so refresh pressure is minimal.
    """
    if not fabric_token:
        return None
    key = hashlib.sha256(fabric_token.encode("utf-8")).hexdigest()

    # Fast path: fresh cache hit.
    cached = _mcp_token_cache.get(key)
    if cached is not None:
        return cached

    # Another coroutine is already running the OBO — await its result.
    inflight = _mcp_token_inflight.get(key)
    if inflight is not None:
        return await inflight

    # We're the first; register our future and do the work.
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, str] | None] = loop.create_future()
    _mcp_token_inflight[key] = fut
    try:
        tokens = await _do_acquire_mcp_tokens(fabric_token)
        if tokens:
            _mcp_token_cache[key] = tokens
        fut.set_result(tokens)
        return tokens
    except Exception as exc:  # propagate to waiters, then re-raise
        if not fut.done():
            fut.set_exception(exc)
        raise
    finally:
        _mcp_token_inflight.pop(key, None)


async def _do_acquire_mcp_tokens(fabric_token: str) -> dict[str, str] | None:
    """Exchange the user's workload token for Fabric API, Power BI API, and OneLake tokens via OBO.

    ``AuthenticationService.get_access_token_on_behalf_of`` already offloads
    the synchronous MSAL call via ``asyncio.to_thread``, so we just fan the
    two scope exchanges out in parallel with ``asyncio.gather``.

    Logging here is deliberately terse: one DEBUG trace at start, one
    INFO summary at end. Per-page-load we fire this ~20 times in
    parallel (workspace preload for ``@`` mention search), so every
    line here is multiplied by that fan-out.
    """
    logger.debug("[OBO] Starting token exchange for MCP tools")
    try:
        auth_service = get_authentication_service()
    except Exception as e:
        logger.warning("[OBO] Auth service unavailable: %s", e)
        return None

    # Decode the tenant ID from the token's claims
    try:
        unverified = jwt.get_unverified_claims(fabric_token)
        tenant_id = unverified.get("tid")
        user_id = unverified.get("oid", "unknown")
        aud = unverified.get("aud", "unknown")
        logger.debug("[OBO] Token claims: tid=%s, oid=%s, aud=%s", tenant_id, user_id, aud)
        # Bind the user id into the correlation contextvar so every log line
        # emitted from this request (and threads/tasks derived from it) is
        # tagged with u:<oid> — these endpoints bypass require_user.
        if user_id and user_id != "unknown":
            from services.correlation import set_user_id
            set_user_id(user_id)
        if not tenant_id:
            logger.warning("[OBO] No 'tid' claim in Fabric token, cannot do OBO")
            return None
    except Exception as e:
        logger.warning("[OBO] Failed to decode token claims: %s", e)
        return None

    # Build a minimal AuthorizationContext for OBO
    auth_context = AuthorizationContext(
        original_subject_token=fabric_token,
        tenant_object_id=tenant_id,
    )

    async def _obo(scopes: list[str], label: str) -> tuple[str, str | None]:
        try:
            tok = await auth_service.get_access_token_on_behalf_of(
                auth_context, scopes
            )
            logger.debug("[OBO] %s token acquired (%d chars)", label, len(tok))
            return label, tok
        except Exception as e:
            logger.warning("[OBO] %s OBO failed: %s", label, e)
            return label, None

    results = await asyncio.gather(
        _obo(_FABRIC_API_SCOPES, "Fabric API"),
        _obo(_POWERBI_API_SCOPES, "Power BI API"),
        _obo(_ONELAKE_SCOPES, "OneLake"),
        _obo(_PBI_SCOPES, "Power BI"),
        _obo(_AZURE_MANAGEMENT_SCOPES, "Azure Management"),
        _obo(_GRAPH_API_SCOPES, "Microsoft Graph"),
    )
    tokens: dict[str, str] = {}
    label_to_key = {
        "Fabric API": "FABRIC_API_TOKEN",
        "Power BI API": "POWERBI_API_TOKEN",
        "OneLake": "ONELAKE_TOKEN",
        "Azure Management": "AZURE_MANAGEMENT_TOKEN",
        "Power BI": "PBI_API_TOKEN",
        "Microsoft Graph": "GRAPH_API_TOKEN",
    }
    for label, tok in results:
        if tok:
            tokens[label_to_key[label]] = tok

    # One summary line per OBO miss (callers with an unexpired cache
    # entry skip this entirely). Downgrade to WARNING if neither token
    # was acquired so failures remain visible.
    if tokens:
        logger.info(
            "[OBO] Token exchange complete (oid=%s): %s",
            user_id, list(tokens.keys()),
        )
    else:
        logger.warning("[OBO] Token exchange produced no tokens (oid=%s)", user_id)
    return tokens if tokens else None


def _copilot_headers(copilot_token: str) -> dict:
    return {
        "Authorization": f"Bearer {copilot_token}",
        "Content-Type": "application/json",
        "Copilot-Integration-Id": "vscode-chat",
        "Editor-Version": "vscode/1.100.0",
        "Editor-Plugin-Version": "copilot-chat/0.25.0",
    }


async def _call_copilot_api(copilot_token: str, body: dict) -> dict:
    """Non-streaming call to the Copilot API. Used during tool-call rounds."""
    # Log the full request for debugging
    tool_names = [t["function"]["name"] for t in body.get("tools", [])]
    logger.info("[COPILOT-REQ] model=%s, messages=%d, tools=%s, tool_choice=%s",
                body.get("model"), len(body.get("messages", [])),
                tool_names, body.get("tool_choice", "none"))

    resp = await _shared_client().post(
        f"{COPILOT_API_BASE}/chat/completions",
        json=body,
        headers=_copilot_headers(copilot_token),
        timeout=60.0,
    )
    if resp.status_code != 200:
        logger.error("[COPILOT-RESP] HTTP %d: %s", resp.status_code, resp.text[:500])
        raise HTTPException(status_code=resp.status_code, detail=f"Copilot API error: {resp.text[:300]}")

    data = resp.json()
    # Log the full response structure
    choices = data.get("choices", [])
    if choices:
        c = choices[0]
        msg = c.get("message", {})
        logger.info("[COPILOT-RESP] finish_reason=%s, role=%s, has_content=%s, has_tool_calls=%s",
                    c.get("finish_reason"), msg.get("role"),
                    bool(msg.get("content")), bool(msg.get("tool_calls")))
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                logger.info("[COPILOT-RESP] tool_call: id=%s, function=%s, args=%s",
                            tc.get("id"), tc.get("function", {}).get("name"),
                            tc.get("function", {}).get("arguments", "")[:200])
        else:
            logger.info("[COPILOT-RESP] content preview: '%s'", (msg.get("content") or "")[:200])
            # Log all keys in the response for debugging
            logger.info("[COPILOT-RESP] response keys: %s, choice keys: %s, message keys: %s",
                        list(data.keys()), list(c.keys()), list(msg.keys()))
    return data


async def _stream_agentic_chat(copilot_token: str, chat_req: ChatRequest, mcp_tokens: dict | None, caller_ctx=None):
    """Agentic loop: tool-call rounds (non-streaming) then stream final response.

    Emits SSE events:
      {"type": "status", "content": "..."} — tool execution progress
      data: ... (standard OpenAI SSE chunks) — streamed final response
    """
    messages = [{"role": m.role, "content": m.content} for m in chat_req.messages]
    assert _mcp_manager is not None, "_stream_agentic_chat invoked without an MCP manager (should be gated by use_tools)"
    tools = _mcp_manager.get_openai_tools_schema()

    logger.info("[AGENT] Starting agentic chat with %d tools, %d input messages, tokens=%s",
                len(tools), len(messages), list(mcp_tokens.keys()) if mcp_tokens else "None")
    # Log each tool schema being sent
    for t in tools:
        fn = t.get("function", {})
        logger.info("[AGENT] Tool schema: %s — params: %s",
                    fn.get("name"), json.dumps(fn.get("parameters", {}))[:200])

    # Merge agent instructions into the system prompt.
    #
    # SECURITY — the frontend may POST a ``{"role": "system", ...}`` message
    # carrying workspace/item context. That content is CLIENT-SUPPLIED and
    # therefore UNTRUSTED: a tampered frontend could place jailbreak text
    # in there. We keep our trusted ``SYSTEM_PROMPT`` at the top and append
    # the frontend's content as **fenced** client context so the model
    # treats it as data, not a continuation of our authoritative
    # instructions. Tool-call arguments and OBO scopes continue to come
    # from the Fabric token (verified), never from the model's output.
    from services.agenthub.attachments import (
        CLIENT_CONTEXT_SHIELD_PROMPT,
        fence_client_context,
    )

    existing_system = next((m for m in messages if m.get("role") == "system"), None)
    if existing_system:
        existing_system["content"] = (
            f"{SYSTEM_PROMPT}\n\n"
            f"{CLIENT_CONTEXT_SHIELD_PROMPT}\n\n"
            f"{fence_client_context(existing_system.get('content', ''))}"
        )
    else:
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    # Determine which model to use for tool-calling rounds.
    # Some models (e.g. Claude via Copilot API) don't return tool_calls in the response.
    # We use GPT-4o for tool-call detection, then stream the final answer with the user's chosen model.
    user_model = chat_req.model
    tool_model = user_model
    if any(prefix in user_model.lower() for prefix in ("claude",)):
        tool_model = TOOL_CALLING_FALLBACK_MODEL
        logger.info("[AGENT] Model '%s' doesn't support tool_calls via Copilot API. "
                    "Using '%s' for tool rounds, '%s' for final response.",
                    user_model, tool_model, user_model)

    for round_num in range(MAX_TOOL_ROUNDS):
        # Non-streaming call to detect tool_calls
        body = {
            "model": tool_model,
            "messages": messages,
            "max_tokens": chat_req.max_tokens,
            "temperature": chat_req.temperature,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        logger.info("[AGENT] Round %d: sending %d tools, %d messages, model=%s",
                    round_num + 1, len(tools) if tools else 0, len(messages), tool_model)

        try:
            response = await _call_copilot_api(copilot_token, body)
        except HTTPException as e:
            logger.error("[AGENT] Round %d: Copilot API error: %s", round_num + 1, e.detail)
            yield f'data: {json.dumps({"type": "status", "content": f"LLM error: {e.detail}"})}\n\n'
            yield 'data: [DONE]\n\n'
            return

        choice = response.get("choices", [{}])[0]
        finish_reason = choice.get("finish_reason", "unknown")
        assistant_msg = choice.get("message", {})
        has_tool_calls = bool(assistant_msg.get("tool_calls"))
        content_preview = (assistant_msg.get("content") or "")[:200]

        logger.info("[AGENT] Round %d result: finish_reason=%s, has_tool_calls=%s",
                    round_num + 1, finish_reason, has_tool_calls)
        logger.info("[AGENT] Round %d content: '%s'", round_num + 1, content_preview)

        # If no tool calls, this is the final response — stream it
        if not has_tool_calls:
            logger.info("[AGENT] Round %d: No tool calls — generating final response", round_num + 1)

            # If we used a different model for tool rounds, re-generate final answer
            # with the user's preferred model (streaming) for better quality.
            if tool_model != user_model and round_num > 0:
                logger.info("[AGENT] Switching to user model '%s' for final streamed response", user_model)
                final_body = {
                    "model": user_model,
                    "messages": messages,
                    "max_tokens": chat_req.max_tokens,
                    "temperature": chat_req.temperature,
                    "stream": True,
                }
                async for line in _stream_chat(_copilot_headers(copilot_token), final_body):
                    yield line
                return

            # Otherwise just return the tool_model's response directly
            content = assistant_msg.get("content", "")
            if content:
                chunk = {
                    "choices": [{
                        "delta": {"content": content},
                        "index": 0,
                        "finish_reason": None,
                    }]
                }
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # Process tool calls
        logger.info("[AGENT] Round %d: processing %d tool calls", round_num + 1, len(assistant_msg["tool_calls"]))
        messages.append(assistant_msg)

        for tool_call in assistant_msg["tool_calls"]:
            fn = tool_call.get("function", {})
            tool_name = fn.get("name", "unknown")
            tool_args_str = fn.get("arguments", "{}")

            logger.info("[AGENT] Calling tool: %s with args: %s", tool_name, tool_args_str[:300])
            yield f'data: {json.dumps({"type": "status", "content": f"Calling tool: {tool_name}..."})}\n\n'

            try:
                tool_args = json.loads(tool_args_str)
            except json.JSONDecodeError:
                tool_args = {}

            if caller_ctx is None:
                # Should not happen when use_tools+fabric_token are true,
                # but guard against misconfiguration — deny the call rather
                # than dispatch without a verified caller identity.
                tool_result = (
                    f"POLICY_DENIED: cannot execute {tool_name!r} without a "
                    f"verified caller context. Ensure the fabric token is "
                    f"present."
                )
                logger.warning("[AGENT] No caller_ctx — refusing dispatch of %s", tool_name)
            else:
                from services.agenthub import tool_runtime
                rt_result = await tool_runtime.execute(
                    tool_name=tool_name,
                    arguments=tool_args,
                    ctx=caller_ctx,
                    mcp_manager=_mcp_manager,
                    mcp_tokens=mcp_tokens,
                )
                tool_result = rt_result.output
                logger.info(
                    "[AGENT] Tool %s decision=%s ok=%s (%d chars)",
                    tool_name, rt_result.policy_decision, rt_result.ok,
                    len(tool_result),
                )

            yield f'data: {json.dumps({"type": "status", "content": f"Tool {tool_name} completed. Thinking..."})}\n\n'

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": tool_result,
            })

        # Continue loop — next round will see tool results

    # Hit safety limit
    limit_msg = "I've reached the maximum number of tool-use rounds. Here is what I found so far based on the tool results above."
    yield f'data: {json.dumps({"choices": [{"delta": {"content": limit_msg}, "index": 0, "finish_reason": "stop"}]})}\n\n'
    yield "data: [DONE]\n\n"


async def _stream_chat(headers: dict, body: dict):
    """Stream SSE chunks from the Copilot API (passthrough, no tools)."""
    async with _shared_client().stream(
        "POST",
        f"{COPILOT_API_BASE}/chat/completions",
        json=body,
        headers=headers,
        timeout=120.0,
    ) as resp:
        if resp.status_code != 200:
            error_body = await resp.aread()
            yield f'data: {{"error": "{error_body.decode()[:200]}"}}\n\n'
            return
        async for line in resp.aiter_lines():
            if line:
                yield f"{line}\n\n"


# --- Branch-out name suggestions ---------------------------------------

class BranchSuggestRequest(BaseModel):
    task_text: str
    source_workspace_name: str | None = None
    context_names: list[str] = []
    file_names: list[str] = []


class BranchSuggestResponse(BaseModel):
    branch_name: str
    workspace_name: str


@router.post("/suggest-branch-names", response_model=BranchSuggestResponse)
async def suggest_branch_names(req: BranchSuggestRequest, request: Request):
    """Generate a concise git branch name + child workspace name from a
    task description + attached context.

    Uses a tiny Copilot prompt with ``gpt-4o-mini`` (fastest model in
    Copilot's catalog) and a small ``max_tokens`` budget so latency is
    typically 300–800 ms. The endpoint returns deterministic JSON; on
    ANY failure (timeout, malformed model response, missing token) we
    return the client-side fallback it already computed.
    """
    github_token = _extract_github_token(request)
    copilot_token = await _get_copilot_token(github_token)

    task = (req.task_text or "").strip()[:800]
    ctx = ", ".join(n for n in (req.context_names or []) if n)[:300]
    files = ", ".join(n for n in (req.file_names or []) if n)[:200]
    src = (req.source_workspace_name or "").strip()[:80]

    system = (
        "You generate concise names for a Fabric branch-out from a task. "
        "Return ONLY compact JSON matching this shape: "
        '{"branch_name": "feature/<kebab-3-5-words>", "workspace_name": "<Title Case 2-5 words>"} . '
        "Rules: branch_name lowercase kebab-case, prefix with 'feature/', no punctuation except '/' and '-'. "
        "workspace_name Title Case, short phrase summarising the goal. "
        "Base both names on the task intent + any listed items/files. Do NOT include the source workspace name."
    )
    user = (
        f"Task: {task}\n"
        f"Source workspace: {src or '(unknown)'}\n"
        f"Attached items: {ctx or '(none)'}\n"
        f"Attached files: {files or '(none)'}\n"
        "Respond with JSON only."
    )

    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 80,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {copilot_token}",
        "Content-Type": "application/json",
        "Copilot-Integration-Id": "vscode-chat",
        "Editor-Version": "vscode/1.100.0",
    }

    try:
        resp = await _shared_client().post(
            f"{COPILOT_API_BASE}/chat/completions",
            json=body,
            headers=headers,
            timeout=6.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"copilot {resp.status_code}")
        data = resp.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        # Strip potential markdown fences.
        if content.startswith("```"):
            content = content.strip("`")
            # Drop a leading "json\n" language marker if present.
            if content.lower().startswith("json"):
                content = content[4:].lstrip()
        parsed = json.loads(content)
        branch = str(parsed.get("branch_name") or "").strip()
        ws = str(parsed.get("workspace_name") or "").strip()
        if not branch or not ws:
            raise ValueError("missing fields")
        # Light sanitisation: enforce basic shape so a misbehaving model
        # can't inject whitespace-only or absurdly long strings.
        if len(branch) > 64:
            branch = branch[:64].rstrip("-/")
        if len(ws) > 80:
            ws = ws[:80].rstrip()
        return BranchSuggestResponse(branch_name=branch, workspace_name=ws)
    except Exception as exc:
        logger.info("suggest-branch-names fallback (%s)", exc)
        # Return a 200 with empty strings so the frontend keeps its
        # local heuristic suggestion — no user-facing error needed.
        return BranchSuggestResponse(branch_name="", workspace_name="")

