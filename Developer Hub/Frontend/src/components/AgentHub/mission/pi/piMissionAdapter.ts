import type { MissionState, PendingApproval } from "../missionReducer";
import type { Artifact, LogCategory, PiMissionUiEvent, SlotProgress } from "../events";
import {
    PI_ASK_USER_PACKAGE,
    PI_AGENTIC_ENGINEERING_EXTENSION,
    PI_FRONTEND_RUNTIME_PACKAGE,
    PI_MISSION_UI_EXTENSION,
    PI_ORCHESTRATION_RUNTIME_PACKAGE,
    PI_SUBAGENTS_PACKAGE,
    buildPiSessionOrchestrationContext,
    piExtensionMetadata,
} from "./piExtensionPackages";

function isoNow(): string {
    return new Date().toISOString();
}

function shortText(value: unknown, max = 320): string {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (!text) return "";
    return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

function eventBase(state: MissionState, seq: number, logCategory: LogCategory = "high_level") {
    return {
        seq,
        sessionId: "mission-control",
        ts: isoNow(),
        logCategory,
    };
}

function slotState(status: SlotProgress["status"]): "queued" | "running" | "blocked" | "done" | "failed" {
    if (status === "done") return "done";
    if (status === "failed") return "failed";
    if (status === "waiting" || status === "approval_required") return "blocked";
    if (status === "queued") return "queued";
    return "running";
}

function approvalRisk(approval: PendingApproval): "low" | "medium" | "high" {
    const blastRadius = String(approval.blastRadius || "").toLowerCase();
    if (blastRadius.includes("delete") || blastRadius.includes("destructive") || blastRadius.includes("tenant")) return "high";
    if (blastRadius.includes("write") || approval.reversible === false) return "medium";
    return "low";
}

function artifactKind(artifact: Artifact): string {
    const kind = String(artifact.kind || "artifact").toLowerCase();
    if (kind.includes("report")) return "report";
    if (kind.includes("verifier")) return "verifier";
    if (kind.includes("diff")) return "diff";
    if (kind.includes("json")) return "json";
    return kind;
}

function terminalStatus(state: MissionState): "completed" | "failed" | "aborted" | null {
    if (state.terminalType === "job_complete" || state.jobStatus === "completed") return "completed";
    if (state.terminalType === "job_cancelled" || state.jobStatus === "cancelled") return "aborted";
    if (state.terminalType === "job_failed" || state.jobStatus === "failed") return "failed";
    return null;
}

function piOrchestrationEvent(state: MissionState, seq: number): PiMissionUiEvent {
    const context = buildPiSessionOrchestrationContext().pi_orchestration;
    return {
        ...eventBase(state, seq),
        type: "pi.orchestration.start",
        schemaVersion: 1,
        runtime: "pi",
        runtimePackage: PI_ORCHESTRATION_RUNTIME_PACKAGE.packageName || "@mariozechner/pi-agent-core",
        runtimePackageSource: PI_ORCHESTRATION_RUNTIME_PACKAGE.source,
        frontendRuntimePackage: PI_FRONTEND_RUNTIME_PACKAGE.packageName || "@mariozechner/pi-web-ui",
        executionSurfaceExtension: PI_MISSION_UI_EXTENSION.packageName || "@fabric-clawhub/pi-mission-ui",
        agenticEngineeringExtension: context.agentic_engineering_extension || PI_AGENTIC_ENGINEERING_EXTENSION.packageName,
        rpiProtocol: context.rpi_protocol,
        qrspiProtocol: context.qrspi_protocol,
        qrspiPhaseModel: context.qrspi_phase_model,
        qrspiQuestionPolicy: context.qrspi_question_policy,
        qrspiResearchPolicy: context.qrspi_research_policy,
        qrspiDesignStructurePolicy: context.qrspi_design_structure_policy,
        qrspiInstructionBudget: context.qrspi_instruction_budget,
        qrspiVerticalSlicePolicy: context.qrspi_vertical_slice_policy,
        qrspiBacktrackPolicy: context.qrspi_backtrack_policy,
        qrspiReviewPolicy: context.qrspi_review_policy,
        contextPackSchema: context.context_pack_schema,
        subagentWorkModel: context.subagent_work_model,
        contextWindowPolicy: context.context_window_policy,
        contextModeFacade: context.context_mode_facade,
        contextModeEvents: context.context_mode_events,
        contextModeControls: context.context_mode_controls,
        streamTransport: context.stream_transport,
        extensions: context.extensions.map((extension) => extension.source),
        backendBridge: "agenthub-fabric-runtime",
        extension: piExtensionMetadata(PI_MISSION_UI_EXTENSION),
    };
}

function ensurePiOrchestrationEvent(state: MissionState, events: PiMissionUiEvent[]): PiMissionUiEvent[] {
    if (events.some((event) => event.type === "pi.orchestration.start")) return events;
    const firstSeq = events.length > 0 ? Math.max(0, events[0].seq - 0.25) : Math.max(0, state.lastSeq - 0.25);
    return [piOrchestrationEvent(state, firstSeq), ...events];
}

function hasNativePiTranscript(events: PiMissionUiEvent[]): boolean {
    return events.some((event) => event.type !== "pi.orchestration.start");
}

function piMergeKey(event: PiMissionUiEvent): string {
    const named = "turnId" in event ? event.turnId
        : "toolCallId" in event ? event.toolCallId
            : "requestId" in event ? event.requestId
                : "artifactId" in event ? event.artifactId
                    : "agentId" in event ? event.agentId
                        : event.type;
    return `${event.seq}:${event.type}:${named}`;
}

function mergeBackendPiHeaderWithDerivedEvents(state: MissionState, derivedEvents: PiMissionUiEvent[]): PiMissionUiEvent[] {
    if (state.piEvents.length === 0) return derivedEvents;
    if (hasNativePiTranscript(state.piEvents)) return ensurePiOrchestrationEvent(state, state.piEvents);

    const backendHeaderEvents = ensurePiOrchestrationEvent(state, state.piEvents);
    const hasBackendHeader = backendHeaderEvents.some((event) => event.type === "pi.orchestration.start");
    const candidates = [
        ...backendHeaderEvents,
        ...derivedEvents.filter((event) => !hasBackendHeader || event.type !== "pi.orchestration.start"),
    ].sort((a, b) => (a.seq || 0) - (b.seq || 0));

    const seen = new Set<string>();
    const merged: PiMissionUiEvent[] = [];
    for (const event of candidates) {
        const key = piMergeKey(event);
        if (seen.has(key)) continue;
        seen.add(key);
        merged.push(event);
    }
    return merged;
}

export function derivePiMissionEventsFromState(state: MissionState): PiMissionUiEvent[] {

    const events: PiMissionUiEvent[] = [];
    const firstLog = state.logs[0];
    const startSeq = firstLog ? Math.max(0, firstLog.seq - 0.5) : Math.max(0, state.lastSeq - 0.5);
    const turnId = `mission-${state.composition?.architecture || "dynamic"}`;
    const agentId = state.activeAgentId || firstLog?.agentId || "pi-agenthub-runtime";
    const agentName = firstLog?.agentName || "AgentHub Pi runtime";
    const taskTitle = shortText(state.composition?.task || "Live mission execution", 120);

    events.push(piOrchestrationEvent(state, Math.max(0, startSeq - 0.1)));

    events.push({
        ...eventBase(state, startSeq),
        type: "pi.turn.start",
        schemaVersion: 1,
        turnId,
        agentId,
        agentName,
        model: "AgentHub live runtime",
        title: taskTitle,
        extension: piExtensionMetadata(PI_FRONTEND_RUNTIME_PACKAGE),
    });

    const narrativeLogs = state.logs
        .filter((log) => log.kind !== "tool_start" && log.kind !== "tool_end" && log.message)
        .slice(-80);

    if (narrativeLogs.length === 0) {
        events.push({
            ...eventBase(state, startSeq + 0.1),
            type: "pi.turn.delta",
            schemaVersion: 1,
            turnId,
            textDelta: "Pi Mission Control is attached and waiting for live runtime events.\n",
            extension: piExtensionMetadata(PI_FRONTEND_RUNTIME_PACKAGE),
        });
    } else {
        for (const log of narrativeLogs) {
            const prefix = log.agentName || log.agentId || "Runtime";
            events.push({
                ...eventBase(state, log.seq, log.logCategory),
                type: "pi.turn.delta",
                schemaVersion: 1,
                turnId,
                textDelta: `${prefix}: ${shortText(log.message)}\n\n`,
                extension: piExtensionMetadata(PI_FRONTEND_RUNTIME_PACKAGE),
            });
        }
    }

    const toolStarts = new Map<string, typeof state.logs[number]>();
    for (const log of state.logs) {
        if (log.kind === "tool_start" && log.callId) {
            toolStarts.set(log.callId, log);
            events.push({
                ...eventBase(state, log.seq, log.logCategory),
                type: "pi.tool.start",
                schemaVersion: 1,
                toolCallId: log.callId,
                turnId,
                agentId: log.agentId,
                agentName: log.agentName,
                toolName: log.toolName || "agenthub_tool",
                summary: shortText(log.message || log.toolName || "Tool started"),
                argsSummary: log.argsPreview ? JSON.stringify(log.argsPreview).slice(0, 500) : undefined,
                sensitivity: log.operationKind === "write" ? "write" : "read-safe",
                extension: piExtensionMetadata(PI_MISSION_UI_EXTENSION),
            });
        }

        if (log.kind === "tool_end" && log.callId) {
            const start = toolStarts.get(log.callId);
            events.push({
                ...eventBase(state, log.seq, log.logCategory),
                type: "pi.tool.end",
                schemaVersion: 1,
                toolCallId: log.callId,
                turnId,
                status: log.toolStatus === "error" ? "error" : "ok",
                durationMs: log.durationMs,
                display: {
                    summary: shortText(log.message || "Tool completed"),
                    outputPreview: log.errorPreview || undefined,
                    trust: { level: log.toolStatus === "error" ? "untrusted" : "trusted", source: "runtime" },
                    fields: {
                        tool: log.toolName || start?.toolName || null,
                        operation: log.operationKind || start?.operationKind || null,
                    },
                },
                errorPreview: log.errorPreview || null,
                extension: piExtensionMetadata(PI_MISSION_UI_EXTENSION),
            });
        }
    }

    let syntheticSeq = state.lastSeq + 1;
    for (const slot of Object.values(state.slotProgress)) {
        events.push({
            ...eventBase(state, syntheticSeq++),
            type: "pi.subagent.update",
            schemaVersion: 1,
            agentId: slot.agentId || slot.slotId,
            agentName: slot.agentName,
            role: slot.role || slot.agentName || "Mission worker",
            state: slotState(slot.status),
            task: slot.currentStep || slot.reason,
            summary: slot.reason || slot.currentStep,
            extension: piExtensionMetadata(PI_SUBAGENTS_PACKAGE),
        });
    }

    for (const approval of Object.values(state.approvals).filter((item) => !item.resolved)) {
        events.push({
            ...eventBase(state, syntheticSeq++),
            type: "pi.approval.request",
            schemaVersion: 1,
            requestId: approval.approvalId,
            turnId,
            toolCallId: approval.toolCallPreview?.name,
            agentId: approval.agentId,
            title: "Approval required",
            summary: shortText(approval.summary, 420),
            risk: approvalRisk(approval),
            actionLabel: "Approve",
            metadata: {
                blastRadius: approval.blastRadius || null,
                reversible: approval.reversible ?? null,
                tool: approval.toolCallPreview?.name || null,
            },
            extension: piExtensionMetadata(PI_ASK_USER_PACKAGE),
        });
    }

    for (const artifact of state.artifactOrder.map((id) => state.artifacts[id]).filter(Boolean)) {
        events.push({
            ...eventBase(state, syntheticSeq++),
            type: "pi.artifact.upsert",
            schemaVersion: 1,
            artifactId: artifact.artifactId,
            turnId,
            agentId: artifact.agentId,
            kind: artifactKind(artifact),
            title: artifact.name,
            summary: artifact.state === "written" ? "Published by the mission runtime" : "Draft artifact from the mission runtime",
            webUrl: artifact.webUrl,
            trust: { level: "trusted", source: "fabric" },
            extension: piExtensionMetadata(PI_MISSION_UI_EXTENSION),
        });
    }

    const status = terminalStatus(state);
    if (status) {
        events.push({
            ...eventBase(state, syntheticSeq++),
            type: "pi.turn.end",
            schemaVersion: 1,
            turnId,
            status,
            reason: status === "completed" ? "Mission completed" : status === "aborted" ? "Mission cancelled" : "Mission failed",
            extension: piExtensionMetadata(PI_FRONTEND_RUNTIME_PACKAGE),
        });
    }

    return mergeBackendPiHeaderWithDerivedEvents(state, events);
}