"""Composition — the single artifact produced by the "direct prompt →
composition" analysis step that replaces the legacy Plan pipeline.

A Composition captures everything the runtime needs to execute a task:

* which **architecture** (coordination topology) to use,
* which **agents** fill the slots in that topology,
* which **skills** each agent is expected to use,
* the **handoff graph** between slots,
* a **budget** (turns / tool calls / wallclock / approval policy).

Crucially, a Composition carries **no pre-materialized plan of steps,
no prerequisites, no workspace_items, no conflicts, no clarifications**.
Those were the wrong abstractions — the orchestrator LLM doesn't reliably
enumerate steps up-front on Fabric tasks. If an agent needs to expose a
mini-plan during execution, it does so as an in-session event artifact,
not a precondition for running.

Wire format is camelCase (matches every other AgentHub DTO), with
Python-side snake_case via pydantic aliases.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_CAMEL_CONFIG = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="forbid",
)


# ── Architecture catalog (wire enum) ──────────────────────────────────
# ``dynamic`` is the only user-facing/default strategy. Legacy values
# remain accepted so historical sessions and explicit fallback tests can
# still deserialize while the product path moves to the live mission graph.
Architecture = Literal[
    "dynamic",
    # v1 architectures with dedicated drivers
    "solo",
    "supervisor",
    "sequential",
    "hierarchical",
    "reflection",
    "mixed",
    # Reserved values — LLM may pick them; runtime falls back to a
    # supervisor driver and tags the node graph with the chosen label.
    # Promote to a dedicated driver when a real task motivates it.
    "network",
]


SlotStatus = Literal["planned", "active", "done", "waiting", "error"]


HandoffKind = Literal[
    "delegate",   # supervisor → worker
    "report",     # worker → supervisor (or next stage)
    "peer",       # undirected peer edge in a network
    "handoff",    # explicit router handoff between two specialists
    "critique",   # reflection: critic → actor
    "verify",     # reflection: critic → verifier (post-convergence gate)
]


# ── Skill reference ──────────────────────────────────────────────────

class SkillRef(BaseModel):
    """Reference to a skill the composition selected for a slot.

    ``id`` matches a skill declared on the agent template's ``skills``
    list (see ``domain.catalogs.skills``). The runtime uses this subset
    to narrow the tool surface it exposes to the agent.
    """

    model_config = _CAMEL_CONFIG

    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)


# ── Agent slot ───────────────────────────────────────────────────────

class AgentSlot(BaseModel):
    """One node in the composition graph — a named role filled by an
    agent from the registry."""

    model_config = _CAMEL_CONFIG

    id: str = Field(min_length=1, max_length=64)
    agent_id: str = Field(min_length=1, max_length=120)
    # Display label for the UI node card. Examples:
    # "Orchestrator · plan & route", "Bronze ingest", "Critic".
    role: str = Field(min_length=1, max_length=160)
    # Subset of the agent's declared skills selected for this task.
    # Empty list is legal (the agent brings its full skill surface).
    skills: list[SkillRef] = Field(default_factory=list)
    # Set on hierarchical / mixed compositions. References another
    # slot.id so the frontend can render sub-team clusters.
    parent_id: str | None = Field(default=None, max_length=64)
    # Optional cluster label for mixed architectures ("diagnostics",
    # "remediation"). Displayed as a frame around the cluster.
    subteam: str | None = Field(default=None, max_length=64)
    # Rendered as the node status chip in the frontend graph.
    status: SlotStatus = "planned"


# ── Handoff edge ─────────────────────────────────────────────────────

class Handoff(BaseModel):
    """Directed edge between two slots in the composition graph."""

    model_config = _CAMEL_CONFIG

    from_: str = Field(alias="from", min_length=1, max_length=64)
    to: str = Field(min_length=1, max_length=64)
    kind: HandoffKind = "delegate"
    # Natural-language trigger describing when this handoff fires. The
    # runtime doesn't parse it — drivers use it as a hint / for logs
    # only. Empty is fine for topologies where edges are implicit
    # (supervisor, sequential).
    condition: str | None = Field(default=None, max_length=240)


# ── Budget & policy ──────────────────────────────────────────────────

class Budget(BaseModel):
    """Hard caps the runtime enforces on a session.

    Prevents runaway agent loops (``max_turns``), runaway tool usage
    (``max_tool_calls``), and runaway wallclock (``max_wallclock_s``).
    """

    model_config = _CAMEL_CONFIG

    max_turns: int = Field(default=20, ge=1, le=200)
    max_tool_calls: int = Field(default=100, ge=1, le=1000)
    max_wallclock_s: int = Field(default=600, ge=10, le=7200)
    require_approvals: bool = True


# ── Composition ──────────────────────────────────────────────────────

class Composition(BaseModel):
    """Everything the runtime needs to execute one session.

    Produced by ``ComposeService.compose()`` in a single LLM call that
    takes (prompt, attachments, architecture catalog, agent+skill
    catalog) as input.
    """

    model_config = _CAMEL_CONFIG

    session_id: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=16_000)
    architecture: Architecture
    rationale: str = Field(min_length=1, max_length=1_200)
    # Human-readable one-liners the UI renders above the graph.
    # Mirrors the PATTERN_META strings the frontend used to hardcode.
    headline: str = Field(min_length=1, max_length=200)
    subtitle: str = Field(min_length=1, max_length=400)

    slots: list[AgentSlot] = Field(default_factory=list)
    handoffs: list[Handoff] = Field(default_factory=list)
    # id of the slot that receives the user's task first.
    entrypoint_slot_id: str = Field(min_length=1, max_length=64)

    budget: Budget = Field(default_factory=Budget)


class CompositionError(Exception):
    """Raised when the compose LLM call cannot produce a valid
    Composition after one retry. The API controller maps this to a
    structured 4xx/5xx so the frontend shows the "We couldn't plan
    this" empty state.
    """

    def __init__(self, reason: str, details: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}
