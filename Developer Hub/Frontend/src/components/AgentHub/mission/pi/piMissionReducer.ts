import type { PiHarnessToolSummary, PiMissionExtensionMetadata, PiMissionUiEvent, PiMissionTrustMetadata, PiSubagentsProgressEntry } from "../events";

export type PiCardStatus = "running" | "completed" | "failed" | "waiting" | "queued";

export interface PiTurnView {
    turnId: string;
    agentId: string;
    agentName: string;
    model?: string;
    title?: string;
    status: PiCardStatus;
    text: string;
    startedSeq: number;
    endedSeq?: number;
    reason?: string;
}

export interface PiToolCardView {
    toolCallId: string;
    turnId?: string;
    agentId?: string;
    agentName?: string;
    toolName: string;
    summary: string;
    argsSummary?: string;
    sensitivity?: string;
    status: PiCardStatus;
    durationMs?: number;
    displaySummary?: string;
    displayDetails?: string;
    outputPreview?: string;
    trust?: PiMissionTrustMetadata;
    errorPreview?: string | null;
    extension?: PiMissionExtensionMetadata;
    startedSeq: number;
    endedSeq?: number;
}

export interface PiArtifactCardView {
    artifactId: string;
    turnId?: string;
    toolCallId?: string;
    agentId?: string;
    kind: string;
    title: string;
    summary?: string;
    webUrl?: string | null;
    previewText?: string;
    trust?: PiMissionTrustMetadata;
    extension?: PiMissionExtensionMetadata;
    seq: number;
}

export interface PiApprovalCardView {
    requestId: string;
    turnId?: string;
    toolCallId?: string;
    agentId?: string;
    title: string;
    summary?: string;
    risk: "low" | "medium" | "high";
    actionLabel?: string;
    metadata?: Record<string, string | number | boolean | null>;
    extension?: PiMissionExtensionMetadata;
    seq: number;
}

export interface PiClarificationCardView {
    requestId: string;
    turnId?: string;
    agentId?: string;
    title: string;
    prompt: string;
    control: "input" | "select" | "multiSelect";
    options: Array<{ label: string; value: string; description?: string }>;
    extension?: PiMissionExtensionMetadata;
    seq: number;
}

export interface PiSubagentView {
    agentId: string;
    agentName: string;
    role: string;
    state: "queued" | "running" | "blocked" | "done" | "failed";
    task?: string;
    summary?: string;
    extension?: PiMissionExtensionMetadata;
    seq: number;
}

export interface PiSubagentsStatusView {
    runId: string;
    asyncId?: string;
    mode: string;
    state: string;
    agent: string;
    agentId?: string;
    agentName?: string;
    task?: string;
    summary?: string;
    activityState?: string;
    currentTool?: string;
    currentToolStartedAt?: number;
    currentPath?: string;
    turnCount?: number;
    toolCount?: number;
    durationMs?: number;
    sessionFile?: string;
    outputFile?: string;
    progress: PiSubagentsProgressEntry[];
    extension?: PiMissionExtensionMetadata;
    seq: number;
}

export interface PiSubagentsControlView {
    key: string;
    runId: string;
    agent: string;
    agentId?: string;
    agentName?: string;
    controlType: string;
    to?: string;
    message: string;
    reason?: string;
    currentTool?: string;
    currentToolDurationMs?: number;
    elapsedMs?: number;
    toolCount?: number;
    turnCount?: number;
    tokens?: number;
    extension?: PiMissionExtensionMetadata;
    seq: number;
}

export interface PiSubagentsResultView {
    key: string;
    runId: string;
    asyncId?: string;
    mode: string;
    status: string;
    agent: string;
    agentId?: string;
    agentName?: string;
    summary: string;
    sessionFile?: string;
    artifactPath?: string;
    artifactPaths?: Record<string, string>;
    usage?: Record<string, unknown>;
    results?: Array<Record<string, unknown>>;
    extension?: PiMissionExtensionMetadata;
    seq: number;
}

export interface PiSubagentsAsyncView {
    asyncId: string;
    runId?: string;
    state: string;
    mode?: string;
    agent?: string;
    agents?: string[];
    summary?: string;
    asyncDir?: string;
    sessionDir?: string;
    outputFile?: string;
    extension?: PiMissionExtensionMetadata;
    seq: number;
}

export interface PiRunMarkerView {
    key: string;
    kind: "retry" | "compaction" | "ui";
    title: string;
    message?: string;
    status?: string;
    extension?: PiMissionExtensionMetadata;
    seq: number;
}

export interface PiOrchestrationView {
    runtime: "pi";
    subagentRuntime?: string;
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
    streamTransport: string;
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
    seq: number;
    extension?: PiMissionExtensionMetadata;
}

export interface PiMissionViewModel {
    turns: PiTurnView[];
    tools: PiToolCardView[];
    artifacts: PiArtifactCardView[];
    approvals: PiApprovalCardView[];
    clarifications: PiClarificationCardView[];
    subagents: PiSubagentView[];
    subagentStatuses: PiSubagentsStatusView[];
    subagentControls: PiSubagentsControlView[];
    subagentResults: PiSubagentsResultView[];
    subagentAsync: PiSubagentsAsyncView[];
    markers: PiRunMarkerView[];
    orchestration: PiOrchestrationView | null;
    availableTools: PiHarnessToolSummary[];
    extensions: string[];
    rawEventCount: number;
    latestSeq: number;
}

const PI_EVENT_TYPES = new Set<string>([
    "pi.orchestration.start",
    "pi.turn.start",
    "pi.turn.delta",
    "pi.turn.end",
    "pi.tool.start",
    "pi.tool.end",
    "pi.artifact.upsert",
    "pi.approval.request",
    "pi.clarification.request",
    "pi.subagent.update",
    "pi.subagents.status",
    "pi.subagents.control",
    "pi.subagents.result",
    "pi.subagents.async",
    "pi.context.compaction",
    "pi.retry",
    "pi.ui.request",
]);

export function isPiMissionUiEvent(event: { type?: string } | null | undefined): event is PiMissionUiEvent {
    return !!event?.type && PI_EVENT_TYPES.has(event.type);
}

export function appendPiMissionEvent(events: PiMissionUiEvent[], event: PiMissionUiEvent): PiMissionUiEvent[] {
    const MAX = 1200;
    const next = events.length >= MAX ? [...events.slice(-MAX + 1), event] : [...events, event];
    return next;
}

function turnStatus(status: PiTurnView["status"] | undefined): PiTurnView["status"] {
    return status || "running";
}

function toolStatus(status: string): PiToolCardView["status"] {
    if (status === "ok") return "completed";
    if (status === "confirm_required") return "waiting";
    return "failed";
}

function extensionLabel(extension?: PiMissionExtensionMetadata): string | null {
    if (!extension) return null;
    return extension.label || extension.packageName || extension.id || null;
}

export function buildPiMissionViewModel(events: PiMissionUiEvent[]): PiMissionViewModel {
    const sorted = [...events].sort((a, b) => (a.seq || 0) - (b.seq || 0));
    const turns = new Map<string, PiTurnView>();
    const turnOrder: string[] = [];
    const tools = new Map<string, PiToolCardView>();
    const toolOrder: string[] = [];
    const artifacts = new Map<string, PiArtifactCardView>();
    const artifactOrder: string[] = [];
    const approvals = new Map<string, PiApprovalCardView>();
    const approvalOrder: string[] = [];
    const clarifications = new Map<string, PiClarificationCardView>();
    const clarificationOrder: string[] = [];
    const subagents = new Map<string, PiSubagentView>();
    const subagentOrder: string[] = [];
    const subagentStatuses = new Map<string, PiSubagentsStatusView>();
    const subagentStatusOrder: string[] = [];
    const subagentControls: PiSubagentsControlView[] = [];
    const subagentResults: PiSubagentsResultView[] = [];
    const subagentAsync = new Map<string, PiSubagentsAsyncView>();
    const subagentAsyncOrder: string[] = [];
    const markers: PiRunMarkerView[] = [];
    let orchestration: PiOrchestrationView | null = null;
    let availableTools: PiHarnessToolSummary[] = [];
    const extensionLabels: string[] = [];
    const extensionSeen = new Set<string>();

    for (const event of sorted) {
        const label = extensionLabel(event.extension);
        if (label && !extensionSeen.has(label)) {
            extensionSeen.add(label);
            extensionLabels.push(label);
        }

        switch (event.type) {
            case "pi.orchestration.start":
                orchestration = {
                    runtime: event.runtime,
                    subagentRuntime: event.subagentRuntime,
                    subagentPackage: event.subagentPackage,
                    subagentHarness: event.subagentHarness,
                    subagentRuntimeMode: event.subagentRuntimeMode,
                    subagentObservability: event.subagentObservability,
                    runtimePackage: event.runtimePackage,
                    runtimePackageSource: event.runtimePackageSource,
                    frontendRuntimePackage: event.frontendRuntimePackage,
                    executionSurfaceExtension: event.executionSurfaceExtension,
                    agenticEngineeringExtension: event.agenticEngineeringExtension,
                    rpiProtocol: event.rpiProtocol,
                    qrspiProtocol: event.qrspiProtocol,
                    qrspiPhaseModel: event.qrspiPhaseModel,
                    qrspiQuestionPolicy: event.qrspiQuestionPolicy,
                    qrspiResearchPolicy: event.qrspiResearchPolicy,
                    qrspiDesignStructurePolicy: event.qrspiDesignStructurePolicy,
                    qrspiInstructionBudget: event.qrspiInstructionBudget,
                    qrspiVerticalSlicePolicy: event.qrspiVerticalSlicePolicy,
                    qrspiBacktrackPolicy: event.qrspiBacktrackPolicy,
                    qrspiReviewPolicy: event.qrspiReviewPolicy,
                    contextPackSchema: event.contextPackSchema,
                    subagentWorkModel: event.subagentWorkModel,
                    contextWindowPolicy: event.contextWindowPolicy,
                    contextModeFacade: event.contextModeFacade,
                    contextModeEvents: event.contextModeEvents,
                    contextModeControls: event.contextModeControls,
                    streamTransport: event.streamTransport,
                    extensions: event.extensions,
                    orchestrationHarness: event.orchestrationHarness,
                    harnessPackage: event.harnessPackage,
                    toolRegistry: event.toolRegistry,
                    toolExecutionBridge: event.toolExecutionBridge,
                    toolCount: event.toolCount,
                    emittedToolCount: event.emittedToolCount,
                    toolPolicySummary: event.toolPolicySummary,
                    tools: event.tools,
                    backendBridge: event.backendBridge,
                    seq: event.seq,
                    extension: event.extension,
                };
                availableTools = event.tools || [];
                for (const extension of event.extensions) {
                    if (!extensionSeen.has(extension)) {
                        extensionSeen.add(extension);
                        extensionLabels.push(extension);
                    }
                }
                break;
            case "pi.turn.start": {
                if (!turns.has(event.turnId)) turnOrder.push(event.turnId);
                turns.set(event.turnId, {
                    turnId: event.turnId,
                    agentId: event.agentId,
                    agentName: event.agentName || event.agentId || "Agent",
                    model: event.model,
                    title: event.title,
                    status: turnStatus(turns.get(event.turnId)?.status),
                    text: turns.get(event.turnId)?.text || "",
                    startedSeq: turns.get(event.turnId)?.startedSeq || event.seq,
                    endedSeq: turns.get(event.turnId)?.endedSeq,
                    reason: turns.get(event.turnId)?.reason,
                });
                break;
            }
            case "pi.turn.delta": {
                const existing = turns.get(event.turnId);
                if (!existing) {
                    turnOrder.push(event.turnId);
                    turns.set(event.turnId, {
                        turnId: event.turnId,
                        agentId: "pi-agent",
                        agentName: "Agent",
                        status: "running",
                        text: event.textDelta,
                        startedSeq: event.seq,
                    });
                } else {
                    turns.set(event.turnId, { ...existing, text: `${existing.text}${event.textDelta}` });
                }
                break;
            }
            case "pi.turn.end": {
                const existing = turns.get(event.turnId);
                const status: PiCardStatus = event.status === "completed" ? "completed" : event.status === "aborted" ? "waiting" : "failed";
                if (!existing) {
                    turnOrder.push(event.turnId);
                    turns.set(event.turnId, {
                        turnId: event.turnId,
                        agentId: "pi-agent",
                        agentName: "Agent",
                        status,
                        text: "",
                        startedSeq: event.seq,
                        endedSeq: event.seq,
                        reason: event.reason,
                    });
                } else {
                    turns.set(event.turnId, { ...existing, status, endedSeq: event.seq, reason: event.reason });
                }
                break;
            }
            case "pi.tool.start": {
                if (!tools.has(event.toolCallId)) toolOrder.push(event.toolCallId);
                tools.set(event.toolCallId, {
                    toolCallId: event.toolCallId,
                    turnId: event.turnId,
                    agentId: event.agentId,
                    agentName: event.agentName,
                    toolName: event.toolName,
                    summary: event.summary,
                    argsSummary: event.argsSummary,
                    sensitivity: event.sensitivity,
                    status: "running",
                    extension: event.extension,
                    startedSeq: event.seq,
                });
                break;
            }
            case "pi.tool.end": {
                const existing = tools.get(event.toolCallId);
                if (!existing) {
                    toolOrder.push(event.toolCallId);
                    tools.set(event.toolCallId, {
                        toolCallId: event.toolCallId,
                        turnId: event.turnId,
                        toolName: event.toolCallId,
                        summary: event.display?.summary || event.errorPreview || "Tool finished",
                        status: toolStatus(event.status),
                        durationMs: event.durationMs,
                        displaySummary: event.display?.summary,
                        displayDetails: event.display?.details,
                        outputPreview: event.display?.outputPreview,
                        trust: event.display?.trust,
                        errorPreview: event.errorPreview,
                        extension: event.extension,
                        startedSeq: event.seq,
                        endedSeq: event.seq,
                    });
                } else {
                    tools.set(event.toolCallId, {
                        ...existing,
                        turnId: event.turnId || existing.turnId,
                        status: toolStatus(event.status),
                        durationMs: event.durationMs,
                        displaySummary: event.display?.summary,
                        displayDetails: event.display?.details,
                        outputPreview: event.display?.outputPreview,
                        trust: event.display?.trust,
                        errorPreview: event.errorPreview,
                        extension: event.extension || existing.extension,
                        endedSeq: event.seq,
                    });
                }
                break;
            }
            case "pi.artifact.upsert": {
                if (!artifacts.has(event.artifactId)) artifactOrder.push(event.artifactId);
                artifacts.set(event.artifactId, {
                    artifactId: event.artifactId,
                    turnId: event.turnId,
                    toolCallId: event.toolCallId,
                    agentId: event.agentId,
                    kind: event.kind,
                    title: event.title,
                    summary: event.summary,
                    webUrl: event.webUrl,
                    previewText: event.previewText,
                    trust: event.trust,
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            }
            case "pi.approval.request": {
                if (!approvals.has(event.requestId)) approvalOrder.push(event.requestId);
                approvals.set(event.requestId, {
                    requestId: event.requestId,
                    turnId: event.turnId,
                    toolCallId: event.toolCallId,
                    agentId: event.agentId,
                    title: event.title,
                    summary: event.summary,
                    risk: event.risk,
                    actionLabel: event.actionLabel,
                    metadata: event.metadata,
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            }
            case "pi.clarification.request": {
                if (!clarifications.has(event.requestId)) clarificationOrder.push(event.requestId);
                clarifications.set(event.requestId, {
                    requestId: event.requestId,
                    turnId: event.turnId,
                    agentId: event.agentId,
                    title: event.title,
                    prompt: event.prompt,
                    control: event.control,
                    options: event.options || [],
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            }
            case "pi.subagent.update": {
                if (!subagents.has(event.agentId)) subagentOrder.push(event.agentId);
                subagents.set(event.agentId, {
                    agentId: event.agentId,
                    agentName: event.agentName || event.agentId,
                    role: event.role,
                    state: event.state,
                    task: event.task,
                    summary: event.summary,
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            }
            case "pi.subagents.status": {
                if (!subagentStatuses.has(event.runId)) subagentStatusOrder.push(event.runId);
                subagentStatuses.set(event.runId, {
                    runId: event.runId,
                    asyncId: event.asyncId,
                    mode: event.mode,
                    state: event.state,
                    agent: event.agent || event.agentName || event.agentId || event.runId,
                    agentId: event.agentId,
                    agentName: event.agentName,
                    task: event.task,
                    summary: event.summary,
                    activityState: event.activityState,
                    currentTool: event.currentTool,
                    currentToolStartedAt: event.currentToolStartedAt,
                    currentPath: event.currentPath,
                    turnCount: event.turnCount,
                    toolCount: event.toolCount,
                    durationMs: event.durationMs,
                    sessionFile: event.sessionFile,
                    outputFile: event.outputFile,
                    progress: event.progress || [],
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            }
            case "pi.subagents.control":
                subagentControls.push({
                    key: `control-${event.runId}-${event.seq}`,
                    runId: event.runId,
                    agent: event.agent,
                    agentId: event.agentId,
                    agentName: event.agentName,
                    controlType: event.controlType,
                    to: event.to,
                    message: event.message,
                    reason: event.reason,
                    currentTool: event.currentTool,
                    currentToolDurationMs: event.currentToolDurationMs,
                    elapsedMs: event.elapsedMs,
                    toolCount: event.toolCount,
                    turnCount: event.turnCount,
                    tokens: event.tokens,
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            case "pi.subagents.result":
                subagentResults.push({
                    key: `result-${event.runId}-${event.seq}`,
                    runId: event.runId,
                    asyncId: event.asyncId,
                    mode: event.mode,
                    status: event.status,
                    agent: event.agent || event.agentName || event.agentId || event.runId,
                    agentId: event.agentId,
                    agentName: event.agentName,
                    summary: event.summary,
                    sessionFile: event.sessionFile,
                    artifactPath: event.artifactPath,
                    artifactPaths: event.artifactPaths,
                    usage: event.usage,
                    results: event.results,
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            case "pi.subagents.async": {
                if (!subagentAsync.has(event.asyncId)) subagentAsyncOrder.push(event.asyncId);
                subagentAsync.set(event.asyncId, {
                    asyncId: event.asyncId,
                    runId: event.runId,
                    state: event.state,
                    mode: event.mode,
                    agent: event.agent,
                    agents: event.agents,
                    summary: event.summary,
                    asyncDir: event.asyncDir,
                    sessionDir: event.sessionDir,
                    outputFile: event.outputFile,
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            }
            case "pi.retry":
                markers.push({
                    key: `retry-${event.seq}`,
                    kind: "retry",
                    title: event.status === "started" ? "Retry started" : "Retry completed",
                    message: event.reason,
                    status: event.status,
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            case "pi.context.compaction":
                markers.push({
                    key: `compaction-${event.seq}`,
                    kind: "compaction",
                    title: event.status === "started" ? "Context compaction started" : "Context compaction completed",
                    message: event.summary,
                    status: event.status,
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            case "pi.ui.request":
                markers.push({
                    key: `ui-${event.requestId}`,
                    kind: "ui",
                    title: event.title,
                    message: event.message || event.widgetKind || event.control,
                    status: event.status || event.control,
                    extension: event.extension,
                    seq: event.seq,
                });
                break;
            default:
                break;
        }
    }

    const latestSeq = sorted.length > 0 ? sorted[sorted.length - 1].seq : 0;
    return {
        turns: turnOrder.map((id) => turns.get(id)).filter(Boolean) as PiTurnView[],
        tools: toolOrder.map((id) => tools.get(id)).filter(Boolean) as PiToolCardView[],
        artifacts: artifactOrder.map((id) => artifacts.get(id)).filter(Boolean) as PiArtifactCardView[],
        approvals: approvalOrder.map((id) => approvals.get(id)).filter(Boolean) as PiApprovalCardView[],
        clarifications: clarificationOrder.map((id) => clarifications.get(id)).filter(Boolean) as PiClarificationCardView[],
        subagents: subagentOrder.map((id) => subagents.get(id)).filter(Boolean) as PiSubagentView[],
        subagentStatuses: subagentStatusOrder.map((id) => subagentStatuses.get(id)).filter(Boolean) as PiSubagentsStatusView[],
        subagentControls,
        subagentResults,
        subagentAsync: subagentAsyncOrder.map((id) => subagentAsync.get(id)).filter(Boolean) as PiSubagentsAsyncView[],
        markers,
        orchestration,
        availableTools,
        extensions: extensionLabels,
        rawEventCount: sorted.length,
        latestSeq,
    };
}