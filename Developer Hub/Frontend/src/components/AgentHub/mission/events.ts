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

export interface PiMissionExtensionMetadata {
    id: string;
    label?: string;
    packageName?: string;
    version?: string;
}

export interface BaseEvent {
    seq: number;
    sessionId: string;
    ts: string;
    logCategory?: LogCategory;
    eventId?: string;
    payloadDigest?: string;
    payloadSummary?: Record<string, unknown>;
    extension?: PiMissionExtensionMetadata;
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
    status: "queued" | "running" | "waiting" | "done" | "approval_required" | "failed";
    activeAgentId?: string;
    reason?: string;
    currentStep?: string;
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
    toolKind?: string;
    operationKind?: string;
    argsPreview?: Record<string, unknown>;
}

export interface ToolCallEndedEvent extends BaseEvent {
    type: "tool_call_ended";
    agentId: string;
    callId: string;
    toolName: string;
    toolKind?: string;
    operationKind?: string;
    durationMs: number;
    latencyBreakdownMs?: Record<string, number>;
    status: "ok" | "error";
    errorPreview?: string | null;
}

/**
 * Live progress signal emitted from inside a long-running MCP tool. Mirrors
 * the backend ``[TOOL_PROGRESS:...]`` log lines (e.g. semantic-model build
 * ``step=lakehouse_table_validation status=started elapsedMs=147466``) so
 * the UI can show the user *what is actually happening right now* inside a
 * tool call instead of an opaque "running" spinner.
 */
export interface ToolProgressEvent extends BaseEvent {
    type: "tool_progress";
    agentId?: string;
    agentName?: string;
    toolName: string;
    toolKind?: string;
    operationKind?: string;
    callId?: string;
    step: string;
    status: string;
    elapsedMs?: number;
    runId?: string;
    taskId?: string;
    taskTitle?: string;
    workspaceId?: string;
    error?: string | null;
    digest?: string;
    /** The full backend payload, retained for diagnostics tooltips. */
    detail?: Record<string, unknown>;
}

export type LlmStreamPhase =
    | "requesting"
    | "thinking"
    | "responding"
    | "tool_input"
    | "tool-use"
    | "tool_use"
    | "idle"
    | "complete"
    | string;

export interface LlmRequestStartedEvent extends BaseEvent {
    type: "llm_request_started";
    agentId?: string;
    agentName?: string;
    requestId?: string;
    model?: string;
    taskTitle?: string;
    promptSummary?: string;
}

export interface LlmStreamPhaseChangedEvent extends BaseEvent {
    type: "llm_stream_phase_changed";
    agentId?: string;
    agentName?: string;
    requestId?: string;
    phase: LlmStreamPhase;
    message?: string;
    taskTitle?: string;
    model?: string;
    tokenCount?: number;
    toolName?: string;
}

export interface AssistantTextStreamEvent extends BaseEvent {
    type: "assistant_text_delta" | "assistant_text_finalized";
    agentId?: string;
    agentName?: string;
    requestId?: string;
    delta?: string;
    text?: string;
    tokenCount?: number;
}

export interface ThinkingStreamEvent extends BaseEvent {
    type: "thinking_started" | "thinking_delta" | "thinking_finalized";
    agentId?: string;
    agentName?: string;
    requestId?: string;
    summary?: string;
    delta?: string;
    text?: string;
    tokenCount?: number;
}

export interface ActivityRollupEvent extends BaseEvent {
    type: "activity_rollup";
    scope: "run" | "task" | "tool_batch" | "mission" | string;
    agentId?: string;
    agentName?: string;
    runId?: string;
    taskId?: string;
    callId?: string;
    toolName?: string;
    toolKind?: string;
    operationKind?: string;
    summary: string;
    coveredSeqStart?: number | null;
    coveredSeqEnd?: number | null;
    detailCount?: number;
    status?: "completed" | "in_progress" | "failed" | string;
    durationMs?: number;
    counts?: Record<string, number | string | boolean | null>;
}

export interface UserMessageQueuedEvent extends BaseEvent {
    type: "user_message_queued" | "user_message_broadcast" | "user_message_delivered" | "user_message_failed";
    steeringId: string;
    targetAgentSessionId?: string | null;
    targetAgentSessionIds?: string[];
    agentId?: string;
    agentName?: string;
    targetMode?: "agent" | "broadcast" | "generalist" | string;
    mode?: "queue" | "interrupt" | string;
    messagePreview?: string;
    reason?: string;
    targetCount?: number;
    queuedAt?: string;
    deliveredAtRound?: number;
}

export interface TurnInterruptEvent extends BaseEvent {
    type: "turn_interrupt_requested" | "turn_interrupt_deferred" | "turn_interrupted";
    steeringId: string;
    targetAgentSessionId?: string | null;
    agentId?: string;
    agentName?: string;
    targetMode?: string;
    mode?: "queue" | "interrupt" | string;
    messagePreview?: string;
    reason?: string;
}

export interface DiagnosticEvent extends BaseEvent {
    type: "diagnostic_baseline_captured" | "diagnostic_new_issues" | "diagnostic_resolved_issues" | "diagnostic_required";
    agentId?: string;
    agentName?: string;
    callId?: string;
    toolName?: string;
    toolKind?: string;
    operationKind?: string;
    status?: string;
    baselineCount?: number;
    newIssueCount?: number;
    resolvedIssueCount?: number;
    summary?: string;
    reason?: string;
    policyDecision?: string;
    diagnosticTool?: string;
    directivePreview?: string;
    issues?: Array<{ severity?: string; code?: string; message?: string } | string>;
}

export interface RuntimeGuardEvent extends BaseEvent {
    type: "budget_exhausted" | "tool_call_denied" | "mission_no_progress" | "subagent_heartbeat";
    slotId?: string;
    agentId?: string;
    agentName?: string;
    runId?: string;
    taskId?: string;
    toolName?: string;
    reason?: string;
    severity?: string;
    summary?: string;
    rationale?: string;
    status?: string;
    feedbackRound?: number;
    maxFeedbackReviewRounds?: number;
}

export interface TrustOrRuntimeEvent extends BaseEvent {
    type:
        | "mcp_server_approval_required"
        | "mcp_server_approved"
        | "mcp_server_rejected"
        | "mcp_session_refreshed"
        | "runtime_config_refreshed"
        | "memory_loaded"
        | "memory_written"
        | "memory_updated"
        | "memory_ignored"
        | "plugin_enabled"
        | "plugin_disabled"
        | "capability_pack_enabled"
        | "capability_pack_disabled"
        | "approval_repeated_denial"
        | "approval_fallback_required";
    serverId?: string;
    source?: string;
    toolsPreview?: string[];
    risk?: string;
    memoryScope?: string;
    pluginId?: string;
    capabilityPackId?: string;
    configVersion?: string;
    summary?: string;
    reason?: string;
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
    folderId?: string | null;
    folderName?: string | null;
    parentFolderId?: string | null;
    createdItems?: MissionOutputCreatedItem[] | null;
    webUrl?: string | null;
    ts: string;
}

export interface MissionOutputCreatedItem {
    id?: string | null;
    itemId?: string | null;
    displayName?: string | null;
    name?: string | null;
    type?: string | null;
    itemType?: string | null;
    workspaceId?: string | null;
    folderId?: string | null;
    folderName?: string | null;
    parentFolderId?: string | null;
    webUrl?: string | null;
    url?: string | null;
    description?: string | null;
    status?: string | null;
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
    summary?: string;
    details?: unknown;
    webUrl?: string | null;
}

export interface SlotProgress {
    slotId: string;
    agentId: string;
    status: "queued" | "running" | "waiting" | "done" | "approval_required" | "failed";
    agentName?: string;
    role?: string;
    reason?: string;
    currentStep?: string;
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
    /**
     * Per-step pass/fail breakdown emitted by the verifier when it
     * decomposes the original goal into discrete phases (ingestion,
     * transformation, semantic-model queryability, report render, ...).
     * Lets the UI show the user *exactly which step* of the mission
     * succeeded or failed instead of a single opaque verdict.
     */
    stepResults?: Array<{
        step: string;
        status: string;
        detail?: string;
        evidence?: string;
        reason?: string;
        via?: string;
        rowCount?: number;
        url?: string;
        exitValue?: string;
    }>;
    criteria: string[];
    decisionRationale: string;
    summary?: string;
    feedbackRound?: number;
    planStateSnapshot?: Record<string, unknown>;
    timestampUtc?: string;
}

export type PiMissionTrustLevel = "trusted" | "untrusted" | "redacted";

export interface PiMissionTrustMetadata {
    level: PiMissionTrustLevel;
    source?: "model" | "tool" | "fabric" | "user" | "runtime" | string;
    redacted?: boolean;
    summaryOnly?: boolean;
}

export interface PiToolDisplaySummary {
    summary?: string;
    details?: string;
    outputPreview?: string;
    trust?: PiMissionTrustMetadata;
    fields?: Record<string, string | number | boolean | null>;
}

export interface PiHarnessToolSummary {
    name: string;
    label?: string;
    description?: string;
    sensitivity?: string;
    autoAllowed?: boolean;
    execution?: string;
    parameters?: Record<string, unknown>;
}

export interface PiTurnStartEvent extends BaseEvent {
    type: "pi.turn.start";
    schemaVersion: 1;
    turnId: string;
    agentId: string;
    agentName?: string;
    model?: string;
    title?: string;
}

export interface PiOrchestrationStartEvent extends BaseEvent {
    type: "pi.orchestration.start";
    schemaVersion: 1;
    runtime: "pi";
    subagentRuntime?: "pi-subagents" | string;
    subagentPackage?: string;
    subagentHarness?: string;
    subagentRuntimeMode?: string;
    subagentObservability?: Record<string, unknown>;
    runtimePackage: string;
    runtimePackageSource?: string;
    frontendRuntimePackage: string;
    executionSurfaceExtension: string;
    agenticEngineeringExtension?: string;
    rpiProtocol?: string;
    qrspiProtocol?: string;
    qrspiPhaseModel?: string[];
    qrspiQuestionPolicy?: Record<string, unknown>;
    qrspiResearchPolicy?: Record<string, unknown>;
    qrspiDesignStructurePolicy?: Record<string, unknown>;
    qrspiInstructionBudget?: Record<string, unknown>;
    qrspiVerticalSlicePolicy?: Record<string, unknown>;
    qrspiBacktrackPolicy?: Record<string, unknown>;
    qrspiReviewPolicy?: Record<string, unknown>;
    contextPackSchema?: string;
    subagentWorkModel?: string;
    contextWindowPolicy?: Record<string, unknown>;
    contextModeFacade?: string;
    contextModeEvents?: string[];
    contextModeControls?: Record<string, unknown>;
    streamTransport: "agenthub-sse-to-pi-extension" | string;
    extensions: string[];
    orchestrationHarness?: string;
    harnessPackage?: string;
    toolRegistry?: string;
    toolExecutionBridge?: string;
    toolCount?: number;
    emittedToolCount?: number;
    toolPolicySummary?: Record<string, number>;
    tools?: PiHarnessToolSummary[];
    backendBridge?: string;
}

export interface PiTurnDeltaEvent extends BaseEvent {
    type: "pi.turn.delta";
    schemaVersion: 1;
    turnId: string;
    textDelta: string;
    trust?: PiMissionTrustMetadata;
}

export interface PiTurnEndEvent extends BaseEvent {
    type: "pi.turn.end";
    schemaVersion: 1;
    turnId: string;
    status: "completed" | "aborted" | "failed";
    reason?: string;
}

export interface PiToolStartEvent extends BaseEvent {
    type: "pi.tool.start";
    schemaVersion: 1;
    toolCallId: string;
    turnId?: string;
    agentId?: string;
    agentName?: string;
    toolName: string;
    summary: string;
    argsSummary?: string;
    sensitivity?: "read-safe" | "read-sensitive" | "write" | "destructive" | string;
}

export interface PiToolEndEvent extends BaseEvent {
    type: "pi.tool.end";
    schemaVersion: 1;
    toolCallId: string;
    turnId?: string;
    status: "ok" | "error" | "confirm_required";
    durationMs?: number;
    display?: PiToolDisplaySummary;
    errorPreview?: string | null;
}

export interface PiArtifactUpsertEvent extends BaseEvent {
    type: "pi.artifact.upsert";
    schemaVersion: 1;
    artifactId: string;
    turnId?: string;
    toolCallId?: string;
    agentId?: string;
    kind: "diff" | "screenshot" | "report" | "markdown" | "json" | "verifier" | string;
    title: string;
    summary?: string;
    webUrl?: string | null;
    previewText?: string;
    trust?: PiMissionTrustMetadata;
}

export interface PiApprovalRequestEvent extends BaseEvent {
    type: "pi.approval.request";
    schemaVersion: 1;
    requestId: string;
    turnId?: string;
    toolCallId?: string;
    agentId?: string;
    title: string;
    summary?: string;
    risk: "low" | "medium" | "high";
    actionLabel?: string;
    metadata?: Record<string, string | number | boolean | null>;
}

export interface PiClarificationRequestEvent extends BaseEvent {
    type: "pi.clarification.request";
    schemaVersion: 1;
    requestId: string;
    turnId?: string;
    agentId?: string;
    title: string;
    prompt: string;
    control: "input" | "select" | "multiSelect";
    options?: Array<{ label: string; value: string; description?: string }>;
}

export interface PiSubagentUpdateEvent extends BaseEvent {
    type: "pi.subagent.update";
    schemaVersion: 1;
    agentId: string;
    agentName?: string;
    role: string;
    state: "queued" | "running" | "blocked" | "done" | "failed";
    task?: string;
    summary?: string;
}

export interface PiSubagentsProgressEntry {
    index?: number;
    agent: string;
    status: "pending" | "running" | "completed" | "complete" | "failed" | "detached" | "paused" | "queued" | string;
    activityState?: "active_long_running" | "needs_attention" | string;
    task?: string;
    skills?: string[];
    lastActivityAt?: number;
    currentTool?: string;
    currentToolArgs?: string;
    currentToolStartedAt?: number;
    currentPath?: string;
    recentTools?: Array<{ tool: string; args?: string; endMs?: number }>;
    recentOutput?: string[];
    toolCount?: number;
    turnCount?: number;
    tokens?: number | { input?: number; output?: number; total?: number };
    durationMs?: number;
    error?: string;
}

export interface PiSubagentsStatusEvent extends BaseEvent {
    type: "pi.subagents.status";
    schemaVersion: 1;
    runId: string;
    asyncId?: string;
    mode: "single" | "parallel" | "chain" | string;
    state: "queued" | "running" | "complete" | "completed" | "failed" | "paused" | "detached" | string;
    agent?: string;
    agentId?: string;
    agentName?: string;
    task?: string;
    summary?: string;
    activityState?: "active_long_running" | "needs_attention" | string;
    currentTool?: string;
    currentToolStartedAt?: number;
    currentPath?: string;
    turnCount?: number;
    toolCount?: number;
    durationMs?: number;
    sessionFile?: string;
    outputFile?: string;
    progress?: PiSubagentsProgressEntry[];
}

export interface PiSubagentsControlEvent extends BaseEvent {
    type: "pi.subagents.control";
    schemaVersion: 1;
    runId: string;
    agent: string;
    agentId?: string;
    agentName?: string;
    controlType: "active_long_running" | "needs_attention" | string;
    to?: "active_long_running" | "needs_attention" | string;
    message: string;
    reason?: string;
    currentTool?: string;
    currentToolDurationMs?: number;
    elapsedMs?: number;
    toolCount?: number;
    turnCount?: number;
    tokens?: number;
}

export interface PiSubagentsResultEvent extends BaseEvent {
    type: "pi.subagents.result";
    schemaVersion: 1;
    runId: string;
    asyncId?: string;
    mode: "single" | "parallel" | "chain" | string;
    status: "completed" | "failed" | "paused" | "detached" | string;
    agent?: string;
    agentId?: string;
    agentName?: string;
    summary: string;
    sessionFile?: string;
    artifactPath?: string;
    artifactPaths?: Record<string, string>;
    usage?: Record<string, unknown>;
    results?: Array<Record<string, unknown>>;
}

export interface PiSubagentsAsyncEvent extends BaseEvent {
    type: "pi.subagents.async";
    schemaVersion: 1;
    asyncId: string;
    runId?: string;
    state: "queued" | "running" | "complete" | "completed" | "failed" | "paused" | string;
    mode?: "single" | "chain" | string;
    agent?: string;
    agents?: string[];
    summary?: string;
    asyncDir?: string;
    sessionDir?: string;
    outputFile?: string;
}

export interface PiContextCompactionEvent extends BaseEvent {
    type: "pi.context.compaction";
    schemaVersion: 1;
    status: "started" | "completed";
    summary?: string;
}

export interface PiRetryEvent extends BaseEvent {
    type: "pi.retry";
    schemaVersion: 1;
    status: "started" | "completed";
    reason?: string;
}

export interface PiUiRequestEvent extends BaseEvent {
    type: "pi.ui.request";
    schemaVersion: 1;
    requestId: string;
    turnId?: string;
    agentId?: string;
    title: string;
    message?: string;
    control: "notify" | "status" | "widget" | "custom";
    widgetKind?: string;
    status?: "info" | "warning" | "error" | "success" | string;
}

export type PiMissionUiEvent =
    | PiOrchestrationStartEvent
    | PiTurnStartEvent
    | PiTurnDeltaEvent
    | PiTurnEndEvent
    | PiToolStartEvent
    | PiToolEndEvent
    | PiArtifactUpsertEvent
    | PiApprovalRequestEvent
    | PiClarificationRequestEvent
    | PiSubagentUpdateEvent
    | PiSubagentsStatusEvent
    | PiSubagentsControlEvent
    | PiSubagentsResultEvent
    | PiSubagentsAsyncEvent
    | PiContextCompactionEvent
    | PiRetryEvent
    | PiUiRequestEvent;

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
    reason?: string;
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
    | ToolProgressEvent
    | LlmRequestStartedEvent
    | LlmStreamPhaseChangedEvent
    | AssistantTextStreamEvent
    | ThinkingStreamEvent
    | ActivityRollupEvent
    | UserMessageQueuedEvent
    | TurnInterruptEvent
    | DiagnosticEvent
    | RuntimeGuardEvent
    | TrustOrRuntimeEvent
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
    | PiMissionUiEvent
    | DynamicMissionLifecycleEvent
    | DynamicResourceLockEvent
    | JobTerminalEvent;
