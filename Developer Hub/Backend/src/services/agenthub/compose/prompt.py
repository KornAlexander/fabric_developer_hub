"""Pure assembler for the composer's system prompt.

The prompt is built at render time from three catalogs:

* architectures (with ``slot_rules``) -> the "Architectures" section
  and the per-pattern structural rules.
* agents (with ``boundaries``) -> the "Agents" section and an
  auto-derived "Boundary matrix" that lists who OWNS / DOES NOT OWN /
  PICKS WHEN / SKIPS WHEN / HANDS OFF TO for every agent.
* recipes -> the "Canonical compositions" section.

The final section ("Global rules" + "Output schema") is the only
architecture- and agent-agnostic prose the composer hard-codes. It's
expressed in terms of the catalogs (the rules reference fields, not
specific ids), so adding a new agent/architecture/recipe is a data
change.

No function here mentions a concrete agent id, architecture id, or
recipe id. Grep this file for ``fabric-`` or ``architect`` or
``modeler`` \u2014 there should be zero hits outside the tests.
"""

from __future__ import annotations

from collections.abc import Iterable

from domain.catalogs.architectures import ArchitectureCatalogEntry
from domain.models.agent_models import AgentTemplate
from services.agenthub.compose.recipes import CompositionRecipe

# ── Sections ────────────────────────────────────────────────────────


def render_architectures_section(
    architectures: Iterable[ArchitectureCatalogEntry],
) -> str:
    """Render the architecture catalog as prompt prose.

    Each entry gets its id, headline, pick-when / watch-for guidance,
    and (if populated) its per-pattern structural ``slot_rules``.
    """
    blocks: list[str] = []
    for a in architectures:
        lines = [
            f"- {a.id}: {a.headline}",
            f"    Pick when: {a.pick_when}",
            f"    Watch for: {a.watch_for}",
        ]
        if a.slot_rules:
            lines.append("    Slot rules:")
            for rule in a.slot_rules:
                lines.append(f"      * {rule}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def render_agents_section(agents: Iterable[AgentTemplate]) -> str:
    """Render each agent's id, one-line description, and skill ids."""
    lines: list[str] = []
    for t in agents:
        skills_str = (
            ", ".join(f"{s.id} ({s.name})" for s in t.skills)
            if t.skills else "(no declared skills)"
        )
        lines.append(f"- {t.id}: {t.description} | skills: {skills_str}")
    return "\n".join(lines)


def render_boundary_matrix(agents: Iterable[AgentTemplate]) -> str:
    """Render structured boundaries per agent, auto-derived from
    :class:`AgentBoundaries`. Agents without a ``boundaries`` field are
    skipped \u2014 the prose in their ``description`` has to carry the load
    for legacy entries, but new agents should populate ``boundaries``.
    """
    blocks: list[str] = []
    for t in agents:
        b = t.boundaries
        if b is None:
            continue
        lines = [f"{t.id}:"]
        if b.owns:
            lines.append("  OWNS:")
            for item in b.owns:
                lines.append(f"    - {item}")
        if b.does_not_own:
            lines.append("  DOES NOT OWN (routes to):")
            for item in b.does_not_own:
                lines.append(f"    - {item}")
        if b.hands_off_to:
            lines.append(
                "  HANDS OFF TO: " + ", ".join(b.hands_off_to)
            )
        if b.pick_when:
            lines.append("  PICK WHEN:")
            for item in b.pick_when:
                lines.append(f"    - {item}")
        if b.skip_when:
            lines.append("  SKIP WHEN:")
            for item in b.skip_when:
                lines.append(f"    - {item}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_recipes_section(recipes: Iterable[CompositionRecipe]) -> str:
    """Render canonical task-type compositions as prompt prose."""
    blocks: list[str] = []
    for r in recipes:
        lines = [
            f"- {r.trigger}",
            f"    architecture: {r.architecture}",
            "    slots (in order): "
            + " -> ".join(r.slot_agent_ids),
        ]
        for note in r.notes:
            lines.append(f"    note: {note}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


# ── Global rules (agent- and architecture-agnostic) ─────────────────

GLOBAL_RULES = """\
# Global rules
1. Prefer the simplest architecture that fits. Start at the smallest \
pattern (solo) and escalate only when the task clearly needs \
coordination.
2. If the caller provides a `preferredArchitecture`, honour it unless \
it's clearly unsuitable. Explain any override in `rationale`.
3. Every slot's `agentId` MUST be an id from the "Agents available" \
section. Every `skills[].id` MUST be one of that agent's declared \
skills.
4. Uniqueness: never emit two slots with the same `agentId` in a \
sequential or supervisor plan. One agent handles its entire phase \
in one slot, even when that phase has internal stages. Repeating an \
agent is only allowed in `parallel` (fan-out over independent inputs) \
or `mixed` (labelled sub-teams).
5. Minimality: omit every agent whose description and boundaries do \
not map to an explicit sentence in the user's task. When unsure, \
leave it out \u2014 the Orchestrator can spawn the agent at runtime if \
execution proves it's needed.
6. Follow the per-architecture Slot rules listed in the \
"Architectures available" section verbatim. They override any \
ambiguity.
7. Follow the per-agent boundaries in the "Agent boundaries" section \
verbatim. An entry "x -> <agent-id>" in DOES NOT OWN means that \
responsibility belongs to the named agent, not this one.
8. If the task matches a canonical composition in the \
"Canonical compositions" section, use that as the default shape. \
Deviate only with a clear reason recorded in `rationale`.
9. Pick a sensible `budget`. Default (20/100/600) is fine for most \
tasks; scale up for genuinely large work, never above the schema caps.
10. `architecture` MUST be exactly one of the ids in the \
"Architectures available" section \u2014 never a slot role like `lead`, \
`worker`, or `reducer`.
"""


OUTPUT_SCHEMA = """\
# Output
Respond with ONLY valid JSON matching this schema (camelCase keys):

{
  "architecture": "<one of the ids above>",
  "rationale": "<why this shape fits this task, 1-2 sentences>",
  "headline": "<one-liner shown above the graph>",
  "subtitle": "<short subtitle \u2014 what this team is going to do>",
  "slots": [
    {
      "id": "<slot id, e.g. 'lead' or 'worker-1'>",
      "agentId": "<agent id>",
      "role": "<what this slot does in this task>",
      "skills": [{"id": "<skill id>", "name": "<skill name>"}],
      "parentId": null,
      "subteam": null
    }
  ],
  "handoffs": [
    {"from": "<slot id>", "to": "<slot id>", "kind": "delegate|report|peer|handoff|critique", "condition": null}
  ],
  "entrypointSlotId": "<slot id>",
  "budget": {"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": true}
}

No markdown fences. No prose outside the JSON object.
"""


# ── Top-level builder ───────────────────────────────────────────────


def build_system_prompt(
    architectures: Iterable[ArchitectureCatalogEntry],
    agents: Iterable[AgentTemplate],
    recipes: Iterable[CompositionRecipe],
) -> str:
    """Assemble the full composer system prompt from the catalogs.

    Pure function of its inputs \u2014 easy to snapshot-test and easy to
    cache on the caller side.
    """
    # Materialise once so we can iterate multiple times if needed.
    archs = list(architectures)
    ags = list(agents)
    recs = list(recipes)

    parts = [
        _HEADER,
        "",
        "# Architectures available",
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
        GLOBAL_RULES,
        "",
        OUTPUT_SCHEMA,
    ]
    return "\n".join(parts)


_HEADER = """\
You are the AgentHub Composer. Given a user's task, the attachments \
they provided, a catalog of available multi-agent architectures, a \
catalog of available agents with structured boundaries, and a catalog \
of canonical compositions, you produce a single Composition \
describing exactly how the task should be executed.

You do NOT produce a plan \u2014 no step list, no prerequisites, no \
workspace item inventory. Plans are emitted by agents at execution \
time if they choose to. Your job is to pick the right shape and the \
right people for the job."""
