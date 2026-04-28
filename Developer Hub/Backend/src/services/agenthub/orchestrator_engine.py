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
from services.agenthub.agent_registry import GENERALIST_AGENT_ID, get_template
from services.agenthub.attachments import ATTACHMENT_SHIELD_PROMPT
from services.agenthub.compose_service import ComposeService, get_compose_service
from services.agenthub.drivers.handoff import HandoffPayload
from services.agenthub.dynamic_orchestrator import DynamicMissionController
from services.agenthub.event_ledger import ledger_digest, ledger_preview, record_event
from services.agenthub import session_event_store
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
MODEL_TOOL_SCHEMA_LIMIT = int(os.environ.get("AGENTHUB_OPENAI_TOOL_SCHEMA_LIMIT", "120"))

_GENERALIST_BOOTSTRAP_TOOLS = frozenset({
    "fabric_list_workspaces",
    "fabric_list_items",
    "fabric_list_folders",
    "fabric_create_folder",
    "fabric_verify_workspace_inventory_solution",
    "fabric_create_workspace_inventory_solution",
})

_TRANSIENT_LLM_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

_PUBLIC_LOG_CATEGORIES = PUBLIC_LOG_CATEGORIES
_HIGH_LEVEL_EVENT_TYPES = {
    "agent_added",
    "agent_context_received",
    "approval.resolved",
    "approval_required",
    "change_recorded",
    "composition_ready",
    "job_cancelled",
    "job_complete",
    "job_failed",
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
    "verifier_verdict",
}
_DIAGNOSTIC_EVENT_TYPES = {
    "agent_error",
    "subagent_inspected",
    "subagent_stale",
    "subagent_steered",
    "tool_progress",
    "tool_call_ended",
    "tool_call_started",
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


def _summary_scalar(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return bounded_text(value, max_chars=180)


def _event_payload_summary(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, redacted support summary for a mission event."""
    summary: dict[str, Any] = {}
    for key in (
        "agentId", "agentName", "slotId", "taskId", "runId", "callId",
        "toolName", "status", "level", "durationMs", "approvalId",
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
        if payload.get("errorPreview"):
            summary["errorPreview"] = bounded_text(payload.get("errorPreview"), max_chars=260)
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
            return _resolve_wildcard_tool_scope(run.agent_id, self.engine.mcp_manager)
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
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self.user_message_queues: dict[str, asyncio.Queue] = {}  # session_id -> Queue
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
            self._emit_with_bound_session(event_type, **kwargs)
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
            return

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
        desired = set(_GENERALIST_BOOTSTRAP_TOOLS)
    else:
        template = get_template(agent_id)
        desired = set(template.available_tools) if template else set()

    if desired:
        resolved = available.intersection(desired)
    else:
        resolved = available
    return set(sorted(resolved)[:MODEL_TOOL_SCHEMA_LIMIT])


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

        # Emit the initial composition frame so the UI can render the
        # graph immediately, even before any slot starts.
        execution.emit(
            "composition_ready",
            composition=job.composition.model_dump(mode="json", by_alias=True),
        )
        # P1 · Mission Control — seed every subscriber with a
        # ``run_overview`` so a late / reconnecting client can render
        # without a separate fetch.
        execution.emit("run_overview", **execution.snapshot_run_overview())

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

        execution.emit(
            "composition_ready",
            composition=job.composition.model_dump(mode="json", by_alias=True),
        )
        execution.emit("run_overview", **execution.snapshot_run_overview())

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
        any_error = any(self._assignment_error_is_unrecovered(job, a) for a in job.agents)
        mission_status = execution.dynamic_mission_state.status if execution.dynamic_mission_state else None
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
        elif any_error:
            terminal = "job_failed"
        else:
            terminal = "job_complete"
        execution.emit(
            terminal,
            jobId=job.id,
            status=job.status.value,
            totalDuration=duration,
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
        all_tools = self.mcp_manager.get_openai_tools_schema() if self.mcp_manager else []
        allowed_names = set(allowed_tools) if allowed_tools is not None else set(template.available_tools)
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
                    user_msg = user_queue.get_nowait()
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
                body["tools"] = tools
                required_creation_tool = _missing_required_creation_tool(assignment, required_creation_tools)
                if (
                    required_creation_tool
                    and required_creation_tool in available_tool_names
                ):
                    body["tool_choice"] = {
                        "type": "function",
                        "function": {"name": required_creation_tool},
                    }
                else:
                    body["tool_choice"] = "auto"

            logger.info("[AGENT:%s] Round %d: %d messages, %d tools",
                        agent_label, round_num + 1, len(messages), len(tools), extra=_log_extra("diagnostic"))

            response: dict[str, Any] | None = None
            round_start_time = time.monotonic()
            last_llm_error: Exception | None = None
            for attempt in range(1, max(1, AGENT_LLM_MAX_ATTEMPTS) + 1):
                try:
                    headers = _copilot_headers(execution.copilot_token)
                    # P4 · Race the HTTP call against the cancel event so
                    # a user-initiated terminate lands within one RTT
                    # rather than waiting for the full client timeout.
                    async with httpx.AsyncClient(timeout=AGENT_ROUND_TIMEOUT) as client:
                        post_task = asyncio.create_task(client.post(
                            f"{COPILOT_API_BASE}/chat/completions",
                            json=body, headers=headers,
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
                execution.emit("tool_call_started",
                                agentId=assignment.session_id,
                                agentName=template.display_name,
                                callId=call_id, toolName=tool_name,
                                argsPreview=args_preview)
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
                    mcp_manager=self.mcp_manager,
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
                issue_level = _major_issue_level(tool_result)
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
                        execution.emit("artifact_added",
                                        artifactId=action.id,
                                        agentId=assignment.session_id,
                                        kind=action.entity_type,
                                        name=action.entity_name,
                                        state="written" if written else "draft",
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
                execution.emit(
                    "tool_call_ended",
                    agentId=assignment.session_id,
                    callId=call_id, toolName=tool_name,
                    durationMs=duration_ms,
                    status="ok" if rt_result.ok else "error",
                    errorPreview=None if rt_result.ok else result_preview,
                )

                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_result})

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

    def inject_message(self, job_id: str, message: str, target_agent_session_id: str | None = None) -> bool:
        """Push a user message into a running agent's queue."""
        exe = self._active_jobs.get(job_id)
        if not exe:
            return False
        if target_agent_session_id and target_agent_session_id in exe.user_message_queues:
            exe.user_message_queues[target_agent_session_id].put_nowait(message)
        else:
            for q in exe.user_message_queues.values():
                q.put_nowait(message)
        return True

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
    text_parts = [assignment.current_step or ""]
    for phase in assignment.phases[-5:]:
        text_parts.extend(phase.details[-5:])
        text_parts.extend(decision.summary for decision in phase.decisions[-3:])
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
                followups.append(FollowupTask.model_validate(raw_task))
            except Exception:
                logger.debug(
                    "Ignoring invalid dynamic follow-up task: %r",
                    raw_task,
                    exc_info=True,
                    extra=_log_extra("trace"),
                )
    return followups


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


def _limit_creation_tools_for_goal(
    goal: str,
    allowed_tools: set[str],
    required_creation_tools: tuple[str, ...],
) -> set[str]:
    limited = set(allowed_tools)
    if not _has_named_creation_ownership(goal):
        limited.update(required_creation_tools)
        return limited

    limited.difference_update(_CREATION_WRITE_TOOLS)
    limited.update(required_creation_tools)
    return limited


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


def _compact_inventory_solution_details(parsed: dict[str, Any]) -> dict[str, Any]:
    summary_keys = (
        "status",
        "workspaceId",
        "folderId",
        "folderName",
        "sourceItemCount",
        "sourceWorkspaceCount",
        "dataSource",
        "notebookWritesEnabled",
        "persistentDataWritten",
        "persistentDataStore",
        "notebookExecution",
        "semanticModelId",
        "reportId",
        "semanticModelDataValidation",
        "reportRenderValidation",
        "createdItems",
        "errors",
        "warnings",
    )
    return {key: parsed[key] for key in summary_keys if key in parsed}


def _inventory_solution_verified(parsed: dict[str, Any]) -> bool:
    if not parsed:
        return False
    status = str(parsed.get("status") or "").lower()
    errors = parsed.get("errors")
    has_blocking_errors = bool(errors) if isinstance(errors, list) else bool(errors)
    model_validation = parsed.get("semanticModelDataValidation")
    report_validation = parsed.get("reportRenderValidation")
    return status == "created" and not has_blocking_errors and bool(model_validation) and bool(report_validation)


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
