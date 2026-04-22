"""Catalog of multi-agent architectures AgentHub can compose.

Single source of truth for:
* the enum the compose LLM may return,
* the descriptions the frontend renders in the "Architecture" panel
  and the "Compare architectures" page,
* the per-pattern "pick when / watch for" guidance the compose LLM
  uses to choose.

Keep this list in lock-step with ``domain.models.composition.Architecture``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArchitectureCatalogEntry:
    id: str               # matches Composition.architecture value
    name: str             # display label
    headline: str         # one-liner shown on the Step2 header
    description: str      # full paragraph for the compare page
    pick_when: str        # guidance to the compose LLM + to the user
    watch_for: str        # failure modes / anti-patterns
    fabric_use_cases: list[str] = field(default_factory=list)
    # Structural constraints for slots laid out under this pattern.
    # Rendered verbatim into the composer prompt so adding a new
    # architecture (or reshaping an existing one) doesn't require
    # editing the prompt string. Each entry should be a single
    # imperative sentence targeting the composer LLM, e.g.
    # "slots are ordered; handoffs go slot[i] -> slot[i+1] with
    # kind='report'".
    slot_rules: list[str] = field(default_factory=list)
    # True if the runtime has a dedicated driver for this pattern.
    # False for reserved values (network / debate / magentic) which
    # fall back to a generic supervisor driver in v1.
    has_driver: bool = True


ARCHITECTURES: list[ArchitectureCatalogEntry] = [
    ArchitectureCatalogEntry(
        id="solo",
        name="Solo agent",
        headline="A single focused specialist.",
        description=(
            "One agent handles the whole task with its own tool belt. "
            "No coordination overhead. Preferred when the task is "
            "small, well-scoped, and fits one specialist's domain."
        ),
        pick_when=(
            "The task maps cleanly to one agent's skills and needs no "
            "handoff, review, or second opinion."
        ),
        watch_for=(
            "Context-window bloat on long tasks — escalate to a "
            "supervisor with sub-agents if the agent starts thrashing."
        ),
        fabric_use_cases=[
            "Fix a single notebook",
            "Add a measure to an existing semantic model",
            "Validate one lakehouse table's schema",
        ],
        slot_rules=[
            "Exactly one slot.",
            "No handoffs — there is no second agent to talk to.",
            "`entrypointSlotId` is the sole slot id.",
        ],
    ),
    ArchitectureCatalogEntry(
        id="supervisor",
        name="Supervisor",
        headline="One lead coordinating specialists.",
        description=(
            "A supervisor LLM decomposes the task and delegates each "
            "sub-task to a named worker, then integrates their "
            "responses. Default choice when multiple specialists are "
            "needed and their roles don't overlap much."
        ),
        pick_when=(
            "The task decomposes cleanly into specialist sub-tasks "
            "and the specialists don't need to talk to each other."
        ),
        watch_for=(
            "Supervisor becomes a bottleneck on wide tasks — switch "
            "to parallel fan-out if sub-tasks are independent."
        ),
        fabric_use_cases=[
            "Create a sales report from the Contoso lakehouse",
            "Provision a new workspace with standard governance",
        ],
        slot_rules=[
            "One lead slot at index 0; every other slot is a worker.",
            "Handoffs: lead → each worker with kind='delegate'.",
            "Workers do not talk to each other directly.",
            "`entrypointSlotId` is the lead.",
        ],
    ),
    ArchitectureCatalogEntry(
        id="sequential",
        name="Sequential pipeline",
        headline="A fixed pipeline: A → B → C.",
        description=(
            "A deterministic DAG where each stage's output feeds the "
            "next. No dynamic routing. Preferred when order is known "
            "and each stage is a distinct transformation."
        ),
        pick_when=(
            "The task is inherently ordered and each stage's output "
            "is the next stage's input (classic ETL / ELT)."
        ),
        watch_for=(
            "One bad stage poisons the run. Add a per-stage validator "
            "or a fallback to reflection on each stage."
        ),
        fabric_use_cases=[
            "Build a Medallion lakehouse (Bronze → Silver → Gold)",
            "Ingest → transform → publish a semantic model",
        ],
        slot_rules=[
            "Slots are ordered; handoffs go slot[i] → slot[i+1] with kind='report'.",
            "No lead / supervisor slot.",
            "Same agentId must not appear twice in a row — one agent handles its whole phase in one slot.",
            "`entrypointSlotId` is the first slot.",
        ],
    ),
    ArchitectureCatalogEntry(
        id="parallel",
        name="Parallel fan-out",
        headline="Split, run in parallel, then reduce.",
        description=(
            "A supervisor fans the task out to N workers that run "
            "concurrently, then a reducer agent consolidates their "
            "outputs. Map-reduce for agents."
        ),
        pick_when=(
            "The work is embarrassingly parallel across N independent "
            "inputs (workspaces, files, tables)."
        ),
        watch_for=(
            "Aggregation quality — the reducer agent needs a clear "
            "rubric to combine outputs, or results drift."
        ),
        fabric_use_cases=[
            "Audit every workspace in the tenant for orphaned items",
            "Review each notebook in a lakehouse for code smells",
        ],
        slot_rules=[
            "Three layers: one fan-out supervisor, N parallel workers, one reducer.",
            "Handoffs: supervisor → workers kind='delegate'; workers → reducer kind='report'.",
            "Workers never talk to each other.",
            "Same agentId MAY repeat across workers — that's the point of fan-out.",
        ],
    ),
    ArchitectureCatalogEntry(
        id="router",
        name="Router / Handoff",
        headline="Triage, then hand off to the right specialist.",
        description=(
            "A triage agent reads the task and hands control off to "
            "exactly one downstream specialist. The specialist may "
            "hand off again (bounded) if its scope doesn't fit. No "
            "central supervisor retains context across hops."
        ),
        pick_when=(
            "Incoming tasks are heterogeneous and each flows to a "
            "single specialist (support triage, 'which team owns "
            "this?' style routing)."
        ),
        watch_for=(
            "Handoff loops — cap total hops and require the final "
            "agent to produce a terminal answer."
        ),
        fabric_use_cases=[
            "Triage incoming Fabric questions",
            "Route user request to data engineer / modeler / admin",
        ],
        slot_rules=[
            "One triage slot, with kind='handoff' edges to each candidate specialist.",
            "Only one downstream specialist executes per run.",
            "No report edges back to triage — the specialist is terminal.",
        ],
    ),
    ArchitectureCatalogEntry(
        id="hierarchical",
        name="Hierarchical tree",
        headline="Lead → sub-leads → workers.",
        description=(
            "A top-level lead delegates to 2–3 sub-leads, each owning "
            "a domain (ingestion, governance, reporting). Sub-leads "
            "manage their own workers. Best for deep, multi-track "
            "tasks where each track needs its own plan."
        ),
        pick_when=(
            "The task is big, domain-sliced, and each domain deserves "
            "its own sub-plan and autonomy."
        ),
        watch_for=(
            "Reporting latency grows with depth — keep the tree ≤ 3 "
            "levels."
        ),
        fabric_use_cases=[
            "Migrate a Synapse warehouse to Fabric",
            "Stand up a new tenant-wide Fabric deployment",
        ],
        slot_rules=[
            "One lead, 2–3 sub-leads under it, workers under each sub-lead.",
            "Use `parentId` to express the tree.",
            "Tree depth ≤ 3 (lead, sub-lead, worker).",
            "Delegates downward, reports upward.",
        ],
    ),
    ArchitectureCatalogEntry(
        id="reflection",
        name="Reflection (actor + critic)",
        headline="An actor drafts; a critic refines.",
        description=(
            "A two-agent loop: the actor produces an artifact, the "
            "critic evaluates it and emits a structured critique, the "
            "actor revises. Bounded by turn cap or a convergence "
            "signal."
        ),
        pick_when=(
            "The output is a single high-stakes artifact (SQL, KQL, "
            "DAX, Bicep, a report) where quality matters more than "
            "throughput."
        ),
        watch_for=(
            "Infinite revision — enforce a hard turn cap and require "
            "the critic to emit a terminal 'LGTM' when it converges."
        ),
        fabric_use_cases=[
            "Tune a slow DAX measure",
            "Optimize a Spark notebook's performance",
            "Review a KQL query for correctness + efficiency",
        ],
        slot_rules=[
            "Exactly two slots: an actor and a critic.",
            "Handoffs: actor → critic kind='report', critic → actor kind='critique'.",
            "Budget must cap turns; the critic is responsible for terminating the loop.",
        ],
    ),
    ArchitectureCatalogEntry(
        id="mixed",
        name="Mixed / composite",
        headline="Sub-teams using different patterns.",
        description=(
            "A top-level supervisor delegates to sub-teams that each "
            "use the pattern that fits their sub-task best. Example: "
            "parallel diagnostics team + sequential remediation "
            "pipeline under a shared supervisor."
        ),
        pick_when=(
            "No single shape fits — parallel sub-teams need different "
            "internal coordination."
        ),
        watch_for=(
            "Graph legibility. Always label sub-teams clearly."
        ),
        fabric_use_cases=[
            "Respond to a production incident",
            "Migration that needs parallel audits and sequential "
            "cutover steps",
        ],
        slot_rules=[
            "Top-level supervisor with sub-teams of different patterns.",
            "Every slot in a sub-team MUST set `subteam` to the cluster label.",
            "Within a sub-team, follow that pattern's rules.",
        ],
    ),
    # ── Reserved: no dedicated driver yet. LLM may still pick them;
    # runtime falls back to a supervisor driver until promoted. ──
    ArchitectureCatalogEntry(
        id="network",
        name="Peer network",
        headline="All agents talk to each other.",
        description=(
            "A full-mesh group chat with a turn-taking policy. Any "
            "agent can speak to any other. Useful for debate, voting, "
            "and cross-domain critique."
        ),
        pick_when=(
            "Domains overlap and peer feedback materially improves "
            "quality (design critique, code review)."
        ),
        watch_for=(
            "Loops and non-convergence — set a vote rule and a "
            "turn cap."
        ),
        fabric_use_cases=[
            "Cross-discipline review of a lakehouse architecture",
        ],
        has_driver=False,
        slot_rules=[
            "Full-mesh peer handoffs with kind='peer'.",
            "Budget must cap turns to prevent non-convergence.",
        ],
    ),
    ArchitectureCatalogEntry(
        id="debate",
        name="Debate",
        headline="Opposing agents, judged by a third.",
        description=(
            "N agents argue opposing positions for a bounded number "
            "of rounds; a judge agent decides."
        ),
        pick_when=(
            "The task is adversarial by nature (safety review, policy "
            "check, architecture trade-off analysis)."
        ),
        watch_for=(
            "Cost — debate is expensive; time-box rounds."
        ),
        fabric_use_cases=[
            "Security / policy review of a new workspace",
        ],
        has_driver=False,
        slot_rules=[
            "N debaters + 1 judge.",
            "Debaters peer each other with kind='peer'; all report to the judge with kind='report'.",
            "Budget must cap rounds.",
        ],
    ),
    ArchitectureCatalogEntry(
        id="magentic",
        name="Magentic / ledger-driven",
        headline="Manager maintains a ledger, picks next agent.",
        description=(
            "A manager LLM maintains a progress ledger of facts, "
            "hypotheses, and open questions. Each turn it picks the "
            "next agent to run and re-plans when progress stalls."
        ),
        pick_when=(
            "The task is ambiguous, long-running, and research-like. "
            "Evidence accumulates and the path is not known up front."
        ),
        watch_for=(
            "Ledger drift — periodically summarize and compact."
        ),
        fabric_use_cases=[
            "Investigate a capacity throttling incident",
            "Root-cause analysis across multiple workspaces",
        ],
        has_driver=False,
        slot_rules=[
            "One manager slot plus any number of specialists.",
            "Manager delegates to a specialist, the specialist reports back, the manager re-plans.",
        ],
    ),
]


ARCHITECTURES_BY_ID: dict[str, ArchitectureCatalogEntry] = {
    a.id: a for a in ARCHITECTURES
}


def get_architecture(arch_id: str) -> ArchitectureCatalogEntry | None:
    """Lookup helper with string fallthrough for reserved values."""
    return ARCHITECTURES_BY_ID.get(arch_id)
