"""Data models for the AgentHub multi-agent orchestration system."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────

class JobStatus(str, enum.Enum):
    PLANNED = "planned"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"


class PhaseStatus(str, enum.Enum):
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentCategory(str, enum.Enum):
    ENGINEERING = "ENGINEERING"
    ANALYTICS = "ANALYTICS"
    ADMIN = "ADMIN"


class MessageType(str, enum.Enum):
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
    tags: List[str] = []
    system_prompt: str
    available_tools: List[str] = []
    default_access_level: str = "read"
    icon: Optional[str] = None
    version: str = "1.0.0"


class UserAgentConfig(BaseModel):
    id: str
    user_id: str
    agent_template_id: str
    access_levels: Dict[str, bool] = {}
    tool_integrations: Dict[str, bool] = {}
    runtime_schedule: Optional[str] = None
    custom_prompt_additions: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Reasoning Phases & Actions ───────────────────────────────────────

class AgentDecision(BaseModel):
    summary: str
    reasoning: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReasoningPhase(BaseModel):
    phase_number: int
    title: str
    description: str
    status: PhaseStatus = PhaseStatus.EXECUTING
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    details: List[str] = []
    decisions: List[AgentDecision] = []


class AgentAction(BaseModel):
    id: str
    action_type: str          # "Modified", "Created", "Optimized", "Deleted"
    entity_name: str
    entity_type: str          # "SQL Script", "Pipeline", "Schema", etc.
    fabric_item_id: Optional[str] = None
    web_url: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: Optional[str] = None


# ── Agent Assignments (inside a Job) ────────────────────────────────

class AgentAssignment(BaseModel):
    agent_id: str             # Template ID
    config_id: Optional[str] = None
    session_id: str           # Runtime session ID (UUID)
    role: str
    status: AgentStatus = AgentStatus.QUEUED
    goal: str
    current_step: Optional[str] = None
    phases: List[ReasoningPhase] = []
    actions: List[AgentAction] = []


# ── Execution Plan ──────────────────────────────────────────────────

class PlannedAgent(BaseModel):
    agent_template_id: str
    role: str
    goal: str
    depends_on: List[str] = []
    tool_groups: List[str] = []


class ExecutionPlan(BaseModel):
    job_id: str
    agents: List[PlannedAgent] = []
    communication_graph: Dict[str, List[str]] = {}
    estimated_duration: Optional[str] = None
    summary: str = ""


# ── Jobs ─────────────────────────────────────────────────────────────

class Job(BaseModel):
    id: str
    user_id: str
    workspace_id: str
    task_description: str
    context: Optional[Dict[str, Any]] = None
    status: JobStatus = JobStatus.PLANNED
    plan: Optional[ExecutionPlan] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    agents: List[AgentAssignment] = []


# ── SSE Event Payloads ──────────────────────────────────────────────

class SSEEvent(BaseModel):
    type: str
    data: Dict[str, Any] = {}


# ── API Request / Response Models ───────────────────────────────────

class CreateJobRequest(BaseModel):
    task_description: str
    workspace_id: str
    context: Optional[Dict[str, Any]] = None


class GeneratePlanRequest(BaseModel):
    task_description: str
    workspace_id: str
    context: Optional[Dict[str, Any]] = None


class ApprovePlanRequest(BaseModel):
    job_id: str


class SendMessageRequest(BaseModel):
    message: str
    target_agent_id: Optional[str] = None


class AgentConfigRequest(BaseModel):
    agent_template_id: str
    access_levels: Dict[str, bool] = {}
    tool_integrations: Dict[str, bool] = {}
    runtime_schedule: Optional[str] = None
    custom_prompt_additions: Optional[str] = None
