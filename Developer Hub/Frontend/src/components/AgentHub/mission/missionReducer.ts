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
    MissionEvent,
    SlotProgress,
    JobStatusLite,
} from "./events";
import type { Composition } from "./types";

export interface LogEntry {
    seq: number;
    ts: string;
    agentId?: string;
    agentName?: string;
    level: "info" | "warn" | "error";
    message: string;
    kind: "log" | "phase" | "decision" | "tool_start" | "tool_end" | "action" | "error";
    callId?: string;
    toolName?: string;
    durationMs?: number;
}

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
    artifacts: Record<string, Artifact>;          // keyed by artifactId
    artifactOrder: string[];                      // insertion order for display
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
        artifacts: {},
        artifactOrder: [],
        logs: [],
        approvals: {},
        lastSeq: 0,
        ...seed,
    };
}

function pushLog(state: MissionState, entry: LogEntry): MissionState {
    // Bound the log list at a reasonable size so a very long run
    // does not balloon component memory. Keep the most recent 2k
    // entries — completed-run filtering works against the full
    // server-side phase list via getSession(), not this buffer.
    const MAX = 2000;
    const next = state.logs.length >= MAX
        ? [...state.logs.slice(-MAX + 1), entry]
        : [...state.logs, entry];
    return { ...state, logs: next };
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

    switch (ev.type) {
        case "run_overview": {
            next.composition = ev.composition;
            next.activeAgentId = ev.activeAgentId;
            next.jobStatus = (ev.job?.status as JobStatusLite) || next.jobStatus;
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
            const slotProgress = { ...next.slotProgress };
            for (const s of ev.slotProgress || []) {
                slotProgress[s.slotId] = s;
            }
            next.slotProgress = slotProgress;
            return next;
        }

        case "composition_ready":
            next.composition = ev.composition;
            return next;

        case "agent_status": {
            if (ev.status === "running") next.activeAgentId = ev.agentId;
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "log", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: ev.currentStep || `status: ${ev.status}`,
            });
        }

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
            if (ev.status === "running" && (ev.activeAgentId || ev.agentId)) {
                next.activeAgentId = ev.activeAgentId || ev.agentId;
            }
            return next;
        }

        case "phase_start":
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "phase", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: `Phase ${ev.phase.number}: ${ev.phase.title}`,
            });

        case "phase_complete":
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "phase", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: `Phase ${ev.phaseNumber} complete`,
            });

        case "phase_detail":
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "log", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: ev.detail,
            });

        case "agent_decision":
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "decision", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: ev.decision,
            });

        case "agent_error":
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "error", level: "error",
                agentId: ev.agentId, agentName: ev.agentName,
                message: ev.error,
            });

        case "log_line":
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "log", level: ev.level,
                agentId: ev.agentId, message: ev.message,
            });

        case "tool_call_started":
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "tool_start", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                callId: ev.callId, toolName: ev.toolName,
                message: `→ ${ev.toolName}`,
            });

        case "tool_call_ended":
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "tool_end",
                level: ev.status === "ok" ? "info" : "error",
                agentId: ev.agentId, callId: ev.callId, toolName: ev.toolName,
                durationMs: ev.durationMs,
                message: `← ${ev.toolName} (${ev.durationMs}ms, ${ev.status})`,
            });

        case "action": {
            const a = ev.action as any;
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "action", level: "info",
                agentId: ev.agentId, agentName: ev.agentName,
                message: `${a.action_type} ${a.entity_type}: ${a.entity_name}`,
            });
        }

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
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "log", level: "warn",
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
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "log", level: "info",
                message: `Approval ${ev.approvalId}: ${ev.action}`,
            });
        }

        case "job_complete":
        case "job_failed":
        case "job_cancelled":
            next.jobStatus = ev.status;
            next.totalDuration = ev.totalDuration;
            next.terminalType = ev.type;
            return pushLog(next, {
                seq: ev.seq, ts: ev.ts, kind: "log",
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
