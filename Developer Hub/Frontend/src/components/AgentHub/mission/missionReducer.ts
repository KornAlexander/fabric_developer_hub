/**
 * Mission Control — pure reducer over the SSE event vocabulary.
 *
 * Intentionally decoupled from React: ``missionReducer`` is a pure
 * function over ``(state, event) → state`` so it can be unit-tested
 * against a canned event tape without mounting any component.
 *
 * The reducer is idempotent on ``seq``: events with a ``seq`` already
 * observed are dropped. This makes ``Last-Event-ID`` replay on
 * reconnect safe — we can merge a brief buffer overlap without
 * double-counting artifacts, log lines, or status transitions.
 */

import type {
    Artifact,
    ChangeRecord,
    MissionEvent,
    PiMissionUiEvent,
    PiSubagentUpdateEvent,
    PublicLogCategory,
    SlotProgress,
    JobStatusLite,
    VerifierVerdictEvent,
} from "./events";
import type { Composition } from "./types";
import { formatActionMessage, formatOperationalMessage, formatToolEndMessage, formatToolName, formatToolStartMessage } from "./logPresentation";
import { appendPiMissionEvent, isPiMissionUiEvent } from "./pi/piMissionReducer";

export interface LogEntry {
    seq: number;
    ts: string;
    agentId?: string;
    agentName?: string;
    level: "info" | "warn" | "error";
    message: string;
    logCategory: PublicLogCategory;
    kind: "log" | "phase" | "decision" | "tool_start" | "tool_end" | "action" | "error" | "rollup" | "steering" | "diagnostic";
    streamId?: string;
    streamKind?: "assistant" | "thinking";
    streamStatus?: "streaming" | "finalized";
    streamText?: string;
    streamUpdatedSeq?: number;
    callId?: string;
    toolName?: string;
    toolKind?: string;
    operationKind?: string;
    argsPreview?: Record<string, unknown>;
    toolStatus?: "ok" | "error";
    errorPreview?: string | null;
    durationMs?: number;
    latencyBreakdownMs?: Record<string, number>;
    coveredSeqStart?: number | null;
    coveredSeqEnd?: number | null;
    detailCount?: number;
    rollupStatus?: string;
    counts?: Record<string, unknown>;
    steeringId?: string;
    targetMode?: string;
    deliveryMode?: string;
    eventId?: string;
    payloadDigest?: string;
    payloadSummary?: Record<string, unknown>;
    sourceEventType?: string;
    progressMode?: "requesting" | "responding" | "thinking" | "tool-input" | "tool-use" | "idle";
    progressSemanticClass?: "preparing" | "search-read" | "thinking" | "tool" | "waiting" | "completed" | "attention";
}

type LogEntryInput = Omit<LogEntry, "logCategory"> & { logCategory?: PublicLogCategory };
type LogSupportFields = Pick<LogEntry, "eventId" | "payloadDigest" | "payloadSummary" | "sourceEventType">;

export interface PendingApproval {
    approvalId: string;
    slotId?: string;
    agentId?: string;
    summary: string;
    blastRadius?: string | null;
    reversible?: boolean | null;
    toolCallPreview?: { name: string; args: Record<string, unknown> } | null;
    recoveryActions?: string[];
    raisedAt: string;
    resolved?: boolean;
    resolvedAction?: string;
}

export interface MissionState {
    jobStatus: JobStatusLite;
    composition: Composition | null;
    activeAgentId: string | null;
    slotProgress: Record<string, SlotProgress>;   // keyed by slotId
    agentStatus: Record<string, "queued" | "running" | "waiting" | "completed" | "error">;
    artifacts: Record<string, Artifact>;          // keyed by artifactId
    artifactOrder: string[];                      // insertion order for display
    changes: Record<string, ChangeRecord>;        // keyed by recordId
    changeOrder: string[];                        // insertion order for display
    logs: LogEntry[];
    piEvents: PiMissionUiEvent[];
    approvals: Record<string, PendingApproval>;
    lastSeq: number;
    totalDuration?: string;
    terminalType?: "job_complete" | "job_failed" | "job_cancelled";
}

export function initialMissionState(seed?: Partial<MissionState>): MissionState {
    return {
        jobStatus: "planned",
        composition: null,
        activeAgentId: null,
        slotProgress: {},
        agentStatus: {},
        artifacts: {},
        artifactOrder: [],
        changes: {},
        changeOrder: [],
        logs: [],
        piEvents: [],
        approvals: {},
        lastSeq: 0,
        ...seed,
    };
}

function mergeChangeRecords(state: MissionState, records: ChangeRecord[] | undefined): MissionState {
    if (!records?.length) return state;
    const changes = { ...state.changes };
    const order = [...state.changeOrder];
    for (const record of records) {
        if (!record?.recordId) continue;
        if (!changes[record.recordId]) order.push(record.recordId);
        changes[record.recordId] = record;
    }
    return { ...state, changes, changeOrder: order };
}

function settleRuntimeAfterTerminal(state: MissionState, terminalType: NonNullable<MissionState["terminalType"]>): MissionState {
    const slotStatus: SlotProgress["status"] = terminalType === "job_failed" ? "failed" : "done";
    const agentStatus: MissionState["agentStatus"][string] = terminalType === "job_failed" ? "error" : "completed";
    const currentStep = terminalType === "job_complete" ? "Run completed"
        : terminalType === "job_cancelled" ? "Run cancelled"
            : "Run failed";
    const slotProgress = Object.fromEntries(
        Object.entries(state.slotProgress).map(([slotId, progress]) => [slotId, {
            ...progress,
            status: progress.status === "failed" ? "failed" : slotStatus,
            currentStep: progress.status === "failed" ? progress.currentStep : currentStep,
        }]),
    ) as MissionState["slotProgress"];
    const nextAgentStatus = { ...state.agentStatus };
    for (const [slotId, progress] of Object.entries(slotProgress)) {
        const nextStatus = progress.status === "failed" ? "error" : agentStatus;
        nextAgentStatus[slotId] = nextStatus;
        if (progress.agentId) nextAgentStatus[progress.agentId] = nextStatus;
    }
    for (const agentId of Object.keys(nextAgentStatus)) {
        if (nextAgentStatus[agentId] === "queued" || nextAgentStatus[agentId] === "running" || nextAgentStatus[agentId] === "waiting") {
            nextAgentStatus[agentId] = agentStatus;
        }
    }
    return { ...state, activeAgentId: null, slotProgress, agentStatus: nextAgentStatus };
}

function defaultLogCategoryForEntry(entry: LogEntryInput): PublicLogCategory {
    if (entry.kind === "tool_start" || entry.kind === "tool_end" || entry.kind === "error") {
        return "diagnostic";
    }
    if (entry.kind === "phase" || entry.kind === "decision" || entry.kind === "action") {
        return "high_level";
    }
    if (entry.kind === "rollup" || entry.kind === "steering") {
        return "high_level";
    }
    if (entry.kind === "diagnostic") {
        return entry.level === "error" || entry.level === "warn" ? "high_level" : "diagnostic";
    }
    if (entry.level === "warn") {
        return "high_level";
    }
    if (entry.level === "error") {
        return "diagnostic";
    }
    return "detailed";
}

function publicLogCategoryFromEvent(ev: MissionEvent): PublicLogCategory | undefined {
    const category = (ev as any).logCategory;
    if (category === "high_level" || category === "detailed" || category === "diagnostic") {
        return category;
    }
    return undefined;
}

function compactList(parts: Array<string | number | null | undefined>): string {
    return parts
        .filter((part) => part !== null && part !== undefined && String(part).trim().length > 0)
        .map((part) => String(part).trim())
        .join(" · ");
}

function shortText(value: unknown, max = 180): string {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (!text) return "";
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function llmProgressMode(phase: unknown): LogEntry["progressMode"] {
    const normalized = String(phase || "").toLowerCase().replace(/[-\s]+/g, "_");
    if (normalized === "requesting" || normalized === "request") return "requesting";
    if (normalized === "thinking" || normalized === "thought") return "thinking";
    if (normalized === "responding" || normalized === "response" || normalized === "streaming") return "responding";
    if (normalized === "tool_input" || normalized === "preparing_tool" || normalized === "action_input") return "tool-input";
    if (normalized === "tool_use" || normalized === "tool" || normalized === "running_tool") return "tool-use";
    if (normalized === "idle" || normalized === "complete" || normalized === "completed") return "idle";
    return "responding";
}

function semanticForProgressMode(mode: LogEntry["progressMode"]): LogEntry["progressSemanticClass"] {
    if (mode === "requesting") return "preparing";
    if (mode === "thinking") return "thinking";
    if (mode === "tool-input" || mode === "tool-use") return "tool";
    if (mode === "idle") return "completed";
    return "thinking";
}

function updateLiveAgentProgress(
    state: MissionState,
    agentId: string | undefined,
    agentName: string | undefined,
    currentStep: string,
    role?: string,
): MissionState {
    if (!agentId) return state;
    const existing = state.slotProgress[agentId] || {} as SlotProgress;
    return {
        ...state,
        activeAgentId: agentId,
        agentStatus: { ...state.agentStatus, [agentId]: "running" },
        slotProgress: {
            ...state.slotProgress,
            [agentId]: {
                ...existing,
                slotId: existing.slotId || agentId,
                agentId,
                agentName: agentName || existing.agentName,
                role: role || existing.role || agentName,
                status: "running",
                currentStep,
            },
        },
    };
}

function llmTaskLabel(ev: MissionEvent): string {
    const summary = (ev as any).taskTitle || (ev as any).promptSummary || (ev as any).payloadSummary?.taskDescription;
    return shortText(summary || "current task", 120);
}

function pushLog(state: MissionState, entry: LogEntryInput): MissionState {
    // Bound the log list at a reasonable size so a very long run
    // does not balloon component memory. Keep the most recent 2k
    // entries — completed-run filtering works against the full
    // server-side phase list via getSession(), not this buffer.
    const MAX = 2000;
    const categorizedEntry: LogEntry = {
        ...entry,
        logCategory: entry.logCategory ?? defaultLogCategoryForEntry(entry),
    };
    const next = state.logs.length >= MAX
        ? [...state.logs.slice(-MAX + 1), categorizedEntry]
        : [...state.logs, categorizedEntry];
    return { ...state, logs: next };
}

function supportLogFields(ev: MissionEvent): LogSupportFields {
    return {
        eventId: (ev as any).eventId,
        payloadDigest: (ev as any).payloadDigest,
        payloadSummary: (ev as any).payloadSummary,
        sourceEventType: (ev as any).type,
    };
}

function pushEventLog(state: MissionState, ev: MissionEvent, entry: LogEntryInput): MissionState {
    return pushLog(state, {
        ...supportLogFields(ev),
        ...entry,
    });
}

function sameStreamSource(entry: LogEntry, ev: MissionEvent, streamKind: LogEntry["streamKind"]): boolean {
    if (entry.streamKind !== streamKind) return false;
    const evAny = ev as any;
    if (evAny.requestId) return entry.streamId === `${streamKind}:${evAny.requestId}`;
    const entryAgent = String(entry.agentId || entry.agentName || "").toLowerCase();
    const eventAgent = String(evAny.agentId || evAny.agentName || "").toLowerCase();
    return !!entryAgent && !!eventAgent && entryAgent === eventAgent;
}

function streamTextFromEvent(ev: MissionEvent): string {
    const evAny = ev as any;
    if (typeof evAny.text === "string") return evAny.text;
    if (typeof evAny.delta === "string") return evAny.delta;
    if (typeof evAny.summary === "string") return evAny.summary;
    return "";
}

function pushOrUpdateStreamLog(
    state: MissionState,
    ev: MissionEvent,
    streamKind: NonNullable<LogEntry["streamKind"]>,
    finalized: boolean,
): MissionState {
    const evAny = ev as any;
    const explicitId = typeof evAny.requestId === "string" && evAny.requestId.trim()
        ? `${streamKind}:${evAny.requestId.trim()}`
        : null;
    const existingIndex = explicitId
        ? state.logs.findIndex((entry) => entry.streamId === explicitId)
        : [...state.logs].reverse().findIndex((entry) => sameStreamSource(entry, ev, streamKind) && entry.streamStatus !== "finalized");
    const resolvedIndex = existingIndex >= 0
        ? (explicitId ? existingIndex : state.logs.length - 1 - existingIndex)
        : -1;
    const incoming = streamTextFromEvent(ev);
    const existing = resolvedIndex >= 0 ? state.logs[resolvedIndex] : undefined;
    const isDelta = typeof evAny.delta === "string" && evAny.delta.length > 0 && typeof evAny.text !== "string";
    const nextText = isDelta
        ? `${existing?.streamText || existing?.message || ""}${evAny.delta}`
        : incoming || existing?.streamText || existing?.message || "";
    const streamText = nextText || (streamKind === "thinking" ? "Thinking" : "Assistant response");
    const message = streamText;
    const counts = typeof evAny.tokenCount === "number" ? { tokenCount: evAny.tokenCount } : existing?.counts;

    if (existing && resolvedIndex >= 0) {
        const logs = [...state.logs];
        logs[resolvedIndex] = {
            ...existing,
            ...supportLogFields(ev),
            ts: evAny.ts || existing.ts,
            agentId: evAny.agentId || existing.agentId,
            agentName: evAny.agentName || existing.agentName,
            level: "info",
            message,
            logCategory: publicLogCategoryFromEvent(ev) || existing.logCategory,
            kind: finalized || streamKind === "thinking" ? "decision" : "log",
            streamId: existing.streamId || explicitId || `${streamKind}:${existing.seq}`,
            streamKind,
            streamStatus: finalized ? "finalized" : "streaming",
            streamText,
            streamUpdatedSeq: evAny.seq,
            counts,
            progressMode: streamKind === "thinking" ? "thinking" : "responding",
            progressSemanticClass: "thinking",
            payloadSummary: { ...(existing.payloadSummary || {}), ...(evAny.payloadSummary || {}), taskDescription: streamKind === "thinking" ? "Thinking" : "Assistant response" },
        };
        return { ...state, logs };
    }

    return pushEventLog(state, ev, {
        seq: evAny.seq,
        ts: evAny.ts,
        logCategory: publicLogCategoryFromEvent(ev),
        kind: finalized || streamKind === "thinking" ? "decision" : "log",
        level: "info",
        agentId: evAny.agentId,
        agentName: evAny.agentName,
        message,
        streamId: explicitId || `${streamKind}:${evAny.seq}`,
        streamKind,
        streamStatus: finalized ? "finalized" : "streaming",
        streamText,
        streamUpdatedSeq: evAny.seq,
        counts,
        progressMode: streamKind === "thinking" ? "thinking" : "responding",
        progressSemanticClass: "thinking",
        payloadSummary: { ...(evAny.payloadSummary || {}), taskDescription: streamKind === "thinking" ? "Thinking" : "Assistant response" },
    });
}

function mapPiSubagentStatus(event: PiSubagentUpdateEvent): "queued" | "running" | "waiting" | "done" | "failed" {
    if (event.state === "done") return "done";
    if (event.state === "failed") return "failed";
    if (event.state === "blocked") return "waiting";
    return event.state;
}

function mapPiSubagentsState(state: string | undefined): "queued" | "running" | "waiting" | "done" | "failed" {
    const normalized = String(state || "running").toLowerCase();
    if (normalized === "complete" || normalized === "completed" || normalized === "done") return "done";
    if (normalized === "failed" || normalized === "error") return "failed";
    if (normalized === "paused" || normalized === "blocked" || normalized === "detached") return "waiting";
    if (normalized === "queued" || normalized === "pending") return "queued";
    return "running";
}

function reducePiMissionEvent(state: MissionState, ev: PiMissionUiEvent): MissionState {
    let next: MissionState = { ...state, piEvents: appendPiMissionEvent(state.piEvents, ev) };
    switch (ev.type) {
        case "pi.turn.start":
            next.activeAgentId = ev.agentId;
            next.agentStatus = { ...next.agentStatus, [ev.agentId]: "running" };
            next.slotProgress = {
                ...next.slotProgress,
                [ev.agentId]: {
                    ...(next.slotProgress[ev.agentId] || {}),
                    slotId: ev.agentId,
                    agentId: ev.agentId,
                    agentName: ev.agentName || next.slotProgress[ev.agentId]?.agentName || ev.agentId,
                    role: ev.title || next.slotProgress[ev.agentId]?.role || "Pi agent",
                    status: "running",
                    currentStep: ev.title || "Streaming assistant turn",
                },
            };
            return next;
        case "pi.turn.end": {
            const turnStart = [...next.piEvents].reverse().find((event) => event.type === "pi.turn.start" && event.turnId === ev.turnId);
            const agentId = turnStart?.type === "pi.turn.start" ? turnStart.agentId : undefined;
            if (!agentId) return next;
            const failed = ev.status === "failed";
            next.agentStatus = { ...next.agentStatus, [agentId]: failed ? "error" : "completed" };
            next.slotProgress = {
                ...next.slotProgress,
                [agentId]: {
                    ...(next.slotProgress[agentId] || {}),
                    slotId: agentId,
                    agentId,
                    status: failed ? "failed" : "done",
                    currentStep: failed ? ev.reason || "Turn failed" : "Turn complete",
                },
            };
            if (next.activeAgentId === agentId) next.activeAgentId = null;
            return next;
        }
        case "pi.tool.start": {
            const agentId = ev.agentId;
            if (!agentId) return next;
            next.activeAgentId = agentId;
            next.agentStatus = { ...next.agentStatus, [agentId]: "running" };
            next.slotProgress = {
                ...next.slotProgress,
                [agentId]: {
                    ...(next.slotProgress[agentId] || {}),
                    slotId: agentId,
                    agentId,
                    agentName: ev.agentName || next.slotProgress[agentId]?.agentName || agentId,
                    role: next.slotProgress[agentId]?.role || "Pi tool work",
                    status: "running",
                    currentStep: ev.summary || `Running ${formatToolName(ev.toolName)}`,
                },
            };
            return next;
        }
        case "pi.approval.request":
            next.approvals = {
                ...next.approvals,
                [ev.requestId]: {
                    approvalId: ev.requestId,
                    agentId: ev.agentId,
                    summary: ev.summary || ev.title,
                    blastRadius: ev.metadata?.scope ? String(ev.metadata.scope) : null,
                    reversible: ev.risk === "low" ? true : null,
                    toolCallPreview: ev.toolCallId ? { name: ev.toolCallId, args: {} } : null,
                    recoveryActions: ["approve", "decline"],
                    raisedAt: ev.ts,
                },
            };
            if (ev.agentId) {
                next.agentStatus = { ...next.agentStatus, [ev.agentId]: "waiting" };
                next.slotProgress = {
                    ...next.slotProgress,
                    [ev.agentId]: {
                        ...(next.slotProgress[ev.agentId] || {}),
                        slotId: ev.agentId,
                        agentId: ev.agentId,
                        status: "approval_required",
                        reason: ev.summary || ev.title,
                        currentStep: "Waiting for approval",
                    },
                };
            }
            return next;
        case "pi.artifact.upsert": {
            if (!next.artifacts[ev.artifactId]) next.artifactOrder = [...next.artifactOrder, ev.artifactId];
            next.artifacts = {
                ...next.artifacts,
                [ev.artifactId]: {
                    artifactId: ev.artifactId,
                    agentId: ev.agentId,
                    kind: ev.kind,
                    name: ev.title,
                    state: "written",
                    webUrl: ev.webUrl ?? null,
                },
            };
            return next;
        }
        case "pi.subagent.update": {
            const status = mapPiSubagentStatus(ev);
            const agentStatus: MissionState["agentStatus"][string] = status === "done" ? "completed"
                : status === "failed" ? "error"
                    : status;
            next.agentStatus = {
                ...next.agentStatus,
                [ev.agentId]: agentStatus,
            };
            next.slotProgress = {
                ...next.slotProgress,
                [ev.agentId]: {
                    ...(next.slotProgress[ev.agentId] || {}),
                    slotId: ev.agentId,
                    agentId: ev.agentId,
                    agentName: ev.agentName || ev.agentId,
                    role: ev.role,
                    status,
                    currentStep: ev.summary || ev.task,
                    reason: ev.state === "blocked" ? ev.summary || ev.task : undefined,
                },
            };
            if (status === "running") next.activeAgentId = ev.agentId;
            return next;
        }
        case "pi.subagents.status": {
            const status = mapPiSubagentsState(ev.state);
            const agentId = ev.agentId || ev.runId;
            const agentName = ev.agentName || ev.agent || agentId;
            const agentStatus: MissionState["agentStatus"][string] = status === "done" ? "completed"
                : status === "failed" ? "error"
                    : status === "waiting" ? "waiting"
                        : status;
            next.agentStatus = {
                ...next.agentStatus,
                [agentId]: agentStatus,
            };
            next.slotProgress = {
                ...next.slotProgress,
                [agentId]: {
                    ...(next.slotProgress[agentId] || {}),
                    slotId: agentId,
                    agentId,
                    agentName,
                    role: ev.task || ev.agent || agentName,
                    status,
                    currentStep: ev.summary || ev.currentTool || ev.task,
                    reason: status === "waiting" || status === "failed" ? ev.summary || ev.currentTool || ev.task : undefined,
                },
            };
            if (status === "running") next.activeAgentId = agentId;
            return next;
        }
        case "pi.subagents.control": {
            const agentId = ev.agentId || ev.runId;
            next.agentStatus = { ...next.agentStatus, [agentId]: "waiting" };
            next.slotProgress = {
                ...next.slotProgress,
                [agentId]: {
                    ...(next.slotProgress[agentId] || {}),
                    slotId: agentId,
                    agentId,
                    agentName: ev.agentName || ev.agent,
                    role: ev.agent,
                    status: "waiting",
                    currentStep: ev.message,
                    reason: ev.reason || ev.message,
                },
            };
            return next;
        }
        case "pi.subagents.result": {
            const agentId = ev.agentId || ev.runId;
            const failed = String(ev.status).toLowerCase() === "failed";
            next.agentStatus = { ...next.agentStatus, [agentId]: failed ? "error" : "completed" };
            next.slotProgress = {
                ...next.slotProgress,
                [agentId]: {
                    ...(next.slotProgress[agentId] || {}),
                    slotId: agentId,
                    agentId,
                    agentName: ev.agentName || ev.agent,
                    role: ev.agent || ev.agentName || "Pi subagent",
                    status: failed ? "failed" : "done",
                    currentStep: ev.summary,
                },
            };
            if (next.activeAgentId === agentId) next.activeAgentId = null;
            return next;
        }
        case "pi.subagents.async":
            return next;
        default:
            return next;
    }
}

/**
 * Pure reducer. Returns the same state object when the event is a
 * duplicate (already-seen ``seq``), or unchanged metadata; React will
 * skip re-rendering in that case.
 */
export function missionReducer(state: MissionState, ev: MissionEvent): MissionState {
    // Heartbeats never carry a seq — short-circuit.
    if ((ev as any).type === "heartbeat") return state;

    const seq = (ev as any).seq as number | undefined;
    if (typeof seq === "number" && seq <= state.lastSeq) {
        return state; // duplicate from replay — dedupe.
    }
    const next: MissionState = typeof seq === "number"
        ? { ...state, lastSeq: seq }
        : { ...state };

    if ((ev as any).logCategory === "trace") {
        return next;
    }

    if (isPiMissionUiEvent(ev)) {
        return reducePiMissionEvent(next, ev);
    }

    switch (ev.type) {
        case "run_overview": {
            next.composition = ev.composition;
            next.activeAgentId = ev.activeAgentId;
            next.jobStatus = (ev.job?.status as JobStatusLite) || next.jobStatus;
            // If the snapshot reveals a terminal status, mark it so
            // the UI stops the timer and shows the correct dot colour.
            const TERMINAL_STATUSES: JobStatusLite[] = ["completed", "failed", "cancelled"];
            if (TERMINAL_STATUSES.includes(next.jobStatus) && !next.terminalType) {
                const typeMap: Record<string, MissionState["terminalType"]> = {
                    completed: "job_complete", failed: "job_failed", cancelled: "job_cancelled",
                };
                next.terminalType = typeMap[next.jobStatus];
                next.totalDuration = (ev as any).totalDuration || next.totalDuration;
            }
            // Merge artifacts preserving existing state; server is
            // authoritative on identity but we keep any local-order
            // we already had.
            const artifacts = { ...next.artifacts };
            const order = [...next.artifactOrder];
            for (const a of ev.artifacts || []) {
                if (!artifacts[a.artifactId]) order.push(a.artifactId);
                artifacts[a.artifactId] = a;
            }
            next.artifacts = artifacts;
            next.artifactOrder = order;
            const changedState = mergeChangeRecords(next, ev.changes || []);
            next.changes = changedState.changes;
            next.changeOrder = changedState.changeOrder;
            const slotProgress = { ...next.slotProgress };
            const agentStatus = { ...next.agentStatus };
            for (const s of ev.slotProgress || []) {
                slotProgress[s.slotId] = s;
                if (s.agentId) {
                    agentStatus[s.agentId] = s.status === "running" ? "running"
                        : s.status === "done" ? "completed"
                            : s.status === "approval_required" || s.status === "waiting" ? "waiting"
                                : s.status === "failed" ? "error"
                                    : "queued";
                }
            }
            next.slotProgress = slotProgress;
            next.agentStatus = agentStatus;
            return next;
        }

        case "composition_ready":
            next.composition = ev.composition;
            return next;

        case "agent_status": {
            if (ev.status === "running" || ev.status === "waiting") next.activeAgentId = ev.agentId;
            next.agentStatus = {
                ...next.agentStatus,
                [ev.agentId]: ev.status === "failed" ? "error" : ev.status,
            };
            const mappedStatus = ev.status === "completed" ? "done"
                : ev.status === "failed" || ev.status === "error" ? "failed"
                    : ev.status;
            next.slotProgress = {
                ...next.slotProgress,
                [ev.agentId]: {
                    ...(next.slotProgress[ev.agentId] || {}),
                    slotId: ev.agentId,
                    agentId: ev.agentId,
                    agentName: ev.agentName || next.slotProgress[ev.agentId]?.agentName,
                    role: ev.role || next.slotProgress[ev.agentId]?.role,
                    status: mappedStatus as any,
                    currentStep: ev.currentStep,
                },
            };
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: formatOperationalMessage(ev.currentStep || `status: ${ev.status}`),
            });
        }

        case "agent_added": {
            const agent = ev.agent || {};
            const slotId = String(agent.sessionId || agent.session_id || "");
            const templateId = String(agent.agentId || agent.agent_id || slotId || "agent");
            const role = String(agent.role || templateId || "Recovery agent");
            if (slotId) {
                next.slotProgress = {
                    ...next.slotProgress,
                    [slotId]: {
                        slotId,
                        agentId: slotId,
                        status: "queued",
                        agentName: role,
                        role,
                    },
                };
                next.agentStatus = {
                    ...next.agentStatus,
                    [slotId]: "queued",
                };
            }
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "warn",
                agentId: slotId || undefined,
                agentName: role,
                message: `Agent added: ${role}`,
            });
        }

        case "mission_seeded":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                message: `Generalist created the mission plan: ${ev.taskCount ?? 0} task${ev.taskCount === 1 ? "" : "s"} queued for delegation, execution, and verification`,
            });

        case "task_created":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                message: `Generalist queued task: ${ev.task.title}${ev.task.objective ? ` — ${shortText(ev.task.objective)}` : ""}`,
            });

        case "task_blocked":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "warn",
                message: `Task blocked: ${ev.message || ev.reason}`,
            });

        case "task_failed":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "error", level: "error",
                message: `Task failed: ${ev.message || ev.reason}`,
            });

        case "generalist_check_in":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "info",
                message: `Generalist checkpoint: ${ev.readyTaskCount ?? 0} ready for assignment, ${ev.runningSubagentCount ?? 0} specialists running, ${ev.completedTaskCount ?? 0} complete, ${ev.blockedTaskCount ?? 0} blocked, ${ev.failedTaskCount ?? 0} failed`,
            });

        case "generalist_context_pack":
        case "agent_context_received": {
            const contextDetails = compactList([
                ev.objectivePreview ? `objective: ${shortText(ev.objectivePreview, 120)}` : null,
                ev.toolScopeCount != null ? `${ev.toolScopeCount} allowed tools` : null,
                ev.upstreamResultCount != null ? `${ev.upstreamResultCount} upstream results` : null,
                ev.acceptanceCriteriaCount != null ? `${ev.acceptanceCriteriaCount} acceptance criteria` : null,
                ev.contextDigest ? `context ${ev.contextDigest}` : null,
            ]);
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "info",
                agentId: ev.runId,
                agentName: ev.agentName || ev.agentId,
                message: `Generalist delegated structured context to ${ev.agentName || ev.agentId} for ${ev.taskTitle || ev.taskId}${contextDetails ? ` (${contextDetails})` : ""}`,
            });
        }

        case "generalist_direct_work":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "info",
                agentId: ev.runId,
                message: `Generalist handled directly instead of delegating: ${ev.taskTitle || ev.taskId}. Reason: ${ev.reason}${ev.objectivePreview ? ` (${shortText(ev.objectivePreview, 140)})` : ""}`,
            });

        case "generalist_state_decision":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: ev.errorCount ? "warn" : "info",
                agentId: ev.runId,
                message: `Generalist reviewed specialist feedback for ${ev.taskId}: ${shortText(ev.summary || ev.rationale || ev.resultStatus || "subagent result reviewed")}${compactList([
                    ev.artifactCount != null ? `${ev.artifactCount} artifacts` : null,
                    ev.evidenceCount != null ? `${ev.evidenceCount} evidence items` : null,
                    ev.errorCount ? `${ev.errorCount} errors` : null,
                    ev.followupTaskCount ? `${ev.followupTaskCount} follow-ups` : null,
                ]) ? ` (${compactList([
                    ev.artifactCount != null ? `${ev.artifactCount} artifacts` : null,
                    ev.evidenceCount != null ? `${ev.evidenceCount} evidence items` : null,
                    ev.errorCount ? `${ev.errorCount} errors` : null,
                    ev.followupTaskCount ? `${ev.followupTaskCount} follow-ups` : null,
                ])})` : ""}`,
            });

        case "generalist_steering":
        case "subagent_steered":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "warn",
                agentId: ev.runId,
                agentName: ev.agentName || ev.agentId,
                message: `Generalist intervened and steered ${ev.agentName || ev.agentId || ev.runId}: ${ev.message || ev.reason}${ev.directiveCount != null ? ` (${ev.directiveCount} directives)` : ""}`,
            });

        case "subagent_inspected": {
            const signal = ev.signal || {};
            const signalLabel = typeof signal.kind === "string" ? signal.kind : "progress";
            const toolName = typeof signal.toolName === "string" ? ` (${signal.toolName})` : "";
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                agentId: ev.runId,
                message: `Generalist inspected specialist progress for ${ev.taskId}: latest ${signalLabel}${toolName}${ev.matchingSignalCount != null ? ` (${ev.matchingSignalCount} matching signals)` : ""}`,
            });
        }

        case "subagent_stale":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "warn",
                agentId: ev.runId,
                message: `Specialist progress is stale for ${ev.taskId}: no useful signal for ${Math.round(ev.staleSeconds ?? 0)} s`,
            });

        case "subagent_abandoned":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "warn",
                agentId: ev.runId,
                message: `Generalist reassigned ${ev.taskId} to ${ev.replacementTaskId}: ${ev.reason}`,
            });

        case "subagent_cancelled":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "warn",
                agentId: ev.runId,
                message: `Generalist cancelled specialist run for ${ev.taskId}: ${ev.reason}`,
            });

        case "subagent_heartbeat":
            return next;

        case "mission_no_progress":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "error", level: "error",
                agentId: ev.runId,
                agentName: ev.agentName || ev.agentId,
                message: `Mission is not converging for ${ev.taskId || "the current task"}: ${shortText(ev.summary || ev.rationale || ev.reason || "no-progress guard triggered", 260)}${ev.feedbackRound != null ? ` (round ${ev.feedbackRound})` : ""}`,
            });

        case "budget_exhausted": {
            const slotId = ev.slotId || ev.agentId;
            if (slotId) {
                next.slotProgress = {
                    ...next.slotProgress,
                    [slotId]: {
                        ...(next.slotProgress[slotId] || {}),
                        slotId,
                        agentId: slotId,
                        status: "failed",
                        reason: ev.reason || "Budget exhausted",
                    },
                };
                next.agentStatus = { ...next.agentStatus, [slotId]: "error" };
            }
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "error", level: "error",
                agentId: slotId,
                message: `Execution budget exhausted${ev.reason ? `: ${shortText(ev.reason, 220)}` : ""}`,
            });
        }

        case "tool_call_denied":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "diagnostic", level: "warn",
                agentId: ev.agentId,
                agentName: ev.agentName,
                toolName: ev.toolName,
                message: `Tool blocked${ev.toolName ? `: ${formatToolName(ev.toolName)}` : ""}${ev.reason ? ` — ${shortText(ev.reason, 220)}` : ""}`,
            });

        case "orchestrator_decision":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "info",
                message: `Orchestrator: ${ev.decision.rationale}`,
            });

        case "subagent_spawned": {
            const sessionId = ev.run.agentSessionId || ev.run.agent_session_id || "";
            if (sessionId) {
                next.slotProgress = {
                    ...next.slotProgress,
                    [sessionId]: {
                        slotId: sessionId,
                        agentId: sessionId,
                        status: "running",
                        agentName: ev.task?.title || ev.run.agentId || ev.run.agent_id,
                        role: ev.task?.title,
                    },
                };
                next.agentStatus = { ...next.agentStatus, [sessionId]: "running" };
                next.activeAgentId = sessionId;
            }
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                agentId: sessionId || undefined,
                agentName: ev.run.agentId || ev.run.agent_id,
                message: `Specialist started: ${ev.run.agentId || ev.run.agent_id || "agent"} handling ${ev.task?.title || ev.run.taskId || ev.run.task_id || ev.run.id}${ev.task?.objective ? ` — ${shortText(ev.task.objective)}` : ""}`,
            });
        }

        case "parallel_group_spawned":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                message: `Started ${ev.runIds.length} tasks in parallel`,
            });

        case "subagent_result": {
            const failed = ev.result.status === "failed" || ev.result.status === "blocked";
            const cancelled = ev.result.status === "cancelled";
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: failed ? "error" : "decision",
                level: failed ? "error" : cancelled ? "warn" : "info",
                agentId: ev.runId,
                message: `Specialist result for ${ev.taskId}: ${ev.result.status} — ${shortText(ev.result.summary)}${compactList([
                    ev.result.artifacts?.length ? `${ev.result.artifacts.length} artifacts` : null,
                    ev.result.errors?.length ? `${ev.result.errors.length} errors` : null,
                    ev.result.caveats?.length ? `${ev.result.caveats.length} caveats` : null,
                ]) ? ` (${compactList([
                    ev.result.artifacts?.length ? `${ev.result.artifacts.length} artifacts` : null,
                    ev.result.errors?.length ? `${ev.result.errors.length} errors` : null,
                    ev.result.caveats?.length ? `${ev.result.caveats.length} caveats` : null,
                ])})` : ""}`,
            });
        }

        case "mission_replanned":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                message: `Mission replanned: added follow-up task ${ev.taskId}`,
            });

        case "verifier_verdict": {
            const passed = !!ev.passed;
            const failures = (ev.structuralFailures || []).filter(Boolean);
            const evidenceDetail: string[] = [];
            const ev2 = ev.evidence || ({} as VerifierVerdictEvent["evidence"]);
            if (ev2.browserVerifiedUrls?.length) {
                evidenceDetail.push(`urls=${ev2.browserVerifiedUrls.length}`);
            }
            if (ev2.screenshotPaths?.length) {
                evidenceDetail.push(`screenshots=${ev2.screenshotPaths.length}`);
            }
            evidenceDetail.push(`visualsRendered=${ev2.visualsRendered ? "yes" : "no"}`);
            if (ev2.loadingStuckObserved) {
                evidenceDetail.push("loadingStuck");
            }
            if (ev2.errorsObserved?.length) {
                evidenceDetail.push(`errors=${ev2.errorsObserved.length}`);
            }
            const verdictLabel = passed ? "Verifier PASSED" : "Verifier REJECTED";
            const detail = failures.length ? `: ${failures.join(", ")}` : "";
            // Per-step pass/fail breakdown, when the verifier
            // decomposed the goal into discrete phases.
            const stepResults = ev.stepResults || [];
            const stepIcons: Record<string, string> = {
                passed: "✓",
                ok: "✓",
                success: "✓",
                failed: "✗",
                error: "✗",
                broken: "✗",
                skipped: "·",
                not_applicable: "·",
                unknown: "?",
            };
            const stepBlock = stepResults.length
                ? `\nSteps: ${stepResults
                    .map((step) => {
                        const icon = stepIcons[String(step.status || "").toLowerCase()] || "?";
                        const trail = step.detail || step.reason || step.evidence;
                        return `${icon} ${step.step} (${step.status})${trail ? ` — ${shortText(trail, 80)}` : ""}`;
                    })
                    .join("; ")}`
                : "";
            const message =
                `${verdictLabel}${ev.targetTaskId ? ` for ${ev.targetTaskId}` : ""}${detail} — ${shortText(ev.summary || ev.decisionRationale, 180)} ` +
                `[${evidenceDetail.join(" · ")}${ev.feedbackRound != null ? ` · round=${ev.feedbackRound}` : ""}]${stepBlock}`;
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev),
                kind: passed ? "decision" : "error",
                level: passed ? "info" : "error",
                agentId: ev.verifierRunId,
                agentName: ev.verifierAgentId || "FabricVerifier",
                message,
            });
        }

        case "resource_lock_acquired":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                message: `Locked ${ev.key} for ${ev.mode || "work"}`,
            });

        case "resource_lock_released":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                message: `Released lock on ${ev.key}`,
            });

        case "mission_completed":
        case "mission_blocked":
        case "mission_failed":
        case "mission_cancelled":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: ev.type === "mission_failed" ? "error" : "log",
                level: ev.type === "mission_completed" ? "info" : "warn",
                message:
                    ev.type === "mission_completed" ? "Mission complete"
                    : ev.type === "mission_cancelled" ? "Mission cancelled"
                    : ev.type === "mission_blocked" ? "Mission blocked"
                    : `Mission failed${ev.reason ? `: ${ev.reason}` : ""}`,
            });

        case "slot_progress": {
            const slotId = ev.slotId || ev.agentId;
            next.slotProgress = {
                ...next.slotProgress,
                [slotId]: {
                    slotId,
                    agentId: ev.agentId,
                    status: ev.status,
                    agentName: ev.agentName,
                    role: ev.role,
                    reason: ev.reason,
                    currentStep: ev.currentStep,
                },
            };
            next.agentStatus = {
                ...next.agentStatus,
                [ev.agentId]: ev.status === "running" ? "running"
                    : ev.status === "done" ? "completed"
                        : ev.status === "approval_required" || ev.status === "waiting" ? "waiting"
                            : ev.status === "failed" ? "error"
                                : "queued",
            };
            if (ev.status === "running" && (ev.activeAgentId || ev.agentId)) {
                next.activeAgentId = ev.activeAgentId || ev.agentId;
            }
            return next;
        }

        case "phase_start":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "phase", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: `Phase ${ev.phase.number}: ${ev.phase.title}`,
            });

        case "phase_complete":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "phase", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: `Phase ${ev.phaseNumber} complete`,
            });

        case "phase_detail":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: formatOperationalMessage(ev.detail),
            });

        case "agent_decision":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: formatOperationalMessage(ev.decision),
            });

        case "agent_error":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "error", level: "error",
                agentId: ev.agentId, agentName: ev.agentName,
                message: formatOperationalMessage(ev.error),
            });

        case "log_line":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: ev.level,
                agentId: ev.agentId, agentName: ev.agentName, message: formatOperationalMessage(ev.message),
            });

        case "tool_call_started":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "tool_start", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                callId: ev.callId, toolName: ev.toolName,
                toolKind: ev.toolKind,
                operationKind: ev.operationKind,
                argsPreview: ev.argsPreview,
                message: formatToolStartMessage(ev.toolName, ev.argsPreview),
            });

        case "tool_call_ended":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "tool_end",
                level: ev.status === "ok" ? "info" : "error",
                agentId: ev.agentId, callId: ev.callId, toolName: ev.toolName,
                toolKind: ev.toolKind,
                operationKind: ev.operationKind,
                toolStatus: ev.status,
                errorPreview: ev.errorPreview,
                durationMs: ev.durationMs,
                latencyBreakdownMs: ev.latencyBreakdownMs,
                message: formatToolEndMessage(ev.toolName, ev.status, ev.durationMs),
            });

        case "tool_progress": {
            // Mirrors backend [TOOL_PROGRESS:...] lines so the user can see
            // *what is actually happening* inside a long-running tool call
            // (e.g. "FabricDataEngineer · create workspace inventory solution
            // · lakehouse table validation started · 147s elapsed") instead
            // of an opaque spinner.
            const elapsedSec = typeof ev.elapsedMs === "number" && ev.elapsedMs >= 1000
                ? ` · ${Math.round(ev.elapsedMs / 1000)}s elapsed`
                : "";
            const stepLabel = String(ev.step || "").replace(/_/g, " ").trim();
            const statusLabel = String(ev.status || "").replace(/_/g, " ").trim();
            const errorTail = ev.error ? ` — ${shortText(ev.error, 200)}` : "";
            const level: LogEntryInput["level"] =
                statusLabel === "failed" || statusLabel === "error"
                    ? "error"
                    : statusLabel === "warning" || statusLabel === "warn"
                        ? "warn"
                        : "info";
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev),
                kind: "log", level,
                agentId: ev.agentId,
                agentName: ev.agentName,
                toolName: ev.toolName,
                toolKind: ev.toolKind,
                operationKind: ev.operationKind,
                callId: ev.callId,
                message: `${formatToolName(ev.toolName)} · ${stepLabel || "step"} ${statusLabel || "update"}${elapsedSec}${errorTail}`,
            });
        }

        case "llm_request_started": {
            const message = `Preparing ${llmTaskLabel(ev)}`;
            const progressed = updateLiveAgentProgress(next, ev.agentId, ev.agentName, message, ev.taskTitle);
            return pushEventLog(progressed, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "phase", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message,
                progressMode: "requesting",
                progressSemanticClass: "preparing",
                payloadSummary: { ...(ev.payloadSummary || {}), taskDescription: ev.taskTitle || ev.promptSummary },
            });
        }

        case "llm_stream_phase_changed": {
            const mode = llmProgressMode(ev.phase);
            const phaseLabel = String(ev.phase || mode || "responding").replace(/[_-]/g, " ");
            const detail = shortText(ev.message || ev.taskTitle || ev.toolName || "current task", 160);
            const message = ev.message
                ? formatOperationalMessage(ev.message)
                : mode === "tool-input"
                    ? `Preparing tool input for ${detail}`
                    : mode === "tool-use"
                        ? `Running tool work for ${detail}`
                        : mode === "thinking"
                            ? `Thinking through ${detail}`
                            : mode === "requesting"
                                ? `Preparing ${detail}`
                                : `Streaming assistant response for ${detail}`;
            const progressed = updateLiveAgentProgress(next, ev.agentId, ev.agentName, message, ev.taskTitle);
            return pushEventLog(progressed, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: mode === "thinking" ? "decision" : "phase", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                toolName: ev.toolName,
                message,
                counts: typeof ev.tokenCount === "number" ? { tokenCount: ev.tokenCount } : undefined,
                progressMode: mode,
                progressSemanticClass: semanticForProgressMode(mode),
                payloadSummary: { ...(ev.payloadSummary || {}), taskDescription: ev.taskTitle || phaseLabel },
            });
        }

        case "thinking_started":
        case "thinking_delta":
        case "thinking_finalized": {
            const detail = shortText(ev.summary || ev.delta || ev.text || llmTaskLabel(ev), 200);
            const message = ev.type === "thinking_finalized"
                ? `Finished thinking: ${detail}`
                : `Thinking: ${detail}`;
            const progressed = updateLiveAgentProgress(next, ev.agentId, ev.agentName, message, ev.summary);
            return pushOrUpdateStreamLog(progressed, ev, "thinking", ev.type === "thinking_finalized");
        }

        case "assistant_text_delta":
        case "assistant_text_finalized": {
            const progressed = updateLiveAgentProgress(
                next,
                ev.agentId,
                ev.agentName,
                ev.type === "assistant_text_finalized" ? "Assistant response ready" : "Streaming assistant response",
                "Assistant response",
            );
            return pushOrUpdateStreamLog(progressed, ev, "assistant", ev.type === "assistant_text_finalized");
        }

        case "activity_rollup": {
            const failed = String(ev.status || "").toLowerCase() === "failed";
            const detailSuffix = ev.detailCount ? ` (+${ev.detailCount} detail events)` : "";
            const countText = ev.counts ? compactList([
                typeof ev.counts.toolCalls === "number" ? `${ev.counts.toolCalls} tool ${ev.counts.toolCalls === 1 ? "call" : "calls"}` : null,
                typeof ev.counts.outputChars === "number" ? `${ev.counts.outputChars} chars` : null,
            ]) : "";
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev),
                kind: "rollup", level: failed ? "error" : "info",
                agentId: ev.agentId,
                agentName: ev.agentName,
                callId: ev.callId,
                toolName: ev.toolName,
                toolKind: ev.toolKind,
                operationKind: ev.operationKind,
                durationMs: ev.durationMs,
                coveredSeqStart: ev.coveredSeqStart,
                coveredSeqEnd: ev.coveredSeqEnd,
                detailCount: ev.detailCount,
                rollupStatus: ev.status,
                counts: ev.counts,
                message: `${shortText(ev.summary, 260)}${countText ? ` (${countText})` : ""}${detailSuffix}`,
            });
        }

        case "user_message_queued":
        case "user_message_broadcast":
        case "user_message_delivered":
        case "user_message_failed": {
            const failed = ev.type === "user_message_failed";
            const delivered = ev.type === "user_message_delivered";
            const broadcast = ev.type === "user_message_broadcast";
            const target = broadcast
                ? `${ev.targetCount ?? ev.targetAgentSessionIds?.length ?? 0} agents`
                : ev.agentName || ev.targetAgentSessionId || ev.agentId || "selected agent";
            const verb = failed ? "Steering failed"
                : delivered ? "Steering delivered"
                    : broadcast ? "Steering broadcast"
                        : ev.mode === "interrupt" ? "Interrupt queued"
                            : "Steering queued";
            const round = delivered && ev.deliveredAtRound ? ` · round ${ev.deliveredAtRound}` : "";
            const reason = failed && ev.reason ? ` — ${shortText(ev.reason, 180)}` : "";
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev),
                kind: "steering", level: failed ? "error" : delivered ? "info" : "warn",
                agentId: ev.agentId || ev.targetAgentSessionId || undefined,
                agentName: ev.agentName,
                steeringId: ev.steeringId,
                targetMode: ev.targetMode,
                deliveryMode: ev.mode,
                message: `${verb} for ${target}${round}: ${shortText(ev.messagePreview || "user instruction", 220)}${reason}`,
            });
        }

        case "turn_interrupt_requested":
        case "turn_interrupt_deferred":
        case "turn_interrupted": {
            const completed = ev.type === "turn_interrupted";
            const deferred = ev.type === "turn_interrupt_deferred";
            const label = completed ? "Interrupt delivered" : deferred ? "Interrupt deferred" : "Interrupt requested";
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev),
                kind: "steering", level: completed ? "info" : "warn",
                agentId: ev.agentId || ev.targetAgentSessionId || undefined,
                agentName: ev.agentName,
                steeringId: ev.steeringId,
                targetMode: ev.targetMode,
                deliveryMode: ev.mode,
                message: `${label}: ${shortText(ev.messagePreview || "user instruction", 220)}${ev.reason ? ` — ${shortText(ev.reason, 180)}` : ""}`,
            });
        }

        case "diagnostic_baseline_captured":
        case "diagnostic_new_issues":
        case "diagnostic_resolved_issues":
        case "diagnostic_required": {
            if (ev.type === "diagnostic_required") {
                return pushEventLog(next, ev, {
                    seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev),
                    kind: "diagnostic", level: "warn",
                    agentId: ev.agentId,
                    agentName: ev.agentName,
                    toolName: ev.toolName,
                    toolKind: ev.toolKind,
                    operationKind: ev.operationKind,
                    message: `Diagnostic checkpoint required${ev.toolName ? ` after ${formatToolName(ev.toolName).toLowerCase()}` : ""}: ${shortText(ev.reason || ev.policyDecision || ev.summary || "inspect evidence before continuing", 220)}`,
                });
            }
            const isNewIssue = ev.type === "diagnostic_new_issues";
            const isResolved = ev.type === "diagnostic_resolved_issues";
            const count = isNewIssue ? ev.newIssueCount : isResolved ? ev.resolvedIssueCount : ev.baselineCount;
            const issuePreview = Array.isArray(ev.issues) && ev.issues.length
                ? ` — ${shortText(typeof ev.issues[0] === "string" ? ev.issues[0] : ev.issues[0]?.message, 180)}`
                : "";
            const label = isNewIssue ? "Diagnostic issue detected"
                : isResolved ? "Diagnostic issue resolved"
                    : "Diagnostic baseline captured";
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev),
                kind: "diagnostic", level: isNewIssue ? "error" : "info",
                agentId: ev.agentId,
                agentName: ev.agentName,
                callId: ev.callId,
                toolName: ev.toolName,
                toolKind: ev.toolKind,
                operationKind: ev.operationKind,
                message: `${label}${ev.toolName ? ` after ${formatToolName(ev.toolName).toLowerCase()}` : ""}${count != null ? ` (${count})` : ""}: ${shortText(ev.summary || "diagnostic checkpoint", 220)}${issuePreview}`,
            });
        }

        case "mcp_server_approval_required":
        case "mcp_server_approved":
        case "mcp_server_rejected":
        case "mcp_session_refreshed":
        case "runtime_config_refreshed":
        case "memory_loaded":
        case "memory_written":
        case "memory_updated":
        case "memory_ignored":
        case "plugin_enabled":
        case "plugin_disabled":
        case "capability_pack_enabled":
        case "capability_pack_disabled":
        case "approval_repeated_denial":
        case "approval_fallback_required": {
            const typeLabel = String(ev.type).replace(/_/g, " ");
            const level: LogEntryInput["level"] = /rejected|ignored|denial|fallback|required/.test(ev.type) ? "warn" : "info";
            const target = compactList([
                ev.serverId ? `server ${ev.serverId}` : null,
                ev.pluginId ? `plugin ${ev.pluginId}` : null,
                ev.capabilityPackId ? `pack ${ev.capabilityPackId}` : null,
                ev.memoryScope ? `memory ${ev.memoryScope}` : null,
                ev.configVersion ? `config ${ev.configVersion}` : null,
            ]);
            const toolPreview = Array.isArray(ev.toolsPreview) && ev.toolsPreview.length
                ? ` (${ev.toolsPreview.slice(0, 4).join(", ")}${ev.toolsPreview.length > 4 ? ` +${ev.toolsPreview.length - 4}` : ""})`
                : "";
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev),
                kind: "diagnostic", level,
                message: `${typeLabel}${target ? ` · ${target}` : ""}: ${shortText(ev.summary || ev.reason || ev.risk || "runtime state updated", 240)}${toolPreview}`,
            });
        }

        case "action": {
            const a = ev.action as any;
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "action", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: formatActionMessage(a),
            });
        }

        case "change_recorded":
            return mergeChangeRecords(next, [{
                recordId: ev.recordId,
                kind: ev.kind,
                status: ev.status,
                targetName: ev.targetName,
                targetType: ev.targetType,
                targetScope: ev.targetScope,
                summary: ev.summary,
                toolName: ev.toolName,
                agentId: ev.agentId,
                agentName: ev.agentName,
                targetId: ev.targetId,
                folderId: ev.folderId,
                folderName: ev.folderName,
                parentFolderId: ev.parentFolderId,
                createdItems: ev.createdItems,
                webUrl: ev.webUrl,
                ts: ev.ts,
            }]);

        case "artifact_added": {
            if (!next.artifacts[ev.artifactId]) {
                next.artifactOrder = [...next.artifactOrder, ev.artifactId];
            }
            next.artifacts = {
                ...next.artifacts,
                [ev.artifactId]: {
                    artifactId: ev.artifactId,
                    agentId: ev.agentId,
                    kind: ev.kind,
                    name: ev.name,
                    state: ev.state,
                    summary: ev.summary,
                    details: ev.details,
                    webUrl: ev.webUrl ?? null,
                },
            };
            return next;
        }

        case "artifact_updated": {
            const existing = next.artifacts[ev.artifactId];
            if (!existing) return next;
            next.artifacts = {
                ...next.artifacts,
                [ev.artifactId]: {
                    ...existing,
                    state: ev.state,
                    webUrl: ev.webUrl ?? existing.webUrl ?? null,
                },
            };
            return next;
        }

        case "approval_required":
            next.approvals = {
                ...next.approvals,
                [ev.approvalId]: {
                    approvalId: ev.approvalId,
                    slotId: ev.slotId,
                    agentId: ev.agentId,
                    summary: ev.summary,
                    blastRadius: ev.blastRadius ?? null,
                    reversible: ev.reversible ?? null,
                    toolCallPreview: ev.toolCallPreview ?? null,
                    recoveryActions: ev.recoveryActions ?? [],
                    raisedAt: ev.ts,
                },
            };
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "warn",
                agentId: ev.agentId,
                message: `Approval required: ${ev.summary}`,
            });

        case "approval.resolved": {
            const existing = next.approvals[ev.approvalId];
            if (existing) {
                next.approvals = {
                    ...next.approvals,
                    [ev.approvalId]: {
                        ...existing,
                        resolved: true,
                        resolvedAction: ev.action,
                    },
                };
            }
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                message: `Approval ${ev.approvalId}: ${ev.action}`,
            });
        }

        case "job_complete":
        case "job_failed":
        case "job_cancelled":
            next.jobStatus = ev.status;
            next.totalDuration = ev.totalDuration;
            next.terminalType = ev.type;
            return pushEventLog(settleRuntimeAfterTerminal(next, ev.type), ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log",
                level: ev.type === "job_complete" ? "info" : ev.type === "job_failed" ? "error" : "warn",
                message:
                    ev.type === "job_complete" ? `Run complete (${ev.totalDuration ?? "?"})`
                    : ev.type === "job_cancelled" ? `Run cancelled (${ev.totalDuration ?? "?"})`
                    : `Run failed (${ev.totalDuration ?? "?"})${ev.reason ? `: ${shortText(ev.reason, 360)}` : ""}`,
            });

        default:
            // Exhaustiveness for new event types added on the backend
            // but not yet mirrored on the union. Compile-time check:
            // if this line errors, add the missing branch above.
            return next;
    }
}
