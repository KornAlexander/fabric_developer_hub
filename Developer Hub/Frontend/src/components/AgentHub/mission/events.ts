/**
 * Mission Control — SSE event contract mirror.
 *
 * These types mirror the backend's ``_JobExecution.emit(...)`` payloads
 * one-to-one (see ``Backend/src/services/agenthub/orchestrator_engine.py``).
 * Wire format is camelCase; ``seq`` is the monotonic ordering key used
 * for ``Last-Event-ID`` resume and reducer deduplication.
 *
 * When a new event type is added on the backend, add it here and extend
 * ``missionReducer`` to handle it — the discriminated union keeps the
 * compiler honest.
 */

import type { Composition } from "./types";

export type PublicLogCategory = "high_level" | "detailed" | "diagnostic";
export type LogCategory = PublicLogCategory | "trace";

export interface BaseEvent {
    seq: number;
    sessionId: string;
    ts: string;
    logCategory?: LogCategory;
    eventId?: string;
    payloadDigest?: string;
    payloadSummary?: Record<string, unknown>;
}

export interface HeartbeatEvent { type: "heartbeat"; ts?: string; }

export interface RunOverviewEvent extends BaseEvent {
    type: "run_overview";
    job: { id: string; status: JobStatusLite; startedAt: string | null; completedAt: string | null };
    composition: Composition | null;
    activeAgentId: string | null;
    artifacts: Artifact[];
    changes?: ChangeRecord[];
    slotProgress: SlotProgress[];
}

export interface CompositionReadyEvent extends BaseEvent {
    type: "composition_ready";
    composition: Composition;
}

export interface AgentStatusEvent extends BaseEvent {
    type: "agent_status";
    agentId: string;
    agentName?: string;
    status: "queued" | "running" | "waiting" | "completed" | "error" | "failed";
    currentStep?: string;
    role?: string;
    goal?: string;
}

export interface SlotProgressEvent extends BaseEvent {
    type: "slot_progress";
    slotId: string;
    agentId: string;
    agentName?: string;
    role?: string;
    status: "queued" | "running" | "done" | "approval_required" | "failed";
    activeAgentId?: string;
    reason?: string;
}

export interface LogLineEvent extends BaseEvent {
    type: "log_line";
    agentId?: string;
    agentName?: string;
    level: "info" | "warn" | "error";
    message: string;
    tags?: string[];
}

export interface PhaseStartEvent extends BaseEvent {
    type: "phase_start";
    agentId: string;
    agentName?: string;
    phase: { number: number; title: string; timestamp: string };
}

export interface PhaseCompleteEvent extends BaseEvent {
    type: "phase_complete";
    agentId: string;
    agentName?: string;
    phaseNumber: number;
}

export interface PhaseDetailEvent extends BaseEvent {
    type: "phase_detail";
    agentId: string;
    agentName?: string;
    phaseNumber: number;
    detail: string;
}

export interface AgentDecisionEvent extends BaseEvent {
    type: "agent_decision";
    agentId: string;
    agentName?: string;
    phaseNumber: number;
    decision: string;
}

export interface AgentErrorEvent extends BaseEvent {
    type: "agent_error";
    agentId: string;
    agentName?: string;
    error: string;
    phase?: number;
}

export interface ToolCallStartedEvent extends BaseEvent {
    type: "tool_call_started";
    agentId: string;
    agentName?: string;
    callId: string;
    toolName: string;
    argsPreview?: Record<string, unknown>;
}

export interface ToolCallEndedEvent extends BaseEvent {
    type: "tool_call_ended";
    agentId: string;
    callId: string;
    toolName: string;
    durationMs: number;
    status: "ok" | "error";
    errorPreview?: string | null;
}

export interface ActionEvent extends BaseEvent {
    type: "action";
    agentId: string;
    agentName?: string;
    action: Record<string, unknown> & { id: string; action_type: string; entity_name: string; entity_type: string };
}

export type ChangeKind = "created" | "updated" | "deleted" | "important_action";
export type ChangeStatus = "applied" | "failed" | "pending";

export interface ChangeRecord {
    recordId: string;
    kind: ChangeKind;
    status: ChangeStatus;
    targetName: string;
    targetType: string;
    targetScope?: "item" | "folder" | "file" | "workspace" | "execution" | "access" | "settings" | "action" | string;
    summary: string;
    toolName: string;
    agentId?: string;
    agentName?: string;
    targetId?: string | null;
    webUrl?: string | null;
    ts: string;
}

export interface ChangeRecordedEvent extends BaseEvent, ChangeRecord {
    type: "change_recorded";
}

export interface Artifact {
    artifactId: string;
    agentId?: string;
    kind: string;
    name: string;
    state: "draft" | "written";
    webUrl?: string | null;
}

export interface SlotProgress {
    slotId: string;
    agentId: string;
    status: "queued" | "running" | "done" | "approval_required" | "failed";
    agentName?: string;
    role?: string;
    reason?: string;
}

export interface ArtifactAddedEvent extends BaseEvent, Artifact {
    type: "artifact_added";
}

export interface ArtifactUpdatedEvent extends BaseEvent {
    type: "artifact_updated";
    artifactId: string;
    state: "draft" | "written";
    webUrl?: string | null;
}

export interface ApprovalRequiredEvent extends BaseEvent {
    type: "approval_required";
    approvalId: string;
    slotId?: string;
    agentId?: string;
    summary: string;
    blastRadius?: string | null;
    reversible?: boolean | null;
    toolCallPreview?: { name: string; args: Record<string, unknown> } | null;
    recoveryActions?: string[];
}

export interface ApprovalResolvedEvent extends BaseEvent {
    type: "approval.resolved";
    approvalId: string;
    action: string;
    reason?: string | null;
}

export interface AgentAddedEvent extends BaseEvent {
    type: "agent_added";
    jobId: string;
    taskId?: string;
    runId?: string;
    agent: {
        agentId?: string;
        agent_id?: string;
        sessionId?: string;
        session_id?: string;
        role?: string;
        goal?: string;
        status?: string;
    };
}

export interface OrchestratorDecisionEvent extends BaseEvent {
    type: "orchestrator_decision";
    decision: {
        id: string;
        type: string;
        rationale: string;
        taskId?: string | null;
        task_id?: string | null;
        targetRunId?: string | null;
        target_run_id?: string | null;
        payload?: Record<string, unknown>;
    };
}

export interface TaskCreatedEvent extends BaseEvent {
    type: "task_created";
    task: {
        id: string;
        title: string;
        objective?: string;
        status?: string;
        assignedAgentRunId?: string | null;
        assigned_agent_run_id?: string | null;
    };
}

export interface TaskBlockedEvent extends BaseEvent {
    type: "task_blocked";
    taskId: string;
    reason: string;
    message?: string;
}

export interface TaskFailedEvent extends BaseEvent {
    type: "task_failed";
    taskId: string;
    reason: string;
    message?: string;
}

export interface GeneralistCheckInEvent extends BaseEvent {
    type: "generalist_check_in";
    queuedTaskCount?: number;
    readyTaskCount?: number;
    runningSubagentCount?: number;
    completedTaskCount?: number;
    blockedTaskCount?: number;
    failedTaskCount?: number;
    readyTaskIds?: string[];
}

export interface GeneralistContextPackEvent extends BaseEvent {
    type: "generalist_context_pack" | "agent_context_received";
    runId: string;
    taskId: string;
    agentId: string;
    agentName?: string;
    contextPackRef?: string;
    contextDigest?: string;
    taskTitle?: string;
    objectivePreview?: string;
    steeringPreview?: string;
    toolScope?: string[];
    toolScopeCount?: number;
    upstreamResultCount?: number;
    specialistCatalogCount?: number;
    acceptanceCriteriaCount?: number;
}

export interface GeneralistDirectWorkEvent extends BaseEvent {
    type: "generalist_direct_work";
    runId: string;
    taskId: string;
    agentId?: string;
    taskTitle?: string;
    reason: string;
    toolScopeCount?: number;
    objectivePreview?: string;
    contextDigest?: string;
}

export interface GeneralistStateDecisionEvent extends BaseEvent {
    type: "generalist_state_decision";
    runId: string;
    taskId: string;
    agentId?: string;
    resultStatus?: string;
    taskStatus?: string;
    summary?: string;
    rationale?: string;
    artifactCount?: number;
    evidenceCount?: number;
    errorCount?: number;
    followupTaskCount?: number;
}

export interface GeneralistSteeringEvent extends BaseEvent {
    type: "generalist_steering" | "subagent_steered";
    runId: string;
    taskId: string;
    agentId?: string;
    agentName?: string;
    reason: string;
    message?: string;
    directiveCount?: number;
}

export interface SubagentInspectedEvent extends BaseEvent {
    type: "subagent_inspected";
    runId: string;
    taskId: string;
    agentId?: string;
    signal?: Record<string, unknown>;
    matchingSignalCount?: number;
}

export interface SubagentStaleEvent extends BaseEvent {
    type: "subagent_stale";
    runId: string;
    taskId: string;
    staleSeconds?: number;
}

export interface SubagentAbandonedEvent extends BaseEvent {
    type: "subagent_abandoned";
    runId: string;
    taskId: string;
    replacementTaskId: string;
    reason: string;
}

export interface SubagentCancelledEvent extends BaseEvent {
    type: "subagent_cancelled";
    runId: string;
    taskId: string;
    reason: string;
}

export interface SubagentSpawnedEvent extends BaseEvent {
    type: "subagent_spawned";
    run: {
        id: string;
        taskId?: string;
        task_id?: string;
        agentId?: string;
        agent_id?: string;
        agentSessionId?: string | null;
        agent_session_id?: string | null;
        status?: string;
    };
    task?: { id: string; title?: string; objective?: string };
}

export interface ParallelGroupSpawnedEvent extends BaseEvent {
    type: "parallel_group_spawned";
    runIds: string[];
}

export interface SubagentResultEvent extends BaseEvent {
    type: "subagent_result";
    runId: string;
    taskId: string;
    result: {
        id: string;
        status: "success" | "partial" | "blocked" | "failed" | "cancelled";
        summary: string;
        artifacts?: Array<Record<string, unknown>>;
        errors?: string[];
        caveats?: string[];
    };
}

export interface MissionReplannedEvent extends BaseEvent {
    type: "mission_replanned";
    parentTaskId: string;
    taskId: string;
}

export interface VerifierVerdictEvent extends BaseEvent {
    type: "verifier_verdict";
    verdictId: string;
    verifierRunId: string;
    verifierTaskId: string;
    verifierAgentId?: string;
    targetTaskId?: string | null;
    passed: boolean;
    verifierClaimedSuccess: boolean;
    structuralFailures: string[];
    requiresUserBrowserRender: boolean;
    deliverables: Array<{ id?: string | null; type: string; name?: string | null; webUrl?: string | null }>;
    evidence: {
        browserVerifiedUrls: string[];
        screenshotPaths: string[];
        visualsRendered: boolean;
        loadingStuckObserved: boolean;
        errorsObserved: string[];
        expectedTextMatched?: boolean | null;
    };
    criteria: string[];
    decisionRationale: string;
    summary?: string;
    feedbackRound?: number;
    planStateSnapshot?: Record<string, unknown>;
    timestampUtc?: string;
}

export interface DynamicMissionLifecycleEvent extends BaseEvent {
    type: "mission_seeded" | "mission_completed" | "mission_blocked" | "mission_failed" | "mission_cancelled";
    taskCount?: number;
    reason?: string;
}

export interface DynamicResourceLockEvent extends BaseEvent {
    type: "resource_lock_acquired" | "resource_lock_released";
    key: string;
    mode?: "read" | "write";
    runId: string;
}

export type JobStatusLite =
    | "planned" | "approved" | "running" | "completed" | "failed" | "cancelled";

export interface JobTerminalEvent extends BaseEvent {
    type: "job_complete" | "job_failed" | "job_cancelled";
    jobId: string;
    status: JobStatusLite;
    totalDuration?: string;
}

export type MissionEvent =
    | RunOverviewEvent
    | CompositionReadyEvent
    | AgentStatusEvent
    | SlotProgressEvent
    | LogLineEvent
    | PhaseStartEvent
    | PhaseCompleteEvent
    | PhaseDetailEvent
    | AgentDecisionEvent
    | AgentErrorEvent
    | ToolCallStartedEvent
    | ToolCallEndedEvent
    | ActionEvent
    | ChangeRecordedEvent
    | ArtifactAddedEvent
    | ArtifactUpdatedEvent
    | ApprovalRequiredEvent
    | ApprovalResolvedEvent
    | AgentAddedEvent
    | OrchestratorDecisionEvent
    | TaskCreatedEvent
    | TaskBlockedEvent
    | TaskFailedEvent
    | GeneralistCheckInEvent
    | GeneralistContextPackEvent
    | GeneralistDirectWorkEvent
    | GeneralistStateDecisionEvent
    | GeneralistSteeringEvent
    | SubagentInspectedEvent
    | SubagentStaleEvent
    | SubagentAbandonedEvent
    | SubagentCancelledEvent
    | SubagentSpawnedEvent
    | ParallelGroupSpawnedEvent
    | SubagentResultEvent
    | MissionReplannedEvent
    | VerifierVerdictEvent
    | DynamicMissionLifecycleEvent
    | DynamicResourceLockEvent
    | JobTerminalEvent;
