/**
 * Mission Control — React hook that drives the mission-control surface.
 *
 * Responsibilities:
 *   - Subscribe to the session's SSE stream via ``EventSource``.
 *   - Dedup + merge each event into state through ``missionReducer``.
 *   - Resume the stream using the browser's native ``Last-Event-ID``
 *     (emitted as ``id: <seq>`` by the backend SSE endpoint).
 *   - Exponential-backoff reconnect on error, capped at 30 s. Falls
 *     back to a one-shot ``GET /api/sessions/{id}`` after three
 *     consecutive failed reconnect attempts.
 *   - Cleans up the EventSource on unmount / sessionId change.
 *
 * The hook is deliberately small: anything persisted lives in the
 * reducer, not in React refs, so the state can be inspected from
 * tests without rendering the component.
 */

import { useEffect, useReducer, useRef } from "react";
import * as api from "../../../controller/AgentHubApi";
import {
    missionReducer,
    initialMissionState,
    type MissionState,
} from "./missionReducer";
import type { MissionEvent } from "./events";

export interface UseMissionStream {
    state: MissionState;
    reconnectCount: number;
    isConnected: boolean;
    error: string | null;
}

const MAX_BACKOFF_MS = 30_000;
const MAX_BEFORE_FALLBACK = 3;

export function useMissionStream(
    sessionId: string | null,
    opts: { enabled?: boolean; getSessionOpts?: { githubToken?: string; fabricToken?: string } } = {},
): UseMissionStream {
    const { enabled = true, getSessionOpts } = opts;
    const [state, dispatch] = useReducer(missionReducer, undefined, initialMissionState);
    const reconnectCountRef = useRef(0);
    const connectedRef = useRef(false);
    const errorRef = useRef<string | null>(null);
    // React doesn't track refs, so we keep a tiny state slice so
    // callers that render the connection indicator re-render on
    // change. ``useReducer`` already covers the payload stream.
    const [, force] = useReducer((n: number) => n + 1, 0);

    useEffect(() => {
        if (!sessionId || !enabled) return undefined;

        let es: EventSource | null = null;
        let backoffTimer: ReturnType<typeof setTimeout> | null = null;
        let cancelled = false;
        let failureStreak = 0;

        const connect = () => {
            if (cancelled) return;
            try {
                es = api.subscribeToSessionEvents(sessionId);
            } catch (e) {
                // EventSource constructor should not throw, but guard anyway.
                errorRef.current = String(e);
                force();
                scheduleReconnect();
                return;
            }
            es.onopen = () => {
                connectedRef.current = true;
                errorRef.current = null;
                failureStreak = 0;
                force();
            };
            es.onmessage = (event: MessageEvent<string>) => {
                try {
                    const payload = JSON.parse(event.data) as MissionEvent;
                    dispatch(payload);
                } catch {
                    /* ignore malformed event */
                }
            };
            es.onerror = () => {
                connectedRef.current = false;
                failureStreak += 1;
                reconnectCountRef.current = failureStreak;
                force();
                // The browser closes the ES on fatal error; closing
                // here is a no-op when it already went to CLOSED.
                try { es?.close(); } catch { /* ok */ }
                if (failureStreak >= MAX_BEFORE_FALLBACK && sessionId) {
                    // Fallback: pull a fresh snapshot so the reducer
                    // catches up to whatever happened while we were
                    // off the wire.
                    (async () => {
                        try {
                            const job = await api.getSession(sessionId, getSessionOpts || {});
                            dispatch({
                                type: "run_overview",
                                seq: Number.MAX_SAFE_INTEGER - 1, // one-shot snapshot, don't pollute lastSeq permanently
                                sessionId,
                                ts: new Date().toISOString(),
                                job: {
                                    id: job.id,
                                    status: job.status,
                                    startedAt: job.started_at || null,
                                    completedAt: job.completed_at || null,
                                },
                                composition: job.composition || null,
                                activeAgentId: null,
                                artifacts: [],
                                slotProgress: [],
                            } as MissionEvent);
                        } catch (e) {
                            errorRef.current = (e as Error)?.message || String(e);
                            force();
                        }
                    })();
                }
                scheduleReconnect();
            };
        };

        const scheduleReconnect = () => {
            if (cancelled) return;
            if (backoffTimer) return;
            const ms = Math.min(
                MAX_BACKOFF_MS,
                500 * Math.pow(2, Math.min(failureStreak, 6)),
            );
            backoffTimer = setTimeout(() => {
                backoffTimer = null;
                connect();
            }, ms);
        };

        connect();

        return () => {
            cancelled = true;
            if (backoffTimer) clearTimeout(backoffTimer);
            try { es?.close(); } catch { /* ok */ }
            connectedRef.current = false;
        };
    // getSessionOpts intentionally omitted — identity changes every render
    // but the captured value at mount is enough for the fallback fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sessionId, enabled]);

    return {
        state,
        reconnectCount: reconnectCountRef.current,
        isConnected: connectedRef.current,
        error: errorRef.current,
    };
}
