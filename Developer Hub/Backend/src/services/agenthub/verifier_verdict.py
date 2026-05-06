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
import re
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

_FOLLOWUP_OBJECTIVE_MAX_CHARS = 4_000


def _clip_followup_objective(text: str) -> str:
    if len(text) <= _FOLLOWUP_OBJECTIVE_MAX_CHARS:
        return text
    return text[: _FOLLOWUP_OBJECTIVE_MAX_CHARS - 3].rstrip() + "..."

USER_RENDERABLE_DELIVERABLE_TYPES = {"Report", "PowerBIReport", "Dashboard", "PaginatedReport"}
LOADING_STUCK_PATTERNS = (
    "loading your report",
    "almost done",
    "loading data",
    "please wait",
)
QUALITY_REVIEW_STEP_PATTERNS = (
    "quality",
    "design",
    "usability",
    "semantic model",
    "data model",
    "maintainability",
    "extensibility",
    "performance",
    "code cleanliness",
)
QUALITY_REVIEW_REQUIRED_ASPECTS = {
    "naming_convention": (
        "naming",
        "name convention",
        "naming convention",
        "workspace convention",
        "display name",
        "artifact name",
    ),
    "style_theme": (
        "style",
        "styling",
        "theme",
        "modern",
        "visual polish",
        "polished",
        "design language",
    ),
    "championship_storytelling": (
        "championship",
        "world championship",
        "data stories",
        "3-30-300",
        "3 30 300",
        "three second",
        "30-second",
        "300-second",
        "top-left overview",
        "filter and zoom",
        "details on demand",
        "details-on-demand",
        "story flow",
        "storytelling",
    ),
    "accessibility": (
        "accessibility",
        "accessible",
        "alt text",
        "contrast",
        "keyboard",
        "tab order",
        "screen reader",
        "color-only",
        "colour-only",
    ),
    "software_engineering": (
        "software engineering",
        "best practice",
        "clean code",
        "code cleanliness",
        "class",
        "classes",
        "function",
        "functions",
        "maintainability",
        "maintainable",
        "extensibility",
        "extensible",
        "readability",
        "readable",
        "error handling",
        "fail fast",
        "runtime errors",
        "exception",
        "warnings",
    ),
}


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
    step_results = _extract_step_results(result)
    deliverables = _verifier_deliverables(state, task, result)
    requires_browser = any(_is_user_renderable_deliverable(d) for d in deliverables)
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
        failures.extend(_professional_quality_review_failures(state, task, step_results))
    return (not failures, failures, evidence, deliverables)


def synthesize_browser_evidence_followup(
    deliverables: list[dict[str, Any]],
    structural_failures: list[str],
) -> FollowupTask:
    """Build a repair task that re-runs verifier with mandatory browser evidence."""
    deliverable_lines = []
    for d in deliverables:
        if not _is_user_renderable_deliverable(d):
            continue
        deliverable_lines.append(
            f"- {d.get('type')} '{d.get('name') or d.get('id')}' webUrl={d.get('webUrl') or '(missing)'}"
        )
    deliverable_block = "\n".join(deliverable_lines) or "(no user-renderable deliverables found)"
    return FollowupTask(
        title="Re-verify deliverable with mandatory browser evidence",
        objective=_clip_followup_objective(
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
        if _is_user_renderable_deliverable(deliverable)
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
        objective=_clip_followup_objective(
            "Independently verify the produced Fabric/Power BI deliverables before the mission can finish. "
            "Do not trust the producer summary.\n\n"
            "Deliverables produced upstream:\n"
            + deliverable_block
            + "\n\n"
            "**STEP-BY-STEP VERIFICATION** — decompose the original user goal into discrete phases "
            "(typical phases for an end-to-end Fabric solution: 1) data ingestion writes Delta tables, "
            "2) transformation runs without errors, 3) semantic model is bound and returns the expected "
            "data when queried with DAX, 4) report renders the expected visuals in the user's browser). "
            "For each phase emit a structured ``stepResults`` evidence entry with fields "
            "``{step, status: passed|failed|not_applicable, evidence, detail}``. Use the appropriate "
            "tools per step:\n"
            "  * ingestion → fabric_list_lakehouse_tables (must list the expected tables)\n"
            "  * transformation → fabric_get_notebook_run (status=Completed, exitValue OK)\n"
            "  * semantic model refresh → call fabric_get_semantic_model_refresh_history "
            "(or sl_get_refresh_history) and read the most recent refresh entry. If status "
            "is 'Failed', extract serviceExceptionJson.errorDescription verbatim into "
            "stepResults[].detail — common errors include 0xC14700C7 ('We cannot access "
            "the source Delta table ...') which means the lakehouse table name does not "
            "match what the semantic model references. Refresh status MUST be 'Completed' "
            "for this step to pass.\n"
            "  * semantic model data → sl_run_dax_query / fabric_query_semantic_model (must return "
            "rows from the bound table — empty/error responses indicate the model binding is broken; "
            "this is the most common silent failure with DirectLake)\n"
            "  * DirectLake identity/permissions → inspect the upstream inventory tool's "
            "directLakeIdentityDiagnostics and/or list the workspace items/users. If the "
            "SemanticModel owner/effective identity differs from the Lakehouse/SQL endpoint "
            "owner (for example a workload principal such as 'Fabric ClawHub' owns the model "
            "while the user's Lakehouse owns the Delta table), fail this step unless DAX data "
            "validation proves the model is queryable. Surface the owner names verbatim.\n"
            "  * report render → browser_verify_visual_render against every Report/Dashboard webUrl. "
            "Reject if browser lands on error page, stays on 'Loading your report...', or shows zero "
            "rendered visuals.\n\n"
            "  * professional quality review → inspect the created report definition, semantic model, "
            "notebook/data path, inferred naming convention, and rendered browser evidence. This step must judge report design/usability, "
            "Power BI Data Stories / world-championship-caliber storytelling, 3-30-300 flow (top-left overview, filter and zoom, details on demand), "
            "visual usefulness, accessibility (alt text, contrast, keyboard/tab order, no color-only meaning), semantic-model clarity, data persistence/freshness, performance risk, code "
            "cleanliness, naming convention fit, default-or-requested style/theme quality, maintainability, and extensibility. For any generated code, explicitly inspect software-engineering quality: classes/functions or equivalent decomposition, readability, extensibility, proper error handling, warning/error surfacing, and fail-fast behavior when outputs are empty or unverified. Fail this step for one-card proof-of-life reports, "
            "hardcoded snapshot models when persisted data exists, unclear measures, brittle notebook logic, swallowed exceptions, false-success paths, "
            "raw temporary-looking item names, default-looking/cramped/single-hue visuals, or visuals that technically render but would not be considered good professional work.\n\n"
            "ALSO inspect the upstream tool result (e.g. inventory tool response). Any string in "
            "its ``errors`` array — particularly entries containing 'refresh FAILED', "
            "'Delta table', 'access permissions', or 0x14700/C14700 hex codes — MUST be surfaced "
            "verbatim in stepResults[].detail and counted as a failed step. Do not mark a "
            "mission successful while such errors are present, even if the report URL loads.\n\n"
            "GENERIC DIAGNOSTIC DUTY — for any failed, partial, blocked, warning-heavy, or suspicious "
            "upstream result, perform root-cause diagnostics before writing the verdict. Use read-only "
            "tools such as fabric_diagnose_workspace_artifacts, fabric_list_items/get_item, workspace roles, "
            "capacity checks, refresh/job/operation history, definitions/bindings, DAX/table queries, and browser "
            "evidence. Classify the failing layer as schema, binding, data missing, owner/permission, capacity/quota, "
            "catalog propagation, browser/render, service bug, or unknown. Surface the concrete evidence; do not only "
            "state that the item is broken.\n\n"
            "Your AgentResult.evidence MUST include:\n"
            "  * The browser verification JSON (finalUrl, screenshotPath, bodyTextSample, visualSummary)\n"
            "  * A ``stepResults`` array (one entry per phase) so the orchestrator can see exactly "
            "which steps passed and which failed. Include a step whose name or detail contains "
            "'professional quality review' and explicitly mentions naming convention fit, style/theme quality, championship/3-30-300 storytelling, accessibility, and generated-code/software-engineering quality; "
            "the deterministic verdict fails if this review or any required aspect is missing.\n\n"
            "Original user goal:\n"
            + original_goal[:1800]
        ),
        candidate_agent_ids=["fabric-verifier"],
        required_capabilities=["fabric-verification"],
        delegation_reason="Mandatory verifier gate for produced user-facing Fabric/Power BI artifacts.",
        context_summary="Orchestrator-scheduled final verification gate; run this verifier task normally and return per-step evidence.",
        acceptance_criteria=[
            "Verifier decomposes the goal into discrete steps and reports per-step pass/fail in evidence.stepResults.",
            "Verifier independently queries the semantic model with DAX (does not trust the producer's claim that the model is queryable).",
            "Verifier calls browser_verify_visual_render for every Report/Dashboard webUrl.",
            "Verifier returns browser evidence with screenshotPath, finalUrl, and visual render signals.",
            "Verifier includes and passes a professional quality review step covering report design/usability, naming convention fit, default-or-requested style/theme quality, championship/3-30-300 storytelling, accessibility, semantic model, generated-code/software-engineering quality, proper error handling, performance, maintainability, and extensibility.",
            "Mission may finish only if EVERY step in stepResults reports status=passed.",
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
        step_results = _extract_step_results(result)
        deliverables = _verifier_deliverables(state, task, result)
        requires_browser = any(_is_user_renderable_deliverable(d) for d in deliverables)
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
            structural_failures.extend(_professional_quality_review_failures(state, task, step_results))
        # Step-level failures: if the verifier explicitly reported any
        # phase as failed, surface that as a distinct structural
        # failure so the orchestrator and UI can show *which* phase
        # broke (e.g. SEMANTIC_MODEL_NOT_QUERYABLE) instead of just
        # "verifier verdict failed".
        failed_steps = [
            row["step"] for row in step_results
            if row.get("status") in {"failed", "error", "broken"}
        ]
        for step in failed_steps:
            tag = re.sub(r"[^A-Za-z0-9]+", "_", step).strip("_").upper()
            if tag:
                structural_failures.append(f"STEP_FAILED:{tag}")
        browser_render_satisfied = (
            requires_browser
            and bool(evidence["browserVerifiedUrls"])
            and evidence["visualsRendered"]
            and not evidence["loadingStuckObserved"]
            and not evidence["errorsObserved"]
        )
        followups_block_verdict = bool(result.followup_tasks) and not browser_render_satisfied
        verifier_passed = (
            result.status == AgentResultStatus.SUCCESS
            and not followups_block_verdict
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
            "verifier emitted no follow-up repair tasks unless browser-render evidence already satisfied the structural rubric",
            "verifier emitted no errors",
        ]
        if requires_browser:
            criteria.extend(
                [
                    "browser_verify_visual_render opened the user-facing URL",
                    'no "Loading your report..." stuck state observed',
                    "no error modal observed",
                    "visual elements rendered (visualsRendered=true)",
                    "professional quality review passed for report design, naming convention fit, style/theme quality, championship/3-30-300 storytelling, accessibility, data/model/generated-code quality, proper error handling, performance, maintainability, and extensibility",
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
            "stepResults": step_results,
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
    for item in _iter_verdict_items(result):
        tool_name = str(item.get("toolName") or item.get("tool") or "").lower()
        kind = str(item.get("kind") or item.get("type") or "").lower()
        entity_type = str(item.get("entityType") or item.get("entity_type") or "").lower()
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
            or entity_type == "browser_visual_render"
            or bool(url and screenshot)
        )
        if not is_browser_evidence:
            continue
        if isinstance(url, str):
            urls.append(url)
        if isinstance(screenshot, str):
            screenshot_paths.append(screenshot)
        item_visual_count = 0
        try:
            if visual_count_raw is not None:
                item_visual_count = int(visual_count_raw)
                if item_visual_count > 0:
                    visuals_rendered = True
        except (TypeError, ValueError):
            pass
        status = str(item.get("status") or "").lower()
        # Stuck-loading detection: "Loading your report..." in scrapeable
        # text means Power BI's report shell painted but the visual never
        # rendered. Even if visualSummary reports a positive
        # visibleVisualLikeElementCount, that count usually only includes
        # the empty visual container. Require BOTH the loading text to be
        # absent AND a non-trivial number of rendered visual-like elements
        # to consider the render successful. We deliberately do NOT treat
        # ok/status=passed from browser_verify_visual_render as authoritative
        # over this signal: a 200-OK page with the spinner still visible is
        # still a broken report from the user's perspective.
        if any(p in text_sample for p in LOADING_STUCK_PATTERNS):
            loading_stuck = True
        if status == "failed" or item.get("ok") is False:
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


def _professional_quality_review_status(step_results: list[dict[str, Any]]) -> str:
    """Return passed, failed, or missing for the verifier quality gate."""
    quality_rows: list[dict[str, Any]] = []
    for row in step_results:
        text = " ".join(
            str(row.get(key) or "")
            for key in ("step", "detail", "evidence", "reason")
        ).lower()
        if any(pattern in text for pattern in QUALITY_REVIEW_STEP_PATTERNS):
            quality_rows.append(row)
    if not quality_rows:
        return "missing"
    if any(str(row.get("status") or "").lower() in {"failed", "error", "broken"} for row in quality_rows):
        return "failed"
    if any(str(row.get("status") or "").lower() == "passed" for row in quality_rows):
        return "passed"
    return "missing"


def _professional_quality_review_missing_aspects(step_results: list[dict[str, Any]]) -> list[str]:
    quality_text = "\n".join(
        " ".join(
            str(row.get(key) or "")
            for key in ("step", "detail", "evidence", "reason")
        ).lower()
        for row in step_results
        if any(
            pattern in " ".join(
                str(row.get(key) or "")
                for key in ("step", "detail", "evidence", "reason")
            ).lower()
            for pattern in QUALITY_REVIEW_STEP_PATTERNS
        )
    )
    if not quality_text:
        return []
    return [
        aspect
        for aspect, patterns in QUALITY_REVIEW_REQUIRED_ASPECTS.items()
        if not any(pattern in quality_text for pattern in patterns)
    ]


def _professional_quality_review_failures(
    state: MissionState,
    task: TaskNode,
    step_results: list[dict[str, Any]],
) -> list[str]:
    failures: list[str] = []
    quality_status = _professional_quality_review_status(step_results)
    upstream_quality = _upstream_professional_quality_review(state, task)
    if quality_status == "missing":
        if upstream_quality.get("status") != "passed":
            failures.append("NO_PROFESSIONAL_QUALITY_REVIEW")
    elif quality_status == "failed":
        failures.append("PROFESSIONAL_QUALITY_REVIEW_FAILED")

    missing_aspects = _professional_quality_review_missing_aspects(step_results)
    if upstream_quality.get("status") == "passed":
        upstream_aspects = set(upstream_quality.get("aspects") or [])
        missing_aspects = [aspect for aspect in missing_aspects if aspect not in upstream_aspects]
    for aspect in missing_aspects:
        failures.append(f"QUALITY_REVIEW_MISSING:{aspect.upper()}")
    return failures


def _upstream_professional_quality_review(state: MissionState, task: TaskNode) -> dict[str, Any]:
    """Return quality proof embedded in the producer's structured result.

    Fabric creation tools can emit deterministic quality validation that is
    stronger than a verifier's prose. The verifier still has to open the
    user-facing artifact in a browser, but naming/style rubric checks can be
    satisfied by those structured producer proofs to avoid endless re-verifying
    when the rendered report is already good.
    """
    task_results = state.blackboard.get("taskResults", {})
    aspects: set[str] = set()
    status: str | None = None

    upstream_task_ids = _upstream_task_result_ids(state, task)
    # Prefer the task ancestry, then fall back to all task results. The fallback
    # covers verifier follow-up chains where each verifier is parented by the
    # previous verifier, while the original producer proof remains elsewhere on
    # the blackboard.
    ordered_task_ids = [
        *upstream_task_ids,
        *(task_id for task_id in task_results if task_id not in upstream_task_ids),
    ]
    for task_id in ordered_task_ids:
        producer_results = task_results.get(task_id, {})
        if not isinstance(producer_results, dict):
            continue
        for item in [*(producer_results.get("artifacts") or []), *(producer_results.get("evidence") or [])]:
            if not isinstance(item, dict):
                continue
            for candidate in _quality_context_candidates(item):
                quality = candidate.get("qualityValidation")
                quality_status = ""
                if isinstance(quality, dict):
                    quality_status = str(quality.get("status") or "").lower()
                text = _quality_context_text(candidate)
                if quality_status == "passed":
                    status = "passed"
                    aspects.update(_structured_quality_aspects(candidate, quality))
                for aspect, patterns in QUALITY_REVIEW_REQUIRED_ASPECTS.items():
                    if any(pattern in text for pattern in patterns):
                        aspects.add(aspect)
                if quality_status == "failed" and status != "passed":
                    status = "failed"
    return {"status": status, "aspects": sorted(aspects)}


def _upstream_task_result_ids(state: MissionState, task: TaskNode) -> list[str]:
    task_ids: list[str] = []
    seen: set[str] = set()
    current_id = task.parent_task_id
    while current_id and current_id not in seen:
        seen.add(current_id)
        task_ids.append(current_id)
        current = state.tasks.get(current_id)
        current_id = current.parent_task_id if current is not None else None
    return task_ids


def _structured_quality_aspects(candidate: dict[str, Any], quality: Any) -> set[str]:
    aspects: set[str] = set()
    if not isinstance(quality, dict):
        return aspects
    if isinstance(candidate.get("namingConvention"), dict) or candidate.get("folderName") or candidate.get("createdItems"):
        aspects.add("naming_convention")
    report_quality = quality.get("report")
    if isinstance(report_quality, dict) and str(report_quality.get("status") or "").lower() == "passed":
        aspects.add("style_theme")
        report_checks = report_quality.get("checks") or []

        def report_check_passed(name: str) -> bool:
            return any(
                isinstance(check, dict)
                and check.get("name") == name
                and check.get("passed") is True
                for check in report_checks
            )

        if report_quality.get("storyFlow3_30_300") is True or any(
            isinstance(check, dict)
            and check.get("name") == "report_follows_3_30_300_story_flow"
            and check.get("passed") is True
            for check in report_checks
        ):
            aspects.add("championship_storytelling")
        if (
            (report_quality.get("accessibilityMetadata") is True or report_check_passed("report_has_accessibility_alt_text_and_titles"))
            and (report_quality.get("guidedTabOrder") is True or report_check_passed("report_has_guided_keyboard_tab_order"))
            and (report_quality.get("highContrastCanvas") is True or report_check_passed("report_has_high_contrast_canvas"))
        ):
            aspects.add("accessibility")
    notebook_quality = quality.get("notebookCode")
    if isinstance(notebook_quality, dict) and str(notebook_quality.get("status") or "").lower() == "passed":
        aspects.add("software_engineering")
    return aspects


def _quality_context_candidates(item: dict[str, Any]):
    yield item
    parsed = _parsed_details(item)
    if parsed is not None:
        yield parsed
    for action in item.get("actions") or []:
        if not isinstance(action, dict):
            continue
        yield action
        parsed_action = _parsed_details(action)
        if parsed_action is not None:
            yield parsed_action


def _quality_context_text(candidate: dict[str, Any]) -> str:
    try:
        raw = json.dumps(candidate, sort_keys=True, default=str)
    except Exception:
        raw = str(candidate)
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw).replace("_", " ")
    return f"{raw}\n{expanded}".lower()


def _extract_step_results(result: AgentResult) -> list[dict[str, Any]]:
    """Pull the verifier's per-step pass/fail breakdown from evidence.

    The verifier task is asked to decompose the original goal into
    discrete phases (ingestion, transformation, semantic-model
    queryability, report render, ...) and emit a ``stepResults`` array
    in evidence so the orchestrator can show the user *which* phases
    passed and which failed instead of a single opaque "passed/failed"
    verdict.

    Accepted shapes (any one is enough):
      * Top-level evidence/artifact dict containing
        ``"stepResults": [{"step": ..., "status": ...}]``.
      * A ``"step"`` field directly on an evidence/artifact item.
    """
    rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    def _normalise(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        step = str(raw.get("step") or raw.get("name") or raw.get("phase") or "").strip()
        if not step:
            return None
        status = str(raw.get("status") or raw.get("result") or "").strip().lower() or "unknown"
        normalised: dict[str, Any] = {"step": step, "status": status}
        for key in ("detail", "evidence", "reason", "via", "rowCount", "url", "exitValue"):
            if raw.get(key) not in (None, "", []):
                normalised[key] = raw.get(key)
        return normalised

    for item in _iter_verdict_items(result):
        bundle = item.get("stepResults") if isinstance(item, dict) else None
        candidates: list[Any] = []
        if isinstance(bundle, list):
            candidates.extend(bundle)
        else:
            candidates.append(item)
        for candidate in candidates:
            normalised = _normalise(candidate)
            if normalised is None:
                continue
            key = (normalised["step"].lower(), normalised["status"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            rows.append(normalised)
    return rows


def _verifier_deliverables(
    state: MissionState,
    task: TaskNode,
    result: AgentResult,
) -> list[dict[str, Any]]:
    deliverables: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    scheduled = state.blackboard.get("mandatoryVerifierScheduledForTasks", {})
    task_results = state.blackboard.get("taskResults", {})
    related_task_ids = _related_task_result_ids(state, task, task_results)
    for task_id in related_task_ids:
        scheduled_deliverables = (scheduled.get(task_id) or {}).get("deliverables") or []
        for deliverable in scheduled_deliverables:
            if isinstance(deliverable, dict):
                _append_deliverable(deliverables, seen, deliverable)
    for task_id in related_task_ids:
        producer_results = task_results.get(task_id, {})
        if isinstance(producer_results, dict):
            _append_deliverables_from_items(
                deliverables,
                seen,
                [
                    *(producer_results.get("artifacts") or []),
                    *(producer_results.get("evidence") or []),
                ],
            )
    _append_deliverables_from_items(deliverables, seen, [*result.artifacts, *result.evidence])
    if not any(_is_user_renderable_deliverable(deliverable) for deliverable in deliverables):
        evidence = _extract_browser_evidence(result)
        for url in evidence["browserVerifiedUrls"]:
            if not isinstance(url, str):
                continue
            lowered_url = url.lower()
            if "/reports/" in lowered_url:
                inferred_type = "Report"
            elif "/dashboards/" in lowered_url:
                inferred_type = "Dashboard"
            else:
                continue
            _append_deliverable(deliverables, seen, {
                "type": inferred_type,
                "name": f"Browser verified {inferred_type.lower()}",
                "webUrl": url,
            })
    return deliverables


def _related_task_result_ids(
    state: MissionState,
    task: TaskNode,
    task_results: dict[str, Any],
) -> list[str]:
    related: list[str] = []
    seen: set[str] = set()

    def visit(task_id: str | None) -> None:
        if not task_id or task_id in seen:
            return
        seen.add(task_id)
        related.append(task_id)
        candidate = state.tasks.get(task_id)
        if candidate is None:
            return
        for dependency_id in candidate.dependencies:
            visit(dependency_id)
        visit(candidate.parent_task_id)

    visit(task.parent_task_id)
    for dependency_id in task.dependencies:
        visit(dependency_id)
    # Verifier tasks are often parented by the generalist while the real Fabric
    # producer is a sibling/follow-up task. The deterministic verdict must still
    # inherit those producer artifacts; otherwise a verified inventory solution
    # can hide the nested Report and incorrectly bypass browser evidence.
    for task_id in task_results:
        if task_id not in seen:
            related.append(task_id)
            seen.add(task_id)
    return related


def _append_deliverables_from_items(
    deliverables: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    items: list[Any],
) -> None:
    for artifact in items:
        if not isinstance(artifact, dict):
            continue
        for nested in _nested_deliverables_from_artifact(artifact):
            _append_deliverable(deliverables, seen, nested)
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
        _append_deliverable(deliverables, seen, {
            "id": artifact_id or None,
            "type": artifact_type,
            "name": artifact.get("name") or artifact.get("displayName") or artifact.get("entityName") or artifact.get("entity_name"),
            "webUrl": artifact.get("webUrl") or artifact.get("web_url") or artifact.get("url"),
        })


def _nested_deliverables_from_artifact(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = _parsed_details(artifact)
    if parsed is None:
        return []
    nested: list[dict[str, Any]] = []
    for item in parsed.get("createdItems") or parsed.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_type = _normalise_deliverable_type(item.get("type") or item.get("itemType"))
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


def _iter_verdict_items(result: AgentResult):
    for item in [*result.evidence, *result.artifacts]:
        if not isinstance(item, dict):
            continue
        yield item
        details = _parsed_details(item)
        if details is not None:
            yield {**details, **{key: value for key, value in item.items() if key not in details}}
        for action in item.get("actions") or []:
            if not isinstance(action, dict):
                continue
            yield action
            action_details = _parsed_details(action)
            if action_details is not None:
                yield {**action_details, **{key: value for key, value in action.items() if key not in action_details}}


def _parsed_details(item: dict[str, Any]) -> dict[str, Any] | None:
    details = item.get("details")
    if isinstance(details, dict):
        return details
    if not isinstance(details, str) or not details.strip():
        return None
    try:
        parsed = json.loads(details)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _append_deliverable(
    deliverables: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    raw: dict[str, Any],
) -> None:
    deliverable_type = _normalise_deliverable_type(raw.get("type") or raw.get("itemType") or raw.get("entityType"))
    if not deliverable_type:
        return
    if deliverable_type.lower() in {"browser_visual_render", "browser_visual"}:
        return
    deliverable_id = str(raw.get("id") or raw.get("itemId") or raw.get("fabricItemId") or "")
    name = raw.get("name") or raw.get("displayName") or raw.get("entityName") or raw.get("entity_name")
    key = (deliverable_type, deliverable_id or str(name or ""))
    if key in seen:
        return
    seen.add(key)
    deliverables.append({
        "id": deliverable_id or None,
        "type": deliverable_type,
        "name": name,
        "webUrl": raw.get("webUrl") or raw.get("web_url") or raw.get("url"),
    })


def _normalise_deliverable_type(raw_type: Any) -> str:
    value = str(raw_type or "").strip()
    lowered = value.lower()
    if lowered in {"powerbireport", "power bi report", "report"}:
        return "Report"
    if lowered in {"dashboard", "powerbidashboard", "power bi dashboard"}:
        return "Dashboard"
    if lowered in {"paginatedreport", "paginated report"}:
        return "PaginatedReport"
    return value


def _is_user_renderable_deliverable(deliverable: dict[str, Any]) -> bool:
    return _normalise_deliverable_type(deliverable.get("type")) in USER_RENDERABLE_DELIVERABLE_TYPES


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
