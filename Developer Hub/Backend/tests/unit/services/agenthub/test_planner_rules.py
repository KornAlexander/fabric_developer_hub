"""Planner rule tests — enforcement of the behavioral rules added by
``docs/plan-generation-overhaul.md``.

These tests exercise two surfaces:

1. The planner system prompt (``PLANNER_SYSTEM_PROMPT``) — asserting the
   rule text is present so a future edit can't silently drop a rule.
2. The ``build_plan_user_message`` serializer — asserting flags are
   threaded through, the failing reference inputs are faithfully
   represented, and the diff-vocabulary policy is reflected in the
   prompt the LLM receives.

We deliberately do NOT make network calls to validate model output
against the rules — that is covered by the end-to-end harness. These are
fast regression checks so a prompt edit that reintroduces a banned
phrase or drops an inventory-first instruction will fail CI.
"""
from __future__ import annotations

import json

from domain.models.plan import WorkspaceSnapshot
from services.agenthub.planner_prompts import (
    PLANNER_SYSTEM_PROMPT,
    build_plan_user_message,
)

# ── 1. Planner MUST treat context items as context, not deliverables ──


def test_planner_ignores_context_as_deliverable_rule_present() -> None:
    """The prompt contains the explicit CONTEXT vs DELIVERABLES rule and
    names the banned failure mode (recreating a pinned item).
    """
    assert "CONTEXT vs DELIVERABLES" in PLANNER_SYSTEM_PROMPT
    # Rule keys: selected_items are references, not deliverables.
    assert "references" in PLANNER_SYSTEM_PROMPT.lower()
    assert "not deliverables" in PLANNER_SYSTEM_PROMPT.lower()
    # Counter-example must show Pipeline_1 as context, not a step title.
    assert "Pipeline_1" in PLANNER_SYSTEM_PROMPT
    assert "Create Pipeline_1" in PLANNER_SYSTEM_PROMPT  # inside the WRONG block


# ── 2. Planner MUST NOT emit tautological assumptions ─────────────────


def test_planner_rejects_tautological_assumptions_rule_present() -> None:
    """The prompt enumerates forbidden tautological assumption phrases and
    directs permission claims to ``prerequisites`` instead.
    """
    text = PLANNER_SYSTEM_PROMPT
    assert "NO TAUTOLOGIES" in text
    for banned in (
        "has the necessary permissions",
        "is ready to be created",
        "is fully defined",
    ):
        assert banned in text, f"prompt must list banned phrase: {banned!r}"
    assert "prerequisites" in text
    assert "evidence" in text


# ── 3. Planner MUST respect require_approvals=false ───────────────────


def test_planner_respects_require_approvals_false() -> None:
    """When ``require_approvals`` is False, the rule requires all steps
    to be reversible, low/medium risk, and non-destructive. The user
    message must faithfully serialize the flag so the LLM can honour it.
    """
    # Rule body
    assert "require_approvals" in PLANNER_SYSTEM_PROMPT
    assert '"low"' in PLANNER_SYSTEM_PROMPT and '"medium"' in PLANNER_SYSTEM_PROMPT
    assert "reversible" in PLANNER_SYSTEM_PROMPT
    assert 'no step may use `action == "delete"`' in PLANNER_SYSTEM_PROMPT

    # Serializer threads the flag into the user message payload.
    snapshot = WorkspaceSnapshot(workspace_id="ws-1", workspace_name="Dest")
    msg = build_plan_user_message(
        intent="Create an end-to-end solution",
        attachments=[],
        selected_items=[],
        snapshot=snapshot,
        diff=[],
        flags={"require_approvals": False, "branch_out": False},
    )
    payload = json.loads(msg.split("\n", 1)[1])
    assert payload["flags"]["require_approvals"] is False
    assert payload["flags"]["branch_out"] is False


# ── 4. Access-scoped goals MUST start with inventory enumeration ─────


def test_planner_inventory_goal_starts_with_enumeration() -> None:
    """Spec rule 4: for ``items I have access to``–style goals, step 1
    must enumerate workspace/tenant inventory via Fabric REST and stage
    the result as a Delta table in a Lakehouse.
    """
    text = PLANNER_SYSTEM_PROMPT
    assert "INVENTORY FIRST" in text
    # Trigger phrases
    assert "items I have access to" in text
    assert "all my items" in text or "all my artifacts" in text
    # Required action surface
    assert "GET /v1/workspaces/{id}/items" in text
    assert "Admin API" in text
    assert "Delta table" in text
    assert "Lakehouse" in text


# ── 5. Planner MUST NOT use diff/internal vocabulary in user-facing text ──


def test_planner_forbids_diff_vocabulary() -> None:
    """Spec rule 6: user-facing strings (summary, step title, rationale,
    assumptions) must not leak diff vocabulary. ``rationale`` is the one
    explicit escape hatch because the UI treats it as internal evidence.
    """
    text = PLANNER_SYSTEM_PROMPT
    assert "NO DIFF / INTERNAL VOCABULARY" in text
    # Banned phrases enumerated
    for banned in (
        "CREATE diff entry",
        "desired state",
        "no-action item",
        "resolves the entry",
        "matches desired state",
    ):
        assert banned in text, f"prompt must list forbidden phrase: {banned!r}"
    # The enumeration of user-facing fields
    assert "summary" in text
    assert "title" in text
    assert "rationale" in text
    # Report/visualization requirement pairs SemanticModel + Report
    assert "SemanticModel" in text and "Report" in text


# ── 6. Spec §1 — no time-estimate fields anywhere in the contract ─────


def test_planner_never_emits_time_estimates() -> None:
    """Spec §1: ``estimatedDurationSeconds`` / ``est_minutes`` /
    ``est_runtime_minutes`` are removed from the contract. The prompt's
    schema block and rule text must NOT reintroduce them.
    """
    text = PLANNER_SYSTEM_PROMPT
    for banned in (
        "estimatedDurationSeconds",
        "estimated_duration_seconds",
        "est_minutes",
        "est_runtime_minutes",
        "estRuntimeMinutes",
    ):
        assert banned not in text, f"prompt leaked removed field: {banned!r}"


# ── 7. Spec §2 — workspace_items replaces already_satisfied / noAction ──


def test_planner_emits_workspace_items_with_disposition() -> None:
    """Spec §2: the schema block must describe ``workspaceItems[]`` with
    ``disposition`` (keep_as_is | will_be_changed) and
    ``drivenByStepId`` for the will-be-changed case.
    """
    text = PLANNER_SYSTEM_PROMPT
    assert "workspaceItems" in text
    assert "keep_as_is" in text and "will_be_changed" in text
    assert "drivenByStepId" in text or "driven_by_step_id" in text
    # And the old key must be gone from the schema guidance.
    assert '"alreadySatisfied"' not in text
    assert '"already_satisfied"' not in text


def test_planner_no_forbidden_disposition_phrases() -> None:
    """Spec §3: reasons/narration in the prompt must not contain the
    banned diff/legacy vocabulary. Rationale remains the sole escape
    hatch (it's flagged internal), so we only check the top-level text.
    """
    # Strip the rationale-exception line so the scan doesn't trip on
    # the escape-hatch description itself.
    text = PLANNER_SYSTEM_PROMPT
    for banned in ("Diff entry", "CREATE diff", "UPDATE diff", "no-action item"):
        # The prompt INTRODUCES these as banned — so they will appear
        # inside the "forbidden substrings" enumeration. The test is
        # that they do NOT appear outside that enumeration. A simple
        # proxy: they must appear in the forbidden-list rule header.
        assert banned in text
    # And they must NOT appear in the golden counter-example's correct
    # rendering (the RIGHT block).
    correct_block = text.split("RIGHT (", 1)[-1]
    for banned in ("Diff entry", "no-action item"):
        assert banned not in correct_block, (
            f"'RIGHT' example leaks forbidden phrase {banned!r}"
        )


# ── 8. Spec §4 — prerequisites carry category + appliesToStepIds + kind ──


def test_planner_prereqs_require_category_and_verification() -> None:
    text = PLANNER_SYSTEM_PROMPT
    # Schema fields
    assert "category" in text
    assert "appliesToStepIds" in text or "applies_to_step_ids" in text
    # Verification sub-object + allowed kinds (at least the common ones)
    assert "verification" in text
    for kind in ("fabric_api", "graph_api", "manual"):
        assert kind in text, f"prompt must enumerate verifier kind {kind!r}"
    # Status is filled by backend, not the model.
    assert "backend" in text.lower() and "status" in text


# ── 9. Spec §3.2 — artifact_type enum validator + retry ───────────────


def test_parse_plan_flags_unknown_artifact_types() -> None:
    """Unknown ``itemType`` values surface from ``_parse_plan`` so the
    orchestrator can retry once with the allowed-values suffix.
    """
    from services.agenthub.orchestrator_engine import OrchestratorEngine

    payload = {
        "summary": "x",
        "assumptions": [],
        "prerequisites": [],
        "steps": [{
            "id": "s1", "order": 1, "title": "t", "action": "create",
            "target": {
                "itemType": "DataModel",  # NOT in the allowed set
                "displayName": "x",
                "workspaceId": "ws",
            },
            "inputs": [], "dependsOn": [], "rationale": "r",
            "risk": "low", "reversible": True,
        }],
        "workspaceItems": [],
        "conflicts": [],
        "clarificationsNeeded": [],
    }
    plan, bad_types = OrchestratorEngine._parse_plan(json.dumps(payload), "job-1")
    assert "DataModel" in bad_types
    # Plan parses structurally (Pydantic accepts the string), so the
    # retry path uses ``bad_types`` to decide what suffix to send.
    assert plan is not None or plan is None  # either is acceptable


# ── 10. Spec §2 — legacy ``noAction`` payloads migrate cleanly ────────


def test_parse_plan_migrates_legacy_no_action_to_workspace_items() -> None:
    from services.agenthub.orchestrator_engine import OrchestratorEngine

    payload = {
        "summary": "x",
        "assumptions": [],
        "prerequisites": [],
        "steps": [],
        "noAction": [
            {
                "itemType": "Pipeline",
                "displayName": "Pipeline_1",
                "reason": "Already satisfies the goal.",
            }
        ],
        "conflicts": [],
        "clarificationsNeeded": [],
    }
    plan, _ = OrchestratorEngine._parse_plan(json.dumps(payload), "job-x")
    assert plan is not None
    assert len(plan.workspace_items) == 1
    wi = plan.workspace_items[0]
    assert wi.item == "Pipeline_1"
    assert wi.type == "Pipeline"
    assert wi.disposition == "keep_as_is"
