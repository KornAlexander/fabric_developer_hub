"""GitHub Copilot API integration via Device Flow authentication.

Handles:
- GitHub OAuth Device Flow (no popups needed in sandboxed iframes)
- Exchanging GitHub tokens for short-lived Copilot API tokens
- Listing available models from the Copilot API (Claude, GPT, Gemini, etc.)
- Proxying chat completions to the Copilot API
- Agentic tool-call loop with OBO-authenticated Fabric/OneLake tokens
"""

import asyncio
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


async def _get_copilot_token(github_token: str) -> str:
    """Exchange a GitHub access token for a short-lived Copilot API token.

    Copilot tokens expire after ~30 minutes. We cache them to avoid
    re-exchanging on every request.
    """
    cache_key = str(hash(github_token))

    # Check cache
    if cache_key in _copilot_token_cache:
        token, expires_at = _copilot_token_cache[cache_key]
        if time.time() < expires_at - 60:  # refresh 60s before expiry
            return token

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            COPILOT_TOKEN_URL,
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/json",
                "Editor-Version": "vscode/1.100.0",
                "Editor-Plugin-Version": "copilot-chat/0.25.0",
            },
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

_mcp_manager = None


def set_mcp_manager(manager) -> None:
    """Called from main.py at startup to inject the MCPClientManager."""
    global _mcp_manager
    _mcp_manager = manager


# --- Endpoints ---

@router.post("/device-code", response_model=DeviceCodeResponse)
async def start_device_flow():
    """Start GitHub Device Flow — returns a user code for github.com/login/device."""

    async with httpx.AsyncClient() as client:
        resp = await client.post(
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
        expires_in=data["expires_in"],
        interval=data["interval"],
    )


@router.post("/poll-token", response_model=PollTokenResponse)
async def poll_token(req: PollTokenRequest):
    """Poll GitHub for the access token after user enters the device code."""

    async with httpx.AsyncClient() as client:
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
    async with httpx.AsyncClient() as client:
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

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{COPILOT_API_BASE}/models",
            headers={
                "Authorization": f"Bearer {copilot_token}",
                "Accept": "application/json",
                "Copilot-Integration-Id": "vscode-chat",
                "Editor-Version": "vscode/1.100.0",
                "Editor-Plugin-Version": "copilot-chat/0.25.0",
            },
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
    has_tools = _mcp_manager.has_tools() if has_manager else False
    use_tools = chat_req.tools_enabled and has_manager and has_tools
    logger.info("[CHAT] MCP manager: %s, has_tools: %s, tools_enabled: %s → use_tools: %s",
                has_manager, has_tools, chat_req.tools_enabled, use_tools)

    # Exchange the workload token for properly-scoped tokens via OBO
    mcp_tokens = None
    if use_tools and fabric_token:
        mcp_tokens = await _acquire_mcp_tokens(fabric_token)
        logger.info("[CHAT] OBO tokens acquired: %s",
                    list(mcp_tokens.keys()) if mcp_tokens else "None")

    if use_tools:
        # Agentic mode: tool-call loop then stream final response
        return StreamingResponse(
            _stream_agentic_chat(copilot_token, chat_req, mcp_tokens),
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
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{COPILOT_API_BASE}/chat/completions",
                    json=body,
                    headers=copilot_headers,
                )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])
            return resp.json()


# --- Copilot API helpers ---

MAX_TOOL_ROUNDS = 10
AGENTIC_REQUEST_TIMEOUT = 300  # 5 minutes total

# Scopes for OBO token exchange
_FABRIC_API_SCOPES = ["https://api.fabric.microsoft.com/.default"]
_ONELAKE_SCOPES = ["https://storage.azure.com/.default"]

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
    """Exchange the user's workload token for Fabric API + OneLake tokens via OBO.

    MSAL's ``acquire_token_on_behalf_of`` is synchronous and does a network
    round-trip to AAD. Calling it directly from the async handler blocks the
    event loop for the entire duration (~300-500 ms per scope), which
    serializes every other in-flight request on the worker. We run each OBO
    in a thread via ``asyncio.to_thread`` and fetch both scopes in parallel
    with ``asyncio.gather``.
    """
    logger.info("[OBO] Starting token exchange for MCP tools")
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
        logger.info("[OBO] Token claims: tid=%s, oid=%s, aud=%s", tenant_id, user_id, aud)
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
            # auth_service.get_access_token_on_behalf_of is itself async but
            # calls synchronous MSAL internally. Offload to a thread so it
            # does not block the event loop.
            tok = await asyncio.to_thread(
                _sync_obo, auth_service, auth_context, scopes
            )
            logger.info("[OBO] %s token acquired (%d chars)", label, len(tok))
            return label, tok
        except Exception as e:
            logger.warning("[OBO] %s OBO failed: %s", label, e)
            return label, None

    results = await asyncio.gather(
        _obo(_FABRIC_API_SCOPES, "Fabric API"),
        _obo(_ONELAKE_SCOPES, "OneLake"),
    )
    tokens: dict[str, str] = {}
    label_to_key = {"Fabric API": "FABRIC_API_TOKEN", "OneLake": "ONELAKE_TOKEN"}
    for label, tok in results:
        if tok:
            tokens[label_to_key[label]] = tok

    logger.info("[OBO] Token exchange complete: %s", list(tokens.keys()))
    return tokens if tokens else None


def _sync_obo(auth_service, auth_context, scopes):
    """Synchronous wrapper for use with ``asyncio.to_thread``.

    ``auth_service.get_access_token_on_behalf_of`` is declared ``async``
    but its body is CPU-bound/blocking MSAL — we run it in its own loop
    inside a worker thread.
    """
    return asyncio.run(auth_service.get_access_token_on_behalf_of(auth_context, scopes))


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

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{COPILOT_API_BASE}/chat/completions",
            json=body,
            headers=_copilot_headers(copilot_token),
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


async def _stream_agentic_chat(copilot_token: str, chat_req: ChatRequest, mcp_tokens: dict | None):
    """Agentic loop: tool-call rounds (non-streaming) then stream final response.

    Emits SSE events:
      {"type": "status", "content": "..."} — tool execution progress
      data: ... (standard OpenAI SSE chunks) — streamed final response
    """
    messages = [{"role": m.role, "content": m.content} for m in chat_req.messages]
    tools = _mcp_manager.get_openai_tools_schema()

    logger.info("[AGENT] Starting agentic chat with %d tools, %d input messages, tokens=%s",
                len(tools), len(messages), list(mcp_tokens.keys()) if mcp_tokens else "None")
    # Log each tool schema being sent
    for t in tools:
        fn = t.get("function", {})
        logger.info("[AGENT] Tool schema: %s — params: %s",
                    fn.get("name"), json.dumps(fn.get("parameters", {}))[:200])

    # Merge agent instructions into the system prompt.
    # The frontend may send a system message with context (workspace ID, item name).
    # We prepend our tool-usage instructions to that, or add a new system message if none exists.
    existing_system = next((m for m in messages if m.get("role") == "system"), None)
    if existing_system:
        existing_system["content"] = SYSTEM_PROMPT + "\n\n" + existing_system["content"]
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

            try:
                result = await _mcp_manager.call_tool(tool_name, tool_args, mcp_tokens)
                tool_result = str(result)
                logger.info("[AGENT] Tool %s succeeded, result length: %d chars, preview: '%s'",
                            tool_name, len(tool_result), tool_result[:200])
            except Exception as e:
                tool_result = f"Error executing {tool_name}: {str(e)}"
                logger.error("[AGENT] Tool %s failed: %s", tool_name, e, exc_info=True)

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
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{COPILOT_API_BASE}/chat/completions",
            json=body,
            headers=headers,
        ) as resp:
            if resp.status_code != 200:
                error_body = await resp.aread()
                yield f'data: {{"error": "{error_body.decode()[:200]}"}}\n\n'
                return
            async for line in resp.aiter_lines():
                if line:
                    yield f"{line}\n\n"
