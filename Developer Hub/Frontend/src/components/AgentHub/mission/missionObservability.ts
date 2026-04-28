import type { MissionEvent } from "./events";

export type MissionObservationKind =
    | "hook_mount"
    | "hook_unmount"
    | "connect_start"
    | "connect_wait_token"
    | "reconnect_scheduled"
    | "sse_open"
    | "sse_event"
    | "sse_error"
    | "sse_close"
    | "poll_full_session_start"
    | "poll_full_session_ok"
    | "poll_full_session_skip"
    | "poll_full_session_error"
    | "poll_status_start"
    | "poll_status_ok"
    | "poll_status_error"
    | "terminal_recovery_start"
    | "terminal_recovery_ok"
    | "terminal_recovery_miss"
    | "terminal_recovery_error"
    | "reducer_drop_duplicate"
    | "reducer_drop_trace";

export interface MissionObservation {
    observedAt: string;
    kind: MissionObservationKind;
    sessionId?: string | null;
    seq?: number | null;
    eventId?: string | null;
    eventType?: string | null;
    payloadDigest?: string | null;
    message?: string | null;
    details?: Record<string, unknown>;
}

const MAX_OBSERVATIONS = 800;
const buffer: MissionObservation[] = [];

function compactDetails(details?: Record<string, unknown>): Record<string, unknown> | undefined {
    if (!details) return undefined;
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(details).slice(0, 20)) {
        if (typeof value === "string") {
            result[key] = value.length > 500 ? `${value.slice(0, 500)}...` : value;
        } else {
            result[key] = value;
        }
    }
    return result;
}

export function recordMissionObservation(observation: Omit<MissionObservation, "observedAt">): void {
    const row: MissionObservation = {
        observedAt: new Date().toISOString(),
        ...observation,
        details: compactDetails(observation.details),
    };
    buffer.push(row);
    if (buffer.length > MAX_OBSERVATIONS) {
        buffer.splice(0, buffer.length - MAX_OBSERVATIONS);
    }

    const win = globalThis as typeof globalThis & {
        __agentHubMissionObservability?: {
            snapshot: () => MissionObservation[];
            clear: () => void;
        };
    };
    if (!win.__agentHubMissionObservability) {
        win.__agentHubMissionObservability = {
            snapshot: getMissionObservationSnapshot,
            clear: clearMissionObservations,
        };
    }
}

export function recordMissionEventObservation(kind: MissionObservationKind, event: MissionEvent | any, details?: Record<string, unknown>): void {
    recordMissionObservation({
        kind,
        sessionId: event?.sessionId ?? null,
        seq: typeof event?.seq === "number" ? event.seq : null,
        eventId: typeof event?.eventId === "string" ? event.eventId : null,
        eventType: typeof event?.type === "string" ? event.type : null,
        payloadDigest: typeof event?.payloadDigest === "string" ? event.payloadDigest : null,
        details,
    });
}

export function getMissionObservationSnapshot(): MissionObservation[] {
    return buffer.slice();
}

export function clearMissionObservations(): void {
    buffer.length = 0;
}
