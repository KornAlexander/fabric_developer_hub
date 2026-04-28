"""Unit tests for ``services.agenthub.compose_service`` helpers.

Focus: the post-parse normalisation step that enforces the sequential
uniqueness rule the system prompt gives the LLM. A correct composer
never emits two sequential slots with the same ``agentId`` because one
agent handles its whole phase. Some LLMs ignore this rule and return
duplicate slots anyway — the helper collapses them back into one slot,
merges roles + skills, and rewrites handoffs.

The full compose flow (LLM call + parse) is integration-territory and
is exercised in the broader test suite; here we pin the collapse
helper specifically so future composer changes don't silently regress
the dedupe invariant.
"""
from __future__ import annotations

import json

import pytest

from domain.models.composition import AgentSlot, Handoff, SkillRef
from services.agenthub.agent_registry import AGENT_TEMPLATES
from services.agenthub.compose_service import (
    ComposeService,
    CompositionError,
    _collapse_duplicate_agent_slots,
    _make_quality_slot,
)


def _slot(slot_id: str, agent_id: str, role: str, skills: list[str] | None = None) -> AgentSlot:
    return AgentSlot(
        id=slot_id,
        agent_id=agent_id,
        role=role,
        skills=[SkillRef(id=s, name=s) for s in (skills or [])],
    )


def _handoff(frm: str, to: str, kind: str = "report") -> Handoff:
    return Handoff.model_validate({"from": frm, "to": to, "kind": kind, "condition": None})


def test_collapse_noop_when_all_unique() -> None:
    slots = [
        _slot("s1", "architect", "Design"),
        _slot("s2", "modeler", "Blueprint"),
        _slot("s3", "fabric-data-engineer", "Build"),
    ]
    handoffs = [_handoff("s1", "s2"), _handoff("s2", "s3")]
    got_slots, got_hs, got_entry = _collapse_duplicate_agent_slots(
        slots, handoffs, entrypoint="s1",
    )
    # No duplicates → returns the original refs unchanged.
    assert got_slots is slots
    assert got_entry == "s1"
    assert len(got_hs) == 2


def test_quality_verifier_slot_prefers_fabric_verifier_super_skill() -> None:
    slot = _make_quality_slot(
        "final-verifier",
        AGENT_TEMPLATES,
        visual=True,
        verifier=True,
        role="Final verifier: check created Fabric report and data.",
        fallback_agent_id="modeler",
    )

    assert slot.agent_id == "fabric-verifier"
    assert [skill.id for skill in slot.skills] == ["fabric-verification"]


def test_quality_reviewer_still_uses_modeler_visual_review_skill() -> None:
    slot = _make_quality_slot(
        "quality-review",
        AGENT_TEMPLATES,
        visual=True,
        verifier=False,
        role="Quality reviewer: review report visual clarity.",
        fallback_agent_id="fabric-data-engineer",
    )

    assert slot.agent_id == "modeler"
    skill_ids = [skill.id for skill in slot.skills]
    assert "powerbi-ibcs" in skill_ids
    assert "fabric-verification" in skill_ids


def test_collapse_merges_two_fabric_data_engineer_slots() -> None:
    """Regression: the composer sometimes emits two FDE slots back-to-
    back ("ingestion" + "transformation"). That splits one continuous
    phase across two disjoint agent loops. The helper must merge them.
    """
    slots = [
        _slot("s1", "fabric-admin", "Provision workspace"),
        _slot("s2", "fabric-data-engineer", "Ingest data", skills=["spark-authoring-cli"]),
        _slot("s3", "fabric-data-engineer", "Transform + report", skills=["powerbi-authoring-cli"]),
    ]
    handoffs = [_handoff("s1", "s2"), _handoff("s2", "s3")]
    got_slots, got_hs, got_entry = _collapse_duplicate_agent_slots(
        slots, handoffs, entrypoint="s1",
    )

    # Two FDE slots collapse to one.
    assert len(got_slots) == 2
    agent_ids = [s.agent_id for s in got_slots]
    assert agent_ids == ["fabric-admin", "fabric-data-engineer"]

    # Roles concatenate so no context is lost.
    fde = got_slots[1]
    assert "Ingest data" in fde.role
    assert "Transform + report" in fde.role

    # Skills from both duplicates are merged, deduplicated.
    skill_ids = {sk.id for sk in fde.skills}
    assert skill_ids == {"spark-authoring-cli", "powerbi-authoring-cli"}

    # Handoff s1→s2 survives; the self-loop created by s2→s3
    # (both remapped to s2) is dropped.
    assert len(got_hs) == 1
    assert (got_hs[0].from_, got_hs[0].to) == ("s1", "s2")

    # Entrypoint still resolves to an existing slot.
    assert got_entry == "s1"


def test_collapse_remaps_entrypoint_when_pointing_to_merged_duplicate() -> None:
    """If the LLM marked the second duplicate as the entrypoint,
    remap it to the surviving slot instead of leaving a dangling id.
    """
    slots = [
        _slot("s1", "fabric-data-engineer", "First pass"),
        _slot("s2", "fabric-data-engineer", "Second pass"),
    ]
    got_slots, _, got_entry = _collapse_duplicate_agent_slots(
        slots, handoffs=[], entrypoint="s2",
    )
    assert len(got_slots) == 1
    assert got_entry == "s1"


def test_collapse_dedupes_duplicate_handoffs_after_remap() -> None:
    """When two separate edges collapse onto the same (from, to, kind)
    triple after remapping, keep only one — otherwise the graph
    renders parallel overlapping curves the frontend already fixed
    once for delegate+report round-trips.
    """
    slots = [
        _slot("s1", "architect", "Design"),
        _slot("s2", "modeler", "Blueprint A"),
        _slot("s3", "modeler", "Blueprint B"),
    ]
    handoffs = [_handoff("s1", "s2"), _handoff("s1", "s3")]
    got_slots, got_hs, _ = _collapse_duplicate_agent_slots(
        slots, handoffs, entrypoint="s1",
    )
    assert len(got_slots) == 2
    # Both s1→s2 and s1→s3 remap to s1→s2; keep one.
    assert len(got_hs) == 1
    assert (got_hs[0].from_, got_hs[0].to) == ("s1", "s2")


def test_parse_normalizes_legacy_solo_report_to_dynamic_seed() -> None:
    svc = ComposeService(agents=AGENT_TEMPLATES)
    raw = json.dumps({
        "architecture": "solo",
        "rationale": "One builder can create the report.",
        "headline": "Create the report.",
        "subtitle": "Single pass.",
        "slots": [{
            "id": "builder",
            "agentId": "fabric-data-engineer",
            "role": "Create the Power BI report page",
            "skills": [{"id": "powerbi-authoring-cli", "name": "Power BI authoring"}],
        }],
        "handoffs": [],
        "entrypointSlotId": "builder",
        "budget": {"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": True},
    })
    comp = svc._parse(
        raw,
        session_id="s-report",
        task=(
            "Create one Power BI report page with aligned visuals, clean "
            "variance styling, and a professional leadership-ready layout."
        ),
        require_approvals=True,
    )

    assert comp.architecture == "dynamic"
    assert [s.id for s in comp.slots] == ["generalist"]
    assert comp.slots[0].agent_id == "generalist"
    assert comp.handoffs == []


def test_parse_preserves_explicit_single_agent_artifact_request() -> None:
    svc = ComposeService(agents=AGENT_TEMPLATES)
    raw = json.dumps({
        "architecture": "solo",
        "rationale": "The user explicitly requested one agent.",
        "headline": "Create one notebook.",
        "subtitle": "Single agent only.",
        "slots": [{
            "id": "builder",
            "agentId": "fabric-data-engineer",
            "role": "Create the notebook shell and summarize the result",
            "skills": [],
        }],
        "handoffs": [],
        "entrypointSlotId": "builder",
        "budget": {"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": True},
    })

    comp = svc._parse(
        raw,
        session_id="s-solo-explicit",
        task=(
            "Within only this workspace, complete a single-agent artifact creation task. "
            "Do not delegate, branch, supervise, debate, or add any extra agents. "
            "Create one notebook item shell and return the item id."
        ),
        require_approvals=True,
    )

    assert comp.architecture == "dynamic"
    assert [s.id for s in comp.slots] == ["generalist"]
    assert comp.handoffs == []


def test_parse_normalizes_multidomain_report_to_dynamic_seed() -> None:
    svc = ComposeService(agents=AGENT_TEMPLATES)
    raw = json.dumps({
        "architecture": "supervisor",
        "rationale": "Coordinator delegates the build.",
        "headline": "Build sales analytics.",
        "subtitle": "Supervisor team.",
        "slots": [
            {"id": "lead", "agentId": "architect", "role": "Coordinate the build", "skills": []},
            {"id": "data", "agentId": "fabric-data-engineer", "role": "Ingest and transform lakehouse data", "skills": []},
            {"id": "report", "agentId": "fabric-data-engineer", "role": "Publish the semantic model and report", "skills": []},
        ],
        "handoffs": [
            {"from": "lead", "to": "data", "kind": "delegate"},
            {"from": "lead", "to": "report", "kind": "delegate"},
        ],
        "entrypointSlotId": "lead",
        "budget": {"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": True},
    })
    comp = svc._parse(
        raw,
        session_id="s-mixed",
        task=(
            "Create an end-to-end executive sales analytics solution: ingest "
            "raw files, transform them into curated lakehouse tables, build "
            "the semantic model, and publish a Power BI report with polished "
            "leadership-ready visuals."
        ),
        require_approvals=True,
    )

    assert comp.architecture == "dynamic"
    assert [s.id for s in comp.slots] == ["generalist"]
    assert comp.handoffs == []


def test_parse_rejects_internal_orchestrator_slot() -> None:
    svc = ComposeService(agents={**AGENT_TEMPLATES, "orchestrator": AGENT_TEMPLATES["architect"]})
    raw = json.dumps({
        "architecture": "supervisor",
        "rationale": "Bad visible internal control plane.",
        "headline": "Coordinate.",
        "subtitle": "Should be rejected.",
        "slots": [
            {"id": "lead", "agentId": "orchestrator", "role": "Coordinate", "skills": []},
            {"id": "worker", "agentId": "architect", "role": "Plan", "skills": []},
        ],
        "handoffs": [{"from": "lead", "to": "worker", "kind": "delegate"}],
        "entrypointSlotId": "lead",
        "budget": {"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": True},
    })

    with pytest.raises(CompositionError, match="internal control plane"):
        svc._parse(raw, session_id="s-internal", task="Do work", require_approvals=True)


def test_parse_preserves_read_only_inventory_as_solo() -> None:
    svc = ComposeService(agents=AGENT_TEMPLATES)
    raw = json.dumps({
        "architecture": "solo",
        "rationale": "Read-only inventory.",
        "headline": "Inspect workspace.",
        "subtitle": "No changes.",
        "slots": [{"id": "reader", "agentId": "fabric-admin", "role": "List current items", "skills": []}],
        "handoffs": [],
        "entrypointSlotId": "reader",
        "budget": {"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": True},
    })

    comp = svc._parse(
        raw,
        session_id="s-readonly",
        task="List the current workspace items and return a read-only inventory summary.",
        require_approvals=True,
    )

    assert comp.architecture == "dynamic"
    assert len(comp.slots) == 1
    assert comp.slots[0].agent_id == "generalist"
    assert comp.handoffs == []
