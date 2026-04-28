"""Helper module — verifier_verdict emission logic.

Kept as its own module so the large helper is easier to test and so
``dynamic_orchestrator.py`` does not balloon further. ``DynamicMissionController``
delegates the verdict emit to ``emit_verifier_verdict`` immediately after
merging a FabricVerifier subagent result.

The verdict is *deterministic*: it re-judges the verifier's claim against a
strict structural rubric so that a verifier LLM cannot accept a Fabric/Power BI
deliverable based on metadata or server-side ``exportTo`` proof alone when the
deliverable must actually open in a user's browser.
"""

from __future__ import annotations

import logging
import json
import uuid
from typing import Any

from domain.models.dynamic_orchestration import (
    AgentResult,
    AgentResultStatus,
    FollowupTask,
    MissionState,
    SubagentRun,
    SubagentStatus,
    TaskNode,
)
from services.observability import bounded_text

logger = logging.getLogger(__name__)

USER_RENDERABLE_DELIVERABLE_TYPES = {"Report", "Dashboard", "PaginatedReport"}
LOADING_STUCK_PATTERNS = (
    "loading your report",
    "almost done",
    "loading data",
    "please wait",
)


def compute_structural_rubric(
    state: MissionState,
    task: TaskNode,
    result: AgentResult,
) -> tuple[bool, list[str], dict[str, Any], list[dict[str, Any]]]:
    """Return ``(rubric_passed, structural_failures, evidence, deliverables)``.

    The orchestrator calls this BEFORE deciding whether to route the verifier
    result to the generalist review path. When the rubric fails for a deliverable
    that requires user-facing rendering, the orchestrator synthesises a repair
    follow-up so the existing review/repair loop runs instead of silently
    accepting the broken artifact.
    """
    evidence = _extract_browser_evidence(result)
    deliverables = _verifier_deliverables(state, task, result)
    requires_browser = any(
        str(d.get("type") or "") in USER_RENDERABLE_DELIVERABLE_TYPES
        for d in deliverables
    )
    failures: list[str] = []
    if requires_browser:
        if not evidence["browserVerifiedUrls"]:
            failures.append("NO_USER_BROWSER_EVIDENCE")
        if evidence["loadingStuckObserved"]:
            failures.append("REPORT_STUCK_LOADING")
        if evidence["errorsObserved"]:
            failures.append("BROWSER_ERROR_OBSERVED")
        if evidence["browserVerifiedUrls"] and not evidence["visualsRendered"]:
            failures.append("VISUALS_NOT_RENDERED")
    return (not failures, failures, evidence, deliverables)


def synthesize_browser_evidence_followup(
    deliverables: list[dict[str, Any]],
    structural_failures: list[str],
) -> FollowupTask:
    """Build a repair task that re-runs verifier with mandatory browser evidence."""
    deliverable_lines = []
    for d in deliverables:
        if str(d.get("type") or "") not in USER_RENDERABLE_DELIVERABLE_TYPES:
            continue
        deliverable_lines.append(
            f"- {d.get('type')} '{d.get('name') or d.get('id')}' webUrl={d.get('webUrl') or '(missing)'}"
        )
    deliverable_block = "\n".join(deliverable_lines) or "(no user-renderable deliverables found)"
    return FollowupTask(
        title="Re-verify deliverable with mandatory browser evidence",
        objective=(
            "FabricVerifier returned success but the structural rubric flagged "
            + ", ".join(structural_failures)
            + ". Re-run FabricVerifier and call browser_verify_visual_render against each "
            + "user-facing deliverable webUrl below. Include the captured screenshotPath, finalUrl, "
            + "visualLikeElementCount, and bodyTextSample in AgentResult.evidence so the orchestrator's "
            + "structural backstop can pass. The verdict can only be accepted when the report renders "
            + "actual visuals (no 'Loading your report...' stuck state, no error modal, visualsRendered=true). "
            + "Deliverables to verify in browser:\n"
            + deliverable_block
        ),
        candidate_agent_ids=["fabric-verifier"],
        parallelism_safe=False,
    )


def user_renderable_deliverables_from_result(
    state: MissionState,
    task: TaskNode,
    result: AgentResult,
) -> list[dict[str, Any]]:
    """Return report/dashboard deliverables produced by a non-verifier task.

    Producer results often contain ``AgentAction`` dictionaries, not raw Fabric
    item dictionaries. For the inventory solution tool, the action has
    ``entityType=WorkspaceInventorySolution`` and a compact JSON ``details``
    field containing ``createdItems``. This helper normalises both shapes so
    the dynamic orchestrator can schedule a mandatory FabricVerifier task even
    when the producer/generalist forgets to ask for one.
    """
    deliverables = _verifier_deliverables(state, task, result)
    return [
        deliverable for deliverable in deliverables
        if str(deliverable.get("type") or "") in USER_RENDERABLE_DELIVERABLE_TYPES
    ]


def synthesize_mandatory_verification_followup(
    deliverables: list[dict[str, Any]],
    *,
    original_goal: str,
) -> FollowupTask:
    """Build the mandatory final FabricVerifier task for user-facing items."""
    lines = []
    for d in deliverables:
        lines.append(
            f"- {d.get('type')} '{d.get('name') or d.get('id')}' id={d.get('id') or '(missing)'} webUrl={d.get('webUrl') or '(missing)'}"
        )
    deliverable_block = "\n".join(lines)
    return FollowupTask(
        title="Verify user-facing Fabric deliverables",
        objective=(
            "Independently verify the produced user-facing Fabric/Power BI deliverables before the mission can finish. "
            "Do not trust the producer summary. Compare the artifacts to the original user goal, inspect the Fabric items, "
            "validate semantic-model/report definitions and data where applicable, and call browser_verify_visual_render "
            "against every Report/Dashboard webUrl below. Your AgentResult.evidence MUST include the browser verification "
            "JSON including finalUrl, screenshotPath, bodyTextSample, and visualSummary/visualLikeElementCount. "
            "Reject the deliverable if the browser lands on an error page, remains stuck on 'Loading your report...', "
            "or shows zero rendered visual elements. Original goal:\n"
            + original_goal[:1800]
            + "\n\nDeliverables:\n"
            + deliverable_block
        ),
        candidate_agent_ids=["fabric-verifier"],
        required_capabilities=["fabric-verification"],
        delegation_reason="Mandatory verifier gate for produced user-facing Fabric/Power BI artifacts.",
        context_summary="This verifier task was scheduled by the orchestrator, not suggested by the producer.",
        acceptance_criteria=[
            "Verifier explicitly states accomplished/not accomplished, good, bad, and lacking.",
            "Verifier calls browser_verify_visual_render for every Report/Dashboard webUrl.",
            "Verifier returns browser evidence with screenshotPath, finalUrl, and visual render signals.",
            "Mission may finish only if the resulting verifier_verdict event has passed=true.",
        ],
        parallelism_safe=False,
    )


def emit_verifier_verdict(
    *,
    state: MissionState,
    task: TaskNode,
    run: SubagentRun,
    result: AgentResult,
    emit,
    now,
    root_task_id_fn,
) -> None:
    """Emit a deterministic ``verifier_verdict`` event with full traceability.

    Args:
        state: live mission state (mutated to persist the verdict on the
            blackboard under ``verifierVerdicts``).
        task: verifier task that just produced ``result``.
        run: subagent run for the verifier.
        result: the verifier's structured result.
        emit: ``self._emit`` bound method on the controller.
        now: ``self.now`` callable returning a UTC datetime.
        root_task_id_fn: ``self._root_task_id`` bound method.
    """
    try:
        evidence = _extract_browser_evidence(result)
        deliverables = _verifier_deliverables(state, task, result)
        requires_browser = any(
            str(d.get("type") or "") in USER_RENDERABLE_DELIVERABLE_TYPES
            for d in deliverables
        )
        structural_failures: list[str] = []
        if requires_browser:
            if not evidence["browserVerifiedUrls"]:
                structural_failures.append("NO_USER_BROWSER_EVIDENCE")
            if evidence["loadingStuckObserved"]:
                structural_failures.append("REPORT_STUCK_LOADING")
            if evidence["errorsObserved"]:
                structural_failures.append("BROWSER_ERROR_OBSERVED")
            if evidence["browserVerifiedUrls"] and not evidence["visualsRendered"]:
                structural_failures.append("VISUALS_NOT_RENDERED")
        verifier_passed = (
            result.status == AgentResultStatus.SUCCESS
            and not result.followup_tasks
            and not result.errors
        )
        passed = verifier_passed and not structural_failures
        rationale_parts: list[str] = []
        if not verifier_passed:
            rationale_parts.append("verifier_returned_failure")
        rationale_parts.extend(structural_failures)
        decision_rationale = (
            "Verifier returned success and structural browser-evidence rubric satisfied."
            if passed
            else "Verifier verdict failed: " + ", ".join(rationale_parts or ["unknown"])
        )
        criteria = [
            "verifier returned success status",
            "verifier emitted no follow-up repair tasks",
            "verifier emitted no errors",
        ]
        if requires_browser:
            criteria.extend(
                [
                    "browser_verify_visual_render opened the user-facing URL",
                    'no "Loading your report..." stuck state observed',
                    "no error modal observed",
                    "visual elements rendered (visualsRendered=true)",
                ]
            )
        feedback_round = int(
            state.blackboard.get("verificationFeedbackLoops", {})
            .get(root_task_id_fn(state, task), {})
            .get("rounds")
            or 0
        )
        verdict_payload: dict[str, Any] = {
            "verdictId": f"verdict-{uuid.uuid4().hex[:12]}",
            "verifierRunId": run.id,
            "verifierTaskId": task.id,
            "verifierAgentId": run.agent_id,
            "targetTaskId": task.parent_task_id,
            "passed": passed,
            "verifierClaimedSuccess": verifier_passed,
            "structuralFailures": structural_failures,
            "requiresUserBrowserRender": requires_browser,
            "deliverables": deliverables,
            "evidence": evidence,
            "criteria": criteria,
            "decisionRationale": decision_rationale,
            "summary": bounded_text(result.summary, max_chars=700),
            "feedbackRound": feedback_round,
            "planStateSnapshot": _plan_state_snapshot(state),
            "timestampUtc": now().isoformat(),
        }
        verdicts = state.blackboard.setdefault("verifierVerdicts", {})
        target_key = task.parent_task_id or task.id
        verdicts.setdefault(target_key, []).append(verdict_payload)
        emit("verifier_verdict", state, **verdict_payload)
    except Exception:  # pragma: no cover - defensive: verdict must never crash run
        logger.exception("Failed to emit verifier_verdict event")


def _extract_browser_evidence(result: AgentResult) -> dict[str, Any]:
    urls: list[str] = []
    screenshot_paths: list[str] = []
    errors_observed: list[str] = []
    visuals_rendered = False
    loading_stuck = False
    expected_text_matched: bool | None = None
    for item in [*result.evidence, *result.artifacts]:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("toolName") or item.get("tool") or "").lower()
        kind = str(item.get("kind") or item.get("type") or "").lower()
        url = item.get("url") or item.get("finalUrl") or item.get("webUrl")
        screenshot = item.get("screenshotPath")
        text_sample = str(
            item.get("bodyTextSample") or item.get("textSample") or ""
        ).lower()
        visual_count_raw = item.get("visualLikeElementCount")
        if visual_count_raw is None:
            summary_block = item.get("visualSummary") if isinstance(item.get("visualSummary"), dict) else None
            if summary_block:
                visual_count_raw = (
                    summary_block.get("visibleVisualLikeElementCount")
                    or summary_block.get("visualLikeElementCount")
                )
        is_browser_evidence = (
            tool_name == "browser_verify_visual_render"
            or kind in {"browser_screenshot", "browser_visual_render"}
            or bool(url and screenshot)
        )
        if not is_browser_evidence:
            continue
        if isinstance(url, str):
            urls.append(url)
        if isinstance(screenshot, str):
            screenshot_paths.append(screenshot)
        try:
            if visual_count_raw is not None and int(visual_count_raw) > 0:
                visuals_rendered = True
        except (TypeError, ValueError):
            pass
        if any(p in text_sample for p in LOADING_STUCK_PATTERNS):
            loading_stuck = True
        if item.get("status") == "failed" or item.get("ok") is False:
            errors_observed.append(
                str(item.get("reason") or item.get("errorCode") or "browser_verification_failed")
            )
        if isinstance(item.get("expectedTextMatched"), bool):
            expected_text_matched = item.get("expectedTextMatched")
    return {
        "browserVerifiedUrls": list(dict.fromkeys(urls)),
        "screenshotPaths": list(dict.fromkeys(screenshot_paths)),
        "visualsRendered": visuals_rendered,
        "loadingStuckObserved": loading_stuck,
        "errorsObserved": errors_observed,
        "expectedTextMatched": expected_text_matched,
    }


def _verifier_deliverables(
    state: MissionState,
    task: TaskNode,
    result: AgentResult,
) -> list[dict[str, Any]]:
    deliverables: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    parent_id = task.parent_task_id
    producer_results = state.blackboard.get("taskResults", {}).get(parent_id or "", {})
    producer_artifacts = producer_results.get("artifacts") or []
    for artifact in [*producer_artifacts, *result.artifacts]:
        if not isinstance(artifact, dict):
            continue
        for nested in _nested_deliverables_from_artifact(artifact):
            nested_id = str(nested.get("id") or "")
            nested_type = str(nested.get("type") or "")
            key = (nested_type, nested_id or str(nested.get("name") or ""))
            if nested_type and key not in seen:
                seen.add(key)
                deliverables.append(nested)
        artifact_id = str(
            artifact.get("id")
            or artifact.get("itemId")
            or artifact.get("fabricItemId")
            or artifact.get("fabric_item_id")
            or ""
        )
        artifact_type = str(
            artifact.get("type")
            or artifact.get("kind")
            or artifact.get("entityType")
            or artifact.get("entity_type")
            or ""
        )
        if not artifact_type:
            continue
        key = (artifact_type, artifact_id or str(artifact.get("name") or artifact.get("entityName") or ""))
        if key in seen:
            continue
        seen.add(key)
        deliverables.append(
            {
                "id": artifact_id or None,
                "type": artifact_type,
                "name": artifact.get("name") or artifact.get("displayName") or artifact.get("entityName") or artifact.get("entity_name"),
                "webUrl": artifact.get("webUrl") or artifact.get("web_url") or artifact.get("url"),
            }
        )
    return deliverables


def _nested_deliverables_from_artifact(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    details = artifact.get("details")
    if not isinstance(details, str) or not details.strip():
        return []
    try:
        parsed = json.loads(details)
    except Exception:
        return []
    if not isinstance(parsed, dict):
        return []
    nested: list[dict[str, Any]] = []
    for item in parsed.get("createdItems") or parsed.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or item.get("itemType") or "")
        if not item_type:
            continue
        nested.append({
            "id": item.get("id") or item.get("itemId"),
            "type": item_type,
            "name": item.get("displayName") or item.get("name"),
            "webUrl": item.get("webUrl") or item.get("url"),
        })
    # Compact action details also carry top-level reportId / semanticModelId
    # even if the createdItems list is missing. Preserve the report id so the
    # verifier objective can still target the right artifact by GUID.
    if parsed.get("reportId"):
        nested.append({
            "id": parsed.get("reportId"),
            "type": "Report",
            "name": parsed.get("reportName") or "Report",
            "webUrl": parsed.get("reportWebUrl"),
        })
    return nested


def _plan_state_snapshot(state: MissionState) -> dict[str, Any]:
    statuses = [t.status.value for t in state.tasks.values()]
    budgets = state.blackboard.get("replanBudgets", {})
    return {
        "taskCountTotal": len(state.tasks),
        "taskCountByStatus": {s: statuses.count(s) for s in set(statuses)},
        "runningSubagentCount": sum(
            1 for r in state.subagent_runs.values() if r.status == SubagentStatus.RUNNING
        ),
        "replanBudgets": {
            root_id: {"budget": int(b.get("budget") or 0), "spent": int(b.get("spent") or 0)}
            for root_id, b in budgets.items()
        },
        "verificationFeedbackLoops": {
            root_id: int((loop or {}).get("rounds") or 0)
            for root_id, loop in state.blackboard.get("verificationFeedbackLoops", {}).items()
        },
    }
