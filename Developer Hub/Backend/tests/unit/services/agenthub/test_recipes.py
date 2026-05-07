"""Tests for the declarative composition recipes catalog."""

from __future__ import annotations

from domain.catalogs.architectures import ARCHITECTURES_BY_ID
from services.agenthub.agent_registry import AGENT_TEMPLATES
from services.agenthub.compose.recipes import (
    RECIPES,
    RECIPES_BY_ID,
    validate_recipes,
)


def test_recipes_are_unique():
    ids = [r.id for r in RECIPES]
    assert len(ids) == len(set(ids)), f"duplicate recipe ids: {ids}"


def test_recipes_by_id_in_sync():
    assert set(RECIPES_BY_ID) == {r.id for r in RECIPES}


def test_every_recipe_resolves_against_live_catalogs():
    errors = validate_recipes(
        architecture_ids=set(ARCHITECTURES_BY_ID),
        agent_ids=set(AGENT_TEMPLATES),
    )
    assert not errors, "\n".join(errors)


def test_every_recipe_has_at_least_one_slot():
    bad = [r.id for r in RECIPES if not r.slot_agent_ids]
    assert not bad, f"recipes with empty slot list: {bad}"


def test_dynamic_recipes_use_single_generalist_slot():
    bad = [
        r.id for r in RECIPES
        if r.architecture == "dynamic" and r.slot_agent_ids != ["generalist"]
    ]
    assert not bad, f"dynamic recipes without the generalist seed slot: {bad}"


def test_default_recipe_uses_dynamic_generalist():
    recipe = RECIPES_BY_ID["dynamic-generalist-default"]
    assert recipe.architecture == "dynamic"
    assert recipe.slot_agent_ids == ["generalist"]
    assert any("full MCP tool access" in note for note in recipe.notes)


def test_no_recipe_exposes_orchestrator_slot():
    bad = [r.id for r in RECIPES if "orchestrator" in r.slot_agent_ids]
    assert not bad, f"recipes expose internal orchestrator: {bad}"


def test_validate_recipes_flags_unknown_architecture():
    errors = validate_recipes(
        architecture_ids=set(),  # nothing resolves
        agent_ids=set(AGENT_TEMPLATES),
    )
    assert errors
    assert all("unknown architecture" in e for e in errors)


def test_validate_recipes_flags_unknown_agent():
    errors = validate_recipes(
        architecture_ids=set(ARCHITECTURES_BY_ID),
        agent_ids=set(),  # nothing resolves
    )
    assert errors
    assert any("unknown agent" in e for e in errors)
