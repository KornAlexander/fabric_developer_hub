"""Declarative catalog of canonical compositions ("recipes").

A recipe is a data-only hint to the composer LLM: "when the task
matches this trigger, the default shape is this architecture with
these agents in this order". Recipes are not enforced — the LLM may
deviate with a reason recorded in ``rationale`` — but they give the
model a concrete answer for common task types instead of asking it to
re-derive the shape from first principles on every call.

Adding a new recipe is a data change: append a :class:`CompositionRecipe`
below. The prompt builder renders them verbatim; the compose LLM picks
one if its trigger matches the user's task.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompositionRecipe:
    """One canonical task-type -> (architecture, agent roster) mapping."""

    id: str
    """Stable identifier for logs and tests."""

    trigger: str
    """Short natural-language description of when this recipe applies.
    Shown to the composer LLM as prose — keep it concrete and
    distinguishing, not generic."""

    architecture: str
    """Architecture id from the architectures catalog."""

    slot_agent_ids: list[str]
    """Ordered agent ids for the slots, in the order they should appear
    in the composition. For ``sequential`` this is the pipeline order.
    For ``supervisor`` / ``hierarchical`` the first id is the lead.
    For ``reflection`` it's [actor, critic]."""

    notes: list[str] = field(default_factory=list)
    """Extra constraints or reminders rendered into the prompt, e.g.
    'one FabricDataEngineer slot covers the whole build phase'."""


RECIPES: list[CompositionRecipe] = [
    CompositionRecipe(
        id="e2e-analytics-greenfield",
        trigger=(
            "End-to-end analytics solution (ingest + transform + "
            "semantic model + report), greenfield or nearly so, with "
            "no external consumer application."
        ),
        architecture="sequential",
        slot_agent_ids=[
            "architect",
            "modeler",
            "fabric-admin",
            "fabric-data-engineer",
        ],
        notes=[
            "One fabric-data-engineer slot covers items, pipelines, "
            "semantic model, and report as a single build phase.",
        ],
    ),
    CompositionRecipe(
        id="e2e-analytics-brownfield",
        trigger=(
            "Small or tactical analytics change in an existing Fabric "
            "workspace \u2014 a tweak, a fix, a single new artefact."
        ),
        architecture="solo",
        slot_agent_ids=["fabric-data-engineer"],
    ),
    CompositionRecipe(
        id="e2e-analytics-with-app",
        trigger=(
            "End-to-end analytics AND an external consumer application "
            "that reads Fabric data."
        ),
        architecture="sequential",
        slot_agent_ids=[
            "architect",
            "modeler",
            "fabric-admin",
            "fabric-data-engineer",
            "fabric-app-dev",
        ],
    ),
    CompositionRecipe(
        id="migration-or-tenant-programme",
        trigger=(
            "Migration or tenant-scale programme with multiple parallel "
            "tracks (e.g. Synapse -> Fabric cutover)."
        ),
        architecture="hierarchical",
        slot_agent_ids=[
            "orchestrator",
            "architect",
            "modeler",
            "fabric-admin",
            "fabric-data-engineer",
        ],
        notes=[
            "Orchestrator is the lead; domain sub-leads group workers.",
        ],
    ),
    CompositionRecipe(
        id="fan-out-audit",
        trigger=(
            "Audit or review across many workspaces, items, or "
            "notebooks \u2014 one independent pass per unit."
        ),
        architecture="parallel",
        slot_agent_ids=[
            "fabric-admin",
            "fabric-data-engineer",
            "fabric-data-engineer",
            "fabric-admin",
        ],
        notes=[
            "Fan-out supervisor + workers + reducer; worker agent may "
            "repeat \u2014 that's the point of parallel.",
        ],
    ),
    CompositionRecipe(
        id="single-artifact-tuning",
        trigger=(
            "Single-artifact quality pass \u2014 tune a DAX measure, "
            "fix one notebook, review one KQL query."
        ),
        architecture="reflection",
        slot_agent_ids=["fabric-data-engineer", "fabric-data-engineer"],
        notes=[
            "First slot is the actor, second is the critic. Budget must "
            "cap turns.",
        ],
    ),
]


RECIPES_BY_ID: dict[str, CompositionRecipe] = {r.id: r for r in RECIPES}


def validate_recipes(
    architecture_ids: set[str],
    agent_ids: set[str],
) -> list[str]:
    """Return a list of validation errors. Empty list means clean.

    Callers (tests, boot-time checks) use this to catch dangling
    references when the catalogs drift apart.
    """
    errors: list[str] = []
    for r in RECIPES:
        if r.architecture not in architecture_ids:
            errors.append(
                f"recipe '{r.id}' references unknown architecture "
                f"'{r.architecture}'"
            )
        for slot_idx, agent_id in enumerate(r.slot_agent_ids):
            if agent_id not in agent_ids:
                errors.append(
                    f"recipe '{r.id}' slot[{slot_idx}] references "
                    f"unknown agent '{agent_id}'"
                )
    return errors
