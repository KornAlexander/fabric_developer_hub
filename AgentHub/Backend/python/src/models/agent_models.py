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
    tags: list[str] = []
    system_prompt: str
    available_tools: list[str] = []
    default_access_level: str = "read"
    icon: str | None = None
    version: str = "1.0.0"


class UserAgentConfig(BaseModel):
    id: str
    user_id: str
    agent_template_id: str
    access_levels: dict[str, bool] = {}
    tool_integrations: dict[str, bool] = {}
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
    details: list[str] = []
    decisions: list[AgentDecision] = []


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
    phases: list[ReasoningPhase] = []
    actions: list[AgentAction] = []


# ── Execution Plan ──────────────────────────────────────────────────

class PlannedAgent(BaseModel):
    agent_template_id: str
    role: str
    goal: str
    depends_on: list[str] = []
    tool_groups: list[str] = []


class ExecutionPlan(BaseModel):
    job_id: str
    agents: list[PlannedAgent] = []
    communication_graph: dict[str, list[str]] = {}
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
    agents: list[AgentAssignment] = []


# ── SSE Event Payloads ──────────────────────────────────────────────

class SSEEvent(BaseModel):
    type: str
    data: dict[str, Any] = {}


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
    job_id: str


class SendMessageRequest(BaseModel):
    message: str
    target_agent_id: str | None = None


class AgentConfigRequest(BaseModel):
    agent_template_id: str
    access_levels: dict[str, bool] = {}
    tool_integrations: dict[str, bool] = {}
    runtime_schedule: str | None = None
    custom_prompt_additions: str | None = None
