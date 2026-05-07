"""Dynamic orchestration strategy catalog.

AgentHub no longer exposes a menu of fixed multi-agent architectures in
the default product path. The runtime starts from one dynamic mission
strategy: an internal generalist inspects the workspace, uses the full
MCP fleet when useful, and spawns specialist subagents only when the
live mission state proves they are needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArchitectureCatalogEntry:
    id: str
    name: str
    headline: str
    description: str
    pick_when: str
    watch_for: str
    fabric_use_cases: list[str] = field(default_factory=list)
    slot_rules: list[str] = field(default_factory=list)
    has_driver: bool = True


ARCHITECTURES: list[ArchitectureCatalogEntry] = [
    ArchitectureCatalogEntry(
        id="dynamic",
        name="Dynamic mission",
        headline="A generalist mission controller with specialist backup.",
        description=(
            "An internal generalist starts each mission, inspects the selected workspace, "
            "uses the available MCP tools directly when that is safe, and creates bounded "
            "task-scoped specialist work only when the mission needs it."
        ),
        pick_when="Default for every new AgentHub mission.",
        watch_for=(
            "Keep the live graph bounded by budgets, locks, approvals, and structured "
            "follow-up tasks so dynamic execution does not drift."
        ),
        fabric_use_cases=[
            "Discover workspace state before deciding which Fabric specialist is needed",
            "Create or modify Fabric artifacts with direct MCP access and approval gates",
            "Spawn admin, data engineering, app development, modeling, or design specialists on demand",
        ],
        slot_rules=[
            "Exactly one initial internal generalist controller task.",
            "Do not expose the generalist as a public catalog agent.",
            "Specialists are spawned dynamically from validated follow-up tasks.",
        ],
    ),
]


ARCHITECTURES_BY_ID: dict[str, ArchitectureCatalogEntry] = {
    a.id: a for a in ARCHITECTURES
}


def get_architecture(architecture_id: str) -> ArchitectureCatalogEntry | None:
    return ARCHITECTURES_BY_ID.get(architecture_id)
