"""Pure assembler for the composer system prompt.

The composer now emits a dynamic mission seed rather than a fixed team
topology. The specialist catalog is still rendered so the runtime
generalist knows which agents and skills it can delegate to later.
"""

from __future__ import annotations

from collections.abc import Iterable

from domain.catalogs.architectures import ArchitectureCatalogEntry
from domain.models.agent_models import AgentTemplate
from services.agenthub.compose.recipes import CompositionRecipe


def render_architectures_section(
    architectures: Iterable[ArchitectureCatalogEntry],
) -> str:
    """Render the dynamic mission strategy catalog as prompt prose."""
    blocks: list[str] = []
    for architecture in architectures:
        lines = [
            f"- {architecture.id}: {architecture.headline}",
            f"    Pick when: {architecture.pick_when}",
            f"    Watch for: {architecture.watch_for}",
        ]
        if architecture.slot_rules:
            lines.append("    Slot rules:")
            for rule in architecture.slot_rules:
                lines.append(f"      * {rule}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def render_agents_section(agents: Iterable[AgentTemplate]) -> str:
    """Render each specialist's id, one-line description, and skill ids."""
    lines: list[str] = []
    for template in agents:
        if template.is_internal:
            continue
        skills_str = (
            ", ".join(f"{skill.id} ({skill.name})" for skill in template.skills)
            if template.skills else "(no declared skills)"
        )
        lines.append(f"- {template.id}: {template.description} | skills: {skills_str}")
    return "\n".join(lines)


def render_boundary_matrix(agents: Iterable[AgentTemplate]) -> str:
    """Render structured boundaries per public specialist agent."""
    blocks: list[str] = []
    for template in agents:
        if template.is_internal:
            continue
        boundaries = template.boundaries
        if boundaries is None:
            continue
        lines = [f"{template.id}:"]
        if boundaries.owns:
            lines.append("  OWNS:")
            for item in boundaries.owns:
                lines.append(f"    - {item}")
        if boundaries.does_not_own:
            lines.append("  DOES NOT OWN (routes to):")
            for item in boundaries.does_not_own:
                lines.append(f"    - {item}")
        if boundaries.hands_off_to:
            lines.append("  HANDS OFF TO: " + ", ".join(boundaries.hands_off_to))
        if boundaries.pick_when:
            lines.append("  PICK WHEN:")
            for item in boundaries.pick_when:
                lines.append(f"    - {item}")
        if boundaries.skip_when:
            lines.append("  SKIP WHEN:")
            for item in boundaries.skip_when:
                lines.append(f"    - {item}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_recipes_section(recipes: Iterable[CompositionRecipe]) -> str:
    """Render canonical dynamic mission hints as prompt prose."""
    blocks: list[str] = []
    for recipe in recipes:
        lines = [
            f"- {recipe.trigger}",
            f"    architecture: {recipe.architecture}",
            "    slots (in order): " + " -> ".join(recipe.slot_agent_ids),
        ]
        for note in recipe.notes:
            lines.append(f"    note: {note}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


WORKSPACE_CONTEXT_RULES = """\
# Workspace context
The user message may include a WORKSPACE INVENTORY section listing all
items in the destination workspace plus a REFERENCED ITEMS section for
explicitly attached items.

Use this to preserve constraints in the dynamic mission seed. Do not
pre-select a specialist roster from the inventory. The runtime
generalist will inspect workspace state and delegate only when needed.
"""


GLOBAL_RULES = """\
# Global rules
1. Always choose `dynamic`. Fixed architecture choices are no longer
part of the default product path.
2. Emit exactly one initial slot: `id` = `generalist`, `agentId` = `generalist`, and a role that describes live mission control.
3. Do not emit a visible specialist team up front. The runtime
generalist decides whether to use MCP tools directly or spawn
specialists later from structured follow-up tasks.
4. Use the "Agents available" and "Agent boundaries" sections as the
specialist registry the generalist should know about, not as an initial
team roster.
5. The runtime generalist should prefer delegating domain work to the
best matching specialist whenever a specialist owns the capability. It
should keep direct work to safe discovery, routing, and small checks.
6. Any later specialist task must be self-contained and structured: pass
the relevant context summary, touch targets, do-not-touch boundaries,
acceptance criteria, resource claims, dependencies, and the reason it is
or is not safe to run in parallel. Parallel work is opt-in; ambiguous or
write-heavy follow-ups must serialize.
7. Never emit `orchestrator` as a slot `agentId`. The Orchestrator is
internal control plane only.
8. Pick a sensible `budget`. Default (20/100/600) is fine for most
tasks; scale up for genuinely large work, never above the schema caps.
9. Preserve explicit user constraints in `rationale`; enforcement,
approval, specialist spawning, and validation happen at runtime.
"""


OUTPUT_SCHEMA = """\
# Output
Respond with ONLY valid JSON matching this schema (camelCase keys):

{
  "architecture": "dynamic",
  "rationale": "<why dynamic mission control fits this task, 1-2 sentences>",
  "headline": "<one-liner shown above the mission>",
  "subtitle": "<short subtitle describing the dynamic mission>",
  "slots": [
    {
      "id": "generalist",
      "agentId": "generalist",
      "role": "Generalist mission controller",
      "skills": [],
      "parentId": null,
      "subteam": null
    }
  ],
  "handoffs": [],
  "entrypointSlotId": "generalist",
  "budget": {"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": true}
}

No markdown fences. No prose outside the JSON object.
"""


def build_system_prompt(
    architectures: Iterable[ArchitectureCatalogEntry],
    agents: Iterable[AgentTemplate],
    recipes: Iterable[CompositionRecipe],
) -> str:
    """Assemble the full composer system prompt from the catalogs."""
    archs = list(architectures)
    ags = list(agents)
    recs = list(recipes)
    parts = [
        _HEADER,
        "",
        "# Mission strategy",
        render_architectures_section(archs),
        "",
        "# Agents available",
        render_agents_section(ags),
        "",
        "# Agent boundaries",
        render_boundary_matrix(ags),
        "",
        "# Canonical compositions",
        render_recipes_section(recs),
        "",
        WORKSPACE_CONTEXT_RULES,
        "",
        GLOBAL_RULES,
        "",
        OUTPUT_SCHEMA,
    ]
    return "\n".join(parts)


_HEADER = """\
You are the AgentHub Composer. Given a user's task, the attachments they
provided, a dynamic mission strategy, a catalog of available specialist
agents with structured boundaries, and canonical mission hints, you
produce a single dynamic Composition seed.

You do NOT produce a plan, a visible team, prerequisites, or workspace
item inventory. Runtime mission control starts with the hidden
generalist and creates specialist work only when execution proves it is
needed."""
