"""Models for dynamic AgentHub mission orchestration."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_CAMEL_CONFIG = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="forbid",
)


class MissionStatus(enum.StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubagentStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentResultStatus(enum.StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResourceMode(enum.StrEnum):
    READ = "read"
    WRITE = "write"


class OrchestratorActionType(enum.StrEnum):
    CREATE_TASK = "create_task"
    SPAWN_SUBAGENT = "spawn_subagent"
    SPAWN_PARALLEL_GROUP = "spawn_parallel_group"
    STEER_SUBAGENT = "steer_subagent"
    INSPECT_SUBAGENT_LOGS = "inspect_subagent_logs"
    CANCEL_SUBAGENT = "cancel_subagent"
    RETRY_TASK = "retry_task"
    MERGE_RESULT = "merge_result"
    REQUEST_USER_APPROVAL = "request_user_approval"
    ASK_USER_CLARIFICATION = "ask_user_clarification"
    MARK_TASK_BLOCKED = "mark_task_blocked"
    FINISH_MISSION = "finish_mission"
    FAIL_MISSION = "fail_mission"


class MissionBudget(BaseModel):
    model_config = _CAMEL_CONFIG

    max_active_subagents: int = Field(default=4, ge=1, le=64)
    max_total_subagents: int = Field(default=20, ge=1, le=500)
    max_replans: int = Field(default=20, ge=0, le=500)
    max_task_graph_depth: int = Field(default=6, ge=1, le=32)


class MissionBrief(BaseModel):
    model_config = _CAMEL_CONFIG

    session_id: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=16_000)
    workspace_id: str = Field(min_length=1, max_length=128)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    budget: MissionBudget = Field(default_factory=MissionBudget)
    preferred_strategy: str | None = Field(default=None, max_length=64)
    initial_context_refs: list[str] = Field(default_factory=list)


class ResourceClaim(BaseModel):
    model_config = _CAMEL_CONFIG

    kind: str = Field(min_length=1, max_length=80)
    id: str = Field(min_length=1, max_length=256)
    mode: ResourceMode = ResourceMode.READ

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"


class ResourceLock(BaseModel):
    model_config = _CAMEL_CONFIG

    key: str = Field(min_length=1, max_length=360)
    mode: ResourceMode
    owner_run_ids: list[str] = Field(default_factory=list)


class FollowupTask(BaseModel):
    model_config = _CAMEL_CONFIG

    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=4_000)
    candidate_agent_ids: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    delegation_reason: str | None = Field(default=None, max_length=1_000)
    context_summary: str | None = Field(default=None, max_length=2_000)
    touch_targets: list[str] = Field(default_factory=list)
    do_not_touch: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    resource_claims: list[ResourceClaim] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)
    parallelism_safe: bool = False
    parallelism_notes: str | None = Field(default=None, max_length=1_000)
    priority: int = Field(default=100, ge=0, le=10_000)


class TaskNode(BaseModel):
    model_config = _CAMEL_CONFIG

    id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}")
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=8_000)
    status: TaskStatus = TaskStatus.QUEUED
    priority: int = Field(default=100, ge=0, le=10_000)
    dependencies: list[str] = Field(default_factory=list)
    assigned_agent_run_id: str | None = None
    candidate_agent_ids: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    delegation_reason: str | None = Field(default=None, max_length=1_000)
    context_summary: str | None = Field(default=None, max_length=2_000)
    touch_targets: list[str] = Field(default_factory=list)
    do_not_touch: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    resource_claims: list[ResourceClaim] = Field(default_factory=list)
    tool_scope: list[str] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)
    parallelism_safe: bool = False
    parallelism_notes: str | None = Field(default=None, max_length=1_000)
    result_ref: str | None = None
    created_by: Literal["orchestrator", "user", "subagent", "composition"] = "orchestrator"
    parent_task_id: str | None = None
    depth: int = Field(default=0, ge=0, le=64)


class SubagentRun(BaseModel):
    model_config = _CAMEL_CONFIG

    id: str = Field(default_factory=lambda: f"run-{uuid.uuid4().hex[:12]}")
    task_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=120)
    agent_session_id: str | None = Field(default=None, max_length=128)
    status: SubagentStatus = SubagentStatus.QUEUED
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    tool_scope: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    context_pack_ref: str | None = None
    log_cursor: int = 0
    result_ref: str | None = None
    cancellation_reason: str | None = None
    directives: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    model_config = _CAMEL_CONFIG

    id: str = Field(default_factory=lambda: f"result-{uuid.uuid4().hex[:12]}")
    run_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    status: AgentResultStatus
    summary: str = Field(min_length=1, max_length=8_000)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any] | str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    followup_tasks: list[FollowupTask] = Field(default_factory=list)
    handoff_context: dict[str, Any] | str | None = None


class OrchestratorAction(BaseModel):
    model_config = _CAMEL_CONFIG

    id: str = Field(default_factory=lambda: f"decision-{uuid.uuid4().hex[:12]}")
    type: OrchestratorActionType
    rationale: str = Field(min_length=1, max_length=2_000)
    task_id: str | None = Field(default=None, max_length=128)
    target_run_id: str | None = Field(default=None, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    applied: bool = True


class MissionState(BaseModel):
    model_config = _CAMEL_CONFIG

    brief: MissionBrief
    status: MissionStatus = MissionStatus.ACTIVE
    tasks: dict[str, TaskNode] = Field(default_factory=dict)
    subagent_runs: dict[str, SubagentRun] = Field(default_factory=dict)
    results: dict[str, AgentResult] = Field(default_factory=dict)
    blackboard: dict[str, Any] = Field(default_factory=dict)
    resource_locks: dict[str, ResourceLock] = Field(default_factory=dict)
    decisions: list[OrchestratorAction] = Field(default_factory=list)
    replans_used: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
