"""Orchestrator engine — executes Compositions and streams session events.

Composition is produced by ``services.agenthub.compose_service`` in a
single LLM analysis step. This engine consumes the composition's slots
and drives per-slot agent loops (``_run_agent``). There is no plan
artifact, no pre-materialised step list, no prerequisite verification
here — agents check prerequisites as tools at execution time if they
need to.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.core.service_registry import get_service_registry
from domain.models.agent_models import (
    AgentAction,
    AgentAssignment,
    AgentDecision,
    AgentStatus,
    Job,
    JobStatus,
    PhaseStatus,
    ReasoningPhase,
)
from domain.models.composition import Composition
from domain.models.dynamic_orchestration import (
    AgentResult,
    AgentResultStatus,
    FollowupTask,
    MissionState,
    MissionStatus,
    SubagentRun,
    TaskNode,
)
from services.agenthub import dynamic_mission_store
from services.agenthub.agent.chat_client import stream_chat_completion
from services.agenthub.agent_registry import GENERALIST_AGENT_ID, get_template
from services.agenthub.attachments import ATTACHMENT_SHIELD_PROMPT
from services.agenthub.compose_service import ComposeService, get_compose_service
from services.agenthub.drivers.handoff import HandoffPayload
from services.agenthub.dynamic_orchestrator import DynamicMissionController
from services.agenthub.event_ledger import ledger_digest, ledger_preview, record_event
from services.agenthub import session_event_store
from services.agenthub.pi_backend_harness import build_pi_harness_manifest
from services.agenthub.session_store import log_audit, update_session
from services.correlation import get_current_otel_ids, get_request_id, reset_session_id, set_session_id
from services.logging_categories import (
    PUBLIC_LOG_CATEGORIES,
    LogCategory,
    normalize_log_category,
)
from services.logging_categories import (
    log_extra as _log_extra,
)
from services.observability import bounded_text, stable_digest

if TYPE_CHECKING:
    from services.agenthub.workspace_context_service import WorkspaceContext

logger = logging.getLogger(__name__)

COPILOT_API_BASE = "https://api.githubcopilot.com"
TOOL_MODEL = "gpt-4o"
MAX_AGENT_ROUNDS = 15
AGENT_ROUND_TIMEOUT = int(os.environ.get("AGENT_ROUND_TIMEOUT_SECONDS", "90"))
AGENT_LLM_MAX_ATTEMPTS = int(os.environ.get("AGENT_LLM_MAX_ATTEMPTS", "3"))
OPENAI_COMPAT_TOOL_SCHEMA_HARD_LIMIT = 128
AGENTHUB_TOOL_SCHEMA_SAFE_LIMIT = 120
MODEL_TOOL_SCHEMA_LIMIT = min(
    int(os.environ.get("AGENTHUB_OPENAI_TOOL_SCHEMA_LIMIT", "120")),
    AGENTHUB_TOOL_SCHEMA_SAFE_LIMIT,
    OPENAI_COMPAT_TOOL_SCHEMA_HARD_LIMIT,
)

_GENERALIST_BOOTSTRAP_TOOLS = frozenset({
    "fabric_list_workspaces",
    "fabric_list_items",
    "fabric_list_folders",
    "fabric_create_folder",
    "fabric_verify_workspace_inventory_solution",
    "fabric_create_workspace_inventory_solution",
    # Cross-cutting utilities — safe everywhere, no Fabric side-effects.
    # Keep the generalist's mission-controller loop able to plan and
    # ground without escalating to a specialist for trivial helpers.
    "sequentialthinking",
    "get_current_time",
    "convert_time",
    "web_search",
    "web_fetch_url",
})

_TRANSIENT_LLM_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

_PUBLIC_LOG_CATEGORIES = PUBLIC_LOG_CATEGORIES
_HIGH_LEVEL_EVENT_TYPES = {
    "agent_added",
    "agent_context_received",
    "activity_rollup",
    "approval.resolved",
    "approval_fallback_required",
    "approval_repeated_denial",
    "approval_required",
    "change_recorded",
    "composition_ready",
    "diagnostic_new_issues",
    "diagnostic_resolved_issues",
    "job_cancelled",
    "job_complete",
    "job_failed",
    "mcp_server_approval_required",
    "mcp_server_approved",
    "mcp_server_rejected",
    "memory_loaded",
    "memory_written",
    "memory_updated",
    "memory_ignored",
    "plugin_enabled",
    "plugin_disabled",
    "capability_pack_enabled",
    "capability_pack_disabled",
    "mission_blocked",
    "mission_cancelled",
    "mission_completed",
    "mission_failed",
    "mission_replanned",
    "mission_seeded",
    "parallel_group_spawned",
    "phase_complete",
    "phase_start",
    "generalist_check_in",
    "generalist_context_pack",
    "generalist_direct_work",
    "generalist_state_decision",
    "generalist_steering",
    "subagent_result",
    "subagent_spawned",
    "subagent_abandoned",
    "subagent_cancelled",
    "task_blocked",
    "task_created",
    "task_failed",
    "turn_interrupt_deferred",
    "turn_interrupt_requested",
    "turn_interrupted",
    "user_message_broadcast",
    "user_message_delivered",
    "user_message_failed",
    "user_message_queued",
    "verifier_verdict",
    "pi.orchestration.start",
    "pi.subagents.status",
    "pi.subagents.control",
    "pi.subagents.result",
    "pi.subagents.async",
}
_DIAGNOSTIC_EVENT_TYPES = {
    "agent_error",
    "diagnostic_required",
    "diagnostic_baseline_captured",
    "mcp_session_refreshed",
    "runtime_config_refreshed",
    "subagent_inspected",
    "subagent_stale",
    "subagent_steered",
    "tool_progress",
    "tool_call_ended",
    "tool_call_started",
    "pi.tool.start",
    "pi.tool.end",
    "pi.turn.start",
    "pi.turn.delta",
    "pi.turn.end",
}
_TRACE_ONLY_EVENT_TYPES = {
    "resource_lock_acquired",
    "resource_lock_released",
    "subagent_heartbeat",
}


def _event_log_category(event_type: str, payload: dict[str, Any]) -> LogCategory:
    requested = payload.get("logCategory") or payload.get("log_category")
    requested_category = normalize_log_category(requested, default=None)
    if requested_category is not None:
        return requested_category

    if event_type in _TRACE_ONLY_EVENT_TYPES:
        return "trace"
    if event_type.startswith("pi."):
        if event_type in _HIGH_LEVEL_EVENT_TYPES:
            return "high_level"
        if event_type in _DIAGNOSTIC_EVENT_TYPES:
            return "diagnostic"
        return "detailed"
    if event_type in _HIGH_LEVEL_EVENT_TYPES:
        return "high_level"
    if event_type in _DIAGNOSTIC_EVENT_TYPES:
        return "diagnostic"
    if event_type == "log_line":
        tags = {str(tag).lower() for tag in payload.get("tags") or []}
        level = str(payload.get("level") or "info").lower()
        if "trace" in tags or "internal" in tags:
            return "trace"
        if "llm_retry" in tags or "diagnostic" in tags:
            return "diagnostic"
        if tags & {"major_issue", "user_action", "orchestrator_recovery"} or level in {"warn", "error"}:
            return "high_level"
        if tags & {"tool_required", "tool_result"}:
            return "diagnostic"
        return "detailed"
    if event_type in {"action", "orchestrator_decision"}:
        return "high_level"
    return "detailed"


@dataclass(frozen=True)
class QueuedUserMessage:
    steering_id: str
    message: str
    target_agent_session_id: str | None
    target_mode: str
    mode: str
    queued_at: str
    message_preview: str


def _preview_user_message(message: str, *, max_chars: int = 220) -> str:
    return bounded_text(message.replace("\n", " "), max_chars=max_chars)


def _tool_operation_kind(tool_name: str) -> str:
    name = (tool_name or "").lower()
    if any(token in name for token in ("delete", "remove", "revoke", "drop")):
        return "destructive"
    if any(token in name for token in ("create", "write", "update", "publish", "deploy", "apply", "grant", "run", "execute")):
        return "write"
    if any(token in name for token in ("verify", "validate", "diagnose", "check")):
        return "validation"
    if any(token in name for token in ("list", "read", "get", "fetch", "inspect", "query", "search")):
        return "read"
    return "tool"


def _tool_rollup_summary(tool_name: str, status: str, duration_ms: int | None = None) -> str:
    operation_kind = _tool_operation_kind(tool_name)
    clean_name = tool_name.replace("fabric_", "").replace("_", " ").strip() or "tool"
    verb = "Completed" if status == "ok" else "Failed"
    if operation_kind == "read":
        action = f"{verb} inspection with {clean_name}"
    elif operation_kind == "validation":
        action = f"{verb} validation with {clean_name}"
    elif operation_kind == "write":
        action = f"{verb} Fabric update with {clean_name}"
    elif operation_kind == "destructive":
        action = f"{verb} guarded destructive action with {clean_name}"
    else:
        action = f"{verb} {clean_name}"
    if duration_ms is not None:
        if duration_ms < 1000:
            action = f"{action} in {duration_ms} ms"
        else:
            action = f"{action} in {round(duration_ms / 1000, 1)} s"
    return action


def _summary_scalar(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return bounded_text(value, max_chars=180)


def _event_payload_summary(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, redacted support summary for a mission event."""
    summary: dict[str, Any] = {}
    for key in (
        "agentId", "agentName", "slotId", "taskId", "runId", "callId",
        "toolName", "toolKind", "operationKind", "status", "level", "durationMs",
        "approvalId", "steeringId", "targetAgentSessionId", "targetMode", "mode",
        "scope", "detailCount", "coveredSeqStart", "coveredSeqEnd", "baselineCount",
        "newIssueCount", "resolvedIssueCount", "source", "serverId", "pluginId",
        "memoryScope", "configVersion",
    ):
        if key in payload and payload[key] not in (None, ""):
            summary[key] = _summary_scalar(payload[key])

    if event_type == "log_line":
        summary["message"] = bounded_text(payload.get("message"), max_chars=260)
        if payload.get("tags"):
            summary["tags"] = list(payload.get("tags") or [])[:8]
    elif event_type == "tool_call_started":
        args_preview = payload.get("argsPreview")
        if isinstance(args_preview, dict):
            summary["argsDigest"] = stable_digest(args_preview)
            summary["argKeys"] = sorted(str(k) for k in args_preview.keys())[:12]
    elif event_type == "tool_call_ended":
        for key in ("policyDecision", "argHash", "outputChars", "resultDigest"):
            if key in payload and payload[key] not in (None, ""):
                summary[key] = _summary_scalar(payload[key])
        if isinstance(payload.get("latencyBreakdownMs"), dict):
            summary["latencyBreakdownMs"] = {
                key: _summary_scalar(value)
                for key, value in payload["latencyBreakdownMs"].items()
                if value not in (None, "")
            }
        if payload.get("errorPreview"):
            summary["errorPreview"] = bounded_text(payload.get("errorPreview"), max_chars=260)
    elif event_type in {
        "user_message_queued", "user_message_delivered", "user_message_broadcast",
        "user_message_failed", "turn_interrupt_requested", "turn_interrupt_deferred",
        "turn_interrupted",
    }:
        if payload.get("messagePreview"):
            summary["messagePreview"] = bounded_text(payload.get("messagePreview"), max_chars=220)
        if payload.get("reason"):
            summary["reason"] = bounded_text(payload.get("reason"), max_chars=220)
    elif event_type == "activity_rollup":
        summary["summary"] = bounded_text(payload.get("summary"), max_chars=320)
        if isinstance(payload.get("counts"), dict):
            summary["counts"] = {
                key: _summary_scalar(value)
                for key, value in payload["counts"].items()
                if value not in (None, "")
            }
    elif event_type.startswith("diagnostic_"):
        if payload.get("summary"):
            summary["summary"] = bounded_text(payload.get("summary"), max_chars=260)
        if isinstance(payload.get("issues"), list):
            summary["issuePreview"] = [
                bounded_text(issue.get("message") if isinstance(issue, dict) else issue, max_chars=160)
                for issue in payload["issues"][:3]
            ]
    elif event_type.startswith("mcp_server_"):
        if payload.get("risk"):
            summary["risk"] = bounded_text(payload.get("risk"), max_chars=220)
        if isinstance(payload.get("toolsPreview"), list):
            summary["toolsPreview"] = list(payload.get("toolsPreview") or [])[:8]
    elif event_type.startswith("memory_"):
        if payload.get("summary"):
            summary["summary"] = bounded_text(payload.get("summary"), max_chars=260)
    elif event_type in {"runtime_config_refreshed", "plugin_enabled", "plugin_disabled", "capability_pack_enabled", "capability_pack_disabled", "mcp_session_refreshed"}:
        if payload.get("summary"):
            summary["summary"] = bounded_text(payload.get("summary"), max_chars=260)
    elif event_type == "orchestrator_decision" and isinstance(payload.get("decision"), dict):
        decision = payload["decision"]
        summary["decisionType"] = _summary_scalar(decision.get("type"))
        summary["rationale"] = bounded_text(decision.get("rationale"), max_chars=260)
        for key in ("taskId", "task_id", "targetRunId", "target_run_id"):
            if decision.get(key):
                summary[key] = _summary_scalar(decision.get(key))
        decision_payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
        for key in ("agentId", "toolScopeCount", "contextDigest", "resultStatus", "taskStatus"):
            if decision_payload.get(key) not in (None, "", []):
                summary[key] = _summary_scalar(decision_payload.get(key))
    elif event_type == "task_created" and isinstance(payload.get("task"), dict):
        task = payload["task"]
        summary["taskId"] = _summary_scalar(task.get("id"))
        summary["taskTitle"] = bounded_text(task.get("title"), max_chars=220)
        summary["taskStatus"] = _summary_scalar(task.get("status"))
        summary["candidateAgents"] = list(task.get("candidateAgentIds") or task.get("candidate_agent_ids") or [])[:8]
        if task.get("delegationReason") or task.get("delegation_reason"):
            summary["delegationReason"] = bounded_text(task.get("delegationReason") or task.get("delegation_reason"), max_chars=260)
    elif event_type in {"subagent_spawned", "generalist_context_pack", "agent_context_received"}:
        for key in ("runId", "taskId", "agentId", "agentName", "agentSessionId", "contextDigest", "goalDigest", "toolScopeCount", "upstreamResultCount", "specialistCatalogCount"):
            if payload.get(key) not in (None, "", []):
                summary[key] = _summary_scalar(payload.get(key))
        if payload.get("taskTitle"):
            summary["taskTitle"] = bounded_text(payload.get("taskTitle"), max_chars=220)
        if payload.get("objectivePreview"):
            summary["objectivePreview"] = bounded_text(payload.get("objectivePreview"), max_chars=260)
        if payload.get("steeringPreview"):
            summary["steeringPreview"] = bounded_text(payload.get("steeringPreview"), max_chars=260)
    elif event_type in {"subagent_result", "generalist_state_decision"}:
        for key in ("runId", "taskId", "agentId", "status", "resultStatus", "taskStatus", "followupTaskCount", "artifactCount", "evidenceCount", "errorCount"):
            if payload.get(key) not in (None, "", []):
                summary[key] = _summary_scalar(payload.get(key))
        if payload.get("summary"):
            summary["summary"] = bounded_text(payload.get("summary"), max_chars=300)
        if payload.get("rationale"):
            summary["rationale"] = bounded_text(payload.get("rationale"), max_chars=260)
    elif event_type == "verifier_verdict":
        for key in (
            "verdictId", "verifierRunId", "verifierTaskId", "verifierAgentId",
            "targetTaskId", "passed", "verifierClaimedSuccess",
            "requiresUserBrowserRender", "feedbackRound",
        ):
            if payload.get(key) not in (None, "", []):
                summary[key] = _summary_scalar(payload.get(key))
        if payload.get("structuralFailures"):
            summary["structuralFailures"] = list(payload.get("structuralFailures") or [])[:8]
        if payload.get("decisionRationale"):
            summary["decisionRationale"] = bounded_text(payload.get("decisionRationale"), max_chars=300)
        if payload.get("summary"):
            summary["summary"] = bounded_text(payload.get("summary"), max_chars=260)
        ev_evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        if ev_evidence:
            summary["evidence"] = {
                "browserVerifiedUrlCount": len(ev_evidence.get("browserVerifiedUrls") or []),
                "screenshotCount": len(ev_evidence.get("screenshotPaths") or []),
                "visualsRendered": bool(ev_evidence.get("visualsRendered")),
                "loadingStuckObserved": bool(ev_evidence.get("loadingStuckObserved")),
                "errorsObservedCount": len(ev_evidence.get("errorsObserved") or []),
            }
        deliverables = payload.get("deliverables") if isinstance(payload.get("deliverables"), list) else []
        if deliverables:
            summary["deliverableCount"] = len(deliverables)
            summary["deliverableTypes"] = sorted({str(d.get("type") or "") for d in deliverables if isinstance(d, dict)})
    elif event_type == "generalist_direct_work":
        for key in ("runId", "taskId", "agentId", "taskTitle", "reason", "toolScopeCount"):
            if payload.get(key) not in (None, "", []):
                summary[key] = _summary_scalar(payload.get(key))
        if payload.get("objectivePreview"):
            summary["objectivePreview"] = bounded_text(payload.get("objectivePreview"), max_chars=260)
        if payload.get("contextDigest"):
            summary["contextDigest"] = _summary_scalar(payload.get("contextDigest"))
    elif event_type in {"generalist_steering", "subagent_steered", "subagent_inspected", "subagent_stale"}:
        for key in ("runId", "taskId", "agentId", "agentName", "reason", "matchingSignalCount", "staleSeconds"):
            if payload.get(key) not in (None, "", []):
                summary[key] = _summary_scalar(payload.get(key))
        if payload.get("message"):
            summary["message"] = bounded_text(payload.get("message"), max_chars=300)
        if isinstance(payload.get("signal"), dict):
            signal = payload["signal"]
            for key in ("kind", "toolName", "argHash"):
                if signal.get(key):
                    summary[key] = _summary_scalar(signal.get(key))
    elif event_type in {"task_blocked", "task_failed", "subagent_abandoned", "subagent_cancelled"}:
        for key in ("runId", "taskId", "replacementTaskId", "reason"):
            if payload.get(key) not in (None, "", []):
                summary[key] = _summary_scalar(payload.get(key))
        if payload.get("message"):
            summary["message"] = bounded_text(payload.get("message"), max_chars=300)
    elif event_type == "tool_progress":
        for key in ("toolName", "step", "status", "elapsedMs", "digest"):
            if payload.get(key) not in (None, "", []):
                summary[key] = _summary_scalar(payload.get(key))
    elif event_type.startswith("pi."):
        for key in (
            "runtime", "runtimePackage", "subagentRuntime", "runId", "asyncId", "mode",
            "state", "status", "agentId", "agentName", "agent", "turnId", "toolCallId",
            "toolName", "activityState", "currentTool", "turnCount", "toolCount",
            "durationMs", "tokens", "reason", "title",
        ):
            if key in payload and payload[key] not in (None, "", []):
                summary[key] = _summary_scalar(payload[key])
        for key in ("summary", "task", "message", "textDelta"):
            if payload.get(key):
                summary[key] = bounded_text(payload.get(key), max_chars=300)
    elif event_type == "diagnostic_required":
        for key in ("toolName", "policyDecision", "reason", "status", "diagnosticTool"):
            if payload.get(key) not in (None, "", []):
                summary[key] = _summary_scalar(payload.get(key))
        if payload.get("directivePreview"):
            summary["directivePreview"] = bounded_text(payload.get("directivePreview"), max_chars=260)
    elif event_type == "action" and isinstance(payload.get("action"), dict):
        action = payload["action"]
        for source, target in (("action_type", "actionType"), ("entity_name", "entityName"), ("entity_type", "entityType"), ("fabric_item_id", "fabricItemId")):
            if action.get(source):
                summary[target] = _summary_scalar(action.get(source))
    elif event_type == "change_recorded":
        for key in ("kind", "targetName", "targetType", "targetScope", "summary", "toolName"):
            if payload.get(key):
                summary[key] = bounded_text(payload.get(key), max_chars=220)
    elif event_type == "artifact_added":
        for key in ("artifactId", "kind", "name", "state", "webUrl"):
            if payload.get(key):
                summary[key] = bounded_text(payload.get(key), max_chars=220)
    elif event_type == "run_overview":
        job = payload.get("job") if isinstance(payload.get("job"), dict) else {}
        summary["jobStatus"] = _summary_scalar(job.get("status"))
        summary["artifactCount"] = len(payload.get("artifacts") or [])
        summary["changeCount"] = len(payload.get("changes") or [])
        summary["slotProgressCount"] = len(payload.get("slotProgress") or [])
    elif event_type.startswith("mission_"):
        summary["reason"] = bounded_text(payload.get("reason"), max_chars=260)
        if isinstance(payload.get("mission"), dict):
            mission = payload["mission"]
            summary["missionStatus"] = _summary_scalar(mission.get("status"))
            summary["taskCount"] = len(mission.get("tasks") or [])
            summary["runCount"] = len(mission.get("subagentRuns") or mission.get("subagent_runs") or [])

    return {key: value for key, value in summary.items() if value not in (None, "", [])}


def _event_summary_text(summary: dict[str, Any]) -> str:
    if not summary:
        return "summary={}"
    parts: list[str] = []
    for key, value in list(summary.items())[:10]:
        if isinstance(value, (dict, list, tuple)):
            text = ledger_preview(value, max_chars=180)
        else:
            text = str(value)
        text = text.replace("\n", " ").replace("\r", " ")
        if " " in text or len(text) > 48:
            text = repr(text[:180])
        parts.append(f"{key}={text}")
    if len(summary) > 10:
        parts.append(f"more={len(summary) - 10}")
    return " ".join(parts)


def _event_actor(payload: dict[str, Any], summary: dict[str, Any]) -> str:
    for key in ("agentName", "agentId", "verifierAgentId", "runId"):
        value = payload.get(key) or summary.get(key)
        if value:
            return str(value)
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    for key in ("agentId", "agent_id", "id"):
        value = run.get(key)
        if value:
            return str(value)
    return "mission"


def _event_task_label(payload: dict[str, Any], summary: dict[str, Any]) -> str:
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    for value in (
        payload.get("taskTitle"),
        summary.get("taskTitle"),
        task.get("title"),
        payload.get("taskId"),
        summary.get("taskId"),
    ):
        if value:
            return bounded_text(value, max_chars=180)
    return "-"


def _event_audit_summary(event_type: str, payload: dict[str, Any], summary: dict[str, Any]) -> str:
    preferred = (
        summary.get("summary")
        or summary.get("decisionRationale")
        or summary.get("rationale")
        or summary.get("message")
        or summary.get("taskTitle")
        or payload.get("reason")
    )
    text = bounded_text(preferred or _event_summary_text(summary), max_chars=700)
    return f"{event_type}: {text}"


_MAJOR_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"capacitynotactive", re.IGNORECASE),
    re.compile(r"capacity\s+not\s+active", re.IGNORECASE),
    re.compile(r"capacity\s+is\s+inactive", re.IGNORECASE),
    re.compile(r"inactive\s+due\s+to\s+the\s+`?capacitynotactive`?", re.IGNORECASE),
    re.compile(r"workspace\s+capacity\s+is\s+inactive", re.IGNORECASE),
    re.compile(r"cannot\s+proceed\s+until\s+capacity", re.IGNORECASE),
    re.compile(r"semantic\s+model\s+refresh\s+failed", re.IGNORECASE),
    re.compile(r"0xC14700C7", re.IGNORECASE),
    re.compile(r"source\s+Delta\s+table", re.IGNORECASE),
    re.compile(r"access\s+permissions", re.IGNORECASE),
    re.compile(r"Direct\s*Lake\s+identity\s+risk", re.IGNORECASE),
    re.compile(r"owner/effective-identity\s+mismatch", re.IGNORECASE),
)

_MAJOR_WARN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"capacity\s+issue", re.IGNORECASE),
    re.compile(r"capacity\s+constraint", re.IGNORECASE),
    re.compile(r"quota\s+exceeded", re.IGNORECASE),
    re.compile(r"throttl(?:ed|ing)?", re.IGNORECASE),
)

_USER_ACTION_RECOVERY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"capacitynotactive", re.IGNORECASE),
    re.compile(r"capacity\s+(?:not\s+active|is\s+inactive|issue|constraint)", re.IGNORECASE),
    re.compile(r"quota\s+exceeded", re.IGNORECASE),
    re.compile(r"unauthori[sz]ed|forbidden|permission\s+denied|rbac", re.IGNORECASE),
    re.compile(r"approval\s+required|requires\s+approval|manual\s+action", re.IGNORECASE),
)

_STOP_RECOVERY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"cross[-\s]?workspace", re.IGNORECASE),
    re.compile(r"policy\s+(?:violation|denied|blocked)", re.IGNORECASE),
    re.compile(r"security\s+(?:violation|boundary|risk)", re.IGNORECASE),
)

_TRANSIENT_RECOVERY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:timeout|timed\s+out|transient|rate\s+limit|429|500|502|503|504)\b", re.IGNORECASE),
    re.compile(r"llm\s+(?:error|call\s+failed)", re.IGNORECASE),
    re.compile(r"reached\s+max\s+rounds", re.IGNORECASE),
)

_DOMAIN_RECOVERY_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"workspace|tenant|rbac|capacity|permission|governance|admin", re.IGNORECASE),
        "fabric-admin",
        "Recovery admin",
    ),
    (
        re.compile(r"lakehouse|warehouse|notebook|pipeline|spark|sql|semantic\s+model|report|power\s*bi|eventhouse|dataflow|fabric_create|fabric_write", re.IGNORECASE),
        "fabric-data-engineer",
        "Recovery builder",
    ),
    (
        re.compile(r"schema|ddl|model|visual|ibcs|quality\s+gate|presentation|design\s+review", re.IGNORECASE),
        "modeler",
        "Recovery modeler",
    ),
    (
        re.compile(r"application|api|odbc|xmla|rest|livy|python|node|frontend|service", re.IGNORECASE),
        "fabric-app-dev",
        "Recovery app developer",
    ),
)


@dataclass(frozen=True)
class _RecoveryDecision:
    action: str
    reason: str
    agent_id: str | None = None
    role: str | None = None
    goal: str | None = None
    approval_summary: str | None = None
    recovery_actions: tuple[str, ...] = ()


@dataclass(slots=True)
class _RuntimeSubagentExecutor:
    """Runs dynamic task nodes through the existing AgentHub agent loop."""

    execution: _JobExecution
    engine: Any
    runner: Any

    async def run(
        self,
        *,
        mission: MissionState,
        run: SubagentRun,
        task: TaskNode,
        context_pack: dict[str, Any],
    ) -> AgentResult:
        assignment = self._ensure_assignment(mission, run, task, context_pack)
        template = get_template(run.agent_id)
        if template is None:
            assignment.status = AgentStatus.ERROR
            assignment.current_step = f"Unknown dynamic agent template: {run.agent_id}"
            update_session(self.execution.job)
            return AgentResult(
                run_id=run.id,
                task_id=task.id,
                status=AgentResultStatus.FAILED,
                summary=assignment.current_step,
                errors=[assignment.current_step],
            )

        upstream_handoffs = _dynamic_upstream_handoffs(task, context_pack)
        try:
            slot_result = await self.runner.run_slot(
                task.id,
                upstream_handoffs=upstream_handoffs,
                max_turns=_dynamic_task_turn_budget(self.execution.job),
                step_label=task.title,
                allowed_tools_override=self._resolve_tool_scope(run),
            )
        except asyncio.CancelledError:
            assignment.status = AgentStatus.ERROR
            assignment.current_step = "Cancelled by dynamic orchestrator"
            update_session(self.execution.job)
            raise

        result = self._result_from_assignment(run, task, assignment, slot_result.status)
        dynamic_mission_store.save_mission_state(mission)
        update_session(self.execution.job)
        return result

    async def steer(self, *, run: SubagentRun, message: str, reason: str) -> None:
        if run.agent_session_id and run.agent_session_id in self.execution.user_message_queues:
            self.execution.user_message_queues[run.agent_session_id].put_nowait(
                f"ORCHESTRATOR DIRECTIVE ({reason}): {message}"
            )
            self.execution.emit(
                "generalist_steering",
                runId=run.id,
                taskId=run.task_id,
                agentId=run.agent_id,
                agentSessionId=run.agent_session_id,
                reason=reason,
                message=message,
                delivered=True,
            )

    async def cancel(self, *, run: SubagentRun, reason: str) -> None:
        assignment = self._assignment_for_run(run)
        if assignment is not None:
            assignment.status = AgentStatus.ERROR
            assignment.current_step = f"Cancelled by orchestrator: {reason}"
            update_session(self.execution.job)
            self.execution.emit(
                "slot_progress",
                slotId=assignment.session_id,
                agentId=assignment.session_id,
                status="failed",
                agentName=_agent_display_name(assignment.agent_id),
                role=assignment.role,
                reason=assignment.current_step,
            )

    def _ensure_assignment(
        self,
        mission: MissionState,
        run: SubagentRun,
        task: TaskNode,
        context_pack: dict[str, Any],
    ) -> AgentAssignment:
        existing = self._assignment_for_run(run)
        if existing is not None:
            return existing

        assignment = AgentAssignment(
            agent_id=run.agent_id,
            session_id=str(uuid.uuid4()),
            role=task.title,
            goal=_build_dynamic_agent_goal(mission, task, context_pack),
            status=AgentStatus.QUEUED,
        )
        run.agent_session_id = assignment.session_id
        self.execution.job.agents.append(assignment)
        self.runner.register_slot(task.id, assignment)
        self.execution.user_message_queues[assignment.session_id] = asyncio.Queue()
        update_session(self.execution.job)
        dynamic_mission_store.save_mission_state(mission)
        self.execution.emit(
            "agent_added",
            jobId=self.execution.job.id,
            taskId=task.id,
            runId=run.id,
            agent=assignment.model_dump(mode="json", by_alias=True),
        )
        self.execution.emit(
            "agent_context_received",
            taskId=task.id,
            runId=run.id,
            agentId=run.agent_id,
            agentName=_agent_display_name(run.agent_id),
            agentSessionId=assignment.session_id,
            goalDigest=stable_digest(assignment.goal),
            contextDigest=stable_digest(context_pack),
            objectivePreview=bounded_text(task.objective, max_chars=500),
            steeringPreview=bounded_text(assignment.goal, max_chars=900),
            upstreamResultCount=len(context_pack.get("upstreamResults") or []),
            specialistCatalogCount=len(context_pack.get("specialistCatalog") or []),
            **_dynamic_context_pack_v2_summary(context_pack),
        )
        if _pi_subagents_observability_enabled():
            self.execution.emit(
                "pi.subagents.status",
                schemaVersion=1,
                runId=run.id,
                agentId=run.agent_id,
                agentName=_agent_display_name(run.agent_id),
                agent=run.agent_id,
                mode="single",
                state="queued",
                task=task.title,
                summary=bounded_text(task.objective, max_chars=500),
                progress=[{
                    "index": 0,
                    "agent": run.agent_id,
                    "status": "queued",
                    "task": task.title,
                    "recentOutput": [],
                    "recentTools": [],
                    "toolCount": 0,
                    "tokens": 0,
                    "durationMs": 0,
                }],
                extension=_pi_subagent_extension(),
            )
        return assignment

    def _assignment_for_run(self, run: SubagentRun) -> AgentAssignment | None:
        if not run.agent_session_id:
            return None
        return next(
            (
                assignment
                for assignment in self.execution.job.agents
                if assignment.session_id == run.agent_session_id
            ),
            None,
        )

    def _resolve_tool_scope(self, run: SubagentRun) -> set[str] | None:
        if "*" in run.tool_scope:
            return _resolve_wildcard_tool_scope(run.agent_id, self.execution.mcp_manager)
        if run.tool_scope:
            return set(run.tool_scope)
        return None

    def _result_from_assignment(
        self,
        run: SubagentRun,
        task: TaskNode,
        assignment: AgentAssignment,
        slot_status: str,
    ) -> AgentResult:
        summary = _summarize_dynamic_assignment(self.runner, task.id, assignment)
        status = _dynamic_result_status(slot_status, assignment)
        errors: list[str] = []
        caveats: list[str] = []
        if status == AgentResultStatus.FAILED:
            errors.append(_assignment_failure_text(assignment))
        if status == AgentResultStatus.PARTIAL:
            caveats.append("The subagent completed without a decisive completion signal.")
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=status,
            summary=summary,
            artifacts=[action.model_dump(mode="json", by_alias=True) for action in assignment.actions],
            evidence=_dynamic_assignment_evidence(assignment),
            errors=errors,
            caveats=caveats,
            followup_tasks=_extract_dynamic_followups(assignment),
            handoff_context={
                "agentSessionId": assignment.session_id,
                "role": assignment.role,
                "currentStep": assignment.current_step,
            },
        )


def _major_issue_level(text: str | None) -> str | None:
    """Classify text for major runtime issues.

    Returns ``"error"`` or ``"warn"`` when known high-impact signatures
    are detected, otherwise ``None``.
    """
    if not text:
        return None
    if any(p.search(text) for p in _MAJOR_ERROR_PATTERNS):
        return "error"
    if any(p.search(text) for p in _MAJOR_WARN_PATTERNS):
        return "warn"
    return None


_STRUCTURED_SUCCESS_STATUSES = {
    "completed",
    "created",
    "ok",
    "rendered",
    "success",
    "verified",
}


def _structured_tool_result_is_success(parsed: dict[str, Any]) -> bool:
    status = str(parsed.get("status") or "").strip().lower()
    if status not in _STRUCTURED_SUCCESS_STATUSES:
        return False
    errors = parsed.get("errors")
    if isinstance(errors, list) and any(str(error).strip() for error in errors):
        return False
    if isinstance(errors, str) and errors.strip():
        return False
    return True


def _tool_result_major_issue_level(tool_result: str) -> str | None:
    """Classify raw tool output without treating successful diagnostics as failures.

    Some read-only diagnostic tools intentionally include historical Fabric
    errors, refresh messages, or permission strings as evidence. If their
    structured top-level status is successful, those nested strings should not
    fail the agent slot; actual structured failures are handled separately by
    ``_tool_result_requires_diagnostics``.
    """
    parsed = _parsed_tool_result_dict(tool_result)
    if parsed and _structured_tool_result_is_success(parsed):
        return None
    return _major_issue_level(tool_result)


def _is_transient_llm_exception(exc: Exception) -> bool:
    if isinstance(exc, (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.NetworkError,
        httpx.PoolTimeout,
        httpx.ReadError,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
        httpx.TimeoutException,
        httpx.WriteError,
        httpx.WriteTimeout,
    )):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in (
        "connection reset",
        "connection refused",
        "name resolution",
        "server disconnected",
        "temporary failure",
        "timed out",
        "timeout",
    ))


def _compact_issue_text(text: str, limit: int = 280) -> str:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _mark_blocking_issue(assignment: AgentAssignment, reason: str) -> str:
    """Persist the first blocking runtime issue observed for this slot."""
    existing = getattr(assignment, "_blocking_issue", None)
    if isinstance(existing, str) and existing:
        return existing
    issue = _compact_issue_text(reason)
    assignment._blocking_issue = issue  # type: ignore[attr-defined]
    return issue


def _get_blocking_issue(assignment: AgentAssignment) -> str | None:
    issue = getattr(assignment, "_blocking_issue", None)
    if isinstance(issue, str) and issue:
        return issue
    return None


def _emit_blocking_slot_progress(execution, assignment: AgentAssignment, template, reason: str) -> None:
    """Emit a single failed slot_progress event for a blocking issue."""
    issue = _mark_blocking_issue(assignment, reason)
    if getattr(assignment, "_blocking_issue_emitted", False):
        return
    assignment._blocking_issue_emitted = True  # type: ignore[attr-defined]
    execution.emit(
        "slot_progress",
        slotId=assignment.session_id,
        agentId=assignment.session_id,
        status="failed",
        agentName=template.display_name,
        reason=issue,
    )


_DIAGNOSTIC_WARNING_MARKERS = (
    "access",
    "auth",
    "capacity",
    "credential",
    "delta table",
    "direct lake",
    "forbidden",
    "owner",
    "permission",
    "quota",
    "refresh",
    "role",
    "semantic model",
    "token",
    "unauthorized",
)


def _tool_result_requires_diagnostics(rt_result: Any, tool_result: str) -> tuple[bool, str]:
    """Return whether the next agent turn must diagnose before retrying.

    This is intentionally generic: any failed policy/runtime dispatch, any
    structured partial/blocked/error result, any non-empty errors array, or any
    high-risk warning asks the agent to inspect evidence before another write.
    """
    if not getattr(rt_result, "ok", False):
        return True, str(getattr(rt_result, "policy_decision", "tool_failed") or "tool_failed")
    parsed = _parsed_tool_result_dict(tool_result)
    if not parsed:
        return False, ""
    status = str(parsed.get("status") or "").strip().lower()
    if _is_transient_browser_capture_miss(parsed):
        return False, ""
    if status in {"partial", "failed", "failure", "error", "blocked", "cancelled", "canceled"}:
        return True, f"structured_status:{status}"
    errors = parsed.get("errors")
    if isinstance(errors, list) and any(str(error).strip() for error in errors):
        return True, "structured_errors"
    if isinstance(errors, str) and errors.strip():
        return True, "structured_errors"
    progress = parsed.get("progress")
    if isinstance(progress, list):
        for row in progress:
            if isinstance(row, dict) and str(row.get("status") or "").lower() in {"failed", "blocked", "error"}:
                return True, f"progress:{row.get('step') or 'unknown'}:{row.get('status')}"
    warnings = parsed.get("warnings")
    warning_text = " ".join(str(w) for w in warnings) if isinstance(warnings, list) else str(warnings or "")
    if warning_text and any(marker in warning_text.lower() for marker in _DIAGNOSTIC_WARNING_MARKERS):
        return True, "high_risk_warning"
    return False, ""


def _is_transient_browser_capture_miss(parsed: dict[str, Any]) -> bool:
    status = str(parsed.get("status") or "").strip().lower()
    if status not in {"failed", "failure", "error"}:
        return False
    error_code = str(parsed.get("errorCode") or parsed.get("error_code") or "").strip().upper()
    if error_code not in {"EXPECTED_TEXT_NOT_VISIBLE", "SCREENSHOT_EMPTY_OR_TOO_SMALL", "BROWSER_CAPTURE_TIMEOUT"}:
        return False
    text = " ".join(
        str(parsed.get(key) or "")
        for key in ("reason", "error", "bodyTextSample", "title", "finalUrl")
    ).lower()
    terminal_markers = (
        "couldn't load",
        "could not load",
        "something went wrong",
        "unable to render",
        "can't display",
        "you don't have access",
        "permission",
        "auth",
        "sign in",
    )
    return not any(marker in text for marker in terminal_markers)


def _diagnostic_directive(tool_name: str, tool_args: dict, tool_result: str, reason: str) -> str:
    parsed = _parsed_tool_result_dict(tool_result)
    workspace_id = (
        tool_args.get("workspace_id")
        or tool_args.get("workspaceId")
        or parsed.get("workspaceId")
        or parsed.get("workspace_id")
    )
    folder_id = parsed.get("folderId") or tool_args.get("folder_id") or tool_args.get("folderId")
    folder_name = parsed.get("folderName") or tool_args.get("folder_name") or tool_args.get("folderName")
    item_ids = _diagnostic_item_ids(parsed)
    error_preview = _compact_issue_text(tool_result, limit=900)
    diagnose_call = None
    if workspace_id:
        args = {"workspace_id": workspace_id}
        if folder_id:
            args["folder_id"] = folder_id
        elif folder_name:
            args["folder_name"] = folder_name
        if item_ids:
            args["item_ids"] = item_ids[:12]
        diagnose_call = f"fabric_diagnose_workspace_artifacts({json.dumps(args, sort_keys=True)})"

    lines = [
        "DIAGNOSTIC CHECKPOINT REQUIRED BEFORE RETRY",
        f"The previous tool call `{tool_name}` produced `{reason}`. Do not call the same mutating tool again until you have diagnosed the observed failure.",
        "Use read-only evidence first, then decide whether to repair, block, or ask for admin/user action.",
        "",
        "Required diagnostic steps:",
        "1. Inspect actual workspace state: list/get the relevant folder and items, including owner/createdBy/lastModifiedBy metadata.",
        "2. Inspect access prerequisites: workspace roles, capacity state, and whether the effective identity/owner can access upstream data sources.",
        "3. Inspect artifact-specific failure evidence: refresh history/serviceExceptionJson for semantic models, operation/job state for long-running jobs, definitions/bindings for reports/models, and browser/render evidence for user-facing items.",
        "4. Compare intended vs actual names, ids, schemas, bindings, storage modes, and permissions. Classify the failing layer as one of: schema, binding, data missing, owner/permission, capacity/quota, catalog propagation, browser/render, service bug, or unknown.",
        "5. Only after recording the root cause and evidence should you call a write/repair tool. If the cause is permission/owner/capacity/admin control or remains unknown after diagnostics, stop and report blocked instead of creating duplicates.",
    ]
    if diagnose_call:
        lines.append("")
        lines.append(f"Start with: `{diagnose_call}`")
    lines.append("")
    lines.append("Return your next message/tool sequence with a concise diagnostic finding: rootCause, evidence, and nextAction.")
    lines.append(f"Failure evidence preview: {error_preview}")
    return "\n".join(lines)


def _diagnostic_item_ids(parsed: dict[str, Any]) -> list[str]:
    item_ids: list[str] = []
    for key in ("semanticModelId", "reportId", "lakehouseId", "warehouseId", "notebookId"):
        value = parsed.get(key)
        if isinstance(value, str) and value:
            item_ids.append(value)
    for item in parsed.get("createdItems") or []:
        if isinstance(item, dict):
            value = item.get("id")
            if isinstance(value, str) and value and value not in item_ids:
                item_ids.append(value)
    store = parsed.get("persistentDataStore")
    if isinstance(store, dict):
        value = store.get("id")
        if isinstance(value, str) and value and value not in item_ids:
            item_ids.append(value)
    return item_ids


class _JobExecution:
    """Runtime state for a single running job."""

    # Upper bound on the per-session event ring buffer used for SSE
    # resume via ``Last-Event-ID``. Tuned large enough to cover a
    # reconnect after a brief disconnect while still bounding memory
    # per active job. The buffer is FIFO: once full, the oldest events
    # are dropped. Clients that disconnect for longer than the buffer
    # covers must fall back to ``GET /api/sessions/{id}`` for a full
    # resnap.
    EVENT_BUFFER_MAX = 500
    TRACE_BUFFER_MAX = 500
    CHANGE_BUFFER_MAX = 300

    def __init__(self, job: Job, copilot_token: str, mcp_tokens: dict | None):
        self.job = job
        self.copilot_token = copilot_token
        self.mcp_tokens = mcp_tokens
        self.mcp_manager = None
        self.mcp_runtime = None
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self.user_message_queues: dict[str, asyncio.Queue] = {}  # session_id -> Queue
        self.pending_interrupts: dict[str, QueuedUserMessage] = {}
        self.tasks: list[asyncio.Task] = []
        self.cancelled = False
        # P1 · Mission Control — cancellation signal every await site
        # can race against. ``.cancelled`` (bool) is preserved for
        # back-compat with call sites that poll it; ``cancel_event``
        # is the modern escape hatch used by the hardened tool-call
        # and LLM-call paths in _run_agent.
        self.cancel_event: asyncio.Event = asyncio.Event()
        # Correlation ID captured at session start so background
        # ``emit`` calls (which run outside the inbound-request scope)
        # still tag their log lines with the originating user action.
        self.correlation_id: str = "-"
        # P1 · Monotonic per-session event counter. Stamped onto every
        # emitted event so clients can (a) dedupe on reconnect and
        # (b) request replay via the SSE ``Last-Event-ID`` header.
        self._seq: int = 0
        self._trace_seq: int = 0
        # Bounded FIFO of the most recent emitted events. Used by the
        # SSE endpoint to replay events newer than a client-supplied
        # ``last_seq``.
        self._ring: list[dict] = []
        self._trace_ring: list[dict] = []
        # P1 · Snapshot state for ``run_overview`` emits. Held here
        # rather than rebuilt each time so late subscribers see the
        # exact same projection every other client already received.
        self._active_agent_id: str | None = None
        self._artifacts: list[dict] = []
        self._changes: list[dict] = []
        self._slot_progress: dict[str, dict] = {}  # slotId -> {status, agentId, ...}
        self.failure_event: asyncio.Event = asyncio.Event()
        self._recovery_handled_agents: set[str] = set()
        self.dynamic_mission_state: MissionState | None = None
        self.dynamic_controller: DynamicMissionController | None = None

    async def close_mcp_runtime(self) -> None:
        runtime = self.mcp_runtime
        if runtime is not None and hasattr(runtime, "close"):
            await runtime.close()
        self.mcp_runtime = None

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _next_trace_seq(self) -> int:
        self._trace_seq += 1
        return self._trace_seq

    def emit(self, event_type: str, **kwargs):
        """Append a new event with a stamped ``seq`` + ``sessionId`` +
        ``ts`` and push it both onto the live queue and the ring buffer.

        Every new emit also logs at INFO level carrying the session's
        correlation ID so post-mortem log slices pick up the event
        timeline alongside the HTTP call that spawned the run.
        """
        session_token = set_session_id(self.job.id)
        try:
            return self._emit_with_bound_session(event_type, **kwargs)
        finally:
            reset_session_id(session_token)

    def _emit_with_bound_session(self, event_type: str, **kwargs):
        category = _event_log_category(event_type, kwargs)
        sanitized_kwargs = {
            key: value for key, value in kwargs.items()
            if key not in {"logCategory", "log_category"}
        }
        trace_id, span_id = get_current_otel_ids()
        if category == "trace":
            trace_seq = self._next_trace_seq()
            payload: dict = {
                "type": event_type,
                "traceSeq": trace_seq,
                "sessionId": self.job.id,
                "ts": datetime.now(UTC).isoformat(),
                "logCategory": "trace",
                **sanitized_kwargs,
            }
            payload["eventId"] = f"{self.job.id}:trace:{trace_seq}"
            payload["payloadDigest"] = ledger_digest(payload)
            summary = _event_payload_summary(event_type, payload)
            payload["payloadSummary"] = summary
            self._trace_ring.append(payload)
            if len(self._trace_ring) > self.TRACE_BUFFER_MAX:
                self._trace_ring.pop(0)
            record_event({
                "sessionId": self.job.id,
                "eventId": payload["eventId"],
                "eventType": event_type,
                "logCategory": "trace",
                "seq": None,
                "traceSeq": trace_seq,
                "requestId": self.correlation_id,
                "otelTraceId": trace_id,
                "otelSpanId": span_id,
                "payloadDigest": payload["payloadDigest"],
                "payloadSummary": summary,
                "payloadPreview": ledger_preview(sanitized_kwargs),
            })
            try:
                logger.debug(
                    "[TRACE:%s traceSeq=%d rid=%s event=%s digest=%s] %s %s",
                    self.job.id[:8], payload["traceSeq"], self.correlation_id,
                    payload["eventId"], payload["payloadDigest"], event_type,
                    _event_summary_text(summary),
                    extra=_log_extra("trace"),
                )
            except Exception:
                pass
            return payload

        seq = self._next_seq()
        payload: dict = {
            "type": event_type,
            "seq": seq,
            "sessionId": self.job.id,
            "ts": datetime.now(UTC).isoformat(),
            "logCategory": category,
            **sanitized_kwargs,
        }
        payload["eventId"] = f"{self.job.id}:{seq}"
        payload["payloadDigest"] = ledger_digest(payload)
        payload["payloadSummary"] = _event_payload_summary(event_type, payload)
        # Ring buffer keeps last N events for ``Last-Event-ID`` replay.
        self._ring.append(payload)
        if len(self._ring) > self.EVENT_BUFFER_MAX:
            # Drop the oldest — resume from here would require a full
            # snapshot; the SSE endpoint handles that path.
            self._ring.pop(0)
        # Track "active agent" — the most recent agent_status/
        # slot_progress/phase_start that reported a running state sets
        # it. Cheap to compute here so the run_overview emit path
        # doesn't have to scan history.
        if event_type in ("agent_status", "slot_progress"):
            status = kwargs.get("status")
            if status == "running":
                aid = kwargs.get("agentId") or kwargs.get("activeAgentId")
                if aid:
                    self._active_agent_id = aid
        if event_type == "artifact_added":
            self._artifacts.append({k: v for k, v in kwargs.items()})
        if event_type == "change_recorded":
            change_record = {
                k: v for k, v in payload.items()
                if k not in {"type", "seq", "sessionId", "logCategory"}
            }
            self._changes.append(change_record)
            if len(self._changes) > self.CHANGE_BUFFER_MAX:
                self._changes.pop(0)
        if event_type == "slot_progress":
            slot_id = kwargs.get("slotId") or kwargs.get("agentId")
            if slot_id:
                self._slot_progress[slot_id] = {k: v for k, v in kwargs.items()}
        if event_type in ("agent_error", "agent_status", "slot_progress"):
            status = str(kwargs.get("status") or "").lower()
            if event_type == "agent_error" or status in ("error", "failed"):
                self.failure_event.set()
        try:
            # High-volume event types (phase_detail, phase_start, phase_complete)
            # are demoted to DEBUG — they're useful for deep debugging but
            # flood the logs at INFO (one agent round can emit 100+ detail events).
            _noisy = event_type in ("phase_detail", "phase_start", "phase_complete")
            log_fn = logger.debug if _noisy else logger.info
            log_fn(
                "[EVENT:%s seq=%d rid=%s event=%s digest=%s cat=%s] %s %s",
                self.job.id[:8], seq, self.correlation_id, payload["eventId"],
                payload["payloadDigest"], category, event_type,
                _event_summary_text(payload.get("payloadSummary") or {}),
                extra=_log_extra(category),
            )
            if not _noisy:
                summary = payload.get("payloadSummary") or {}
                logger.info(
                    "[MISSION_TRACE:%s seq=%d event=%s actor=%s task=%s digest=%s] %s",
                    self.job.id[:8],
                    seq,
                    event_type,
                    _event_actor(payload, summary),
                    _event_task_label(payload, summary),
                    payload["payloadDigest"],
                    _event_audit_summary(event_type, payload, summary),
                    extra=_log_extra(category),
                )
        except Exception:
            # Logging must never break emit.
            pass
        record_event({
            "sessionId": self.job.id,
            "eventId": payload["eventId"],
            "eventType": event_type,
            "logCategory": category,
            "seq": seq,
            "traceSeq": None,
            "requestId": self.correlation_id,
            "otelTraceId": trace_id,
            "otelSpanId": span_id,
            "payloadDigest": payload["payloadDigest"],
            "payloadSummary": payload.get("payloadSummary") or {},
            "payloadPreview": ledger_preview(sanitized_kwargs),
        })
        # Persist the event to SQLite so re-loading the session after the
        # execution finishes (or after a backend restart) replays the full
        # live log instead of showing an empty trace. Trace events stay
        # internal-only — they are excluded by the seq>0 guard inside the
        # store.
        try:
            session_event_store.append_event(self.job.id, payload)
        except Exception:
            logger.debug("session_events append failed", exc_info=True)
        try:
            summary = payload.get("payloadSummary") or {}
            log_audit(
                self.job.id,
                _event_actor(payload, summary),
                f"mission_event:{event_type}",
                {
                    "seq": seq,
                    "eventId": payload.get("eventId"),
                    "eventType": event_type,
                    "logCategory": category,
                    "payloadDigest": payload.get("payloadDigest"),
                    "task": _event_task_label(payload, summary),
                    "summary": summary,
                },
                _event_audit_summary(event_type, payload, summary),
                self.job.user_id,
                self.job.user_upn,
                success=(event_type not in {"mission_failed", "task_failed", "agent_error"}),
                log_category=category,
            )
        except Exception:
            logger.debug("mission event audit append failed", exc_info=True)
        self.event_queue.put_nowait(payload)
        self._emit_pi_subagents_projection(event_type, payload)
        return payload

    def _dynamic_pi_context_for_event(self, payload: dict) -> dict[str, Any] | None:
        mission = self.dynamic_mission_state
        if mission is None:
            return None
        run_id = str(payload.get("runId") or "").strip()
        agent_session_id = str(payload.get("agentId") or payload.get("slotId") or payload.get("activeAgentId") or "").strip()
        run: SubagentRun | None = None
        if run_id:
            run = mission.subagent_runs.get(run_id)
        if run is None and agent_session_id:
            run = next(
                (candidate for candidate in mission.subagent_runs.values() if candidate.agent_session_id == agent_session_id),
                None,
            )
        if run is None:
            return None
        assignment = next(
            (agent for agent in self.job.agents if agent.session_id == run.agent_session_id),
            None,
        )
        task = mission.tasks.get(run.task_id)
        agent_name = str(payload.get("agentName") or "").strip() or _agent_display_name(run.agent_id)
        return {
            "runId": run.id,
            "taskId": run.task_id,
            "taskTitle": task.title if task is not None else None,
            "taskObjective": task.objective if task is not None else None,
            "agent": run.agent_id,
            "agentId": run.agent_session_id or agent_session_id or run.agent_id,
            "agentName": agent_name,
            "role": assignment.role if assignment is not None else agent_name,
        }

    def _emit_pi_subagents_projection(self, event_type: str, payload: dict) -> None:
        if event_type.startswith("pi.") or not _pi_subagents_observability_enabled():
            return
        context = self._dynamic_pi_context_for_event(payload)
        if context is None:
            return

        source_seq = payload.get("seq")
        status = str(payload.get("status") or "").lower()
        current_step = str(payload.get("currentStep") or payload.get("summary") or "").strip()
        tool_name = str(payload.get("toolName") or "").strip()
        duration_ms = payload.get("durationMs") if isinstance(payload.get("durationMs"), int) else None
        tool_count = sum(1 for agent in self.job.agents if agent.session_id == context["agentId"] for _ in agent.actions)

        state = "running"
        activity_state = "active_long_running"
        summary = current_step or "Subagent is running."
        current_tool = None
        if event_type == "agent_added":
            state = "queued"
            activity_state = "queued"
            summary = bounded_text(str(context.get("taskObjective") or context.get("taskTitle") or "Queued"), max_chars=500)
        elif event_type in {"tool_call_started", "diagnostic_baseline_captured"}:
            state = "running"
            current_tool = tool_name or None
            summary = f"Running {tool_name}" if tool_name else "Running tool"
        elif event_type == "tool_call_ended":
            current_tool = tool_name or None
            state = "running" if status == "ok" else "failed"
            activity_state = "active_long_running" if status == "ok" else "needs_attention"
            summary = f"{tool_name} completed" if status == "ok" else f"{tool_name} failed"
        elif event_type == "slot_progress":
            if status in {"done", "completed", "success"}:
                state = "complete"
                activity_state = "complete"
                summary = str(payload.get("reason") or "Subagent completed.")
            elif status in {"failed", "error"}:
                state = "failed"
                activity_state = "needs_attention"
                summary = str(payload.get("reason") or "Subagent needs attention.")
            elif status == "running":
                summary = str(payload.get("reason") or "Subagent is running.")
        elif event_type == "agent_status":
            if status in {"completed", "done", "success"}:
                state = "complete"
                activity_state = "complete"
                summary = current_step or "Subagent completed."
            elif status in {"error", "failed"}:
                state = "failed"
                activity_state = "needs_attention"
                summary = current_step or "Subagent needs attention."
            elif status == "queued":
                state = "queued"
                activity_state = "queued"
                summary = current_step or "Subagent queued."
            else:
                state = "running"
                summary = current_step or "Subagent is running."
                if summary.lower().startswith("calling ") and summary.endswith("..."):
                    current_tool = summary[len("Calling "):-3]
        elif event_type == "agent_error":
            state = "failed"
            activity_state = "needs_attention"
            summary = str(payload.get("error") or "Subagent failed.")
        elif event_type == "diagnostic_required":
            state = "blocked"
            activity_state = "needs_attention"
            current_tool = tool_name or None
            summary = str(payload.get("directivePreview") or payload.get("reason") or "Diagnostic checkpoint required.")
        else:
            return

        common = {
            "schemaVersion": 1,
            "runId": context["runId"],
            "taskId": context["taskId"],
            "taskTitle": context.get("taskTitle"),
            "agent": context["agent"],
            "agentId": context["agentId"],
            "agentName": context["agentName"],
            "extension": _pi_subagent_extension(),
            "sourceEventSeq": source_seq,
        }
        self._emit_with_bound_session(
            "pi.subagents.status",
            **common,
            mode="single",
            state=state,
            activityState=activity_state,
            task=context.get("taskTitle") or context.get("role"),
            summary=bounded_text(summary, max_chars=1000),
            currentTool=current_tool,
            toolCount=tool_count,
            durationMs=duration_ms,
            progress=[{
                "index": 0,
                "agent": context["agent"],
                "status": state,
                "task": context.get("taskTitle") or context.get("role"),
                "recentOutput": [bounded_text(summary, max_chars=500)],
                "recentTools": [current_tool] if current_tool else [],
                "toolCount": tool_count,
                "tokens": 0,
                "durationMs": duration_ms or 0,
            }],
        )

        if event_type == "tool_call_started":
            self._emit_with_bound_session(
                "pi.subagents.control",
                **common,
                controlType="active_long_running",
                to="tool",
                message=bounded_text(f"Calling {tool_name} through AgentHub policy.", max_chars=1000),
                currentTool=tool_name or None,
                toolCount=tool_count,
            )
        elif event_type in {"diagnostic_required", "agent_error"} or (event_type == "slot_progress" and state == "failed"):
            self._emit_with_bound_session(
                "pi.subagents.control",
                **common,
                controlType="needs_attention",
                to="operator",
                message=bounded_text(summary, max_chars=1000),
                reason=str(payload.get("reason") or payload.get("status") or event_type),
                currentTool=current_tool,
                toolCount=tool_count,
            )
        elif event_type in {"agent_added", "agent_status"} and state in {"queued", "running"}:
            self._emit_with_bound_session(
                "pi.subagents.async",
                **common,
                asyncId=f"async-{context['runId']}",
                mode="single",
                state=state,
                agents=[context["agent"]],
                summary=bounded_text(summary, max_chars=500),
                sessionDir=f"agenthub://sessions/{self.job.id}/subagents",
                outputFile=f"agenthub://sessions/{self.job.id}/subagents/{context['runId']}.jsonl",
            )

        if event_type == "slot_progress" and state in {"complete", "failed"}:
            self._emit_with_bound_session(
                "pi.subagents.result",
                **common,
                mode="single",
                status="completed" if state == "complete" else "failed",
                summary=bounded_text(summary, max_chars=4000),
                usage={"toolCount": tool_count, "durationMs": duration_ms or 0},
                sessionFile=f"agenthub://sessions/{self.job.id}/subagents/{context['runId']}.jsonl",
                artifactPaths={
                    "jsonlPath": f"agenthub://sessions/{self.job.id}/subagents/{context['runId']}.jsonl",
                    "metadataPath": f"agenthub://sessions/{self.job.id}/subagents/{context['runId']}_meta.json",
                },
            )

    def snapshot_run_overview(self) -> dict:
        """Build a ``run_overview`` event payload the UI can use as the
        single source of truth when (re)connecting. Intentionally
        compact: job status, composition, active agent, and the
        accumulated slot/artifact state. Full phase history stays
        accessible via ``GET /api/sessions/{id}``.
        """
        return {
            "job": {
                "id": self.job.id,
                "status": self.job.status.value if hasattr(self.job.status, "value") else str(self.job.status),
                "startedAt": self.job.started_at.isoformat() if self.job.started_at else None,
                "completedAt": self.job.completed_at.isoformat() if self.job.completed_at else None,
            },
            "composition": (
                self.job.composition.model_dump(mode="json", by_alias=True)
                if self.job.composition else None
            ),
            "activeAgentId": self._active_agent_id,
            "artifacts": list(self._artifacts),
            "changes": list(self._changes),
            "slotProgress": list(self._slot_progress.values()),
        }

    def replay_since(self, last_seq: int) -> list[dict]:
        """Return buffered events with ``seq > last_seq`` (in order).

        If ``last_seq`` predates the oldest buffered event, the caller
        must treat the ring buffer as insufficient and instead rely on
        the ``run_overview`` snapshot emitted at subscribe time.
        """
        if not self._ring:
            return []
        return [ev for ev in self._ring if ev.get("seq", 0) > last_seq]

    async def events(self, *, last_seq: int | None = None) -> AsyncGenerator[dict]:
        """Stream events, starting with optional resume replay.

        When ``last_seq`` is provided, any ring-buffered events with
        ``seq > last_seq`` are yielded first (in order), after which
        the loop drains the live queue. The stream terminates on any
        of the three terminal event types (``job_complete`` /
        ``job_failed`` / ``job_cancelled``).
        """
        if last_seq is not None:
            for ev in self.replay_since(last_seq):
                yield ev
        while True:
            try:
                # 15s keeps the heartbeat within typical proxy idle
                # windows; browsers treat a missed keepalive at 30–60s
                # as a silent disconnect.
                ev = await asyncio.wait_for(self.event_queue.get(), timeout=15)
                yield ev
                if ev.get("type") in ("job_complete", "job_failed", "job_cancelled"):
                    return
            except TimeoutError:
                # Heartbeats carry no seq so they never pollute the
                # client's ``Last-Event-ID`` tracker on resume.
                yield {"type": "heartbeat", "ts": datetime.now(UTC).isoformat()}


def _copilot_headers(copilot_token: str) -> dict:
    return {
        "Authorization": f"Bearer {copilot_token}",
        "Content-Type": "application/json",
        "Copilot-Integration-Id": "vscode-chat",
        "Editor-Version": "vscode/1.100.0",
        "Editor-Plugin-Version": "copilot-chat/0.25.0",
    }


def _openai_tool_names(mcp_manager: Any) -> set[str]:
    if not mcp_manager:
        return set()
    return {
        tool.get("function", {}).get("name")
        for tool in mcp_manager.get_openai_tools_schema()
        if tool.get("function", {}).get("name")
    }


def _resolve_wildcard_tool_scope(agent_id: str, mcp_manager: Any) -> set[str]:
    available = _openai_tool_names(mcp_manager)
    if not available:
        return set()

    if agent_id == GENERALIST_AGENT_ID:
        desired_ordered = list(_GENERALIST_BOOTSTRAP_TOOLS)
    else:
        template = get_template(agent_id)
        desired_ordered = list(template.available_tools) if template else []

    if desired_ordered:
        resolved = [tool for tool in desired_ordered if tool in available]
    else:
        resolved = sorted(available)
    return set(resolved[:MODEL_TOOL_SCHEMA_LIMIT])


def _mission_mcp_runtime_required() -> bool:
    mode = os.environ.get("AGENTHUB_MCP_RUNTIME", "auto").lower()
    if mode in {"container", "sidecar", "required", "pi-subagents", "container-pi-subagents"}:
        return True
    if mode in {"off", "disabled", "global"}:
        return False
    return os.environ.get("AGENT_ISOLATION", "inprocess").lower() == "container"


def _pi_subagents_observability_enabled() -> bool:
    runtime = os.environ.get("AGENTHUB_ORCHESTRATION_RUNTIME", "dynamic").strip().lower()
    observability = os.environ.get("AGENTHUB_PI_OBSERVABILITY", "").strip().lower()
    return runtime in {"pi-subagents", "pisubagents", "pi_subagents"} or observability in {"pi-subagents", "pisubagents", "pi_subagents", "1", "true", "on"}


def _pi_subagent_extension() -> dict[str, str]:
    return {
        "id": "pi-subagents",
        "label": "pi-subagents",
        "packageName": "pi-subagents",
        "version": "0.21.3",
    }


class OrchestratorEngine:
    """Executes Compositions: drives per-slot agent loops and streams
    session events.

    The orchestrator has **no plan-generation responsibility**. That
    lives on ``ComposeService``. The orchestrator only runs what's
    already been composed.
    """

    def __init__(
        self,
        mcp_manager=None,
        copilot_token_fn: Callable[[str], Awaitable[str]] | None = None,
        acquire_mcp_tokens_fn: Callable[[str], Awaitable[dict | None]] | None = None,
        compose_service: ComposeService | None = None,
    ):
        self.mcp_manager = mcp_manager
        self.copilot_token_fn = copilot_token_fn
        self.acquire_mcp_tokens_fn = acquire_mcp_tokens_fn
        self._compose: ComposeService = compose_service or get_compose_service()
        self._active_jobs: dict[str, _JobExecution] = {}

    def configure(self, mcp_manager, copilot_token_fn, acquire_mcp_tokens_fn) -> None:
        """Inject shared dependencies at application startup."""
        self.mcp_manager = mcp_manager
        self.copilot_token_fn = copilot_token_fn
        self.acquire_mcp_tokens_fn = acquire_mcp_tokens_fn

    async def dispose_async(self) -> None:
        """Best-effort cleanup for active mission resources during shutdown."""
        executions = list(self._active_jobs.values())
        if executions:
            for execution in executions:
                execution.cancelled = True
                execution.cancel_event.set()
                for task in execution.tasks:
                    if not task.done():
                        task.cancel()

            await asyncio.gather(
                *(execution.close_mcp_runtime() for execution in executions),
                return_exceptions=True,
            )

        try:
            from services.mcp.mission_runtime_manager import cleanup_mission_mcp_runtimes

            await cleanup_mission_mcp_runtimes(active_session_ids=set())
        except Exception as exc:
            logger.warning(
                "[ORCHESTRATOR] failed to sweep mission MCP runtimes during shutdown: %s",
                exc,
                extra=_log_extra("diagnostic"),
            )

        if executions:
            await asyncio.gather(
                *(task for execution in executions for task in execution.tasks),
                return_exceptions=True,
            )
        self._active_jobs.clear()

    async def _attach_mission_mcp_runtime(self, execution: _JobExecution) -> None:
        if not _mission_mcp_runtime_required():
            execution.mcp_manager = self.mcp_manager
            return
        if self.mcp_manager is None:
            raise RuntimeError("Mission MCP runtime requires a validated startup MCP manager")
        from services.mcp.mission_runtime_manager import start_mission_mcp_runtime

        runtime = await start_mission_mcp_runtime(execution.job.id, self.mcp_manager)
        execution.mcp_runtime = runtime
        execution.mcp_manager = runtime

    def _duration_text(self, job: Job) -> str:
        if not job.started_at:
            return "0s"
        completed_at = job.completed_at or datetime.now(UTC)
        secs = max(0, int((completed_at - job.started_at).total_seconds()))
        mins = secs // 60
        secs_rem = secs % 60
        return f"{mins}m {secs_rem}s" if mins else f"{secs_rem}s"

    def _emit_startup_snapshot(self, execution: _JobExecution) -> None:
        context = execution.job.context if isinstance(execution.job.context, dict) else {}
        pi_context = context.get("pi_orchestration") if isinstance(context.get("pi_orchestration"), dict) else {}
        if context.get("runtime") == "pi" or context.get("orchestration_runtime") == "pi" or pi_context.get("runtime") == "pi":
            extensions = pi_context.get("extensions") if isinstance(pi_context.get("extensions"), list) else []
            extension_sources = [
                str(item.get("source"))
                for item in extensions
                if isinstance(item, dict) and item.get("source")
            ]
            harness_manifest = build_pi_harness_manifest()
            context_tools = pi_context.get("tools") if isinstance(pi_context.get("tools"), list) else None
            context_policy_summary = pi_context.get("toolPolicySummary") if isinstance(pi_context.get("toolPolicySummary"), dict) else None
            execution.emit(
                "pi.orchestration.start",
                schemaVersion=1,
                runtime="pi",
                subagentRuntime=str(pi_context.get("subagent_runtime") or "pi-subagents"),
                subagentPackage=str(pi_context.get("subagent_package") or "npm:pi-subagents@0.21.3"),
                subagentHarness=str(pi_context.get("subagentHarness") or harness_manifest.get("subagentHarness") or "pi-subagents"),
                subagentRuntimeMode=str(pi_context.get("subagentRuntimeMode") or harness_manifest.get("subagentRuntimeMode") or "foreground-status-control-results"),
                subagentObservability=pi_context.get("subagent_observability") or harness_manifest.get("subagentObservability"),
                runtimePackage=str(pi_context.get("runtime_package_name") or "@mariozechner/pi-agent-core"),
                runtimePackageSource=str(pi_context.get("runtime_package") or "npm:@mariozechner/pi-agent-core@0.71.1"),
                frontendRuntimePackage=str(pi_context.get("frontend_runtime_package_name") or "@mariozechner/pi-web-ui"),
                executionSurfaceExtension=str(pi_context.get("execution_surface_extension") or "@fabric-clawhub/pi-mission-ui"),
                agenticEngineeringExtension=str(pi_context.get("agentic_engineering_extension") or "@fabric-clawhub/pi-agentic-engineering"),
                rpiProtocol=str(pi_context.get("rpi_protocol") or "research-plan-implement-context-gates"),
                qrspiProtocol=str(pi_context.get("qrspi_protocol") or "question-research-design-structure-plan-implement-verify-review"),
                qrspiPhaseModel=list(pi_context.get("qrspi_phase_model") or ["question", "research", "design", "structure", "plan", "worktree", "implement", "verify", "review"]),
                qrspiQuestionPolicy=pi_context.get("qrspi_question_policy"),
                qrspiResearchPolicy=pi_context.get("qrspi_research_policy"),
                qrspiDesignStructurePolicy=pi_context.get("qrspi_design_structure_policy"),
                qrspiInstructionBudget=pi_context.get("qrspi_instruction_budget"),
                qrspiVerticalSlicePolicy=pi_context.get("qrspi_vertical_slice_policy"),
                qrspiBacktrackPolicy=pi_context.get("qrspi_backtrack_policy"),
                qrspiReviewPolicy=pi_context.get("qrspi_review_policy"),
                contextPackSchema=str(pi_context.get("context_pack_schema") or "ContextPackV2"),
                subagentWorkModel=str(pi_context.get("subagent_work_model") or "context-window-fork"),
                contextWindowPolicy=pi_context.get("context_window_policy"),
                contextModeFacade=str(pi_context.get("context_mode_facade") or "agenthub-governed-context-mode"),
                contextModeEvents=list(pi_context.get("context_mode_events") or []),
                contextModeControls=pi_context.get("context_mode_controls"),
                streamTransport=str(pi_context.get("stream_transport") or "agenthub-sse-to-pi-extension"),
                extensions=extension_sources,
                orchestrationHarness=str(pi_context.get("orchestrationHarness") or harness_manifest["orchestrationHarness"]),
                harnessPackage=str(pi_context.get("harnessPackage") or harness_manifest["harnessPackage"]),
                toolRegistry=str(pi_context.get("toolRegistry") or harness_manifest["toolRegistry"]),
                toolExecutionBridge=str(pi_context.get("toolExecutionBridge") or harness_manifest["toolExecutionBridge"]),
                toolCount=int(pi_context.get("toolCount") or harness_manifest["toolCount"]),
                emittedToolCount=int(pi_context.get("emittedToolCount") or len(context_tools or harness_manifest["tools"])),
                toolPolicySummary=context_policy_summary or harness_manifest["toolPolicySummary"],
                tools=context_tools or harness_manifest["tools"],
                backendBridge="agenthub-fabric-runtime",
                extension={
                    "id": "fabric-clawhub-mission-ui",
                    "label": "Fabric ClawHub Pi Mission UI",
                    "packageName": "@fabric-clawhub/pi-mission-ui",
                    "version": "0.1.0",
                },
                logCategory="high_level",
            )
        execution.emit(
            "composition_ready",
            composition=execution.job.composition.model_dump(mode="json", by_alias=True),
        )
        execution.emit("run_overview", **execution.snapshot_run_overview())
        execution.emit(
            "slot_progress",
            slotId=GENERALIST_AGENT_ID,
            agentId=GENERALIST_AGENT_ID,
            agentName="Generalist",
            role="Mission controller",
            status="running",
            currentStep="Preparing isolated tool runtime",
        )
        execution.emit(
            "log_line",
            agentId=GENERALIST_AGENT_ID,
            agentName="Generalist",
            level="info",
            message="Mission accepted. Preparing the isolated tool runtime and attaching live events.",
            tags=["startup"],
        )

    async def _fail_job_before_runtime_ready(self, execution: _JobExecution, exc: Exception) -> str:
        job = execution.job
        reason = bounded_text(str(exc) or exc.__class__.__name__, max_chars=1200)
        user_message = (
            "Mission failed while preparing the isolated tool runtime. "
            "This is an AgentHub runtime/startup error, not a problem with your prompt. "
            f"Details: {reason}"
        )
        job.status = JobStatus.FAILED
        job.completed_at = datetime.now(UTC)
        update_session(job)
        execution.emit(
            "slot_progress",
            slotId=GENERALIST_AGENT_ID,
            agentId=GENERALIST_AGENT_ID,
            agentName="Generalist",
            role="Mission controller",
            status="failed",
            currentStep="Tool runtime failed before work started",
            reason="runtime_start_failed",
        )
        execution.emit(
            "agent_error",
            agentId=GENERALIST_AGENT_ID,
            agentName="Generalist",
            error=user_message,
        )
        execution.emit(
            "job_failed",
            jobId=job.id,
            status=job.status.value,
            totalDuration=self._duration_text(job),
            reason=user_message,
        )
        try:
            await asyncio.shield(execution.close_mcp_runtime())
        except Exception as close_exc:
            logger.warning(
                "[ORCHESTRATOR] failed to close failed-start mission MCP runtime: %s",
                close_exc,
                extra=_log_extra("diagnostic"),
            )
        self._active_jobs.pop(job.id, None)
        return job.id

    # ── Composition (single LLM analysis step) ──────────────────────

    async def compose(
        self,
        task_description: str,
        workspace_id: str,
        copilot_token: str,
        *,
        session_id: str | None = None,
        attachments: list[Any] | None = None,
        preferred_architecture: str | None = None,
        require_approvals: bool = True,
        branch_out: bool = False,
        model: str | None = None,
        workspace_context: WorkspaceContext | None = None,
    ) -> Composition:
        """Delegate to ``ComposeService``. Present here so callers have a
        single engine-level entrypoint rather than reaching into the
        compose service directly.
        """
        return await self._compose.compose(
            task_description=task_description,
            workspace_id=workspace_id,
            copilot_token=copilot_token,
            session_id=session_id,
            attachments=attachments,
            preferred_architecture=preferred_architecture,
            require_approvals=require_approvals,
            branch_out=branch_out,
            model=model,
            workspace_context=workspace_context,
        )

    # ── Execution ───────────────────────────────────────────────────

    async def start_job(
        self,
        job: Job,
        copilot_token: str,
        mcp_tokens: dict | None,
    ) -> str:
        """Begin executing an already-composed job. Returns the job id.

        Dynamic generalist orchestration is the product default: the
        reviewed ``Composition`` seeds a live mission graph, then the
        mission controller dispatches task-scoped subagents through the
        existing agent runners. Set ``AGENTHUB_ORCHESTRATION_RUNTIME``
        to ``fixed`` or ``maf`` to use the previous fixed-composition
        driver path for local debugging.
        """
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)

        if not job.composition:
            raise RuntimeError("start_job called without a Composition")

        if _use_fixed_composition_runtime():
            return await self._start_fixed_composition_job(job, copilot_token, mcp_tokens)

        return await self._start_dynamic_job(job, copilot_token, mcp_tokens)

    async def _start_fixed_composition_job(
        self,
        job: Job,
        copilot_token: str,
        mcp_tokens: dict | None,
    ) -> str:
        """Previous fixed-composition runtime, retained as an explicit debug path."""

        # One AgentAssignment per slot. The runtime skips slots that
        # reference an unknown agent template (logged).
        for slot in job.composition.slots:
            tpl = get_template(slot.agent_id)
            if tpl is None:
                logger.warning(
                    "[ORCHESTRATOR] Slot %s references unknown agent '%s' — skipped",
                    slot.id, slot.agent_id,
                    extra=_log_extra("diagnostic"),
                )
                continue
            assignment_session_id = str(uuid.uuid4())
            job.agents.append(
                AgentAssignment(
                    agent_id=slot.agent_id,
                    session_id=assignment_session_id,
                    role=slot.role,
                    goal=_build_slot_goal(
                        task=job.composition.task,
                        slot_id=slot.id,
                        slot_role=slot.role,
                        skills=[s.name for s in slot.skills],
                    ),
                    status=AgentStatus.QUEUED,
                )
            )

        update_session(job)

        execution = _JobExecution(job, copilot_token, mcp_tokens)
        execution.correlation_id = get_request_id()
        self._active_jobs[job.id] = execution
        self._emit_startup_snapshot(execution)
        try:
            await self._attach_mission_mcp_runtime(execution)
        except Exception as exc:
            logger.exception(
                "[ORCHESTRATOR] Mission MCP runtime failed before fixed mission start: %s",
                exc,
                extra=_log_extra("high_level"),
            )
            return await self._fail_job_before_runtime_ready(execution, exc)
        execution.emit(
            "log_line",
            agentId=GENERALIST_AGENT_ID,
            agentName="Generalist",
            level="info",
            message="Tool runtime is ready. Starting the mission controller.",
            tags=["startup"],
        )

        for agent in job.agents:
            tpl = get_template(agent.agent_id)
            if not tpl:
                continue
            user_q: asyncio.Queue = asyncio.Queue()
            execution.user_message_queues[agent.session_id] = user_q

        # ── Architecture driver dispatch ──────────────────────────
        from services.agenthub.drivers.budget import BudgetTracker
        from services.agenthub.drivers.registry import DriverRegistry
        from services.agenthub.drivers.slot_runner import SlotRunner
        from services.agenthub.drivers.step_tracker import StepTracker

        driver = DriverRegistry.get(job.composition.architecture)
        budget_tracker = BudgetTracker(budget=job.composition.budget)

        # Build the step tracker for structured observability
        tracker = StepTracker(job.id, job.composition.architecture)
        # Register slot_id → human-readable agent display name
        slot_names = {}
        for slot in job.composition.slots:
            tpl = get_template(slot.agent_id)
            slot_names[slot.id] = tpl.display_name if tpl else slot.agent_id
        tracker.register_names(slot_names)

        # Select slot runner mode: containerized (each agent in its own
        # Docker container) or in-process (legacy, same Python process).
        isolation = os.environ.get("AGENT_ISOLATION", "inprocess").lower()
        if isolation == "container":
            from services.agenthub.drivers.container_backend import DockerBackend
            from services.agenthub.drivers.container_pool import ContainerPool
            from services.agenthub.drivers.container_runner import ContainerSlotRunner
            backend = DockerBackend()
            pool = ContainerPool()
            runner = ContainerSlotRunner(execution, self, budget_tracker, backend, pool)
            logger.info("[ORCHESTRATOR] Using container-isolated agents for session %s", job.id, extra=_log_extra("high_level"))
        else:
            runner = SlotRunner(execution, self, budget_tracker, tracker=tracker)

        # Register slot_id → assignment mappings so the SlotRunner can
        # look up assignments by composition slot id.
        for slot, agent in zip(job.composition.slots, job.agents, strict=False):
            runner.register_slot(slot.id, agent)

        driver_task = asyncio.create_task(
            self._run_driver(execution, driver, runner, budget_tracker, tracker)
        )
        execution.tasks.append(driver_task)

        asyncio.create_task(self._monitor_job(execution))

        return job.id

    async def _start_dynamic_job(
        self,
        job: Job,
        copilot_token: str,
        mcp_tokens: dict | None,
    ) -> str:
        """Start the dynamic mission-controller runtime."""
        update_session(job)

        execution = _JobExecution(job, copilot_token, mcp_tokens)
        execution.correlation_id = get_request_id()
        self._active_jobs[job.id] = execution
        self._emit_startup_snapshot(execution)
        try:
            await self._attach_mission_mcp_runtime(execution)
        except Exception as exc:
            logger.exception(
                "[ORCHESTRATOR] Mission MCP runtime failed before dynamic mission start: %s",
                exc,
                extra=_log_extra("high_level"),
            )
            return await self._fail_job_before_runtime_ready(execution, exc)
        execution.emit(
            "log_line",
            agentId=GENERALIST_AGENT_ID,
            agentName="Generalist",
            level="info",
            message="Tool runtime is ready. Starting the mission controller.",
            tags=["startup"],
        )

        from services.agenthub.drivers.budget import BudgetTracker
        from services.agenthub.drivers.slot_runner import SlotRunner
        from services.agenthub.drivers.step_tracker import StepTracker

        budget_tracker = BudgetTracker(budget=job.composition.budget)
        tracker = StepTracker(job.id, f"dynamic:{job.composition.architecture}")
        tracker.register_names({
            slot.id: _agent_display_name(slot.agent_id)
            for slot in job.composition.slots
        })

        isolation = os.environ.get("AGENT_ISOLATION", "inprocess").lower()
        if isolation == "container":
            from services.agenthub.drivers.container_backend import DockerBackend
            from services.agenthub.drivers.container_pool import ContainerPool
            from services.agenthub.drivers.container_runner import ContainerSlotRunner

            backend = DockerBackend()
            pool = ContainerPool()
            runner = ContainerSlotRunner(execution, self, budget_tracker, backend, pool)
            logger.info("[ORCHESTRATOR] Dynamic runtime using container-isolated agents for session %s", job.id, extra=_log_extra("high_level"))
        else:
            runner = SlotRunner(execution, self, budget_tracker, tracker=tracker)

        executor = _RuntimeSubagentExecutor(
            execution=execution,
            engine=self,
            runner=runner,
        )
        controller = DynamicMissionController(
            executor=executor,
            event_sink=execution,
            on_state_change=dynamic_mission_store.save_mission_state,
        )
        state = controller.seed_from_job(job)
        execution.dynamic_controller = controller
        execution.dynamic_mission_state = state
        dynamic_mission_store.save_mission_state(state)

        mission_task = asyncio.create_task(self._run_dynamic_mission(execution, controller, state))
        execution.tasks.append(mission_task)
        asyncio.create_task(self._monitor_job(execution))
        return job.id

    async def _run_dynamic_mission(
        self,
        execution: _JobExecution,
        controller: DynamicMissionController,
        state: MissionState,
    ) -> None:
        try:
            await controller.run_until_idle(state)
            dynamic_mission_store.save_mission_state(state)
        except asyncio.CancelledError:
            state.status = MissionStatus.CANCELLED
            for active_task in list(controller._active_tasks):
                if not active_task.done():
                    active_task.cancel()
            if controller._active_tasks:
                await asyncio.gather(*controller._active_tasks.keys(), return_exceptions=True)
            dynamic_mission_store.save_mission_state(state)
            execution.emit("mission_cancelled", mission=state.model_dump(mode="json", by_alias=True))
            raise
        except Exception as exc:
            logger.error("[DYNAMIC] mission controller failed: %s", exc, exc_info=True, extra=_log_extra("diagnostic"))
            state.status = MissionStatus.FAILED
            dynamic_mission_store.save_mission_state(state)
            reason = _compact_issue_text(f"Dynamic mission controller failed: {exc}")
            for assignment in execution.job.agents:
                if assignment.status in (AgentStatus.QUEUED, AgentStatus.RUNNING, AgentStatus.WAITING):
                    assignment.status = AgentStatus.ERROR
                    assignment.current_step = reason
                    execution.emit(
                        "slot_progress",
                        slotId=assignment.session_id,
                        agentId=assignment.session_id,
                        status="failed",
                        agentName=_agent_display_name(assignment.agent_id),
                        role=assignment.role,
                        reason=reason,
                    )
            execution.emit("mission_failed", reason=reason, mission=state.model_dump(mode="json", by_alias=True))
            update_session(execution.job)

    async def _monitor_job(self, execution: _JobExecution):
        """Wait for all agent tasks to complete, then mark job done."""
        dynamic_runtime = execution.dynamic_mission_state is not None
        supervisor_task = None if dynamic_runtime else asyncio.create_task(self._supervise_agent_failures(execution))
        try:
            while True:
                if not dynamic_runtime:
                    await self._handle_agent_failures_once(execution)
                pending = [task for task in execution.tasks if not task.done()]
                if not pending:
                    if not dynamic_runtime:
                        await self._handle_agent_failures_once(execution)
                    pending = [task for task in execution.tasks if not task.done()]
                    if not pending:
                        break

                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.error("[ORCHESTRATOR] Runtime task error: %s", e, exc_info=True, extra=_log_extra("diagnostic"))
        except Exception as e:
            logger.error("[ORCHESTRATOR] Monitor error: %s", e, exc_info=True, extra=_log_extra("diagnostic"))
        finally:
            if supervisor_task is not None:
                supervisor_task.cancel()
                try:
                    await supervisor_task
                except asyncio.CancelledError:
                    pass

        job = execution.job
        # P4 · Honour cancellation as a distinct terminal state so the
        # UI doesn't misclassify a user-initiated stop as a failure.
        was_cancelled = execution.cancelled or execution.cancel_event.is_set()
        mission_status = execution.dynamic_mission_state.status if execution.dynamic_mission_state else None
        dynamic_completed = mission_status == MissionStatus.COMPLETED
        any_error = False if dynamic_completed else any(self._assignment_error_is_unrecovered(job, a) for a in job.agents)
        dynamic_failed = mission_status in (MissionStatus.FAILED, MissionStatus.BLOCKED)
        dynamic_cancelled = mission_status == MissionStatus.CANCELLED
        if was_cancelled:
            job.status = JobStatus.CANCELLED
        elif dynamic_cancelled:
            job.status = JobStatus.CANCELLED
        else:
            job.status = JobStatus.FAILED if any_error or dynamic_failed else JobStatus.COMPLETED
        job.completed_at = datetime.now(UTC)
        update_session(job)

        duration = ""
        if job.started_at:
            secs = (job.completed_at - job.started_at).total_seconds()
            mins = int(secs // 60)
            secs_rem = int(secs % 60)
            duration = f"{mins}m {secs_rem}s" if mins else f"{secs_rem}s"

        if was_cancelled:
            terminal = "job_cancelled"
        elif job.status == JobStatus.FAILED:
            terminal = "job_failed"
        else:
            terminal = "job_complete"
        execution.emit(
            terminal,
            jobId=job.id,
            status=job.status.value,
            totalDuration=duration,
        )
        try:
            await asyncio.shield(execution.close_mcp_runtime())
        except Exception as exc:
            logger.warning(
                "[ORCHESTRATOR] failed to stop mission MCP runtime: %s",
                exc,
                extra=_log_extra("diagnostic"),
            )
        self._active_jobs.pop(job.id, None)

    async def _supervise_agent_failures(self, execution: _JobExecution) -> None:
        """React to slot failures while other work is still in flight."""
        while not execution.cancel_event.is_set():
            await execution.failure_event.wait()
            execution.failure_event.clear()
            await self._handle_agent_failures_once(execution)

    async def _handle_agent_failures_once(self, execution: _JobExecution) -> None:
        if execution.cancelled or execution.cancel_event.is_set():
            return

        for assignment in list(execution.job.agents):
            if assignment.status != AgentStatus.ERROR:
                continue
            if assignment.session_id in execution._recovery_handled_agents:
                continue

            execution._recovery_handled_agents.add(assignment.session_id)
            decision = self._decide_failure_recovery(execution.job, assignment)
            assignment._recovery_decision = decision.action  # type: ignore[attr-defined]
            assignment._recovery_reason = decision.reason  # type: ignore[attr-defined]
            logger.info(
                "[RECOVERY:%s] decision=%s failed_agent=%s reason=%s",
                execution.job.id[:8], decision.action, assignment.session_id[:8], decision.reason,
                extra=_log_extra("high_level"),
            )

            if decision.action == "spawn_agent" and decision.agent_id:
                await self._spawn_recovery_agent(execution, assignment, decision)
            elif decision.action == "user_action":
                self._emit_user_action_required(execution, assignment, decision)
            elif decision.action == "stop":
                self._stop_for_unrecoverable_failure(execution, assignment, decision)
            else:
                execution.emit(
                    "log_line",
                    agentId=assignment.session_id,
                    level="warn",
                    message=f"Orchestrator recovery decision: observe existing work after failure — {decision.reason}",
                    tags=["orchestrator_recovery", "observe"],
                )

    def _decide_failure_recovery(self, job: Job, assignment: AgentAssignment) -> _RecoveryDecision:
        reason = _assignment_failure_text(assignment)
        lowered = reason.lower()

        if getattr(assignment, "_recovery_for", None):
            return _RecoveryDecision(
                action="stop",
                reason="A recovery agent failed; stopping rather than creating an unbounded recovery loop.",
            )

        if any(pattern.search(reason) for pattern in _STOP_RECOVERY_PATTERNS):
            return _RecoveryDecision(
                action="stop",
                reason="Failure crossed a safety or policy boundary; continuing could compound the issue.",
            )

        if any(pattern.search(reason) for pattern in _USER_ACTION_RECOVERY_PATTERNS):
            return _RecoveryDecision(
                action="user_action",
                reason="The failed slot needs an external user or tenant action before agents can continue safely.",
                approval_summary=_compact_issue_text(
                    f"{assignment.role} is blocked: {reason}. Review the environment, then choose whether to retry, continue without this slot, or stop the session.",
                    500,
                ),
                recovery_actions=("retry_after_fix", "continue_other_agents", "stop_session"),
            )

        selected_agent_id: str | None = None
        selected_role: str | None = None
        for pattern, agent_id, role in _DOMAIN_RECOVERY_RULES:
            if pattern.search(reason) and get_template(agent_id):
                selected_agent_id = agent_id
                selected_role = role
                break

        if selected_agent_id == assignment.agent_id and not any(pattern.search(reason) for pattern in _TRANSIENT_RECOVERY_PATTERNS):
            selected_agent_id = None
            selected_role = None

        if selected_agent_id is None and any(pattern.search(reason) for pattern in _TRANSIENT_RECOVERY_PATTERNS):
            selected_agent_id = assignment.agent_id
            selected_role = f"Recovery retry for {assignment.role}"

        if selected_agent_id and get_template(selected_agent_id):
            return _RecoveryDecision(
                action="spawn_agent",
                reason=f"A {selected_agent_id} recovery agent has the closest matching skill surface for the failed work.",
                agent_id=selected_agent_id,
                role=selected_role or f"Recovery for {assignment.role}",
                goal=_build_recovery_goal(job, assignment, reason),
            )

        if "missing" in lowered or "unknown" in lowered:
            return _RecoveryDecision(
                action="user_action",
                reason="The failure suggests missing runtime capability or unavailable configuration.",
                approval_summary=_compact_issue_text(
                    f"{assignment.role} needs configuration before recovery can proceed: {reason}",
                    500,
                ),
                recovery_actions=("configure_and_retry", "stop_session"),
            )

        return _RecoveryDecision(
            action="stop",
            reason="No safe recovery agent or user-action path could be inferred for this failure.",
        )

    async def _spawn_recovery_agent(
        self,
        execution: _JobExecution,
        failed_assignment: AgentAssignment,
        decision: _RecoveryDecision,
    ) -> None:
        recovery = await self.add_agent_to_job(
            execution.job.id,
            agent_id=decision.agent_id or failed_assignment.agent_id,
            role=decision.role or f"Recovery for {failed_assignment.role}",
            goal=decision.goal,
        )
        if recovery is None:
            failed_assignment._recovery_status = "unavailable"  # type: ignore[attr-defined]
            execution.emit(
                "log_line",
                agentId=failed_assignment.session_id,
                level="error",
                message="Orchestrator recovery decision: recovery agent could not be attached; user action is required.",
                tags=["orchestrator_recovery", "user_action"],
            )
            self._emit_user_action_required(
                execution,
                failed_assignment,
                _RecoveryDecision(
                    action="user_action",
                    reason="Recovery agent attachment failed.",
                    approval_summary=f"Recovery agent could not be attached for {failed_assignment.role}. Decide whether to retry or stop the session.",
                    recovery_actions=("retry_after_fix", "stop_session"),
                ),
            )
            return

        failed_assignment._recovery_status = "delegated"  # type: ignore[attr-defined]
        failed_assignment._recovery_agent_session_id = recovery.session_id  # type: ignore[attr-defined]
        recovery._recovery_for = failed_assignment.session_id  # type: ignore[attr-defined]
        execution.emit(
            "log_line",
            agentId=failed_assignment.session_id,
            level="warn",
            message=(
                "Orchestrator recovery decision: spawned "
                f"{decision.agent_id} to recover failed slot {failed_assignment.role}."
            ),
            tags=["orchestrator_recovery", "spawn_agent"],
        )
        update_session(execution.job)

    def _emit_user_action_required(
        self,
        execution: _JobExecution,
        assignment: AgentAssignment,
        decision: _RecoveryDecision,
    ) -> None:
        assignment._recovery_status = "requires_user"  # type: ignore[attr-defined]
        approval_id = f"recovery-{assignment.session_id[:8]}"
        execution.emit(
            "approval_required",
            approvalId=approval_id,
            slotId=assignment.session_id,
            agentId=assignment.session_id,
            summary=decision.approval_summary or decision.reason,
            blastRadius="Current run may finish partially until this blocker is resolved.",
            reversible=True,
            toolCallPreview=None,
            recoveryActions=list(decision.recovery_actions or ("retry_after_fix", "stop_session")),
        )
        execution.emit(
            "log_line",
            agentId=assignment.session_id,
            level="warn",
            message=f"Orchestrator recovery decision: user action required — {decision.reason}",
            tags=["orchestrator_recovery", "user_action"],
        )
        update_session(execution.job)

    def _stop_for_unrecoverable_failure(
        self,
        execution: _JobExecution,
        assignment: AgentAssignment,
        decision: _RecoveryDecision,
    ) -> None:
        assignment._recovery_status = "stopped"  # type: ignore[attr-defined]
        reason = _compact_issue_text(decision.reason)
        execution.emit(
            "log_line",
            agentId=assignment.session_id,
            level="error",
            message=f"Orchestrator recovery decision: stopping remaining work — {reason}",
            tags=["orchestrator_recovery", "stop"],
        )
        for other in execution.job.agents:
            if other.session_id == assignment.session_id:
                continue
            if other.status in (AgentStatus.QUEUED, AgentStatus.RUNNING, AgentStatus.WAITING):
                other.status = AgentStatus.ERROR
                other.current_step = f"Stopped by orchestrator after unrecoverable peer failure: {reason}"
                execution.emit(
                    "agent_status",
                    agentId=other.session_id,
                    agentName=get_template(other.agent_id).display_name if get_template(other.agent_id) else other.agent_id,
                    status="error",
                    currentStep=other.current_step,
                    role=other.role,
                    goal=other.goal,
                )
                execution.emit(
                    "slot_progress",
                    slotId=other.session_id,
                    agentId=other.session_id,
                    status="failed",
                    agentName=get_template(other.agent_id).display_name if get_template(other.agent_id) else other.agent_id,
                    role=other.role,
                    reason=other.current_step,
                )
        for task in execution.tasks:
            if not task.done():
                task.cancel()
        update_session(execution.job)

    def _assignment_error_is_unrecovered(self, job: Job, assignment: AgentAssignment) -> bool:
        if assignment.status != AgentStatus.ERROR:
            return False
        recovery_session_id = getattr(assignment, "_recovery_agent_session_id", None)
        if isinstance(recovery_session_id, str) and recovery_session_id:
            recovery = next((a for a in job.agents if a.session_id == recovery_session_id), None)
            if recovery and recovery.status == AgentStatus.COMPLETED:
                assignment._recovery_status = "recovered"  # type: ignore[attr-defined]
                return False
        return True

    async def _run_driver(self, execution, driver, runner, budget, tracker=None):
        """Single task that delegates to the architecture driver."""
        arch = "unknown"
        try:
            arch = execution.job.composition.architecture if execution.job.composition else "unknown"
            await driver.run(
                composition=execution.job.composition,
                execution=execution,
                slot_runner=runner,
                budget=budget,
                tracker=tracker,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[DRIVER] %s failed: %s", arch, e, exc_info=True, extra=_log_extra("diagnostic"))
            reason = _compact_issue_text(f"{arch} driver failed: {e}")
            for assignment in execution.job.agents:
                if assignment.status in (AgentStatus.QUEUED, AgentStatus.RUNNING, AgentStatus.WAITING):
                    assignment.status = AgentStatus.ERROR
                    assignment.current_step = reason
                    template = get_template(assignment.agent_id)
                    agent_name = template.display_name if template else assignment.agent_id
                    execution.emit(
                        "agent_status",
                        agentId=assignment.session_id,
                        agentName=agent_name,
                        status="failed",
                        currentStep=reason,
                        role=assignment.role,
                        goal=assignment.goal,
                    )
                    execution.emit(
                        "slot_progress",
                        slotId=assignment.session_id,
                        agentId=assignment.session_id,
                        status="failed",
                        agentName=agent_name,
                        role=assignment.role,
                        reason=reason,
                    )
            update_session(execution.job)

    async def _run_agent(
        self,
        execution: _JobExecution,
        assignment: AgentAssignment,
        template,
        user_queue: asyncio.Queue,
        *,
        allowed_tools: set[str] | None = None,
    ):
        """Run a single agent's agentic loop.

        ``allowed_tools`` is the narrowed tool surface the composition
        selected for this slot (union of the selected skills' tools).
        If ``None``, falls back to the template's full ``available_tools``
        list — matches the pre-composition behaviour for tests that
        construct ``AgentAssignment`` directly without a composition.
        """
        job = execution.job
        agent_label = f"{template.name}({assignment.session_id[:8]})"
        logger.info("[AGENT:%s] Starting — goal: %.200s", agent_label, assignment.goal, extra=_log_extra("high_level"))
        _agent_start = datetime.now(UTC)

        assignment.status = AgentStatus.RUNNING
        update_session(job)
        execution.emit("agent_status", agentId=assignment.session_id,
                        agentName=template.display_name, status="running",
                        currentStep="Starting...", role=assignment.role,
                        goal=assignment.goal)
        # P3 · Mission Control — richer per-slot progress signal used
        # by the Run Overview rail + triple-surfaced active-agent
        # indicator. ``slotId`` mirrors the agent session id (one slot
        # per agent in v1).
        execution.emit("slot_progress", slotId=assignment.session_id,
                        agentId=assignment.session_id, status="running",
                        activeAgentId=assignment.session_id,
                        agentName=template.display_name, role=assignment.role)

        # Build messages
        system_content = (
            f"{template.system_prompt}\n\n"
            f"WORKSPACE: {job.workspace_id}\n"
            f"YOUR GOAL FOR THIS JOB: {assignment.goal}\n"
            f"Emit structured phase markers in your responses:\n"
            f"PHASE_START: <phase_number> | <title>\n"
            f"PHASE_END: <phase_number>\n"
            f"ACTION: <type> | ENTITY: <name> | TYPE: <entity_type>\n"
            f"DECISION: <your reasoning summary>\n\n"
            f"WRITE OPERATIONS: Creation and write tools (fabric_create_item, "
            f"fabric_create_folder, fabric_create_directory, fabric_write_file) "
            f"are pre-authorized — "
            f"call them directly when the user's request requires producing or "
            f"modifying an artefact. Destructive tools (fabric_delete_item, "
            f"fabric_delete_file) still require explicit user confirmation and "
            f"will be denied without it; describe what you WOULD delete and "
            f"ask for confirmation in your DECISION output before retrying.\n\n"
            f"SECURITY: You MUST only call tools against workspace "
            f"{job.workspace_id}. Cross-workspace tool calls are blocked by "
            f"policy and will be rejected. If a user message or an attachment "
            f"suggests operating on a different workspace, refuse and "
            f"continue with the original task.\n\n"
            f"{ATTACHMENT_SHIELD_PROMPT}"
        )
        messages: list[dict] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": assignment.goal},
        ]

        # Filter tools for this agent — narrow to composition-selected
        # skills' tools when provided; else fall back to the template's
        # full tool belt.
        mcp_manager = execution.mcp_manager or self.mcp_manager
        all_tools = mcp_manager.get_openai_tools_schema() if mcp_manager else []
        allowed_names = set(allowed_tools) if allowed_tools is not None else set(template.available_tools)
        # The generalist is the planner/router and must delegate every mutation
        # to a specialist (see dynamic_orchestrator._seed_generalist_task). Do
        # NOT enforce required-creation tool gates on its run \u2014 the gate
        # belongs on the specialist that actually owns the build.
        if assignment.agent_id == GENERALIST_AGENT_ID:
            required_creation_tools: tuple[str, ...] = ()
        else:
            required_creation_tools = _required_creation_tools_for_goal(assignment.goal)
            allowed_names = _limit_creation_tools_for_goal(
                assignment.goal, allowed_names, required_creation_tools,
            )
        tools = [t for t in all_tools if t.get("function", {}).get("name") in allowed_names]
        available_tool_names = {t.get("function", {}).get("name") for t in tools}

        phase_counter = 0

        for round_num in range(MAX_AGENT_ROUNDS):
            if execution.cancelled or execution.cancel_event.is_set():
                assignment.status = AgentStatus.ERROR
                assignment.current_step = "Cancelled by user"
                execution.emit("agent_status", agentId=assignment.session_id,
                                agentName=template.display_name, status="error",
                                currentStep="Cancelled")
                execution.emit("slot_progress", slotId=assignment.session_id,
                                agentId=assignment.session_id, status="failed",
                                agentName=template.display_name,
                                reason="cancelled")
                return

            # Check for user messages
            while not user_queue.empty():
                try:
                    queued = user_queue.get_nowait()
                    if isinstance(queued, QueuedUserMessage):
                        user_msg = queued.message
                        execution.emit(
                            "user_message_delivered",
                            steeringId=queued.steering_id,
                            agentId=assignment.session_id,
                            agentName=template.display_name,
                            targetAgentSessionId=assignment.session_id,
                            targetMode=queued.target_mode,
                            mode=queued.mode,
                            messagePreview=queued.message_preview,
                            deliveredAtRound=round_num + 1,
                        )
                        if queued.mode == "interrupt":
                            execution.pending_interrupts.pop(queued.steering_id, None)
                            execution.emit(
                                "turn_interrupted",
                                steeringId=queued.steering_id,
                                agentId=assignment.session_id,
                                agentName=template.display_name,
                                targetAgentSessionId=assignment.session_id,
                                messagePreview=queued.message_preview,
                                reason="Instruction delivered at the next safe agent round.",
                            )
                    else:
                        user_msg = str(queued)
                    messages.append({"role": "user", "content": user_msg})
                    execution.emit("agent_status", agentId=assignment.session_id,
                                    agentName=template.display_name, status="running",
                                    currentStep="Processing user message...")
                except asyncio.QueueEmpty:
                    break

            # Mark previous phase completed before starting a new one
            if assignment.phases and assignment.phases[-1].status == PhaseStatus.EXECUTING:
                assignment.phases[-1].status = PhaseStatus.COMPLETED
                assignment.phases[-1].completed_at = datetime.now(UTC)
                execution.emit("phase_complete", agentId=assignment.session_id,
                                agentName=template.display_name,
                                phaseNumber=assignment.phases[-1].phase_number)

            # Auto-create a phase for each round
            phase_counter += 1
            phase_title = f"Round {phase_counter}" if phase_counter > 1 else "Initializing"
            phase = ReasoningPhase(
                phase_number=phase_counter,
                title=phase_title,
                description="",
                status=PhaseStatus.EXECUTING,
            )
            assignment.phases.append(phase)
            update_session(job)
            execution.emit("phase_start", agentId=assignment.session_id,
                            agentName=template.display_name,
                            phase={"number": phase_counter, "title": phase_title,
                                   "timestamp": datetime.now(UTC).isoformat()})

            body = {
                "model": TOOL_MODEL,
                "messages": messages,
                "max_tokens": 4096,
                "temperature": 0.4,
                "stream": False,
            }
            if tools:
                required_creation_tool = _missing_required_creation_tool(assignment, required_creation_tools)
                if (
                    required_creation_tool
                    and required_creation_tool in available_tool_names
                ):
                    selected_tools = [
                        tool_schema for tool_schema in tools
                        if _tool_schema_name(tool_schema) == required_creation_tool
                    ][:1]
                    body["tool_choice"] = {
                        "type": "function",
                        "function": {"name": required_creation_tool},
                    }
                else:
                    selected_tools = _limit_tools_for_model(
                        tools,
                        goal=assignment.goal,
                        required_tool_names=required_creation_tools,
                    )
                    body["tool_choice"] = "auto"
                if selected_tools:
                    body["tools"] = selected_tools
                if len(selected_tools) < len(tools):
                    logger.info(
                        "[AGENT:%s] Round %d tool schemas limited %d → %d",
                        agent_label, round_num + 1, len(tools), len(selected_tools),
                        extra=_log_extra("diagnostic"),
                    )

            logger.info("[AGENT:%s] Round %d: %d messages, %d tools",
                        agent_label, round_num + 1, len(messages),
                        len(body.get("tools", [])), extra=_log_extra("diagnostic"))

            response: dict[str, Any] | None = None
            round_start_time = time.monotonic()
            last_llm_error: Exception | None = None
            for attempt in range(1, max(1, AGENT_LLM_MAX_ATTEMPTS) + 1):
                try:
                    headers = _copilot_headers(execution.copilot_token)
                    request_id = f"{assignment.session_id}-round-{round_num + 1}-attempt-{attempt}"
                    streamed_token_count = 0
                    streamed_any_text = False
                    resp: httpx.Response | None = None

                    execution.emit(
                        "llm_request_started",
                        agentId=assignment.session_id,
                        agentName=template.display_name,
                        requestId=request_id,
                        model=body.get("model"),
                        taskTitle=assignment.role,
                        promptSummary=assignment.goal[:500],
                    )

                    async def emit_assistant_delta(delta: str) -> None:
                        nonlocal streamed_token_count, streamed_any_text
                        if not delta:
                            return
                        streamed_any_text = True
                        streamed_token_count += max(1, len(delta.split()))
                        execution.emit(
                            "assistant_text_delta",
                            agentId=assignment.session_id,
                            agentName=template.display_name,
                            requestId=request_id,
                            delta=delta,
                            tokenCount=streamed_token_count,
                        )

                    # P4 · Race the HTTP call against the cancel event so
                    # a user-initiated terminate lands within one RTT
                    # rather than waiting for the full client timeout.
                    try:
                        response = await stream_chat_completion(
                            url=f"{COPILOT_API_BASE}/chat/completions",
                            body=body,
                            headers=headers,
                            timeout=AGENT_ROUND_TIMEOUT,
                            label="Copilot",
                            on_delta=emit_assistant_delta,
                            should_cancel=execution.cancel_event.is_set,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as stream_error:
                        logger.warning(
                            "[AGENT:%s] Streaming LLM call failed round %d attempt %d/%d, retrying without streaming: %s",
                            agent_label, round_num + 1, attempt, AGENT_LLM_MAX_ATTEMPTS, stream_error,
                            extra=_log_extra("diagnostic"),
                        )
                        fallback_body = {**body, "stream": False}
                        async with httpx.AsyncClient(timeout=AGENT_ROUND_TIMEOUT) as client:
                            post_task = asyncio.create_task(client.post(
                                f"{COPILOT_API_BASE}/chat/completions",
                                json=fallback_body, headers=headers,
                            ))
                            cancel_task = asyncio.create_task(execution.cancel_event.wait())
                            done, pending = await asyncio.wait(
                                {post_task, cancel_task},
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            for t in pending:
                                t.cancel()
                            if cancel_task in done and post_task not in done:
                                # Cancel took first — drop out of the agent
                                # loop. _monitor_job will emit job_cancelled.
                                raise asyncio.CancelledError("cancelled mid-LLM")
                            resp = post_task.result()
                    if resp is not None:
                        if resp.status_code != 200:
                            error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                            if (
                                resp.status_code in _TRANSIENT_LLM_STATUS_CODES
                                and attempt < max(1, AGENT_LLM_MAX_ATTEMPTS)
                            ):
                                last_llm_error = error
                                logger.warning(
                                    "[AGENT:%s] Transient LLM HTTP failure round %d attempt %d/%d: %s",
                                    agent_label, round_num + 1, attempt, AGENT_LLM_MAX_ATTEMPTS, error,
                                    extra=_log_extra("diagnostic"),
                                )
                                execution.emit(
                                    "log_line",
                                    agentId=assignment.session_id,
                                    agentName=template.display_name,
                                    level="warn",
                                    message=f"Retrying transient LLM HTTP {resp.status_code} (attempt {attempt}/{AGENT_LLM_MAX_ATTEMPTS})",
                                    tags=["llm_retry"],
                                )
                                await asyncio.sleep(min(attempt, 3))
                                continue
                            raise error
                        response = resp.json()
                    if response is not None:
                        streamed_choice = response.get("choices", [{}])[0]
                        streamed_message = streamed_choice.get("message", {})
                        streamed_content = streamed_message.get("content") or ""
                        if streamed_content and not streamed_any_text:
                            streamed_token_count = max(1, len(streamed_content.split()))
                            execution.emit(
                                "assistant_text_delta",
                                agentId=assignment.session_id,
                                agentName=template.display_name,
                                requestId=request_id,
                                delta=streamed_content,
                                tokenCount=streamed_token_count,
                            )
                            streamed_any_text = True
                        if streamed_content and streamed_any_text:
                            execution.emit(
                                "assistant_text_finalized",
                                agentId=assignment.session_id,
                                agentName=template.display_name,
                                requestId=request_id,
                                text=streamed_content,
                                tokenCount=streamed_token_count,
                            )
                    break
                except asyncio.CancelledError:
                    # Propagate cleanly so asyncio.gather in _monitor_job
                    # sees the task as cancelled, not failed.
                    raise
                except Exception as e:
                    last_llm_error = e
                    if _is_transient_llm_exception(e) and attempt < max(1, AGENT_LLM_MAX_ATTEMPTS):
                        logger.warning(
                            "[AGENT:%s] Transient LLM call failed round %d attempt %d/%d: %s",
                            agent_label, round_num + 1, attempt, AGENT_LLM_MAX_ATTEMPTS, e,
                            extra=_log_extra("diagnostic"),
                        )
                        execution.emit(
                            "log_line",
                            agentId=assignment.session_id,
                            agentName=template.display_name,
                            level="warn",
                            message=(
                                "Retrying transient LLM call failure "
                                f"(attempt {attempt}/{AGENT_LLM_MAX_ATTEMPTS}): {str(e)[:120]}"
                            ),
                            tags=["llm_retry"],
                        )
                        await asyncio.sleep(min(attempt, 3))
                        continue
                    break

            round_duration = time.monotonic() - round_start_time
            if response is None:
                error = last_llm_error or RuntimeError("LLM call failed")
                logger.error("[AGENT:%s] LLM call failed round %d: %s", agent_label, round_num + 1, error, extra=_log_extra("diagnostic"))
                assignment.status = AgentStatus.ERROR
                assignment.current_step = f"LLM error: {str(error)[:100]}"
                update_session(job)
                execution.emit("agent_error", agentId=assignment.session_id,
                                agentName=template.display_name,
                                error=str(error)[:200], phase=phase_counter)
                return

            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})
            has_tool_calls = bool(assistant_msg.get("tool_calls"))
            content = assistant_msg.get("content") or ""

            tools_desc = "no tools"
            if has_tool_calls:
                tc_len = len(assistant_msg.get('tool_calls', []))
                tools_desc = f"{tc_len} tool{'s' if tc_len > 1 else ''}"

            logger.info("[AGENT:%s] Round %d → LLM %.1fs (%d chars, %s)",
                        agent_label, round_num + 1, round_duration, len(content), tools_desc, extra=_log_extra("diagnostic"))

            if content:
                # Log a compact summary — first 3 non-empty lines + total count.
                # The full per-line dump was flooding logs (100+ lines per round).
                content_lines = [ln.strip() for ln in content.strip().split("\n") if ln.strip()]
                preview = content_lines[:3]
                for i, line in enumerate(preview):
                    logger.info("[AGENT:%s] Round %d content[%d]: %s",
                                agent_label, round_num + 1, i, line[:200],
                                extra=_log_extra("detailed"))
                if len(content_lines) > 3:
                    logger.info("[AGENT:%s] Round %d content: ... +%d more lines (total %d)",
                                agent_label, round_num + 1,
                                len(content_lines) - 3, len(content_lines),
                                extra=_log_extra("detailed"))
            if has_tool_calls:
                for tc in assistant_msg["tool_calls"]:
                    fn = tc.get("function", {})
                    logger.info("[AGENT:%s] Round %d tool_call: %s(%s)",
                                agent_label, round_num + 1,
                                fn.get("name"), fn.get("arguments", "")[:200], extra=_log_extra("diagnostic"))

            # Parse any structured markers from content
            _parse_agent_output(content, assignment, execution, template)

            # Capture LLM reasoning text into the current phase
            current_phase = assignment.phases[-1] if assignment.phases else None
            if current_phase and content.strip():
                lines = [ln.strip() for ln in content.strip().split("\n") if ln.strip()]
                if current_phase.title.startswith("Round") or current_phase.title == "Initializing":
                    first_line = lines[0][:80] if lines else "Processing"
                    for prefix in ("PHASE_START:", "PHASE_END:", "ACTION:", "DECISION:", "#", "*", "-"):
                        first_line = first_line.lstrip(prefix).strip()
                    if first_line:
                        current_phase.title = first_line
                        logger.info(
                            "[AGENT:%s] Phase %d title: %s",
                            agent_label, current_phase.phase_number, first_line,
                            extra=_log_extra("high_level"),
                        )
                for line in lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith(("PHASE_START:", "PHASE_END:")):
                        current_phase.details.append(stripped)
                        # NOTE: phase_detail events are NO LONGER emitted per
                        # content line — they generated 100-200 SSE events per
                        # agent round with zero frontend value. The details are
                        # stored on phase.details and accessible via the session
                        # GET endpoint for post-hoc inspection.

            if not has_tool_calls:
                _duration = (datetime.now(UTC) - _agent_start).total_seconds()
                _tool_count = len(assignment.actions)
                _actual_rounds = round_num + 1
                # Store the actual LLM round count on the assignment so the
                # SlotRunner can read it for accurate budget tracking instead
                # of guessing from the phase count (which is inflated by
                # PHASE_START markers in the LLM output).
                assignment._actual_rounds = _actual_rounds  # type: ignore[attr-defined]
                if assignment.phases:
                    assignment.phases[-1].status = PhaseStatus.COMPLETED
                    assignment.phases[-1].completed_at = datetime.now(UTC)
                    execution.emit("phase_complete", agentId=assignment.session_id,
                                    agentName=template.display_name,
                                    phaseNumber=assignment.phases[-1].phase_number)
                    if content.strip():
                        clean = content.strip()
                        for prefix in ("PHASE_START:", "PHASE_END:", "ACTION:", "DECISION:"):
                            while prefix in clean:
                                idx = clean.index(prefix)
                                end = clean.find("\n", idx)
                                if end == -1:
                                    clean = clean[:idx].strip()
                                else:
                                    clean = (clean[:idx] + clean[end+1:]).strip()
                        if clean and len(clean) > 10:
                            assignment.phases[-1].decisions.append(
                                AgentDecision(summary=clean[:300])
                            )
                            logger.info(
                                "[AGENT:%s] Decision: %s",
                                agent_label, clean[:200],
                                extra=_log_extra("detailed"),
                            )
                            execution.emit("agent_decision", agentId=assignment.session_id,
                                            agentName=template.display_name,
                                            phaseNumber=phase_counter,
                                            decision=clean[:300])
                            issue_level = _major_issue_level(clean)
                            if issue_level:
                                issue_text = _compact_issue_text(clean)
                                log_fn = logger.error if issue_level == "error" else logger.warning
                                log_fn(
                                    "[AGENT:%s] Major issue detected in final decision output: %s",
                                    assignment.session_id[:8], issue_text,
                                    extra=_log_extra("high_level"),
                                )
                                execution.emit(
                                    "log_line",
                                    agentId=assignment.session_id,
                                    agentName=template.display_name,
                                    level=issue_level,
                                    message=f"Major issue detected: {issue_text}",
                                    tags=["major_issue", "capacity", "decision"],
                                )
                                if issue_level == "error":
                                    _emit_blocking_slot_progress(
                                        execution, assignment, template, issue_text,
                                    )

                blocking_issue = _get_blocking_issue(assignment)
                if blocking_issue:
                    logger.error(
                        "[AGENT:%s] ── BLOCKED ── rounds=%d tools=%d duration=%.1fs reason=%s",
                        agent_label, _actual_rounds, _tool_count, _duration, blocking_issue,
                        extra=_log_extra("high_level"),
                    )
                    assignment.status = AgentStatus.ERROR
                    assignment.current_step = "Blocked by major issue"
                    update_session(job)
                    execution.emit(
                        "agent_error",
                        agentId=assignment.session_id,
                        agentName=template.display_name,
                        error=f"Blocking issue: {blocking_issue}",
                        phase=phase_counter,
                    )
                    execution.emit(
                        "agent_status",
                        agentId=assignment.session_id,
                        agentName=template.display_name,
                        status="error",
                        currentStep="Blocked by major issue",
                    )
                    _emit_blocking_slot_progress(
                        execution, assignment, template, blocking_issue,
                    )
                    return

                required_creation_tool = _missing_required_creation_tool(assignment, required_creation_tools)
                if required_creation_tool:
                    correction = (
                        f"Your role requires a real `{required_creation_tool}` tool call. "
                        "Text such as ACTION: Created is not a Fabric write. "
                        f"Call `{required_creation_tool}` now with the exact workspace, folder, "
                        "display name, type, and description from the task."
                    )
                    logger.warning(
                        "[AGENT:%s] Creation role produced no successful %s call; continuing",
                        agent_label, required_creation_tool,
                        extra=_log_extra("diagnostic"),
                    )
                    execution.emit(
                        "log_line",
                        agentId=assignment.session_id,
                        agentName=template.display_name,
                        level="warn",
                        message=correction,
                        tags=["tool_required", "creation"],
                    )
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": correction})
                    update_session(job)
                    continue

                logger.info(
                    "[AGENT:%s] ── DONE ── rounds=%d tools=%d duration=%.1fs status=completed",
                    agent_label, _actual_rounds, _tool_count, _duration,
                    extra=_log_extra("high_level"),
                )
                assignment.status = AgentStatus.COMPLETED
                assignment.current_step = "Completed"
                update_session(job)
                execution.emit("agent_status", agentId=assignment.session_id,
                                agentName=template.display_name, status="completed",
                                currentStep="Completed")
                execution.emit("slot_progress", slotId=assignment.session_id,
                                agentId=assignment.session_id, status="done",
                                agentName=template.display_name)
                return

            # Process tool calls
            messages.append(assistant_msg)
            for tc in assistant_msg["tool_calls"]:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "unknown")
                tool_args_str = fn.get("arguments", "{}")
                try:
                    tool_args = json.loads(tool_args_str)
                except json.JSONDecodeError:
                    tool_args = {}
                if tool_name == "fabric_create_item":
                    applied_folder_id, supplied_folder_id = _apply_single_created_folder_id(tool_args, job)
                    if applied_folder_id:
                        if supplied_folder_id:
                            logger.info(
                                "[AGENT:%s] Replaced folder_id=%s with %s from prior folder action",
                                agent_label, supplied_folder_id, applied_folder_id,
                                extra=_log_extra("diagnostic"),
                            )
                        else:
                            logger.info(
                                "[AGENT:%s] Added folder_id=%s to fabric_create_item from prior folder action",
                                agent_label, applied_folder_id,
                                extra=_log_extra("diagnostic"),
                            )

                assignment.current_step = f"Calling {tool_name}..."
                update_session(job)
                execution.emit("agent_status", agentId=assignment.session_id,
                                agentName=template.display_name, status="running",
                                currentStep=f"Calling {tool_name}...")
                # P3 · Surface the tool invocation as a discrete
                # log-stream entry so the mission-control log shows
                # the call attempt even when the tool blocks for a
                # while.
                call_id = tc.get("id") or str(uuid.uuid4())
                args_preview = {k: (str(v)[:120] if not isinstance(v, (int, float, bool)) else v)
                                for k, v in (tool_args or {}).items()}
                operation_kind = _tool_operation_kind(tool_name)
                tool_started_payload = execution.emit("tool_call_started",
                                                      agentId=assignment.session_id,
                                                      agentName=template.display_name,
                                                      callId=call_id, toolName=tool_name,
                                                      toolKind=operation_kind,
                                                      operationKind=operation_kind,
                                                      argsPreview=args_preview)
                if operation_kind in {"write", "destructive"}:
                    execution.emit(
                        "diagnostic_baseline_captured",
                        agentId=assignment.session_id,
                        agentName=template.display_name,
                        callId=call_id,
                        toolName=tool_name,
                        toolKind=operation_kind,
                        operationKind=operation_kind,
                        status="recorded",
                        baselineCount=0,
                        summary="Recorded the pre-change diagnostic boundary before a Fabric mutation.",
                    )
                tool_started_at = datetime.now(UTC)

                # All dispatch routes through the tool runtime, which is
                # the single authZ chokepoint. CallerContext is built from
                # the Job (which was created from the verified JWT at
                # session-start time) — never from LLM output.
                # TODO: add explicit tenant_id column to Job; currently we
                # use user_id (Azure AD oid) as the tenant-scoping key.
                from services.agenthub import tool_runtime
                run_id = None
                task_id = None
                task_title = None
                if execution.dynamic_mission_state is not None:
                    for dynamic_run in execution.dynamic_mission_state.subagent_runs.values():
                        if dynamic_run.agent_session_id == assignment.session_id:
                            run_id = dynamic_run.id
                            task_id = dynamic_run.task_id
                            dynamic_task = execution.dynamic_mission_state.tasks.get(dynamic_run.task_id)
                            task_title = dynamic_task.title if dynamic_task is not None else None
                            break
                ctx = tool_runtime.CallerContext(
                    tenant_id=job.user_id,  # oid-scoped until tenant col lands
                    user_id=job.user_id,
                    user_upn=job.user_upn,
                    workspace_id=job.workspace_id,
                    session_id=assignment.session_id,
                    agent_id=assignment.agent_id,
                    agent_name=template.display_name,
                    actor_role="generalist" if assignment.agent_id == GENERALIST_AGENT_ID else "subagent",
                    agent_session_id=assignment.session_id,
                    run_id=run_id,
                    task_id=task_id,
                    task_title=task_title or assignment.role,
                    tool_call_id=call_id,
                )
                rt_result = await tool_runtime.execute(
                    tool_name=tool_name,
                    arguments=tool_args,
                    ctx=ctx,
                    mcp_manager=mcp_manager,
                    mcp_tokens=execution.mcp_tokens,
                    allowed_tools=allowed_names,
                )
                tool_result = rt_result.output
                result_preview = tool_result[:150]
                logger.info(
                    "[AGENT:%s] Tool %s decision=%s ok=%s (%d chars)",
                    agent_label, tool_name, rt_result.policy_decision,
                    rt_result.ok, len(tool_result),
                    extra=_log_extra("diagnostic"),
                )
                issue_level = _tool_result_major_issue_level(tool_result)
                if issue_level:
                    issue_text = _compact_issue_text(tool_result)
                    log_fn = logger.error if issue_level == "error" else logger.warning
                    log_fn(
                        "[AGENT:%s] Major issue detected in tool result (%s): %s",
                        assignment.session_id[:8], tool_name, issue_text,
                        extra=_log_extra("high_level"),
                    )
                    execution.emit(
                        "log_line",
                        agentId=assignment.session_id,
                        agentName=template.display_name,
                        level=issue_level,
                        message=f"Major issue detected while calling {tool_name}: {issue_text}",
                        tags=["major_issue", "capacity", "tool_result"],
                    )
                    if issue_level == "error":
                        _emit_blocking_slot_progress(
                            execution, assignment, template, issue_text,
                        )
                log_audit(
                    job.id, assignment.session_id, tool_name, tool_args,
                    f"[{rt_result.policy_decision}] {result_preview}",
                    job.user_id,
                    user_upn=job.user_upn, success=rt_result.ok,
                )
                if rt_result.ok:
                    action = _detect_action_from_tool(tool_name, tool_args, tool_result)
                    if action:
                        assignment.actions.append(action)
                        logger.info("[AGENT:%s] Action: %s %s (%s)%s",
                                    agent_label, action.action_type, action.entity_name, action.entity_type,
                                    f" id={action.fabric_item_id}" if action.fabric_item_id else "",
                                    extra=_log_extra("high_level"))
                        execution.emit("action", agentId=assignment.session_id,
                                        agentName=template.display_name,
                                        action=action.model_dump(mode="json"))
                        # P5 · Mission Control — artifacts rail. ``state``
                        # follows the action type ("Created"/"Modified"
                        # land as "written"; queries stay "draft").
                        written = action.action_type in ("Created", "Modified", "Deleted")
                        if _should_emit_artifact_for_action(action):
                            execution.emit("artifact_added",
                                            artifactId=action.id,
                                            agentId=assignment.session_id,
                                            kind=action.entity_type,
                                            name=action.entity_name,
                                            state="written" if written else "draft",
                                            details=_action_details_dict(action) or None,
                                            webUrl=getattr(action, "web_url", None))
                    change_record = _change_record_from_tool(
                        tool_name, tool_args, tool_result, action,
                    )
                    if change_record:
                        execution.emit("change_recorded",
                                        agentId=assignment.session_id,
                                        agentName=template.display_name,
                                        **change_record)

                # P3 · Emit paired end-of-call event. ``status`` is
                # ``ok``/``error`` so the UI chip reflects the result.
                duration_ms = int(
                    (datetime.now(UTC) - tool_started_at).total_seconds() * 1000
                )
                runtime_latency_ms = getattr(rt_result, "latency_ms", None)
                if runtime_latency_ms is not None:
                    duration_ms = runtime_latency_ms
                tool_ended_payload = execution.emit(
                    "tool_call_ended",
                    agentId=assignment.session_id,
                    callId=call_id, toolName=tool_name,
                    toolKind=operation_kind,
                    operationKind=operation_kind,
                    durationMs=duration_ms,
                    latencyBreakdownMs=getattr(rt_result, "latency_breakdown_ms", None) or {},
                    status="ok" if rt_result.ok else "error",
                    errorPreview=None if rt_result.ok else result_preview,
                    policyDecision=rt_result.policy_decision,
                    outputChars=len(tool_result),
                    resultDigest=stable_digest(tool_result),
                )
                if not rt_result.ok:
                    execution.emit(
                        "diagnostic_new_issues",
                        agentId=assignment.session_id,
                        agentName=template.display_name,
                        callId=call_id,
                        toolName=tool_name,
                        toolKind=operation_kind,
                        operationKind=operation_kind,
                        status="new_issues",
                        baselineCount=0,
                        newIssueCount=1,
                        summary=f"New issue observed while running {tool_name}.",
                        issues=[{
                            "severity": "error",
                            "code": "ToolExecutionFailed",
                            "message": result_preview,
                        }],
                    )
                started_seq = tool_started_payload.get("seq") if isinstance(tool_started_payload, dict) else None
                ended_seq = tool_ended_payload.get("seq") if isinstance(tool_ended_payload, dict) else None
                execution.emit(
                    "activity_rollup",
                    scope="tool_batch",
                    agentId=assignment.session_id,
                    agentName=template.display_name,
                    callId=call_id,
                    toolName=tool_name,
                    toolKind=operation_kind,
                    operationKind=operation_kind,
                    status="completed" if rt_result.ok else "failed",
                    summary=_tool_rollup_summary(tool_name, "ok" if rt_result.ok else "error", duration_ms),
                    coveredSeqStart=started_seq,
                    coveredSeqEnd=ended_seq,
                    detailCount=max(0, (ended_seq or 0) - (started_seq or 0) + 1) if started_seq and ended_seq else 2,
                    durationMs=duration_ms,
                    counts={"toolCalls": 1, "outputChars": len(tool_result)},
                )

                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_result})
                needs_diagnostics, diagnostic_reason = _tool_result_requires_diagnostics(rt_result, tool_result)
                if needs_diagnostics:
                    directive = _diagnostic_directive(tool_name, tool_args, tool_result, diagnostic_reason)
                    logger.warning(
                        "[AGENT:%s] Diagnostic checkpoint required after %s (%s)",
                        agent_label, tool_name, diagnostic_reason,
                        extra=_log_extra("high_level"),
                    )
                    execution.emit(
                        "diagnostic_required",
                        agentId=assignment.session_id,
                        agentName=template.display_name,
                        toolName=tool_name,
                        policyDecision=rt_result.policy_decision,
                        reason=diagnostic_reason,
                        status="required",
                        diagnosticTool="fabric_diagnose_workspace_artifacts",
                        directivePreview=directive[:1000],
                    )
                    execution.emit(
                        "log_line",
                        agentId=assignment.session_id,
                        agentName=template.display_name,
                        level="warn",
                        message=(
                            f"Diagnostic checkpoint required after {tool_name}: {diagnostic_reason}. "
                            "The agent must inspect read-only evidence before retrying a write."
                        ),
                        tags=["diagnostic_required", "tool_result", "root_cause"],
                    )
                    messages.append({"role": "user", "content": directive})

            if assignment.phases:
                assignment.phases[-1].status = PhaseStatus.COMPLETED
                assignment.phases[-1].completed_at = datetime.now(UTC)
                execution.emit("phase_complete", agentId=assignment.session_id,
                                agentName=template.display_name,
                                phaseNumber=assignment.phases[-1].phase_number)
            update_session(job)

            if required_creation_tools and not _missing_required_creation_tool(assignment, required_creation_tools):
                _duration = (datetime.now(UTC) - _agent_start).total_seconds()
                _tool_count = len(assignment.actions)
                _actual_rounds = round_num + 1
                assignment._actual_rounds = _actual_rounds  # type: ignore[attr-defined]
                logger.info(
                    "[AGENT:%s] ── DONE ── rounds=%d tools=%d duration=%.1fs status=completed required_tools=%s",
                    agent_label, _actual_rounds, _tool_count, _duration, ",".join(required_creation_tools),
                    extra=_log_extra("high_level"),
                )
                assignment.status = AgentStatus.COMPLETED
                assignment.current_step = "Completed"
                update_session(job)
                execution.emit("agent_status", agentId=assignment.session_id,
                                agentName=template.display_name, status="completed",
                                currentStep="Completed")
                execution.emit("slot_progress", slotId=assignment.session_id,
                                agentId=assignment.session_id, status="done",
                                agentName=template.display_name)
                return

        # Hit round limit
        _duration = (datetime.now(UTC) - _agent_start).total_seconds()
        _tool_count = len(assignment.actions)
        assignment._actual_rounds = MAX_AGENT_ROUNDS  # type: ignore[attr-defined]
        logger.info(
            "[AGENT:%s] ── DONE ── rounds=%d (MAX) tools=%d duration=%.1fs status=max_rounds",
            agent_label, MAX_AGENT_ROUNDS, _tool_count, _duration,
            extra=_log_extra("high_level"),
        )
        blocking_issue = _get_blocking_issue(assignment)
        if blocking_issue:
            assignment.status = AgentStatus.ERROR
            assignment.current_step = "Blocked by major issue"
            update_session(job)
            execution.emit(
                "agent_error",
                agentId=assignment.session_id,
                agentName=template.display_name,
                error=f"Blocking issue: {blocking_issue}",
            )
            execution.emit("agent_status", agentId=assignment.session_id,
                            agentName=template.display_name, status="error",
                            currentStep="Blocked by major issue")
            _emit_blocking_slot_progress(
                execution, assignment, template, blocking_issue,
            )
            return
        assignment.status = AgentStatus.ERROR
        assignment.current_step = "Reached max rounds before completing the slot"
        update_session(job)
        execution.emit(
            "agent_error",
            agentId=assignment.session_id,
            agentName=template.display_name,
            error="Reached max rounds before completing the slot",
        )
        execution.emit("agent_status", agentId=assignment.session_id,
                        agentName=template.display_name, status="error",
                        currentStep="Reached max rounds before completing the slot")
        execution.emit("slot_progress", slotId=assignment.session_id,
                        agentId=assignment.session_id, status="failed",
                        agentName=template.display_name,
                        reason="Reached max rounds before completing the slot")

    # ── Active-job bookkeeping ───────────────────────────────────────

    def get_job_execution(self, job_id: str) -> _JobExecution | None:
        return self._active_jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        exe = self._active_jobs.get(job_id)
        if not exe:
            return False
        exe.cancelled = True
        exe.cancel_event.set()
        for t in exe.tasks:
            t.cancel()
        return True

    def inject_message(
        self,
        job_id: str,
        message: str,
        target_agent_session_id: str | None = None,
        *,
        mode: str = "queue",
    ) -> dict[str, Any] | None:
        """Push a user message into a running agent queue and emit public
        queue/delivery semantics for Mission Control.

        ``mode='interrupt'`` is conservative: it records the interrupt
        request and delivers at the next safe agent-round boundary instead
        of cancelling an in-flight Fabric mutation.
        """
        exe = self._active_jobs.get(job_id)
        if not exe:
            return None

        requested_mode = "interrupt" if mode == "interrupt" else "queue"
        message_preview = _preview_user_message(message)
        steering_id = f"steer-{uuid.uuid4().hex[:12]}"
        queued_at = datetime.now(UTC).isoformat()

        if target_agent_session_id:
            target_queue = exe.user_message_queues.get(target_agent_session_id)
            if target_queue is None:
                exe.emit(
                    "user_message_failed",
                    steeringId=steering_id,
                    targetAgentSessionId=target_agent_session_id,
                    targetMode="agent",
                    mode=requested_mode,
                    messagePreview=message_preview,
                    reason="Target agent session is not running.",
                )
                return None
            targets = [(target_agent_session_id, target_queue)]
            target_mode = "agent"
        else:
            targets = list(exe.user_message_queues.items())
            target_mode = "broadcast"

        if not targets:
            exe.emit(
                "user_message_failed",
                steeringId=steering_id,
                targetAgentSessionId=target_agent_session_id,
                targetMode=target_mode,
                mode=requested_mode,
                messagePreview=message_preview,
                reason="No live agent queues are available.",
            )
            return None

        queued = QueuedUserMessage(
            steering_id=steering_id,
            message=message,
            target_agent_session_id=target_agent_session_id,
            target_mode=target_mode,
            mode=requested_mode,
            queued_at=queued_at,
            message_preview=message_preview,
        )
        for _, queue in targets:
            queue.put_nowait(queued)

        if requested_mode == "interrupt":
            exe.pending_interrupts[steering_id] = queued
            exe.emit(
                "turn_interrupt_requested",
                steeringId=steering_id,
                targetAgentSessionId=target_agent_session_id,
                targetMode=target_mode,
                mode=requested_mode,
                messagePreview=message_preview,
            )
            exe.emit(
                "turn_interrupt_deferred",
                steeringId=steering_id,
                targetAgentSessionId=target_agent_session_id,
                targetMode=target_mode,
                mode=requested_mode,
                messagePreview=message_preview,
                reason="Current work will receive the instruction at the next safe agent round.",
            )

        exe.emit(
            "user_message_queued",
            steeringId=steering_id,
            targetAgentSessionId=target_agent_session_id,
            targetAgentSessionIds=[target_id for target_id, _ in targets],
            targetMode=target_mode,
            mode=requested_mode,
            messagePreview=message_preview,
            queuedAt=queued_at,
            targetCount=len(targets),
        )
        if target_mode == "broadcast":
            exe.emit(
                "user_message_broadcast",
                steeringId=steering_id,
                targetAgentSessionIds=[target_id for target_id, _ in targets],
                targetMode=target_mode,
                mode=requested_mode,
                messagePreview=message_preview,
                targetCount=len(targets),
            )
        return {
            "status": "queued",
            "steeringId": steering_id,
            "targetMode": target_mode,
            "mode": requested_mode,
            "targetAgentSessionIds": [target_id for target_id, _ in targets],
            "targetCount": len(targets),
            "messagePreview": message_preview,
        }

    async def add_agent_to_job(
        self,
        job_id: str,
        *,
        agent_id: str,
        role: str,
        goal: str | None = None,
    ) -> AgentAssignment | None:
        """Attach a brand-new agent to an already-running job.

        This is the runtime-side half of the Orchestrator's
        ``team-orchestration`` skill: when execution reveals that the
        original composition is missing a capability (e.g. the plan
        needs a ``fabric-admin`` to provision a workspace but none was
        composed), the Orchestrator (or a human supervisor, via the
        ``POST /api/sessions/{id}/agents`` endpoint) can spawn an
        additional agent without rebuilding the composition.

        Mirrors the per-slot branch of :meth:`start_job` — builds an
        ``AgentAssignment``, creates a user-message queue for it,
        narrows the tool surface to the union of the template's
        declared skills, and schedules the agent loop task on the same
        ``_JobExecution``. Emits ``agent_added`` so SSE subscribers
        can render the new node live.

        Returns the created ``AgentAssignment`` on success, ``None``
        when the job isn't running or the agent id is unknown.
        """
        exe = self._active_jobs.get(job_id)
        if exe is None:
            logger.info(
                "[ORCHESTRATOR] add_agent_to_job: no active job %s", job_id,
                extra=_log_extra("diagnostic"),
            )
            return None
        if exe.cancelled or exe.cancel_event.is_set():
            logger.info(
                "[ORCHESTRATOR] add_agent_to_job: job %s already stopping", job_id,
                extra=_log_extra("diagnostic"),
            )
            return None
        tpl = get_template(agent_id)
        if tpl is None:
            logger.warning(
                "[ORCHESTRATOR] add_agent_to_job: unknown agent '%s'", agent_id,
                extra=_log_extra("diagnostic"),
            )
            return None

        job = exe.job
        assignment = AgentAssignment(
            agent_id=agent_id,
            session_id=str(uuid.uuid4()),
            role=role,
            goal=(
                goal
                or _build_slot_goal(
                    task=(job.composition.task if job.composition else job.task_description),
                    slot_id=None,
                    slot_role=role,
                    skills=[sk.name for sk in tpl.skills],
                )
            ),
            status=AgentStatus.QUEUED,
        )
        job.agents.append(assignment)
        update_session(job)

        user_q: asyncio.Queue = asyncio.Queue()
        exe.user_message_queues[assignment.session_id] = user_q
        # Dynamically-added agents get the template's full tool surface
        # by default — there's no composition slot to narrow against.
        allowed = set(tpl.available_tools)
        task = asyncio.create_task(
            self._run_agent(exe, assignment, tpl, user_q, allowed_tools=allowed)
        )
        exe.tasks.append(task)

        exe.emit(
            "agent_added",
            jobId=job.id,
            agent=assignment.model_dump(mode="json", by_alias=True),
        )
        logger.info(
            "[ORCHESTRATOR] add_agent_to_job: attached %s (%s) to job %s",
            agent_id, assignment.session_id, job_id,
            extra=_log_extra("high_level"),
        )
        return assignment


# ── Output parsing helpers (stateless) ───────────────────────────────


def _use_fixed_composition_runtime() -> bool:
    runtime = os.environ.get("AGENTHUB_ORCHESTRATION_RUNTIME", "dynamic").strip().lower()
    dynamic_flag = os.environ.get("AGENTHUB_DYNAMIC_ORCHESTRATION", "1").strip().lower()
    if dynamic_flag in ("0", "false", "no", "off"):
        return True
    return runtime in ("fixed", "maf", "legacy", "composition")


def _agent_display_name(agent_id: str) -> str:
    template = get_template(agent_id)
    return template.display_name if template else agent_id


def _dynamic_task_turn_budget(job: Job) -> int:
    if job.composition is None:
        return MAX_AGENT_ROUNDS
    return max(1, min(MAX_AGENT_ROUNDS, job.composition.budget.max_turns))


def _build_dynamic_agent_goal(
    mission: MissionState,
    task: TaskNode,
    context_pack: dict[str, Any],
) -> str:
    upstream_results = context_pack.get("upstreamResults") or []
    upstream_text = json.dumps(upstream_results, default=str, ensure_ascii=False)
    specialist_catalog = _render_dynamic_specialist_catalog(context_pack.get("specialistCatalog") or [])
    context_contract = json.dumps(_dynamic_context_window_contract(context_pack), default=str, ensure_ascii=False, indent=2)
    task_brief = json.dumps(
        {
            "taskId": task.id,
            "title": task.title,
            "objective": task.objective,
            "delegationReason": task.delegation_reason,
            "contextSummary": task.context_summary,
            "touchTargets": list(task.touch_targets),
            "doNotTouch": list(task.do_not_touch),
            "acceptanceCriteria": list(task.acceptance_criteria),
            "dependencies": list(task.dependencies),
            "resourceClaims": [claim.model_dump(mode="json", by_alias=True) for claim in task.resource_claims],
            "parallelismSafe": task.parallelism_safe,
            "parallelismNotes": task.parallelism_notes,
        },
        default=str,
        ensure_ascii=False,
        indent=2,
    )
    if GENERALIST_AGENT_ID in task.candidate_agent_ids:
        agent_contract = (
            "You are the hidden generalist mission controller. Use the full MCP tool fleet only for safe discovery, "
            "routing, and straightforward checks. Prefer to outsource implementation, modeling, report, governance, "
            "and app-development work to the best matching specialist from the catalog. Before creating follow-up "
            "tasks, decide whether they can run in parallel: mark parallelismSafe=true only when dependencies and "
            "resourceClaims make independence explicit; otherwise leave it false so mission control serializes sibling work."
        )
    else:
        agent_contract = (
            "You are a specialist receiving a structured task from mission control. Stay inside the provided "
            "touchTargets, avoid every doNotTouch item, respect dependencies and resourceClaims, and return blockers "
            "instead of expanding scope."
        )
    return (
        "MISSION GOAL:\n"
        f"{mission.brief.goal}\n\n"
        "DYNAMIC TASK:\n"
        f"Title: {task.title}\n"
        f"Objective: {task.objective}\n"
        f"Task id: {task.id}\n"
        f"Workspace id: {mission.brief.workspace_id}\n\n"
        "UPSTREAM RESULTS:\n"
        f"{upstream_text if upstream_results else '(none)'}\n\n"
        "STRUCTURED TASK BRIEF:\n"
        f"{task_brief}\n\n"
        "CONTEXT WINDOW CONTRACT:\n"
        f"{context_contract}\n\n"
        "SPECIALIST CATALOG:\n"
        f"{specialist_catalog}\n\n"
        "AGENT CONTRACT:\n"
        f"{agent_contract}\n\n"
        "CONSTRAINTS:\n"
        + "\n".join(f"- {constraint}" for constraint in mission.brief.constraints or ["Operate only inside the selected workspace."])
        + "\n\nOUTPUT CONTRACT:\n"
        "Work only on this task objective. End with a concise DECISION that states what was completed, "
        "what remains, and any blockers. If you discover genuinely new follow-up work for the orchestrator, "
        "include this exact optional block in your final response. Each follow-up must be self-contained and "
        "must preserve every relevant constraint, touch/no-touch boundary, acceptance criterion, dependency, "
        "resource claim, and parallelism decision:\n"
        "DYNAMIC_RESULT_START\n"
        '{"followupTasks":[{"title":"...","objective":"...","candidateAgentIds":["fabric-admin"],"requiredCapabilities":[],"delegationReason":"Why this specialist should own it.","contextSummary":"Important prior findings and constraints.","touchTargets":["workspace item or artifact to change/read"],"doNotTouch":["out-of-scope item or protected artifact"],"acceptanceCriteria":["observable done condition"],"resourceClaims":[{"kind":"workspace-item","id":"item-id-or-name","mode":"read"}],"parallelismSafe":false,"parallelismNotes":"Why this must serialize, or why it is independent."}]}\n'
        "DYNAMIC_RESULT_END\n"
    )


def _dynamic_context_pack_v2_summary(context_pack: dict[str, Any]) -> dict[str, Any]:
    context_pack_v2 = context_pack.get("contextPackV2") if isinstance(context_pack.get("contextPackV2"), dict) else {}
    context_budget = context_pack_v2.get("contextBudget") if isinstance(context_pack_v2.get("contextBudget"), dict) else {}
    instruction_budget = context_pack_v2.get("instructionBudget") if isinstance(context_pack_v2.get("instructionBudget"), dict) else {}
    phase_inputs = context_pack_v2.get("phaseInputs") if isinstance(context_pack_v2.get("phaseInputs"), dict) else {}
    backtrack_policy = context_pack_v2.get("backtrackPolicy") if isinstance(context_pack_v2.get("backtrackPolicy"), dict) else {}
    context_mode = context_pack_v2.get("contextMode") if isinstance(context_pack_v2.get("contextMode"), dict) else {}
    return {
        "contextPackSchemaVersion": context_pack_v2.get("schemaVersion"),
        "qrspiProtocol": context_pack_v2.get("qrspiProtocol"),
        "qrspiPhaseModel": list(context_pack_v2.get("qrspiPhaseModel") or [])[:12],
        "contextPhase": context_pack_v2.get("phase"),
        "contextGoal": bounded_text(context_pack_v2.get("contextGoal"), max_chars=280),
        "contextBudgetEstimatedTokens": context_budget.get("estimatedTokens"),
        "contextBudgetMaxTokens": context_budget.get("maxTokens"),
        "contextCompactionThreshold": context_budget.get("compactionThreshold"),
        "instructionBudgetPhaseLimit": instruction_budget.get("phaseInstructionLimit"),
        "instructionBudgetBasis": instruction_budget.get("budgetBasis"),
        "phaseOriginalTaskHiddenFromResearch": phase_inputs.get("originalTaskHiddenFromResearch"),
        "phaseQuestionCount": len(phase_inputs.get("neutralQuestions") or []),
        "subagentWorkModel": (context_pack_v2.get("executionTemplate") or {}).get("subagentWorkModel") if isinstance(context_pack_v2.get("executionTemplate"), dict) else None,
        "verticalSlicePolicy": context_pack_v2.get("verticalSlicePolicy"),
        "backtrackAllowed": backtrack_policy.get("allowed"),
        "backtrackTargetPhases": list(backtrack_policy.get("validPreviousPhases") or [])[:8],
        "reviewPolicy": context_pack_v2.get("reviewPolicy"),
        "contextHandoffDigest": context_pack_v2.get("handoffDigest"),
        "contextModePackage": context_mode.get("package"),
        "contextModeFacade": context_mode.get("facade"),
        "contextModeSavedTokenEstimate": context_mode.get("savedTokenEstimate"),
        "contextModePurgeHandle": context_mode.get("purgeHandle"),
    }


def _dynamic_context_window_contract(context_pack: dict[str, Any]) -> dict[str, Any]:
    context_pack_v2 = context_pack.get("contextPackV2") if isinstance(context_pack.get("contextPackV2"), dict) else {}
    context_mode = context_pack_v2.get("contextMode") if isinstance(context_pack_v2.get("contextMode"), dict) else {}
    return {
        "schemaVersion": context_pack_v2.get("schemaVersion"),
        "qrspiProtocol": context_pack_v2.get("qrspiProtocol"),
        "qrspiPhaseModel": context_pack_v2.get("qrspiPhaseModel"),
        "phase": context_pack_v2.get("phase"),
        "subagentWorkModel": (context_pack_v2.get("executionTemplate") or {}).get("subagentWorkModel") if isinstance(context_pack_v2.get("executionTemplate"), dict) else "context-window-fork",
        "agentIdRole": (context_pack_v2.get("executionTemplate") or {}).get("agentIdRole") if isinstance(context_pack_v2.get("executionTemplate"), dict) else "execution-template",
        "contextGoal": context_pack_v2.get("contextGoal"),
        "contextBudget": context_pack_v2.get("contextBudget"),
        "instructionBudget": context_pack_v2.get("instructionBudget"),
        "phaseInputs": context_pack_v2.get("phaseInputs"),
        "sourceBudget": context_pack_v2.get("sourceBudget"),
        "sourceRefs": context_pack_v2.get("sourceRefs"),
        "retrievalProvenance": context_pack_v2.get("retrievalProvenance"),
        "omissionPolicy": context_pack_v2.get("omissionPolicy"),
        "returnContract": context_pack_v2.get("returnContract"),
        "verticalSlicePolicy": context_pack_v2.get("verticalSlicePolicy"),
        "backtrackPolicy": context_pack_v2.get("backtrackPolicy"),
        "reviewPolicy": context_pack_v2.get("reviewPolicy"),
        "handoffDigest": context_pack_v2.get("handoffDigest"),
        "contextMode": {
            "package": context_mode.get("package"),
            "facade": context_mode.get("facade"),
            "savedTokenEstimate": context_mode.get("savedTokenEstimate"),
            "purgeHandle": context_mode.get("purgeHandle"),
            "events": context_mode.get("events"),
        },
    }


def _render_dynamic_specialist_catalog(catalog: list[dict[str, Any]]) -> str:
    if not catalog:
        return "(no specialist catalog available)"
    lines: list[str] = []
    for agent in catalog:
        lines.append(
            f"- {agent.get('id')}: {agent.get('name')} — {agent.get('description')}"
        )
        skills = agent.get("skills") or []
        if skills:
            skill_bits = []
            for skill in skills:
                tools = ", ".join(str(tool) for tool in skill.get("tools") or [])
                if tools:
                    skill_bits.append(f"{skill.get('id')} ({skill.get('name')}): {tools}")
                else:
                    skill_bits.append(f"{skill.get('id')} ({skill.get('name')})")
            lines.append("  skills: " + "; ".join(skill_bits))
    return "\n".join(lines)


def _dynamic_upstream_handoffs(
    task: TaskNode,
    context_pack: dict[str, Any],
) -> list[HandoffPayload] | None:
    handoffs: list[HandoffPayload] = []
    for idx, result in enumerate(context_pack.get("upstreamResults") or []):
        task_id = str(result.get("taskId") or f"upstream-{idx}")
        status_raw = str(result.get("status") or "success").lower()
        if status_raw in ("failed", "error"):
            status = "error"
        elif status_raw in ("partial", "blocked"):
            status = "partial"
        else:
            status = "success"
        handoffs.append(
            HandoffPayload(
                from_slot_id=task_id,
                to_slot_id=task.id,
                kind="report",
                status=status,
                summary=str(result.get("summary") or result)[:2000],
                artifacts=list(result.get("artifacts") or []),
                key_outputs={"handoffContext": str(result.get("handoffContext") or "")[:200]},
                error="; ".join(str(error) for error in result.get("errors") or [])[:500] or None,
            )
        )
    return handoffs or None


def _summarize_dynamic_assignment(runner: Any, task_id: str, assignment: AgentAssignment) -> str:
    try:
        handoff = runner.extract_handoff(task_id, "orchestrator", "report")
        if handoff.summary:
            return handoff.summary
    except Exception:
        logger.debug(
            "Dynamic handoff extraction failed for task %s",
            task_id,
            exc_info=True,
            extra=_log_extra("trace"),
        )
    if assignment.phases:
        phase = assignment.phases[-1]
        if phase.decisions:
            return phase.decisions[-1].summary
        if phase.details:
            return "\n".join(phase.details[-3:])[:2000]
    return assignment.current_step or f"Task {task_id} finished with status {assignment.status.value}"


def _dynamic_result_status(slot_status: str, assignment: AgentAssignment) -> AgentResultStatus:
    if slot_status == "success":
        return AgentResultStatus.SUCCESS
    if slot_status == "partial":
        return AgentResultStatus.PARTIAL
    if slot_status == "cancelled":
        return AgentResultStatus.CANCELLED
    if slot_status == "budget_exhausted":
        return AgentResultStatus.BLOCKED
    if assignment.status == AgentStatus.COMPLETED:
        return AgentResultStatus.PARTIAL
    return AgentResultStatus.FAILED


def _dynamic_assignment_evidence(assignment: AgentAssignment) -> list[dict[str, Any] | str]:
    evidence: list[dict[str, Any] | str] = []
    container_tool_evidence = getattr(assignment, "_container_tool_evidence", None)
    if isinstance(container_tool_evidence, list):
        evidence.extend(item for item in container_tool_evidence if isinstance(item, dict))
    for phase in assignment.phases[-5:]:
        evidence.append(
            {
                "phaseNumber": phase.phase_number,
                "title": phase.title,
                "details": list(phase.details[-5:]),
                "decisions": [decision.summary for decision in phase.decisions[-3:]],
            }
        )
    if assignment.actions:
        evidence.append(
            {
                "actions": [
                    action.model_dump(mode="json", by_alias=True)
                    for action in assignment.actions[-10:]
                ]
            }
        )
    return evidence


def _extract_dynamic_followups(assignment: AgentAssignment) -> list[FollowupTask]:
    # The DYNAMIC_RESULT block routinely spans dozens of lines (full JSON of
    # follow-up tasks). Earlier we sliced phase.details[-5:], which silently
    # truncated the block and made the regex fail. Take ALL detail/decision
    # text from the recent phases instead so the structured delegation block
    # the generalist emits is always parseable end-to-end.
    text_parts = [assignment.current_step or ""]
    for phase in assignment.phases[-5:]:
        text_parts.extend(phase.details)
        text_parts.extend(decision.summary for decision in phase.decisions)
    text = "\n".join(part for part in text_parts if part)
    payloads = [
        match.group(1)
        for match in re.finditer(
            r"DYNAMIC_RESULT_START\s*(\{.*?\})\s*DYNAMIC_RESULT_END",
            text,
            flags=re.DOTALL,
        )
    ]
    payloads.extend(
        match.group(1)
        for match in re.finditer(
            r"```json\s*(\{[^`]*followupTasks[^`]*\})\s*```",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    )
    followups: list[FollowupTask] = []
    for payload in payloads:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        raw_tasks = data.get("followupTasks") or data.get("followup_tasks") or []
        if not isinstance(raw_tasks, list):
            continue
        for raw_task in raw_tasks:
            try:
                followups.append(FollowupTask.model_validate(_sanitize_dynamic_followup_task(raw_task)))
            except Exception:
                logger.debug(
                    "Ignoring invalid dynamic follow-up task: %r",
                    raw_task,
                    exc_info=True,
                    extra=_log_extra("trace"),
                )
    return followups


def _sanitize_dynamic_followup_task(raw_task: Any) -> Any:
    if not isinstance(raw_task, dict):
        return raw_task
    sanitized = dict(raw_task)
    for key, limit in (
        ("title", 240),
        ("objective", 4_000),
        ("delegationReason", 1_000),
        ("delegation_reason", 1_000),
        ("contextSummary", 2_000),
        ("context_summary", 2_000),
        ("parallelismNotes", 1_000),
        ("parallelism_notes", 1_000),
    ):
        value = sanitized.get(key)
        if isinstance(value, str):
            sanitized[key] = bounded_text(value, max_chars=limit)
    return sanitized


def _assignment_failure_text(assignment: AgentAssignment) -> str:
    parts = [
        _get_blocking_issue(assignment),
        assignment.current_step,
        assignment.role,
        assignment.goal,
    ]
    for phase in assignment.phases[-3:]:
        parts.extend(detail for detail in phase.details[-3:] if detail)
        parts.extend(decision.summary for decision in phase.decisions[-2:] if decision.summary)
    text = " | ".join(str(part).strip() for part in parts if str(part or "").strip())
    return _compact_issue_text(text or "Agent failed without a detailed reason", 1200)


def _build_recovery_goal(job: Job, failed_assignment: AgentAssignment, reason: str) -> str:
    original_task = job.composition.task if job.composition else job.task_description
    return (
        f"Task: {original_task}\n"
        f"Recovery target: slot {failed_assignment.role} ({failed_assignment.agent_id}).\n"
        f"Failure observed: {reason}\n"
        "Recover only the failed portion of the mission. Preserve completed work, "
        "avoid duplicating successful artifacts, and use the tools appropriate to your own skills. "
        "If the blocker requires tenant, capacity, RBAC, credentials, or other external action, "
        "stop and emit a concise DECISION explaining exactly what user action is required."
    )


def _build_slot_goal(*, task: str, slot_id: str | None = None, slot_role: str, skills: list[str]) -> str:
    """Construct the per-slot goal string the agent sees.

    Pulls the original user task, the slot's role in this composition,
    and the skills the composer selected. Intentionally compact —
    long-form context goes through the agent's own system prompt and
    tool calls.
    """
    skill_line = (
        f"Your expected skills for this slot: {', '.join(skills)}." if skills else ""
    )
    slot_line = f"Your slot id: {slot_id}.\n" if slot_id else ""
    return (
        f"Task: {task}\n"
        f"{slot_line}"
        f"Your role: {slot_role}.\n"
        f"{skill_line}\n"
        "Execute your part. If another slot owns a sub-task, defer to them."
    ).strip()


def _required_creation_tool_for_goal(goal: str) -> str | None:
    tools = _required_creation_tools_for_goal(goal)
    return tools[0] if tools else None


def _required_creation_tools_for_goal(goal: str) -> tuple[str, ...]:
    goal_lower = goal.lower()
    if _is_verification_only_goal(goal):
        return ()
    if (
        "fabric_create_workspace_inventory_solution" in goal_lower
        or (
            "fabric item" in goal_lower
            and "semantic" in goal_lower
            and "report" in goal_lower
            and re.search(r"\b(?:visuali[sz]ation|visuali[sz]e|dashboard|solution)\b", goal_lower)
        )
    ):
        return ("fabric_create_workspace_inventory_solution",)

    role = ""
    slot_id = ""
    for line in goal.splitlines():
        lower = line.lower()
        if lower.startswith("your role:"):
            role = line.split(":", 1)[1].strip().strip(".")
        elif lower.startswith("title:") and not role:
            role = line.split(":", 1)[1].strip().strip(".")
        elif lower.startswith("your slot id:"):
            slot_id = line.split(":", 1)[1].strip().strip(".")
        elif lower.startswith("task id:") and not slot_id:
            slot_id = line.split(":", 1)[1].strip().strip(".")
    role_lower = role.lower()
    if "creat" not in role_lower:
        identity_text = f"{slot_id} {role}".strip()
        if not _identity_owns_creation(goal, identity_text):
            return ()
    role_creates_folder = bool(
        re.search(r"\bcreat\w*\s+(?:the\s+|a\s+|an\s+)?(?:run\s+|target\s+|workspace\s+)?folder\b", role_lower)
    )
    role_creates_item = bool(
        re.search(
            r"\bcreat\w*\s+(?:the\s+|a\s+|an\s+)?(?:\w+\s+){0,4}(?:item|lakehouse|notebook|warehouse|report)\b",
            role_lower,
        )
    )
    identity_text = f"{slot_id} {role}".strip()
    if not role_creates_folder:
        role_creates_folder = _identity_owns_creation(goal, identity_text, entity="folder")
    if not role_creates_item:
        role_creates_item = _identity_owns_creation(goal, identity_text, entity="item")
    required_tools: list[str] = []
    if role_creates_folder and "fabric_create_folder" in goal_lower:
        required_tools.append("fabric_create_folder")
    if role_creates_item and "fabric_create_item" in goal_lower:
        required_tools.append("fabric_create_item")
    return tuple(required_tools)


_CREATION_STOP_WORDS = {
    "a", "an", "and", "for", "in", "of", "only", "or", "the", "to", "with",
    "all", "other", "task", "workspace", "workspaces", "sub", "team", "teams",
    "managing", "manage", "readiness", "text", "shell", "slots", "slot",
    "after", "before", "exactly", "first", "fixed", "next", "order", "then",
    "this", "use", "peer", "network", "debate", "contribute", "contributes",
    "preserve", "preserving", "while",
}

_CREATION_WRITE_TOOLS = frozenset({
    "fabric_create_folder",
    "fabric_create_item",
    "fabric_create_directory",
    "fabric_create_workspace_inventory_solution",
    "fabric_write_file",
})

_CREATION_OWNER_HINTS = frozenset({
    "actor",
    "admin",
    "architect",
    "coordinator",
    "critic",
    "data",
    "diagnostics",
    "engineer",
    "governance",
    "ingestion",
    "lead",
    "modeler",
    "modeling",
    "programme",
    "remediation",
    "review",
    "sublead",
    "tester",
    "top",
    "worker",
})

_MISSION_TOOL_PRIORITY = (
    "fabric_create_workspace_inventory_solution",
    "fabric_verify_workspace_inventory_solution",
    "browser_verify_visual_render",
    "fabric_diagnose_workspace_artifacts",
    "fabric_list_workspaces",
    "fabric_list_items",
    "fabric_list_folders",
    "fabric_get_item_definition",
    "fabric_get_item",
    "fabric_create_folder",
    "fabric_create_item",
    "fabric_write_file",
    "database_operations",
    "model_operations",
    "table_operations",
    "column_operations",
    "measure_operations",
    "relationship_operations",
    "dax_query_operations",
    "sl_clone_report",
    "sl_rebind_report",
    "sl_get_report_definition",
    "sl_create_report_from_reportjson",
    "get_knowledge",
    "microsoft_docs_search",
    "microsoft_docs_fetch",
    "microsoft_code_sample_search",
    "sequentialthinking",
    "get_current_time",
)

_AZURE_TOOL_PRIORITY = (
    "get_azure_bestpractices_get",
    "get_azure_bestpractices_ai_app",
    "azure_diagnose_resource",
    "azure_get_resource_health",
    "azure_get_activity_log",
    "azure_query_metrics",
)


def _tool_schema_name(tool_schema: dict[str, Any]) -> str:
    function = tool_schema.get("function")
    if not isinstance(function, dict):
        return ""
    name = function.get("name")
    return name if isinstance(name, str) else ""


def _limit_tools_for_model(
    tools: list[dict[str, Any]],
    *,
    goal: str,
    required_tool_names: tuple[str, ...] = (),
    limit: int = MODEL_TOOL_SCHEMA_LIMIT,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    effective_limit = min(limit, OPENAI_COMPAT_TOOL_SCHEMA_HARD_LIMIT)
    by_name = {
        _tool_schema_name(tool_schema): tool_schema
        for tool_schema in tools
        if _tool_schema_name(tool_schema)
    }
    selected: list[dict[str, Any]] = []
    selected_names: set[str] = set()

    def add_name(tool_name: str) -> None:
        if len(selected) >= effective_limit or not tool_name or tool_name in selected_names:
            return
        tool_schema = by_name.get(tool_name)
        if tool_schema is None:
            return
        selected.append(tool_schema)
        selected_names.add(tool_name)

    for required_tool_name in required_tool_names:
        add_name(required_tool_name)

    goal_lower = goal.lower()
    if any(term in goal_lower for term in ("fabric", "workspace", "item", "inventory", "report", "semantic model")):
        for priority_name in _MISSION_TOOL_PRIORITY:
            add_name(priority_name)

    if any(term in goal_lower for term in ("azure", "foundry", "cloud", "deploy", "deployment", "rbac", "entra")):
        for priority_name in _AZURE_TOOL_PRIORITY:
            add_name(priority_name)

    for tool_schema in tools:
        if len(selected) >= effective_limit:
            break
        tool_name = _tool_schema_name(tool_schema)
        if tool_name and tool_name not in selected_names:
            selected.append(tool_schema)
            selected_names.add(tool_name)

    return selected


def _limit_creation_tools_for_goal(
    goal: str,
    allowed_tools: set[str],
    required_creation_tools: tuple[str, ...],
) -> set[str]:
    limited = set(allowed_tools)
    if _is_verification_only_goal(goal):
        limited.difference_update(_CREATION_WRITE_TOOLS)
        return limited
    if not _has_named_creation_ownership(goal):
        limited.update(required_creation_tools)
        return limited

    limited.difference_update(_CREATION_WRITE_TOOLS)
    limited.update(required_creation_tools)
    return limited


def _is_verification_only_goal(goal: str) -> bool:
    # IMPORTANT: this check must look at the AGENT'S OWN role / objective,
    # never at peripheral text such as the SPECIALIST CATALOG. Earlier the
    # mere catalog mention of "FabricVerifier" or "browser_verify_visual_render"
    # was enough to flip a producer goal into "verifier-only" mode, which
    # then disabled the required-creation gate and let producers ship a
    # text-only ACTION marker as if they had really called the build tool.
    goal_lower = goal.lower()
    role_lines: list[str] = []
    for line in goal_lower.splitlines():
        stripped = line.strip()
        if stripped.startswith(("title:", "objective:", "your role:", "task:")):
            role_lines.append(stripped)
    if not role_lines:
        return False
    role_text = "\n".join(role_lines)
    return (
        "verify user-facing fabric deliverables" in role_text
        or "independently verify the produced" in role_text
        or "fabricverifier" in role_text
        or re.search(r"^\s*(?:title|objective|your role):\s*verif", role_text, re.MULTILINE) is not None
    )


def _has_named_creation_ownership(goal: str) -> bool:
    goal_lower = goal.lower()
    if "explicit creation ownership" in goal_lower or "must not create" in goal_lower:
        return True

    for segment in _creation_owner_segments(goal):
        segment_lower = segment.lower()
        create_match = re.search(r"\bcreat(?:e|es|ed|ing)\b", segment_lower)
        if not create_match:
            continue
        owner_keywords = set(_creation_owner_keywords(segment[:create_match.start()]))
        if owner_keywords.intersection(_CREATION_OWNER_HINTS):
            return True
    return False


def _identity_owns_creation(goal: str, identity_text: str, *, entity: str | None = None) -> bool:
    if not identity_text:
        return False
    identity_keywords = set(_creation_identity_keywords(identity_text))
    if not identity_keywords:
        return False

    entity_pattern = {
        "folder": re.compile(r"\b(?:run\s+|target\s+|workspace\s+)?folder\b", re.IGNORECASE),
        "item": re.compile(r"\b(?:item|lakehouse|notebook|warehouse|report)\b", re.IGNORECASE),
        None: re.compile(r"\b(?:folder|item|lakehouse|notebook|warehouse|report)\b", re.IGNORECASE),
    }[entity]

    for segment in _creation_owner_segments(goal):
        segment_lower = segment.lower()
        create_match = re.search(r"\bcreat(?:e|es|ed|ing)\b", segment_lower)
        if not create_match:
            continue
        creation_target = re.split(
            r"\b(?:inside|in|under|within|with|using|before|after)\b",
            segment[create_match.end():],
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        if not entity_pattern.search(creation_target):
            continue
        if re.search(r"\b(?:must|do)\s+not\s+creat", segment_lower):
            continue
        owner_keywords = _creation_owner_keywords(segment[:create_match.start()])
        if not owner_keywords:
            continue
        overlap = identity_keywords.intersection(owner_keywords)
        if len(overlap) >= min(2, len(set(owner_keywords))):
            return True
    return False


def _creation_owner_segments(text: str) -> list[str]:
    return re.split(
        r"[.;\n,]|\b(?:and|then)\s+(?=(?:the\s+|a\s+|an\s+)?[a-z0-9 _-]{1,80}\bcreat(?:e|es|ed|ing)\b)",
        text,
    )


def _creation_owner_keywords(owner_text: str) -> list[str]:
    owner_phrase = owner_text.rsplit(":", 1)[-1]
    return _creation_keywords(owner_phrase)[-8:]


def _creation_identity_keywords(identity_text: str) -> list[str]:
    owner_phrase = re.split(
        r"\b(?:from|using|based\s+on|according\s+to|per)\b",
        identity_text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return _creation_keywords(owner_phrase)


def _creation_keywords(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 1 and token not in _CREATION_STOP_WORDS
    ]


def _missing_required_creation_tool(
    assignment: AgentAssignment,
    required_tools: tuple[str, ...],
) -> str | None:
    for required_tool in required_tools:
        if not _has_successful_required_creation(assignment, required_tool):
            return required_tool
    return None


def _has_successful_required_creation(
    assignment: AgentAssignment,
    required_tool: str,
) -> bool:
    for action in assignment.actions:
        if action.action_type != "Created":
            continue
        if required_tool == "fabric_create_folder" and action.entity_type == "Folder":
            return True
        if required_tool == "fabric_create_item" and action.entity_type != "Folder":
            return True
        if required_tool == "fabric_create_workspace_inventory_solution" and action.entity_type == "WorkspaceInventorySolution":
            details = _parsed_tool_result_dict(action.details or "")
            if _inventory_solution_verified(details):
                return True
    return False


def _infer_single_created_folder_id(job: Job) -> str | None:
    folder_ids = _created_folder_ids(job)
    if len(folder_ids) == 1:
        return next(iter(folder_ids))
    return None


def _created_folder_ids(job: Job) -> set[str]:
    return {
        action.fabric_item_id
        for agent in job.agents
        for action in agent.actions
        if action.action_type == "Created"
        and action.entity_type == "Folder"
        and action.fabric_item_id
    }


def _apply_single_created_folder_id(tool_args: dict[str, Any], job: Job) -> tuple[str | None, str | None]:
    inferred_folder_id = _infer_single_created_folder_id(job)
    if not inferred_folder_id:
        supplied = tool_args.get("folder_id") or tool_args.get("folderId")
        return None, str(supplied) if supplied else None
    supplied = tool_args.get("folder_id") or tool_args.get("folderId")
    supplied_text = str(supplied) if supplied else None
    if supplied_text == inferred_folder_id and tool_args.get("folder_id") == inferred_folder_id:
        return None, supplied_text
    tool_args.pop("folderId", None)
    tool_args["folder_id"] = inferred_folder_id
    return inferred_folder_id, supplied_text


def _slot_for_agent(composition: Composition, agent_id: str):
    """First slot in the composition that references ``agent_id``.

    When a composition uses the same agent for multiple slots we take
    the first match — the tool surface is identical regardless. Call
    sites that need the *exact* slot should thread the slot id
    directly.
    """
    for s in composition.slots:
        if s.agent_id == agent_id:
            return s
    return None


def _parse_agent_output(content: str, assignment: AgentAssignment,
                        execution: _JobExecution, template):
    """Extract structured phase/action/decision markers from LLM output."""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("PHASE_START:"):
            if assignment.phases and assignment.phases[-1].status == PhaseStatus.EXECUTING:
                assignment.phases[-1].status = PhaseStatus.COMPLETED
                assignment.phases[-1].completed_at = datetime.now(UTC)
                execution.emit("phase_complete", agentId=assignment.session_id,
                                agentName=template.display_name,
                                phaseNumber=assignment.phases[-1].phase_number)
            parts = line[len("PHASE_START:"):].strip().split("|", 1)
            num = len(assignment.phases) + 1
            title = parts[1].strip() if len(parts) > 1 else parts[0].strip()
            phase = ReasoningPhase(
                phase_number=num, title=title,
                description="", status=PhaseStatus.EXECUTING,
            )
            assignment.phases.append(phase)
            execution.emit("phase_start", agentId=assignment.session_id,
                            agentName=template.display_name,
                            phase={"number": num, "title": title,
                                   "timestamp": datetime.now(UTC).isoformat()})

        elif line.startswith("PHASE_END:"):
            if assignment.phases:
                assignment.phases[-1].status = PhaseStatus.COMPLETED
                assignment.phases[-1].completed_at = datetime.now(UTC)

        elif line.startswith("DECISION:"):
            decision_text = line[len("DECISION:"):].strip()
            if assignment.phases:
                assignment.phases[-1].decisions.append(
                    AgentDecision(summary=decision_text)
                )
            execution.emit("agent_decision", agentId=assignment.session_id,
                            agentName=template.display_name,
                            phaseNumber=len(assignment.phases),
                            decision=decision_text)
            issue_level = _major_issue_level(decision_text)
            if issue_level:
                issue_text = _compact_issue_text(decision_text)
                log_fn = logger.error if issue_level == "error" else logger.warning
                log_fn(
                    "[AGENT:%s] Major issue detected in decision output: %s",
                    assignment.session_id[:8], issue_text,
                    extra=_log_extra("high_level"),
                )
                execution.emit(
                    "log_line",
                    agentId=assignment.session_id,
                    agentName=template.display_name,
                    level=issue_level,
                    message=f"Major issue detected: {issue_text}",
                    tags=["major_issue", "capacity"],
                )
                if issue_level == "error":
                    _emit_blocking_slot_progress(
                        execution, assignment, template, issue_text,
                    )

        elif line.startswith("ACTION:"):
            parts = [p.strip() for p in line[len("ACTION:"):].split("|")]
            if len(parts) >= 3:
                atype = parts[0]
                ename = parts[1].replace("ENTITY:", "").strip() if "ENTITY:" in parts[1] else parts[1]
                etype = parts[2].replace("TYPE:", "").strip() if "TYPE:" in parts[2] else parts[2]
                if atype.strip().lower() in {"created", "modified", "deleted"}:
                    execution.emit(
                        "log_line",
                        agentId=assignment.session_id,
                        agentName=template.display_name,
                        level="warn",
                        message=(
                            f"Ignoring text-only ACTION marker for {atype} {ename}; "
                            "mutating Fabric actions must come from successful tool calls."
                        ),
                        tags=["tool_required", "action_marker"],
                    )
                    continue
                action = AgentAction(
                    id=str(uuid.uuid4()), action_type=atype,
                    entity_name=ename, entity_type=etype,
                )
                assignment.actions.append(action)
                execution.emit("action", agentId=assignment.session_id,
                                agentName=template.display_name,
                                action=action.model_dump(mode="json"))


def _detect_action_from_tool(tool_name: str, tool_args: dict, result: str) -> AgentAction | None:
    """Infer an action from a tool call."""
    if tool_name == "fabric_create_workspace_inventory_solution":
        return _detect_inventory_solution_action(tool_args, result)
    if tool_name == "browser_verify_visual_render":
        return _detect_browser_visual_action(tool_args, result)

    result_lower = result.lower()
    is_existing_conflict = _is_existing_create_conflict(result_lower)
    is_error = any(marker in result_lower for marker in (
        "error", "failed", "unauthorized", "forbidden", "not found",
        "featurenotavailable", "badrequest", "capacitynotactive",
        "capacity not active", "capacity is inactive",
    )) and not is_existing_conflict
    fabric_identity = _fabric_identity_from_tool_result(result)

    if tool_name == "fabric_create_item":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Failed" if is_error else "Created",
            entity_name=tool_args.get("display_name", "unknown"),
            entity_type=tool_args.get("item_type", "Item"),
            fabric_item_id=fabric_identity.get("id"),
            web_url=fabric_identity.get("web_url"),
            details=result[:200],
        )
    if tool_name == "fabric_create_folder":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Failed" if is_error else "Created",
            entity_name=tool_args.get("display_name", "unknown"),
            entity_type="Folder",
            fabric_item_id=fabric_identity.get("id"),
            web_url=fabric_identity.get("web_url"),
            details=result[:200],
        )
    if tool_name == "fabric_write_file":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Failed" if is_error else "Modified",
            entity_name=tool_args.get("file_path", "unknown"),
            entity_type="File",
            details=result[:200] if is_error else None,
        )
    if tool_name == "fabric_delete_file":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Failed" if is_error else "Deleted",
            entity_name=tool_args.get("file_path", "unknown"),
            entity_type="File",
        )
    if tool_name == "fabric_delete_item":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Failed" if is_error else "Deleted",
            entity_name=tool_args.get("item_id", "unknown"),
            entity_type="Item",
            details=result[:200],
        )
    if tool_name == "fabric_create_directory":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Failed" if is_error else "Created",
            entity_name=tool_args.get("directory_path", "unknown"),
            entity_type="Directory",
            details=result[:200] if is_error else None,
        )
    if tool_name == "fabric_list_workspaces":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Queried",
            entity_name="All workspaces",
            entity_type="Workspace",
            details=f"{result[:120]}..." if len(result) > 120 else result,
        )
    if tool_name == "fabric_list_items":
        ws = tool_args.get("workspace_id", "?")[:8]
        item_type = tool_args.get("item_type", "all items")
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Queried",
            entity_name=f"{item_type} in workspace {ws}...",
            entity_type="Items",
            details=f"{result[:120]}..." if len(result) > 120 else result,
        )
    if tool_name == "fabric_list_files":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Queried",
            entity_name=tool_args.get("path", "/"),
            entity_type="Files",
        )
    if tool_name == "fabric_read_file":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Read",
            entity_name=tool_args.get("file_path", "unknown"),
            entity_type="File",
        )
    return None


def _detect_inventory_solution_action(tool_args: dict, result: str) -> AgentAction:
    parsed = _parsed_tool_result_dict(result)
    verified = _inventory_solution_verified(parsed)
    folder_id = parsed.get("folderId") if isinstance(parsed.get("folderId"), str) else None
    folder_name = (
        parsed.get("folderName")
        if isinstance(parsed.get("folderName"), str)
        else tool_args.get("folder_name") or tool_args.get("folderName") or "Workspace inventory solution"
    )
    details = _compact_inventory_solution_details(parsed) if parsed else {"raw": result[:1200]}
    return AgentAction(
        id=str(uuid.uuid4()),
        action_type="Created" if verified else "Failed",
        entity_name=str(folder_name),
        entity_type="WorkspaceInventorySolution",
        fabric_item_id=folder_id,
        details=json.dumps(details, sort_keys=True, default=str),
    )


def _detect_browser_visual_action(tool_args: dict, result: str) -> AgentAction:
    parsed = _parsed_tool_result_dict(result)
    details = parsed if parsed else {"raw": result[:1200]}
    final_url = details.get("finalUrl") or tool_args.get("url")
    status = str(details.get("status") or "").lower()
    ok = bool(details.get("ok")) or status == "passed"
    return AgentAction(
        id=str(uuid.uuid4()),
        action_type="Verified" if ok else "Failed",
        entity_name=str(final_url or tool_args.get("url") or "browser visual render"),
        entity_type="browser_visual_render",
        web_url=str(final_url) if final_url else tool_args.get("url"),
        details=json.dumps(details, sort_keys=True, default=str),
    )


def _should_emit_artifact_for_action(action: AgentAction) -> bool:
    entity_type = str(action.entity_type or "").strip().lower().replace(" ", "_")
    return entity_type not in {"browser_visual_render", "browser_visual", "browser_screenshot"}


def _action_details_dict(action: AgentAction | None) -> dict[str, Any]:
    if action is None or not action.details:
        return {}
    try:
        parsed = json.loads(action.details)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _compact_inventory_solution_details(parsed: dict[str, Any]) -> dict[str, Any]:
    summary_keys = (
        "status",
        "workspaceId",
        "folderId",
        "folderName",
        "sourceItemCount",
        "sourceWorkspaceCount",
        "dataSource",
        "semanticModelStorageMode",
        "notebookWritesEnabled",
        "persistentDataWritten",
        "persistentDataStore",
        "notebookExecution",
        "semanticModelId",
        "reportId",
        "semanticModelDataValidation",
        "reportRenderValidation",
        "qualityValidation",
        "createdItems",
        "errors",
        "warnings",
    )
    return {key: parsed[key] for key in summary_keys if key in parsed}


def _inventory_solution_verified(parsed: dict[str, Any]) -> bool:
    if not parsed:
        return False
    if str(parsed.get("status") or "").lower() != "created":
        return False

    errors = parsed.get("errors")
    if isinstance(errors, list) and any(str(error).strip() for error in errors):
        return False
    if isinstance(errors, str) and errors.strip():
        return False

    if "dataSource" in parsed and parsed.get("dataSource") != "lakehouse_delta_tables":
        return False
    if "semanticModelStorageMode" in parsed and parsed.get("semanticModelStorageMode") != "DirectLake":
        return False
    if "notebookWritesEnabled" in parsed and parsed.get("notebookWritesEnabled") is not True:
        return False
    if "persistentDataWritten" in parsed and parsed.get("persistentDataWritten") is not True:
        return False

    store = parsed.get("persistentDataStore")
    if store is not None:
        if not isinstance(store, dict):
            return False
        if str(store.get("type") or "").lower() != "lakehouse":
            return False
        if "written" in store and store.get("written") is not True:
            return False

    semantic_validation = parsed.get("semanticModelDataValidation")
    if semantic_validation is not None:
        if not isinstance(semantic_validation, dict):
            return False
        if str(semantic_validation.get("status") or "").lower() != "queryable":
            return False
        row_count = semantic_validation.get("rowCount")
        if row_count is not None and (not isinstance(row_count, int) or row_count <= 0):
            return False

    render_validation = parsed.get("reportRenderValidation")
    if render_validation is not None:
        if not isinstance(render_validation, dict):
            return False
        if str(render_validation.get("status") or "").lower() != "rendered":
            return False

    quality_validation = parsed.get("qualityValidation")
    if quality_validation is not None:
        if not isinstance(quality_validation, dict):
            return False
        if str(quality_validation.get("status") or "").lower() != "passed":
            return False

    created_items = parsed.get("createdItems")
    if isinstance(created_items, list):
        for item in created_items:
            if isinstance(item, dict) and str(item.get("type") or "").lower() == "warehouse":
                return False

    return True


_READ_ONLY_TOOL_VERBS = frozenset({
    "cat", "describe", "docs", "evaluate", "find", "get", "help", "inspect",
    "list", "ls", "model", "pages", "query", "read", "report", "scan", "schema",
    "show", "tree", "validate", "visuals",
})
_MUTATING_TOOL_VERBS = frozenset({
    "add", "apply", "assign", "backup", "cancel", "clone", "commit", "configure",
    "copy", "cp", "create", "delete", "deploy", "export", "fix", "generate", "grant",
    "migrate", "move", "mv", "new", "publish", "rebind", "refresh", "remove",
    "restore", "revoke", "rm", "run", "set", "start", "stop", "trigger", "update",
    "upgrade", "write",
})
_IMPORTANT_ACTION_MARKERS = (
    "assign", "capacity", "commit_to_git", "connection", "endorsement", "git",
    "job", "permission", "pipeline", "publish", "rebind", "refresh", "role",
    "run", "schedule", "user",
)


def _change_record_from_tool(
    tool_name: str,
    tool_args: dict,
    result: str,
    action: AgentAction | None,
) -> dict | None:
    """Build a user-facing side-effect record for successful writes only."""
    if not _is_mutating_tool(tool_name):
        return None
    if action and action.action_type == "Failed":
        return None
    if _tool_result_has_error(result):
        return None

    change_kind = _change_kind(tool_name, action)
    target_type, target_name, target_scope = _change_target(tool_name, tool_args, action)
    action_details = _action_details_dict(action)
    parsed_result = _parsed_tool_result_dict(result)
    if action and action.entity_type == "WorkspaceInventorySolution":
        target_type = "Folder"
        target_name = str(action_details.get("folderName") or action.entity_name)
        target_scope = "folder"
    record_id = action.id if action else str(uuid.uuid4())
    summary = _change_summary(change_kind, target_type, target_name, tool_name)

    record: dict[str, Any] = {
        "recordId": record_id,
        "kind": change_kind,
        "status": "applied",
        "targetName": target_name,
        "targetType": target_type,
        "targetScope": target_scope,
        "summary": summary,
        "toolName": tool_name,
    }
    if action and action.fabric_item_id:
        record["targetId"] = action.fabric_item_id
    if action and action.web_url:
        record["webUrl"] = action.web_url
    folder_id = (
        action_details.get("folderId")
        or parsed_result.get("folderId")
        or tool_args.get("folder_id")
        or tool_args.get("folderId")
    )
    if isinstance(folder_id, str) and folder_id.strip():
        if target_scope == "folder" and "targetId" not in record:
            record["targetId"] = folder_id.strip()
        elif target_scope != "folder":
            record["folderId"] = folder_id.strip()
    folder_name = action_details.get("folderName") or parsed_result.get("folderName") or tool_args.get("folder_name") or tool_args.get("folderName")
    if isinstance(folder_name, str) and folder_name.strip():
        record["folderName"] = folder_name.strip()
    parent_folder_id = parsed_result.get("parentFolderId") or tool_args.get("parent_folder_id") or tool_args.get("parentFolderId")
    if isinstance(parent_folder_id, str) and parent_folder_id.strip():
        record["parentFolderId"] = parent_folder_id.strip()
    created_items = action_details.get("createdItems")
    if isinstance(created_items, list):
        record["createdItems"] = [item for item in created_items if isinstance(item, dict)]
    return record


def _is_mutating_tool(tool_name: str) -> bool:
    try:
        from services.agenthub.tool_runtime import ToolSensitivity, get_policy
        policy = get_policy(tool_name)
        if policy is not None:
            if policy.sensitivity in {ToolSensitivity.WRITE, ToolSensitivity.DESTRUCTIVE}:
                return True
            if policy.sensitivity in {ToolSensitivity.READ_SAFE, ToolSensitivity.READ_SENSITIVE}:
                return False
    except Exception:
        pass

    tokens = _tool_tokens(tool_name)
    if any(token in _READ_ONLY_TOOL_VERBS for token in tokens):
        return False
    return any(token in _MUTATING_TOOL_VERBS for token in tokens)


def _tool_tokens(tool_name: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", tool_name.lower()) if token]


def _tool_result_has_error(result: str) -> bool:
    parsed = _parsed_tool_result_dict(result)
    if parsed:
        errors = parsed.get("errors")
        if isinstance(errors, list) and any(str(error).strip() for error in errors):
            return True
        if isinstance(errors, str) and errors.strip():
            return True
        status = str(parsed.get("status") or parsed.get("state") or "").lower()
        if status in {"created", "ok", "success", "succeeded", "verified", "passed", "rendered", "queryable"}:
            return False
        if parsed.get("ok") is True:
            return False
        if status in {"error", "failed", "failure", "blocked", "partial"}:
            return True

    if parsed and "errors" in parsed:
        sanitized = dict(parsed)
        sanitized.pop("errors", None)
        result_lower = json.dumps(sanitized, sort_keys=True, default=str).lower()
    else:
        result_lower = result.lower()
    return any(marker in result_lower for marker in (
        "error", "failed", "unauthorized", "forbidden", "not found",
        "featurenotavailable", "badrequest", "capacitynotactive",
        "capacity not active", "capacity is inactive",
    )) and not _is_existing_create_conflict(result_lower)


def _change_kind(tool_name: str, action: AgentAction | None) -> str:
    if action:
        if action.action_type == "Created":
            return "created"
        if action.action_type == "Modified":
            return "updated"
        if action.action_type == "Deleted":
            return "deleted"

    lower = tool_name.lower()
    tokens = _tool_tokens(tool_name)
    if any(marker in lower for marker in _IMPORTANT_ACTION_MARKERS):
        return "important_action"
    if any(token in {"delete", "remove", "rm", "drop"} for token in tokens):
        return "deleted"
    if any(token in {"create", "new", "add", "clone", "copy", "cp", "export", "backup"} for token in tokens):
        return "created"
    if any(token in {"run", "execute", "refresh", "deploy", "publish", "trigger", "start", "stop", "cancel"} for token in tokens):
        return "important_action"
    return "updated"


def _change_target(
    tool_name: str,
    tool_args: dict,
    action: AgentAction | None,
) -> tuple[str, str, str]:
    if action:
        return action.entity_type, action.entity_name, _target_scope_from_type(action.entity_type)

    target_name = _target_name_from_args(tool_args)
    lower = tool_name.lower()
    if any(marker in lower for marker in ("pipeline", "run", "job", "refresh", "maintenance")):
        return _title_from_tool(tool_name), target_name, "execution"
    if any(marker in lower for marker in ("user", "role", "permission")):
        return _title_from_tool(tool_name), target_name, "access"
    if any(marker in lower for marker in ("capacity", "git", "connection", "endorsement", "schedule")):
        return _title_from_tool(tool_name), target_name, "settings"
    if any(marker in lower for marker in ("file", "pbir", "report", "semantic_model", "model")):
        return _title_from_tool(tool_name), target_name, "item"
    return _title_from_tool(tool_name), target_name, "action"


def _target_scope_from_type(entity_type: str) -> str:
    normalized = entity_type.strip().lower().replace(" ", "")
    if normalized in {"file", "directory"}:
        return "file"
    if normalized == "folder":
        return "folder"
    if normalized == "workspace":
        return "workspace"
    return "item"


def _target_name_from_args(tool_args: dict) -> str:
    for key in (
        "display_name", "displayName", "name", "item_name", "itemName",
        "report_name", "reportName", "dataset_name", "datasetName",
        "semantic_model_name", "semanticModelName", "workspace_name", "workspaceName",
        "file_path", "filePath", "directory_path", "directoryPath", "path",
        "item_id", "itemId", "report_id", "reportId", "dataset_id", "datasetId",
        "pipeline_id", "pipelineId", "notebook_id", "notebookId", "workspace_id",
        "workspaceId", "capacity_id", "capacityId", "job_type", "jobType",
    ):
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Applied change"


def _title_from_tool(tool_name: str) -> str:
    name = tool_name
    for prefix in ("fabric_", "sl_", "pbir_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if name.startswith("fix_"):
        name = name[len("fix_"):]
    return " ".join(part.capitalize() for part in name.split("_") if part)


def _change_summary(change_kind: str, target_type: str, target_name: str, tool_name: str) -> str:
    target_label = target_type.strip() or _title_from_tool(tool_name)
    if change_kind == "created":
        return f"Created {target_label} {target_name}."
    if change_kind == "updated":
        return f"Updated {target_label} {target_name}."
    if change_kind == "deleted":
        return f"Deleted {target_label} {target_name}."
    return f"Applied {_title_from_tool(tool_name)} to {target_name}."


def _is_existing_create_conflict(result_lower: str) -> bool:
    return any(
        marker in result_lower
        for marker in (
            "already exists",
            "alreadyexist",
            "conflict",
            "itemdisplaynamealreadyinuse",
            "display name is already in use",
            "same display name",
        )
    )


def _fabric_identity_from_tool_result(result: str) -> dict[str, str]:
    parsed = _parsed_tool_result_dict(result)
    if not isinstance(parsed, dict):
        return {}

    identity: dict[str, str] = {}
    item_id = parsed.get("id")
    if isinstance(item_id, str) and item_id:
        identity["id"] = item_id
    web_url = parsed.get("webUrl") or parsed.get("web_url")
    if isinstance(web_url, str) and web_url:
        identity["web_url"] = web_url
    return identity


def _parsed_tool_result_dict(result: str) -> dict[str, Any]:
    text = _unwrap_tool_result(result)
    if text.lower().startswith("error creating inventory solution:"):
        text = text.split(":", 1)[1].strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _unwrap_tool_result(result: str) -> str:
    open_marker = "<<<UNTRUSTED_TOOL_OUTPUT_BEGIN>>>"
    close_marker = "<<<UNTRUSTED_TOOL_OUTPUT_END>>>"
    if open_marker not in result or close_marker not in result:
        return result.strip()

    inner = result.split(open_marker, 1)[1].split(close_marker, 1)[0].strip()
    lines = inner.splitlines()
    if lines and lines[0].startswith("tool="):
        lines = lines[1:]
    return "\n".join(lines).strip()


# ── Singleton accessor (backed by ServiceRegistry) ───────────────────


def get_orchestrator_engine() -> OrchestratorEngine:
    """Get the singleton OrchestratorEngine from ServiceRegistry."""
    registry = get_service_registry()
    if not registry.has(OrchestratorEngine):
        registry.register(OrchestratorEngine, OrchestratorEngine())
    return registry.get(OrchestratorEngine)
