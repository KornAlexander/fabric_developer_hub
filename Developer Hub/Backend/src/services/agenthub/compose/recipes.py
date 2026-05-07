"""Declarative catalog of canonical dynamic mission seeds."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompositionRecipe:
    """One canonical task-type -> dynamic mission seed mapping."""

    id: str
    trigger: str
    architecture: str
    slot_agent_ids: list[str]
    notes: list[str] = field(default_factory=list)


RECIPES: list[CompositionRecipe] = [
    CompositionRecipe(
        id="dynamic-generalist-default",
        trigger=(
            "Any AgentHub mission: discovery, read-only inspection, artifact creation, "
            "analytics build, report/model work, governance, app development, or mixed Fabric work."
        ),
        architecture="dynamic",
        slot_agent_ids=["generalist"],
        notes=[
            "Start with the hidden generalist controller, not an upfront visible team.",
            "The generalist has full MCP tool access inside the selected workspace.",
            "The generalist should prefer outsourcing domain work to specialists when their skills match.",
            "Specialists are spawned later from structured follow-up tasks that include context, touch/no-touch boundaries, acceptance criteria, resource claims, and parallelism notes.",
        ],
    ),
]


RECIPES_BY_ID: dict[str, CompositionRecipe] = {recipe.id: recipe for recipe in RECIPES}


def validate_recipes(
    architecture_ids: set[str],
    agent_ids: set[str],
) -> list[str]:
    """Return validation errors for dangling architecture or agent references."""
    errors: list[str] = []
    for recipe in RECIPES:
        if recipe.architecture not in architecture_ids:
            errors.append(
                f"recipe '{recipe.id}' references unknown architecture '{recipe.architecture}'"
            )
        for slot_idx, agent_id in enumerate(recipe.slot_agent_ids):
            if agent_id not in agent_ids:
                errors.append(
                    f"recipe '{recipe.id}' slot[{slot_idx}] references unknown agent '{agent_id}'"
                )
    return errors
