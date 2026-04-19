"""Plan — the grounded execution plan returned by the orchestrator.

This schema is consumed 1:1 by the frontend Plan view. Field names use
camelCase on the wire (via Pydantic aliases) to preserve the Fabric
workload wire convention; Python code always uses snake_case.

Every field in this file is either literally named in the Job 2 spec or
directly required by the UI rendering contract. Do not add loose fields —
if the model needs to grow, extend the spec first.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


_CAMEL_CONFIG = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="forbid",
)


class PlanTarget(BaseModel):
    """The Fabric entity a step acts on.

    ``itemType`` mirrors the Fabric item-type string (``Lakehouse``,
    ``Warehouse``, ``Notebook``, ``Pipeline``, ``SemanticModel``,
    ``Report``) or one of the non-item pseudo-types ``workspace``,
    ``connection``, ``capacity`` that the planner may target.
    """

    model_config = _CAMEL_CONFIG

    item_type: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=128)
    existing_item_id: str | None = Field(default=None, max_length=128)


StepAction = Literal["create", "update", "delete", "configure", "validate", "clarify"]
RiskLevel = Literal["low", "medium", "high"]
PrereqStatus = Literal["satisfied", "missing", "unknown"]


class PlanStep(BaseModel):
    model_config = _CAMEL_CONFIG

    id: str = Field(min_length=1, max_length=64)
    order: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    action: StepAction
    target: PlanTarget
    inputs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=1200)
    risk: RiskLevel = "low"
    risk_notes: str | None = Field(default=None, max_length=600)
    reversible: bool = True
    # NOTE: ``estimated_duration_seconds`` was removed per
    # docs/plan-generation-overhaul.md §1. Fabric operation durations
    # depend too heavily on data volume, capacity SKU, and external
    # latency for the model to estimate reliably. The field is no longer
    # in the contract; do NOT reintroduce it.


# ── Artifact type enum (spec §3.2) ─────────────────────────────────
# Canonical set of Fabric item types the planner may emit on steps.
# Any value outside this set triggers a single retry of the LLM call;
# a second failure surfaces as a structured ``PlanValidationError`` so
# the UI shows the "Plan could not be generated" empty state.
VALID_ARTIFACT_TYPES: frozenset[str] = frozenset({
    "Lakehouse",
    "Warehouse",
    "Pipeline",
    "Dataflow",
    "Notebook",
    "SemanticModel",
    "Report",
    "KQLDatabase",
    "Eventstream",
    "AISkill",
    "None",
    # Pseudo-targets the planner legitimately uses:
    "workspace",
    "connection",
    "capacity",
})


class PlanValidationError(Exception):
    """Raised when a generated plan repeatedly fails the server-side
    validators (e.g. unknown ``artifact_type`` after one retry).

    The caller maps this to a structured response so the UI shows the
    "Plan could not be generated" empty state instead of silently
    degrading to a clarify-only plan.
    """

    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


# ── Prerequisites (spec §4) ────────────────────────────────────────

PrereqCategory = Literal[
    "workspace_role",
    "tenant_scope",
    "capacity",
    "item_permission",
    "source_access",
    "connection",
    "git_alm",
    "feature_flag",
    "license",
    "quota",
]

VerifierKind = Literal[
    "fabric_api",
    "graph_api",
    "capacity_api",
    "connection_probe",
    "license_lookup",
    "git_status",
    "manual",
]


class PrereqVerification(BaseModel):
    """Result of running the backend verifier for a prerequisite.

    ``kind`` + ``spec`` come from the planner; ``status`` +
    ``checked_at`` + ``evidence`` + ``unknown_reason`` are populated by
    the backend before the plan is returned to the UI. Status is NEVER
    produced by the model.
    """

    model_config = _CAMEL_CONFIG

    kind: VerifierKind = "manual"
    spec: dict[str, Any] = Field(default_factory=dict)
    status: PrereqStatus = "unknown"
    checked_at: str | None = None
    evidence: str | None = Field(default=None, max_length=600)
    unknown_reason: str | None = Field(default=None, max_length=600)



class Prerequisite(BaseModel):
    model_config = _CAMEL_CONFIG

    id: str = Field(min_length=1, max_length=64)
    # Legacy short label kept for the existing UI sub-row; new contract
    # adds ``category`` + ``applies_to_step_ids`` + structured
    # ``verification`` per spec §4.2.
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=800)
    # Top-level ``status`` mirrors ``verification.status`` so existing
    # consumers (pre-spec-§4 UI) keep working. The UI should read
    # ``verification.status`` going forward.
    status: PrereqStatus = "unknown"
    evidence: str | None = Field(default=None, max_length=600)

    # ── Spec §4.2 additions ────────────────────────────────────────
    # ``text`` is the one-line user-facing statement (spec names it
    # exactly that). We keep the historical ``title`` populated from
    # ``text`` if the model only emits the new shape.
    text: str | None = Field(default=None, max_length=200)
    category: PrereqCategory = "workspace_role"
    applies_to_step_ids: list[str] = Field(default_factory=list)
    verification: PrereqVerification = Field(default_factory=PrereqVerification)

    @model_validator(mode="before")
    @classmethod
    def _backfill_title_from_text(cls, data: Any) -> Any:
        """Accept either the new shape (``text`` only) or the legacy
        shape (``title`` + ``description``).

        The planner prompt was updated (spec §4.2) to emit a single
        ``text`` field instead of separate ``title`` / ``description``.
        The Pydantic model still carries the legacy fields for the
        existing UI sub-row, so we backfill them here when the model
        returns only ``text`` to avoid spurious schema validation
        failures. Truncates to the field limits.
        """
        if not isinstance(data, dict):
            return data
        text_val = data.get("text")
        has_title = bool(data.get("title"))
        has_description = bool(data.get("description"))
        if text_val and (not has_title or not has_description):
            txt = str(text_val).strip()
            if not has_title:
                data["title"] = txt[:200]
            if not has_description:
                data["description"] = txt[:800]
        return data


class NoActionItem(BaseModel):
    """Legacy shape — kept only to drain old cached plans. New plans
    use ``Plan.workspace_items[]`` instead (see spec §2).
    """
    model_config = _CAMEL_CONFIG

    item_type: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=400)


# ── Spec §2: workspace_items replaces already_satisfied / noAction ──

WorkspaceItemDisposition = Literal["keep_as_is", "will_be_changed"]


class WorkspaceItem(BaseModel):
    """An item that exists in the destination workspace today, classified
    relative to the user's goal.

    ``keep_as_is``       — already satisfies the goal; plan leaves it alone.
    ``will_be_changed``  — overlaps with the goal and must be updated,
                           renamed, extended, or re-parented by one of the
                           plan's steps. ``driven_by_step_id`` is REQUIRED
                           in this case and MUST reference a real step.
    """

    model_config = _CAMEL_CONFIG

    item: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=64)
    disposition: WorkspaceItemDisposition
    reason: str = Field(min_length=1, max_length=400)
    driven_by_step_id: str | None = Field(default=None, max_length=64)



class Conflict(BaseModel):
    model_config = _CAMEL_CONFIG

    item_type: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=800)
    resolution_options: list[str] = Field(default_factory=list)


class Clarification(BaseModel):
    model_config = _CAMEL_CONFIG

    question: str = Field(min_length=1, max_length=400)
    blocks_steps: list[str] = Field(default_factory=list)


class PlanFooter(BaseModel):
    """Server-computed roll-up used by the plan card footer.

    ``execution_blocked`` is true when any prerequisite has
    ``verification.status == "missing"`` — the UI uses it to disable the
    Execute button (spec §4.4 / §4.5).
    """

    model_config = _CAMEL_CONFIG

    agent_count: int = 0
    step_count: int = 0
    approval_points: int = 0
    execution_blocked: bool = False


class Plan(BaseModel):
    """Grounded execution plan. This is the wire shape the UI consumes."""

    model_config = _CAMEL_CONFIG

    # Carried here rather than on the enclosing Job so the planner can
    # generate a plan without having to own session state.
    job_id: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=280)
    assumptions: list[str] = Field(default_factory=list)
    prerequisites: list[Prerequisite] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    # Spec §2 replacement for the old ``noAction`` / "Already done"
    # section. The shape carries a disposition so the UI can render two
    # sub-groups (Keep as-is / Will be changed).
    workspace_items: list[WorkspaceItem] = Field(default_factory=list)
    # Kept for migration only: old cached plans / early-retry drafts may
    # still emit ``noAction``. We coerce those to ``workspace_items`` at
    # parse time so nothing downstream sees the legacy shape.
    no_action: list[NoActionItem] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    clarifications_needed: list[Clarification] = Field(default_factory=list)
    footer: PlanFooter = Field(default_factory=PlanFooter)

    def is_actionable(self) -> bool:
        """A plan is actionable when it has at least one non-clarify step and
        no outstanding conflicts / clarifications.
        """
        if self.conflicts or self.clarifications_needed:
            return False
        return any(s.action != "clarify" for s in self.steps)


# ── Orchestrator input DTOs (server-side, never sent to UI) ──────────


class DiffEntryAction(BaseModel):
    """A single reality-check entry the planner will consume.

    The planner is not allowed to invent; every step it proposes must be
    justified by one of these entries (or a prerequisite / assumption).
    """

    model_config = _CAMEL_CONFIG

    kind: Literal["CREATE", "UPDATE", "NO_ACTION", "CONFLICT", "MISSING_PREREQ"]
    item_type: str
    display_name: str
    existing_item_id: str | None = None
    details: str = ""


class WorkspaceSnapshot(BaseModel):
    """What we know about the destination workspace right now.

    Populated by ``workspace_state.gather_current_state``. Kept small and
    serializable so it can be inlined in the planner prompt.
    """

    model_config = _CAMEL_CONFIG

    workspace_id: str
    workspace_name: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    lakehouse_tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    semantic_model_tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    # Soft-failed lookups: we record them so the planner knows the picture is
    # incomplete and can return a ``clarify`` step instead of guessing.
    lookup_failures: list[str] = Field(default_factory=list)
