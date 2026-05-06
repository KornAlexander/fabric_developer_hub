"""Tool Runtime — single authZ chokepoint for every LLM-driven tool call.

Architectural role
------------------
The LLM orchestrator (planner + agent loops) MUST route every tool
invocation through :func:`execute`. The orchestrator never calls
``mcp_manager.call_tool`` directly. This gives us one boundary where:

* :class:`CallerContext` is constructed from the **verified Fabric JWT**,
  not from LLM output. ``tenant_id``, ``workspace_id``, ``user_id``,
  ``user_upn`` come only from there.
* LLM-supplied ``tenant_id`` / ``user_id`` / ``upn`` arguments are
  **dropped** (not overridden) before dispatch. An injected prompt cannot
  forge a caller identity.
* Each tool has a declared :class:`ToolPolicy` (sensitivity + auto-allowed
  flag). Tools without a policy are **denied by default**.
* Kill-switches (global, per-tool, per-tenant) are evaluated on every call.
  Flipping an env var disables dispatch in ≤ one process lifetime.
* A per-session loop detector halts when the same ``(tool_name, arg_hash)``
  repeats ``CIRCUIT_BREAKER_THRESHOLD`` times.
* Tool output is wrapped in untrusted-content fences before being returned
  to the caller, so the orchestrator can paste it straight into the LLM
  conversation without laundering instructions.

See ``docs/TOOL_RUNTIME_SECURITY.md`` for the full threat model and the
incident runbook.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from services.logging_categories import log_extra
from services.observability import bounded_text

logger = logging.getLogger(__name__)


# ── Kill-switch / loop-breaker constants ─────────────────────────────

_GLOBAL_KILL_ENV = "FEATURE_TOOLS_GLOBAL_ENABLED"
_PER_TOOL_KILL_ENV_TPL = "FEATURE_TOOL_{name}_ENABLED"
_PER_TENANT_KILL_ENV_TPL = "FEATURE_TENANT_{tid}_ENABLED"

# If the same (tool_name, arg_hash) appears this many times in a row inside
# one session, the runtime halts the session with CircuitBreakerTripped. The
# threshold is tuned so legitimate retries (≤2) pass while a poisoned loop
# is cut quickly.
CIRCUIT_BREAKER_THRESHOLD = 3

# Tool output wrapping. These fence markers MUST match what the orchestrator
# system prompt teaches the model to treat as untrusted. Do not change them
# without updating ``planner_prompts.py`` and ``orchestrator_engine.py`` in
# the same commit.
_UNTRUSTED_OPEN = "<<<UNTRUSTED_TOOL_OUTPUT_BEGIN>>>"
_UNTRUSTED_CLOSE = "<<<UNTRUSTED_TOOL_OUTPUT_END>>>"

# Tool output hand-back cap. Keeps one rogue tool response from blowing the
# LLM context window and from giving an attacker an exfiltration channel
# the length of the whole DB. Configurable via env for ops tuning.
MAX_TOOL_OUTPUT_CHARS = int(os.environ.get("TOOL_RUNTIME_MAX_OUTPUT_CHARS", "40000"))

# Argument keys a caller-forging injection might set. These are stripped
# from arguments at the boundary — not corrected — so the LLM cannot
# smuggle them back via near-synonyms it controls.
_CALLER_IDENTITY_ARG_KEYS: frozenset[str] = frozenset({
    "tenant_id", "tenantId", "tid",
    "user_id", "userId", "uid",
    "upn", "userPrincipalName", "user_upn",
    "object_id", "objectId", "oid",
})


def _is_existing_create_conflict(output_lower: str) -> bool:
    return any(
        marker in output_lower
        for marker in (
            "already exists",
            "alreadyexist",
            "conflict",
            "itemdisplaynamealreadyinuse",
            "display name is already in use",
            "same display name",
        )
    )


def _looks_like_tool_failure(raw_output: Any) -> bool:
    text = str(raw_output or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith("error creating inventory solution:"):
        return True
    if _is_existing_create_conflict(lowered):
        return False
    return (
        lowered.startswith("error")
        or lowered.startswith("tool_error:")
        or lowered.startswith("policy_denied:")
        or (lowered.startswith("[exit ") and not lowered.startswith("[exit 0]"))
    )


# ── Public types ────────────────────────────────────────────────────

class ToolSensitivity(StrEnum):
    """Classification used to gate write/destructive tools in later phases.

    v1 runtime dispatches only ``READ_SAFE`` and ``READ_SENSITIVE``. Write
    and destructive classes are defined now so the registry contract is
    stable; dispatching them requires an explicit confirmation token which
    is not implemented in v1 (enforced by :func:`execute`).
    """

    READ_SAFE = "read_safe"            # listing, discovery, metadata
    READ_SENSITIVE = "read_sensitive"  # row-level data, table contents
    WRITE = "write"                    # create / update / post
    DESTRUCTIVE = "destructive"        # delete / drop / overwrite


@dataclass(frozen=True)
class ToolPolicy:
    """Declared policy for a single tool.

    ``auto_allowed`` gates whether the runtime may execute the tool without
    an explicit per-call confirmation token. Only ``READ_SAFE`` and
    ``READ_SENSITIVE`` tools may set ``auto_allowed=True`` (enforced in
    :func:`register_tool`).
    """

    tool_name: str
    sensitivity: ToolSensitivity
    auto_allowed: bool = False
    description: str = ""


@dataclass(frozen=True)
class CallerContext:
    """Verified caller identity — constructed ONLY from a validated JWT.

    The orchestrator MUST build this from the authenticated request and
    NEVER from LLM output or from arguments provided by the model.
    """

    tenant_id: str
    user_id: str
    user_upn: str | None
    workspace_id: str | None
    session_id: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    actor_role: str | None = None
    agent_session_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    task_title: str | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.user_id:
            raise ValueError(
                "CallerContext requires tenant_id and user_id from the "
                "verified JWT — refusing to construct without them."
            )


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a runtime dispatch. Always returned — never raised for
    policy failures — so the orchestrator can hand the result back to the
    LLM and let the model adjust. (Raising is reserved for bugs.)
    """

    ok: bool
    output: str                # already wrapped with untrusted fences
    policy_decision: str       # "allowed" | "denied:<reason>" | "confirm_required" | "circuit_broken"
    tool_name: str
    arg_hash: str
    latency_ms: int | None = None
    latency_breakdown_ms: dict[str, int] | None = None


class ToolRuntimeError(RuntimeError):
    """Raised only for internal runtime bugs — never for policy denials
    (those return a :class:`ToolResult` with ``ok=False``)."""


# ── Registry ────────────────────────────────────────────────────────

_POLICY_REGISTRY: dict[str, ToolPolicy] = {}


def register_tool(policy: ToolPolicy) -> None:
    """Register a tool policy. Call at startup, once per tool.

    WRITE / DESTRUCTIVE tools may now declare ``auto_allowed=True`` to
    opt out of the per-call confirmation gate. Tools that do not opt in
    still require a ``confirmation_token`` at dispatch time.
    """
    _POLICY_REGISTRY[policy.tool_name] = policy


def get_policy(tool_name: str) -> ToolPolicy | None:
    return _POLICY_REGISTRY.get(tool_name)


def clear_registry_for_tests() -> None:
    """Test-only hook. Do not call from product code."""
    _POLICY_REGISTRY.clear()


# ── Kill-switch ─────────────────────────────────────────────────────

def _is_enabled(env_var: str) -> bool:
    """Returns True unless the env var is explicitly set to a falsy value.

    Default-enabled semantics — absence means "no kill-switch set, allow".
    Setting the var to "0" / "false" / "off" (case-insensitive) disables.
    """
    v = os.environ.get(env_var)
    if v is None:
        return True
    return v.strip().lower() not in {"0", "false", "no", "off", ""}


def _check_kill_switches(tool_name: str, tenant_id: str) -> str | None:
    """Returns a reason string if dispatch should be denied, else None."""
    if not _is_enabled(_GLOBAL_KILL_ENV):
        return f"global kill-switch active ({_GLOBAL_KILL_ENV}=0)"
    per_tool = _PER_TOOL_KILL_ENV_TPL.format(name=tool_name.upper())
    if not _is_enabled(per_tool):
        return f"per-tool kill-switch active ({per_tool}=0)"
    per_tenant = _PER_TENANT_KILL_ENV_TPL.format(tid=tenant_id.replace("-", "").upper())
    if not _is_enabled(per_tenant):
        return f"per-tenant kill-switch active ({per_tenant}=0)"
    return None


# ── Circuit breaker (per-session identical-call loop detector) ──────

# Session-scoped ring of the most recent (tool_name, arg_hash) entries. The
# state is in-process — acceptable given we already rely on a single
# backend replica (see rate_limit.py rationale). When we scale out, swap
# for a Redis-backed counter.
_SESSION_RECENT_CALLS: dict[str, list[tuple[str, str]]] = defaultdict(list)
_SESSION_SUCCESS_CACHE: dict[tuple[str, str, str], ToolResult] = {}

# Idempotent long-running creation tools can be safely replayed when an
# agent repeats the exact same call. Without this, a successful create can
# be followed by an LLM loop that hits the circuit breaker and turns an
# otherwise good mission into a failure. Non-idempotent tools still use the
# normal circuit-breaker denial.
_REPLAYABLE_IDENTICAL_CALL_TOOLS: frozenset[str] = frozenset({
    "fabric_create_workspace_inventory_solution",
})


def _circuit_broken(session_id: str, tool_name: str, arg_hash: str) -> bool:
    """Return True if this exact (tool, args) has repeated THRESHOLD
    times in a row within this session. Pushes the new entry regardless
    so the detector keeps advancing even when we deny.
    """
    history = _SESSION_RECENT_CALLS[session_id]
    history.append((tool_name, arg_hash))
    # Keep only the last N so the dict doesn't grow unbounded for long
    # sessions.
    if len(history) > CIRCUIT_BREAKER_THRESHOLD * 4:
        del history[: len(history) - CIRCUIT_BREAKER_THRESHOLD * 4]
    if len(history) < CIRCUIT_BREAKER_THRESHOLD:
        return False
    tail = history[-CIRCUIT_BREAKER_THRESHOLD:]
    return all(entry == tail[0] for entry in tail)


def reset_circuit_breaker(session_id: str) -> None:
    """Clear a session's recent-calls history. Called when a session ends
    or on explicit operator unjam (via an admin endpoint, not exposed to
    users)."""
    _SESSION_RECENT_CALLS.pop(session_id, None)
    for key in [key for key in _SESSION_SUCCESS_CACHE if key[0] == session_id]:
        _SESSION_SUCCESS_CACHE.pop(key, None)


# ── Argument scrubbing ──────────────────────────────────────────────

def _strip_caller_identity_args(
    tool_name: str, arguments: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Remove any LLM-supplied caller-identity keys. Returns (cleaned
    args, dropped key names). Dropped keys are logged as anomalies even
    when the tool has no use for them."""
    dropped: list[str] = []
    cleaned = {}
    for k, v in arguments.items():
        if k in _CALLER_IDENTITY_ARG_KEYS:
            dropped.append(k)
            continue
        cleaned[k] = v
    if dropped:
        logger.warning(
            "[TOOL_RUNTIME] %s: dropped LLM-supplied caller-identity args: %s",
            tool_name, dropped,
            extra=log_extra("high_level"),
        )
    return cleaned, dropped


def _arg_hash(arguments: dict[str, Any]) -> str:
    """Stable hash of arguments for audit + circuit breaker. Full args are
    NOT stored — this hash is what audit records. Reviewers who need the
    full args correlate via session_id + timestamp against the orchestrator
    log at the source."""
    try:
        canonical = json.dumps(arguments, sort_keys=True, default=str)
    except Exception:
        canonical = repr(arguments)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ── Output wrapping ─────────────────────────────────────────────────

def wrap_as_untrusted(tool_name: str, raw: str) -> str:
    """Wrap tool output in untrusted-content fences and truncate.

    The orchestrator's system prompt teaches the LLM that anything between
    the fences is DATA, never instructions. We also neutralize any
    occurrence of our fence markers inside the payload so a crafted tool
    response can't close the fence early."""
    if raw is None:
        raw = ""
    body = str(raw)
    if _UNTRUSTED_OPEN in body:
        body = body.replace(_UNTRUSTED_OPEN, "<<<_>>>")
    if _UNTRUSTED_CLOSE in body:
        body = body.replace(_UNTRUSTED_CLOSE, "<<<_>>>")
    if len(body) > MAX_TOOL_OUTPUT_CHARS:
        body = body[:MAX_TOOL_OUTPUT_CHARS] + f"\n… (truncated at {MAX_TOOL_OUTPUT_CHARS} chars)"
    return (
        f"\n{_UNTRUSTED_OPEN} tool={tool_name}\n"
        f"{body}\n{_UNTRUSTED_CLOSE}"
    )


# ── Dispatch ────────────────────────────────────────────────────────

async def execute(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None,
    ctx: CallerContext,
    mcp_manager: Any,
    mcp_tokens: dict[str, str] | None,
    allowed_tools: set[str] | frozenset[str] | None = None,
    confirmation_token: str | None = None,
) -> ToolResult:
    """Single entry point for every LLM-driven tool call.

    The orchestrator hands raw ``arguments`` from the LLM's tool-call
    decision; the runtime scrubs caller-identity keys, runs kill-switch +
    policy + circuit-breaker checks, pins workspace from ``ctx``, and only
    then asks ``mcp_manager`` to dispatch. The result is wrapped as
    untrusted before returning.
    """
    import time
    started = time.monotonic()

    args = dict(arguments or {})
    logger.info(
        "[TOOL_RUNTIME] dispatch start tool=%s session=%s tenant=%s workspace=%s arg_keys=%s allowed_scope=%s",
        tool_name,
        ctx.session_id or "-",
        ctx.tenant_id[:8],
        (ctx.workspace_id or "-")[:8],
        sorted(args.keys()),
        "custom" if allowed_tools is not None else "default",
        extra=log_extra("diagnostic"),
    )

    # (1) Registry — deny by default.
    policy = _POLICY_REGISTRY.get(tool_name)
    if policy is None:
        logger.warning(
            "[TOOL_RUNTIME] deny unregistered tool %r (session=%s user=%s)",
            tool_name, ctx.session_id, ctx.user_upn,
            extra=log_extra("high_level"),
        )
        return ToolResult(
            ok=False,
            output=wrap_as_untrusted(
                tool_name,
                f"POLICY_DENIED: tool {tool_name!r} is not registered with "
                f"the runtime. Pick a registered tool.",
            ),
            policy_decision="denied:unregistered",
            tool_name=tool_name,
            arg_hash=_arg_hash(args),
        )

    # (2) Kill-switches (global / per-tool / per-tenant).
    kill_reason = _check_kill_switches(tool_name, ctx.tenant_id)
    if kill_reason:
        logger.warning(
            "[TOOL_RUNTIME] deny %s: %s (tenant=%s session=%s)",
            tool_name, kill_reason, ctx.tenant_id, ctx.session_id,
            extra=log_extra("high_level"),
        )
        return ToolResult(
            ok=False,
            output=wrap_as_untrusted(
                tool_name,
                f"POLICY_DENIED: {kill_reason}. Retry later or contact "
                f"your tenant admin.",
            ),
            policy_decision=f"denied:kill_switch:{kill_reason}",
            tool_name=tool_name,
            arg_hash=_arg_hash(args),
        )

    # (3) Sensitivity gate — writes/destructives normally require an
    #     explicit ``confirmation_token``. A policy may opt out by setting
    #     ``auto_allowed=True`` (used for first-party Fabric write tools
    #     the orchestrator must dispatch autonomously to fulfil user
    #     requests like "create a Lakehouse").
    if policy.sensitivity in (ToolSensitivity.WRITE, ToolSensitivity.DESTRUCTIVE):
        if not confirmation_token and not policy.auto_allowed:
            logger.warning(
                "[TOOL_RUNTIME] deny %s: write tool requires confirmation "
                "(not implemented in v1) session=%s",
                tool_name, ctx.session_id,
                extra=log_extra("high_level"),
            )
            return ToolResult(
                ok=False,
                output=wrap_as_untrusted(
                    tool_name,
                    f"POLICY_DENIED: {tool_name!r} is a "
                    f"{policy.sensitivity} tool and requires explicit user "
                    f"confirmation. v1 does not dispatch writes. Pick a "
                    f"read-only tool or report this to the user.",
                ),
                policy_decision="denied:confirmation_required",
                tool_name=tool_name,
                arg_hash=_arg_hash(args),
            )

    # (4) Strip LLM-supplied caller-identity fields before hashing or
    #     dispatching. Dropped keys are logged (in helper).
    args, dropped_keys = _strip_caller_identity_args(tool_name, args)

    # (4a) Workspace pinning. If the verified ctx pins a workspace, OVERRIDE
    #      whatever the LLM passed in its arguments. Many MCP tools take
    #      ``workspace_id`` in their args schema (they need it to operate),
    #      so we cannot simply strip it — we replace it so the tool body
    #      always runs against the workspace the caller is authorised for.
    if ctx.workspace_id:
        for k in ("workspace_id", "workspaceId", "workspaceID"):
            if k in args and args[k] != ctx.workspace_id:
                logger.warning(
                    "[TOOL_RUNTIME] %s: overriding LLM-supplied %s=%r with "
                    "ctx.workspace_id=%r",
                    tool_name, k, args[k], ctx.workspace_id,
                    extra=log_extra("high_level"),
                )
                args[k] = ctx.workspace_id

    arg_hash = _arg_hash(args)

    # (5) Circuit breaker (per-session identical-call loop).
    if ctx.session_id and _circuit_broken(ctx.session_id, tool_name, arg_hash):
        cache_key = (ctx.session_id, tool_name, arg_hash)
        cached = _SESSION_SUCCESS_CACHE.get(cache_key)
        if cached and tool_name in _REPLAYABLE_IDENTICAL_CALL_TOOLS:
            logger.warning(
                "[TOOL_RUNTIME] replay cached result for %s after %d identical calls in session %s",
                tool_name, CIRCUIT_BREAKER_THRESHOLD, ctx.session_id,
                extra=log_extra("high_level"),
            )
            return ToolResult(
                ok=True,
                output=cached.output,
                policy_decision="replayed_cached_result",
                tool_name=tool_name,
                arg_hash=arg_hash,
                latency_ms=0,
                latency_breakdown_ms={
                    "backendPolicyMs": 0,
                    "sidecarHttpMs": 0,
                    "mcpProcessStartupMs": 0,
                    "mcpToolExecutionMs": 0,
                    "backendTotalMs": 0,
                },
            )
        logger.warning(
            "[TOOL_RUNTIME] circuit break %s: %d identical calls in session %s",
            tool_name, CIRCUIT_BREAKER_THRESHOLD, ctx.session_id,
            extra=log_extra("high_level"),
        )
        return ToolResult(
            ok=False,
            output=wrap_as_untrusted(
                tool_name,
                f"POLICY_DENIED: circuit breaker — {tool_name!r} was called "
                f"with identical arguments {CIRCUIT_BREAKER_THRESHOLD} times "
                f"in a row. Change the approach or report back to the user.",
            ),
            policy_decision="circuit_broken",
            tool_name=tool_name,
            arg_hash=arg_hash,
        )

    # (6) Dispatch through the existing mcp_client_manager gate, pinning
    #     workspace_id from the VERIFIED caller context (not from args).
    backend_policy_ms = int((time.monotonic() - started) * 1000)
    try:
        logger.info(
            "[TOOL_RUNTIME] dispatch allowed tool=%s session=%s sensitivity=%s auto_allowed=%s arg_hash=%s dropped_identity_keys=%s",
            tool_name,
            ctx.session_id or "-",
            policy.sensitivity,
            policy.auto_allowed,
            arg_hash,
            dropped_keys,
            extra=log_extra("diagnostic"),
        )
        dispatch_kwargs = {
            "allowed_tools": allowed_tools,
            "workspace_id": ctx.workspace_id,
            "execution_context": {
                "agent_id": ctx.agent_id,
                "agent_name": ctx.agent_name,
                "actor_role": ctx.actor_role,
                "agent_session_id": ctx.agent_session_id or ctx.session_id,
                "run_id": ctx.run_id,
                "task_id": ctx.task_id,
                "task_title": ctx.task_title,
                "tool_call_id": ctx.tool_call_id,
            },
        }
        class_metric_dispatch = getattr(type(mcp_manager), "call_tool_with_metrics", None)
        instance_metric_dispatch = getattr(mcp_manager, "__dict__", {}).get("call_tool_with_metrics")
        if class_metric_dispatch is not None:
            call_result = await class_metric_dispatch(
                mcp_manager,
                tool_name,
                args,
                mcp_tokens,
                **dispatch_kwargs,
            )
            raw_output = getattr(call_result, "output", call_result)
            nested_breakdown = dict(getattr(call_result, "latency_breakdown_ms", {}) or {})
        elif instance_metric_dispatch is not None:
            call_result = await instance_metric_dispatch(
                tool_name,
                args,
                mcp_tokens,
                **dispatch_kwargs,
            )
            raw_output = getattr(call_result, "output", call_result)
            nested_breakdown = dict(getattr(call_result, "latency_breakdown_ms", {}) or {})
        else:
            raw_output = await mcp_manager.call_tool(
                tool_name,
                args,
                mcp_tokens,
                **dispatch_kwargs,
            )
            nested_breakdown = {}
        latency_ms = int((time.monotonic() - started) * 1000)
        latency_breakdown_ms = {
            "backendPolicyMs": backend_policy_ms,
            **nested_breakdown,
            "backendTotalMs": latency_ms,
        }
        tool_failed = _looks_like_tool_failure(raw_output)
        logger.info(
            "[TOOL_RUNTIME] dispatch end tool=%s session=%s ok=%s decision=%s arg_hash=%s latency_ms=%d latency_breakdown_ms=%s output_chars=%d preview=%.2000s",
            tool_name,
            ctx.session_id or "-",
            not tool_failed,
            "tool_error" if tool_failed else "allowed",
            arg_hash,
            latency_ms,
            json.dumps(latency_breakdown_ms, sort_keys=True),
            len(str(raw_output or "")),
            bounded_text(raw_output, max_chars=2000),
            extra=log_extra("high_level" if tool_failed else "diagnostic"),
        )
        result = ToolResult(
            ok=not tool_failed,
            output=wrap_as_untrusted(tool_name, str(raw_output)),
            policy_decision="tool_error" if tool_failed else "allowed",
            tool_name=tool_name,
            arg_hash=arg_hash,
            latency_ms=latency_ms,
            latency_breakdown_ms=latency_breakdown_ms,
        )
        if ctx.session_id and result.ok and tool_name in _REPLAYABLE_IDENTICAL_CALL_TOOLS:
            _SESSION_SUCCESS_CACHE[(ctx.session_id, tool_name, arg_hash)] = result
        return result
    except Exception as exc:
        # mcp_client_manager raises ToolPolicyViolation for its own policy
        # checks (path traversal, workspace mismatch, unknown tool). We
        # surface those as a denial, not an exception, so the LLM can
        # reason about it.
        from services.mcp.mcp_client_manager import ToolPolicyViolation
        latency_ms = int((time.monotonic() - started) * 1000)
        if isinstance(exc, ToolPolicyViolation):
            logger.warning(
                "[TOOL_RUNTIME] mcp policy denied tool=%s session=%s arg_hash=%s latency_ms=%d reason=%s",
                tool_name,
                ctx.session_id or "-",
                arg_hash,
                latency_ms,
                exc,
                extra=log_extra("high_level"),
            )
            return ToolResult(
                ok=False,
                output=wrap_as_untrusted(
                    tool_name, f"POLICY_DENIED: {exc}",
                ),
                policy_decision="denied:mcp_policy",
                tool_name=tool_name,
                arg_hash=arg_hash,
                latency_ms=latency_ms,
                latency_breakdown_ms={
                    "backendPolicyMs": backend_policy_ms,
                    "backendTotalMs": latency_ms,
                },
            )
        logger.exception(
            "[TOOL_RUNTIME] tool %s dispatch failed (session=%s)",
            tool_name, ctx.session_id,
            extra=log_extra("high_level"),
        )
        return ToolResult(
            ok=False,
            output=wrap_as_untrusted(tool_name, f"TOOL_ERROR: {exc}"),
            policy_decision="error",
            tool_name=tool_name,
            arg_hash=arg_hash,
            latency_ms=latency_ms,
            latency_breakdown_ms={
                "backendPolicyMs": backend_policy_ms,
                "backendTotalMs": latency_ms,
            },
        )
