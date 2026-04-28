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
    PublicLogCategory,
    SlotProgress,
    JobStatusLite,
    VerifierVerdictEvent,
} from "./events";
import type { Composition } from "./types";
import { formatActionMessage, formatOperationalMessage, formatToolEndMessage, formatToolStartMessage } from "./logPresentation";

export interface LogEntry {
    seq: number;
    ts: string;
    agentId?: string;
    agentName?: string;
    level: "info" | "warn" | "error";
    message: string;
    logCategory: PublicLogCategory;
    kind: "log" | "phase" | "decision" | "tool_start" | "tool_end" | "action" | "error";
    callId?: string;
    toolName?: string;
    argsPreview?: Record<string, unknown>;
    toolStatus?: "ok" | "error";
    errorPreview?: string | null;
    durationMs?: number;
    eventId?: string;
    payloadDigest?: string;
    payloadSummary?: Record<string, unknown>;
    sourceEventType?: string;
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

function defaultLogCategoryForEntry(entry: LogEntryInput): PublicLogCategory {
    if (entry.kind === "tool_start" || entry.kind === "tool_end" || entry.kind === "error") {
        return "diagnostic";
    }
    if (entry.kind === "phase" || entry.kind === "decision" || entry.kind === "action") {
        return "high_level";
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
                            : s.status === "approval_required" ? "waiting"
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
            if (ev.status === "running") next.activeAgentId = ev.agentId;
            next.agentStatus = {
                ...next.agentStatus,
                [ev.agentId]: ev.status === "failed" ? "error" : ev.status,
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
                message: `Mission seeded with ${ev.taskCount ?? 0} task${ev.taskCount === 1 ? "" : "s"}`,
            });

        case "task_created":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                message: `Task queued: ${ev.task.title}`,
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
                message: `Generalist checkpoint: ${ev.readyTaskCount ?? 0} ready, ${ev.runningSubagentCount ?? 0} running, ${ev.completedTaskCount ?? 0} complete`,
            });

        case "generalist_context_pack":
        case "agent_context_received":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "info",
                agentId: ev.runId,
                agentName: ev.agentName || ev.agentId,
                message: `Delegated structured context to ${ev.agentName || ev.agentId} for ${ev.taskTitle || ev.taskId}`,
            });

        case "generalist_direct_work":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "info",
                agentId: ev.runId,
                message: `Generalist handled directly: ${ev.taskTitle || ev.taskId} - ${ev.reason}`,
            });

        case "generalist_state_decision":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: ev.errorCount ? "warn" : "info",
                agentId: ev.runId,
                message: `Generalist integrated feedback: ${ev.summary || ev.rationale || ev.resultStatus || "subagent result reviewed"}`,
            });

        case "generalist_steering":
        case "subagent_steered":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "warn",
                agentId: ev.runId,
                agentName: ev.agentName || ev.agentId,
                message: `Generalist steered ${ev.agentName || ev.agentId || ev.runId}: ${ev.reason}`,
            });

        case "subagent_inspected": {
            const signal = ev.signal || {};
            const signalLabel = typeof signal.kind === "string" ? signal.kind : "progress";
            const toolName = typeof signal.toolName === "string" ? ` (${signal.toolName})` : "";
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "info",
                agentId: ev.runId,
                message: `Generalist inspected subagent ${signalLabel}${toolName}`,
            });
        }

        case "subagent_stale":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log", level: "warn",
                agentId: ev.runId,
                message: `Subagent progress stale after ${Math.round(ev.staleSeconds ?? 0)} s`,
            });

        case "subagent_abandoned":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "warn",
                agentId: ev.runId,
                message: `Subagent reassigned to ${ev.replacementTaskId}: ${ev.reason}`,
            });

        case "subagent_cancelled":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "decision", level: "warn",
                agentId: ev.runId,
                message: `Subagent cancelled: ${ev.reason}`,
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
                message: `Subagent started: ${ev.task?.title || ev.run.agentId || ev.run.agent_id || ev.run.id}`,
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
                message: `Task result: ${ev.result.summary}`,
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
            const message =
                `${verdictLabel}${detail} — ${ev.decisionRationale} ` +
                `[${evidenceDetail.join(" · ")}]`;
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
                },
            };
            next.agentStatus = {
                ...next.agentStatus,
                [ev.agentId]: ev.status === "running" ? "running"
                    : ev.status === "done" ? "completed"
                        : ev.status === "approval_required" ? "waiting"
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
                argsPreview: ev.argsPreview,
                message: formatToolStartMessage(ev.toolName, ev.argsPreview),
            });

        case "tool_call_ended":
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "tool_end",
                level: ev.status === "ok" ? "info" : "error",
                agentId: ev.agentId, callId: ev.callId, toolName: ev.toolName,
                toolStatus: ev.status,
                errorPreview: ev.errorPreview,
                durationMs: ev.durationMs,
                message: formatToolEndMessage(ev.toolName, ev.status, ev.durationMs),
            });

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
            return pushEventLog(next, ev, {
                seq: ev.seq, ts: ev.ts, logCategory: publicLogCategoryFromEvent(ev), kind: "log",
                level: ev.type === "job_complete" ? "info" : "warn",
                message:
                    ev.type === "job_complete" ? `Run complete (${ev.totalDuration ?? "?"})`
                    : ev.type === "job_cancelled" ? `Run cancelled (${ev.totalDuration ?? "?"})`
                    : `Run failed (${ev.totalDuration ?? "?"})`,
            });

        default:
            // Exhaustiveness for new event types added on the backend
            // but not yet mirrored on the union. Compile-time check:
            // if this line errors, add the missing branch above.
            return next;
    }
}
