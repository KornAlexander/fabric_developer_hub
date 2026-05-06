"""Versioned context-pack models for AgentHub dynamic missions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_CAMEL_CONFIG = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="forbid",
)

ContextPackPhase = Literal[
    "question",
    "research",
    "design",
    "structure",
    "plan",
    "worktree",
    "implement",
    "review",
    "verify",
    "repair",
]


class ContextPackBudgetV2(BaseModel):
    model_config = _CAMEL_CONFIG

    estimated_tokens: int = Field(default=0, ge=0)
    max_tokens: int = Field(default=8_000, ge=1)
    reserved_output_tokens: int = Field(default=1_200, ge=0)
    compaction_threshold: int = Field(default=6_400, ge=1)


class ContextPackSourceBudgetV2(BaseModel):
    model_config = _CAMEL_CONFIG

    max_files: int = Field(default=12, ge=0)
    max_docs: int = Field(default=6, ge=0)
    max_fabric_items: int = Field(default=8, ge=0)
    max_prior_findings: int = Field(default=5, ge=0)


class ContextPackCompactionV2(BaseModel):
    model_config = _CAMEL_CONFIG

    prior_summary_digest: str | None = None
    omitted_detail_count: int = Field(default=0, ge=0)
    freshness: str = "current"
    reason: str = "spawn_subagent_context_window"
    threshold_tokens: int = Field(default=6_400, ge=1)


class ContextPackInstructionBudgetV2(BaseModel):
    model_config = _CAMEL_CONFIG

    phase_instruction_limit: int = Field(default=5, ge=1)
    inherited_instruction_count: int = Field(default=0, ge=0)
    max_instruction_tokens: int = Field(default=900, ge=1)
    budget_basis: str = "instructions-not-only-tokens"
    no_magic_words_required: bool = True
    compact_handoff_required: bool = True


class ContextPackBacktrackPolicyV2(BaseModel):
    model_config = _CAMEL_CONFIG

    allowed: bool = True
    valid_previous_phases: list[str] = Field(default_factory=list)
    evidence_required: list[str] = Field(default_factory=list)
    checkpoint_required: bool = True


class ContextModeTelemetryV2(BaseModel):
    model_config = _CAMEL_CONFIG

    package: str
    package_name: str = "context-mode"
    facade: str = "agenthub-governed-context-mode"
    mcp_server: dict[str, Any]
    indexed_source_refs: list[str] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)
    saved_token_estimate: int = Field(default=0, ge=0)
    compaction_digest: str | None = None
    rehydration_source: str = "none"
    purge_handle: str
    isolation_scope: str
    events: list[str] = Field(default_factory=list)


class ContextPackV2(BaseModel):
    model_config = _CAMEL_CONFIG

    schema_version: Literal[2] = 2
    qrspi_protocol: str = "question-research-design-structure-plan-implement-verify-review"
    qrspi_phase_model: list[str] = Field(default_factory=list)
    phase: ContextPackPhase
    mission: dict[str, Any]
    task: dict[str, Any]
    execution_template: dict[str, Any]
    context_goal: str
    context_budget: ContextPackBudgetV2
    instruction_budget: ContextPackInstructionBudgetV2
    phase_inputs: dict[str, Any] = Field(default_factory=dict)
    source_budget: ContextPackSourceBudgetV2
    source_refs: list[str] = Field(default_factory=list)
    retrieval_provenance: list[str] = Field(default_factory=list)
    tool_policy: dict[str, Any]
    compaction: ContextPackCompactionV2
    evidence_requirements: list[str] = Field(default_factory=list)
    redaction_proof: dict[str, Any]
    omission_policy: list[str] = Field(default_factory=list)
    return_contract: list[str] = Field(default_factory=list)
    vertical_slice_policy: dict[str, Any] = Field(default_factory=dict)
    backtrack_policy: ContextPackBacktrackPolicyV2
    review_policy: dict[str, Any] = Field(default_factory=dict)
    handoff_digest: str
    context_mode: ContextModeTelemetryV2
