"""Data models for the AgentHub multi-agent orchestration system."""

from __future__ import annotations

import enum
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from domain.models.composition import Composition
from domain.models.skill import Skill

# Size caps for user-submitted request payloads. These are defense-in-depth
# limits that guard against CPU/memory exhaustion on the LLM and embedding
# pipelines; they are not intended as primary authorization checks.
_MAX_TASK_DESCRIPTION_LEN = 16_000
_MAX_MESSAGE_LEN = 8_000
_MAX_PROMPT_ADDITIONS_LEN = 4_000
_MAX_ATTACHMENTS = 10
# Per-attachment string cap on the raw wire payload. For binary kinds
# (image, pdf) this is a base64 data URI, which inflates by 4/3 plus a
# small header + padding. We size this to fit a file up to
# ``services.agenthub.attachments.MAX_BYTES_PER_FILE`` (10 MB) after
# base64 expansion, with slack. The authoritative byte-level caps live in
# ``attachments.py`` (per-file + total-across-attachments) and are applied
# *after* decoding.
_MAX_ATTACHMENT_CONTENT_LEN = 14 * 1024 * 1024  # ~14 MB — fits 10 MB raw, base64-encoded
_MAX_CONTEXT_DEPTH = 5
_WORKSPACE_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_AGENT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")

# ── Enums ────────────────────────────────────────────────────────────

class JobStatus(enum.StrEnum):
    PLANNED = "planned"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"


class PhaseStatus(enum.StrEnum):
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentCategory(enum.StrEnum):
    ENGINEERING = "ENGINEERING"
    ANALYTICS = "ANALYTICS"
    ADMIN = "ADMIN"


class MessageType(enum.StrEnum):
    USER_INTERVENTION = "user_intervention"
    AGENT_MESSAGE = "agent_message"
    ORCHESTRATOR_DIRECTIVE = "orchestrator_directive"
    STATUS_UPDATE = "status_update"


# ── Agent Templates ──────────────────────────────────────────────────

class AgentTemplate(BaseModel):
    id: str
    name: str
    display_name: str
    category: AgentCategory
    description: str
    tags: list[str] = Field(default_factory=list)
    # First-class skills. Replaces ``tags`` as the discoverability
    # surface the compose LLM uses to pick *which skills from each
    # agent* to expose for a task. ``tags`` remain as short display
    # chips but are no longer referenced by compose.
    skills: list[Skill] = Field(default_factory=list)
    system_prompt: str
    available_tools: list[str] = Field(default_factory=list)
    default_access_level: str = "read"
    icon: str | None = None
    version: str = "1.0.0"


class UserAgentConfig(BaseModel):
    id: str
    user_id: str
    # Human-readable UPN (e.g. "alice@contoso.com"). Stored alongside
    # ``user_id`` (the oid-based machine key) purely so a human browsing
    # the DB or logs can tell who owns a row at a glance. Internal lookups
    # must always use ``user_id``.
    user_upn: str | None = None
    agent_template_id: str
    access_levels: dict[str, bool] = Field(default_factory=dict)
    tool_integrations: dict[str, bool] = Field(default_factory=dict)
    runtime_schedule: str | None = None
    custom_prompt_additions: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Reasoning Phases & Actions ───────────────────────────────────────

class AgentDecision(BaseModel):
    summary: str
    reasoning: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReasoningPhase(BaseModel):
    phase_number: int
    title: str
    description: str
    status: PhaseStatus = PhaseStatus.EXECUTING
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    details: list[str] = Field(default_factory=list)
    decisions: list[AgentDecision] = Field(default_factory=list)
    # P6 · Mission Control — owner_agent lets the completed-run log filter
    # entries by agent. Optional because pre-P6 phases won't carry it.
    owner_agent: str | None = None


class AgentAction(BaseModel):
    id: str
    action_type: str          # "Modified", "Created", "Optimized", "Deleted"
    entity_name: str
    entity_type: str          # "SQL Script", "Pipeline", "Schema", etc.
    fabric_item_id: str | None = None
    web_url: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: str | None = None


# ── Agent Assignments (inside a Job) ────────────────────────────────

class AgentAssignment(BaseModel):
    agent_id: str             # Template ID
    config_id: str | None = None
    session_id: str           # Runtime session ID (UUID)
    role: str
    status: AgentStatus = AgentStatus.QUEUED
    goal: str
    current_step: str | None = None
    phases: list[ReasoningPhase] = Field(default_factory=list)
    actions: list[AgentAction] = Field(default_factory=list)


# ── Jobs ─────────────────────────────────────────────────────────────

class Job(BaseModel):
    id: str
    user_id: str
    # Human-readable UPN stored alongside ``user_id`` for easy inspection
    # in the DB / logs. Not used for any authorization decision.
    user_upn: str | None = None
    workspace_id: str
    task_description: str
    context: dict[str, Any] | None = None
    status: JobStatus = JobStatus.PLANNED
    # Composition artifact produced by ``ComposeService.compose()`` — the
    # architecture + slot + handoff graph the runtime executes. Replaces
    # the legacy ``Plan`` object.
    composition: Composition | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Populated when a user cancels the session via the UI. Preserved on
    # the row so the Recent-Sessions view can surface "who stopped this
    # and when" instead of silently deleting the record.
    cancelled_at: datetime | None = None
    cancelled_by_user_id: str | None = None
    cancelled_by_upn: str | None = None
    agents: list[AgentAssignment] = Field(default_factory=list)


# ── SSE Event Payloads ──────────────────────────────────────────────

class SSEEvent(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


# ── API Request / Response Models ───────────────────────────────────

class PromptAttachment(BaseModel):
    """A file the user attached to their prompt.

    ``kind`` determines how ``content`` is interpreted:

    * ``"text"``  — raw UTF-8 file contents (code, markdown, CSV, etc.).
    * ``"image"`` — base64 data URI (``data:image/png;base64,…``).
    * ``"pdf"``   — base64 data URI (``data:application/pdf;base64,…``);
      the backend extracts text via ``pypdf``.
    """

    name: str = Field(max_length=255)
    kind: str = Field(pattern=r"^(text|image|pdf)$")
    mime: str | None = Field(default=None, max_length=127)
    content: str = Field(max_length=_MAX_ATTACHMENT_CONTENT_LEN)


class CreateJobRequest(BaseModel):
    task_description: str = Field(min_length=1, max_length=_MAX_TASK_DESCRIPTION_LEN)
    workspace_id: str = Field(min_length=1, max_length=128)
    context: dict[str, Any] | None = None
    attachments: list[PromptAttachment] | None = Field(default=None, max_length=_MAX_ATTACHMENTS)

    @field_validator("workspace_id")
    @classmethod
    def _validate_workspace_id(cls, v: str) -> str:
        if not _WORKSPACE_ID_RE.match(v):
            raise ValueError("workspace_id must be a UUID")
        return v.lower()


class GeneratePlanRequest(BaseModel):
    """Legacy alias — kept as a type so ``api.agenthub_controller`` can
    import it without breaking during the compose cutover. The new
    ``/api/orchestrate/compose`` endpoint uses ``ComposeRequest`` below.
    """
    task_description: str = Field(min_length=1, max_length=_MAX_TASK_DESCRIPTION_LEN)
    workspace_id: str = Field(min_length=1, max_length=128)
    context: dict[str, Any] | None = None
    attachments: list[PromptAttachment] | None = Field(default=None, max_length=_MAX_ATTACHMENTS)

    @field_validator("workspace_id")
    @classmethod
    def _validate_workspace_id(cls, v: str) -> str:
        if not _WORKSPACE_ID_RE.match(v):
            raise ValueError("workspace_id must be a UUID")
        return v.lower()


class ComposeRequest(BaseModel):
    """Request body for ``POST /api/orchestrate/compose`` — the single
    analysis step that produces a ``Composition``.
    """

    task_description: str = Field(min_length=1, max_length=_MAX_TASK_DESCRIPTION_LEN)
    workspace_id: str = Field(min_length=1, max_length=128)
    context: dict[str, Any] | None = None
    attachments: list[PromptAttachment] | None = Field(default=None, max_length=_MAX_ATTACHMENTS)
    # Optional architecture override (same wire values as
    # ``Composition.architecture``). If set, the compose LLM is told to
    # prefer this shape. Used by the "Regenerate as …" UI affordance.
    preferred_architecture: str | None = Field(default=None, max_length=32)
    require_approvals: bool = True
    branch_out: bool = False

    @field_validator("workspace_id")
    @classmethod
    def _validate_workspace_id(cls, v: str) -> str:
        if not _WORKSPACE_ID_RE.match(v):
            raise ValueError("workspace_id must be a UUID")
        return v.lower()


class RunSessionRequest(BaseModel):
    """Request body for ``POST /api/sessions/{id}/run`` — starts
    executing an already-composed session."""

    session_id: str = Field(min_length=1, max_length=128)


class ApprovePlanRequest(BaseModel):
    """Legacy alias kept so the existing approval-card flow doesn't
    break; routes prefer ``RunSessionRequest`` for the new surface."""
    session_id: str = Field(min_length=1, max_length=128)


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=_MAX_MESSAGE_LEN)
    target_agent_id: str | None = Field(default=None, max_length=64)

    @field_validator("target_agent_id")
    @classmethod
    def _validate_agent_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _AGENT_ID_RE.match(v):
            raise ValueError("target_agent_id has an invalid format")
        return v


class AgentConfigRequest(BaseModel):
    agent_template_id: str = Field(min_length=1, max_length=64)
    access_levels: dict[str, bool] = Field(default_factory=dict)
    tool_integrations: dict[str, bool] = Field(default_factory=dict)
    runtime_schedule: str | None = Field(default=None, max_length=256)
    custom_prompt_additions: str | None = Field(default=None, max_length=_MAX_PROMPT_ADDITIONS_LEN)

    @field_validator("agent_template_id")
    @classmethod
    def _validate_template_id(cls, v: str) -> str:
        if not _AGENT_ID_RE.match(v):
            raise ValueError("agent_template_id has an invalid format")
        return v
