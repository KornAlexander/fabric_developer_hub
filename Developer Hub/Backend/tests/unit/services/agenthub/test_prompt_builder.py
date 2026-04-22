"""Tests for the modular composer prompt builder."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from domain.catalogs.architectures import ARCHITECTURES
from domain.models.agent_models import AgentBoundaries, AgentCategory, AgentTemplate
from services.agenthub.agent_registry import list_templates
from services.agenthub.compose import build_system_prompt
from services.agenthub.compose.prompt import (
    render_agents_section,
    render_architectures_section,
    render_boundary_matrix,
    render_recipes_section,
)
from services.agenthub.compose.recipes import RECIPES, CompositionRecipe


# ── Fixtures ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _StubArch:
    id: str
    name: str = "Stub"
    headline: str = "stub headline"
    description: str = "stub description"
    pick_when: str = "stub pick when"
    watch_for: str = "stub watch for"
    fabric_use_cases: list[str] = field(default_factory=list)
    slot_rules: list[str] = field(default_factory=list)
    has_driver: bool = True


def _stub_agent(
    agent_id: str,
    *,
    description: str = "stub description",
    boundaries: AgentBoundaries | None = None,
) -> AgentTemplate:
    return AgentTemplate(
        id=agent_id,
        name=agent_id,
        display_name=agent_id,
        category=AgentCategory.ENGINEERING,
        description=description,
        boundaries=boundaries,
        system_prompt="you are a stub",
    )


# ── Section renderers ────────────────────────────────────────────────


def test_render_architectures_includes_slot_rules():
    arch = _StubArch(
        id="seq",
        headline="pipeline.",
        pick_when="ordered steps.",
        watch_for="coupling.",
        slot_rules=["slot[i] -> slot[i+1]", "no lead"],
    )
    out = render_architectures_section([arch])
    assert "- seq: pipeline." in out
    assert "Pick when: ordered steps." in out
    assert "Watch for: coupling." in out
    assert "Slot rules:" in out
    assert "* slot[i] -> slot[i+1]" in out
    assert "* no lead" in out


def test_render_architectures_omits_slot_rules_when_empty():
    arch = _StubArch(id="x", slot_rules=[])
    out = render_architectures_section([arch])
    assert "Slot rules:" not in out


def test_render_agents_lists_id_and_skills():
    agent = _stub_agent("agent-a", description="does A")
    out = render_agents_section([agent])
    assert "- agent-a: does A" in out
    assert "(no declared skills)" in out


def test_render_boundary_matrix_skips_agents_without_boundaries():
    plain = _stub_agent("plain")
    structured = _stub_agent(
        "structured",
        boundaries=AgentBoundaries(
            owns=["thing one"],
            does_not_own=["other thing -> plain"],
            hands_off_to=["plain"],
            pick_when=["when A"],
            skip_when=["when B"],
        ),
    )
    out = render_boundary_matrix([plain, structured])
    assert "structured:" in out
    assert "plain:" not in out  # no boundaries -> skipped
    assert "OWNS:" in out
    assert "- thing one" in out
    assert "DOES NOT OWN (routes to):" in out
    assert "- other thing -> plain" in out
    assert "HANDS OFF TO: plain" in out
    assert "PICK WHEN:" in out
    assert "- when A" in out
    assert "SKIP WHEN:" in out
    assert "- when B" in out


def test_render_recipes_includes_order_and_notes():
    recipe = CompositionRecipe(
        id="r1",
        trigger="do the thing.",
        architecture="sequential",
        slot_agent_ids=["a", "b", "c"],
        notes=["one agent covers build"],
    )
    out = render_recipes_section([recipe])
    assert "- do the thing." in out
    assert "architecture: sequential" in out
    assert "slots (in order): a -> b -> c" in out
    assert "note: one agent covers build" in out


# ── build_system_prompt (integration) ────────────────────────────────


def test_build_system_prompt_contains_all_sections():
    prompt = build_system_prompt(ARCHITECTURES, list_templates(), RECIPES)
    for section in (
        "# Architectures available",
        "# Agents available",
        "# Agent boundaries",
        "# Canonical compositions",
        "# Global rules",
        "# Output",
    ):
        assert section in prompt, f"missing section: {section}"


def test_build_system_prompt_has_no_hardcoded_ids_when_catalogs_are_empty():
    """The assembler must be pure \u2014 no agent/architecture/recipe id
    should appear in the prompt if all three catalogs are empty."""
    prompt = build_system_prompt([], [], [])
    # Known ids from the real catalogs \u2014 none should leak into the
    # prompt when the catalogs are empty.
    # Distinctive ids only — generic English words ("architect",
    # "supervisor") are intentionally excluded because the header
    # legitimately talks about "architectures" as a concept.
    forbidden = [
        "fabric-admin",
        "fabric-data-engineer",
        "fabric-app-dev",
        "magentic",
        "e2e-analytics-greenfield",
        "migration-or-tenant-programme",
    ]
    for fid in forbidden:
        assert fid not in prompt, (
            f"assembler leaked hardcoded id '{fid}' with empty catalogs"
        )


def test_build_system_prompt_adding_new_agent_shows_in_output():
    new_agent = _stub_agent(
        "brand-new-agent",
        description="totally new responsibility.",
        boundaries=AgentBoundaries(owns=["the new thing"]),
    )
    prompt = build_system_prompt(
        ARCHITECTURES,
        list(list_templates()) + [new_agent],
        RECIPES,
    )
    assert "brand-new-agent" in prompt
    assert "totally new responsibility." in prompt
    assert "the new thing" in prompt


def test_build_system_prompt_adding_new_architecture_shows_slot_rules():
    new_arch = _StubArch(
        id="brand-new-pattern",
        headline="a new pattern.",
        pick_when="when X.",
        watch_for="Y.",
        slot_rules=["this is the new rule"],
    )
    prompt = build_system_prompt(
        list(ARCHITECTURES) + [new_arch],
        list_templates(),
        RECIPES,
    )
    assert "brand-new-pattern" in prompt
    assert "this is the new rule" in prompt


# ── Real catalogs wiring ─────────────────────────────────────────────


def test_every_registered_architecture_has_slot_rules():
    missing = [a.id for a in ARCHITECTURES if not a.slot_rules]
    assert not missing, (
        f"architectures missing slot_rules: {missing} \u2014 "
        "each entry should encode its structural constraints "
        "so the composer prompt can render them."
    )


@pytest.mark.parametrize("agent_id", [
    "fabric-admin",
    "fabric-app-dev",
    "fabric-data-engineer",
    "architect",
    "modeler",
    "creator",
    "orchestrator",
])
def test_every_registered_agent_has_boundaries(agent_id: str):
    agents = {a.id: a for a in list_templates()}
    agent = agents[agent_id]
    assert agent.boundaries is not None, (
        f"{agent_id} missing AgentBoundaries \u2014 the boundary matrix "
        "section depends on this."
    )
