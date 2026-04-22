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

export interface BaseEvent {
    seq: number;
    sessionId: string;
    ts: string;
}

export interface HeartbeatEvent { type: "heartbeat"; ts?: string; }

export interface RunOverviewEvent extends BaseEvent {
    type: "run_overview";
    job: { id: string; status: JobStatusLite; startedAt: string | null; completedAt: string | null };
    composition: Composition | null;
    activeAgentId: string | null;
    artifacts: Artifact[];
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
    status: "queued" | "running" | "waiting" | "completed" | "error";
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
    | ArtifactAddedEvent
    | ArtifactUpdatedEvent
    | ApprovalRequiredEvent
    | ApprovalResolvedEvent
    | JobTerminalEvent;
