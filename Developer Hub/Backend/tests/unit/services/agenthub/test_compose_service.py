"""Unit tests for ``services.agenthub.compose_service`` helpers.

Focus: the post-parse normalisation step that enforces the uniqueness
rule the system prompt gives the LLM. A correct composer NEVER emits
two slots with the same ``agentId`` in a sequential or supervisor
plan (one agent handles its whole phase). Some LLMs ignore this rule
and return duplicate slots anyway — the helper collapses them back
into one slot, merges roles + skills, and rewrites handoffs.

The full compose flow (LLM call + parse) is integration-territory and
is exercised in the broader test suite; here we pin the collapse
helper specifically so future composer changes don't silently regress
the dedupe invariant.
"""
from __future__ import annotations

from domain.models.composition import AgentSlot, Handoff, SkillRef
from services.agenthub.compose_service import _collapse_duplicate_agent_slots


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
