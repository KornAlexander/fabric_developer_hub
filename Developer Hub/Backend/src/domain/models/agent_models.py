"""Data models for the AgentHub multi-agent orchestration system."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

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
    system_prompt: str
    available_tools: list[str] = Field(default_factory=list)
    default_access_level: str = "read"
    icon: str | None = None
    version: str = "1.0.0"


class UserAgentConfig(BaseModel):
    id: str
    user_id: str
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


# ── Execution Plan ──────────────────────────────────────────────────

class PlannedAgent(BaseModel):
    agent_template_id: str
    role: str
    goal: str
    depends_on: list[str] = Field(default_factory=list)
    tool_groups: list[str] = Field(default_factory=list)


class ExecutionPlan(BaseModel):
    job_id: str
    agents: list[PlannedAgent] = Field(default_factory=list)
    communication_graph: dict[str, list[str]] = Field(default_factory=dict)
    estimated_duration: str | None = None
    summary: str = ""


# ── Jobs ─────────────────────────────────────────────────────────────

class Job(BaseModel):
    id: str
    user_id: str
    workspace_id: str
    task_description: str
    context: dict[str, Any] | None = None
    status: JobStatus = JobStatus.PLANNED
    plan: ExecutionPlan | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    agents: list[AgentAssignment] = Field(default_factory=list)


# ── SSE Event Payloads ──────────────────────────────────────────────

class SSEEvent(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


# ── API Request / Response Models ───────────────────────────────────

class CreateJobRequest(BaseModel):
    task_description: str
    workspace_id: str
    context: dict[str, Any] | None = None


class GeneratePlanRequest(BaseModel):
    task_description: str
    workspace_id: str
    context: dict[str, Any] | None = None


class ApprovePlanRequest(BaseModel):
    session_id: str


class SendMessageRequest(BaseModel):
    message: str
    target_agent_id: str | None = None


class AgentConfigRequest(BaseModel):
    agent_template_id: str
    access_levels: dict[str, bool] = Field(default_factory=dict)
    tool_integrations: dict[str, bool] = Field(default_factory=dict)
    runtime_schedule: str | None = None
    custom_prompt_additions: str | None = None
