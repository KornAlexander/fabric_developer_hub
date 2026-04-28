/**
 * Mission Control — React hook that drives the mission-control surface.
 *
 * It subscribes to the Server-Sent Events (SSE) endpoint to receive
 * real-time granular events (log_line, tool_call_started, phase_start, etc)
 * using the fetch-based `subscribeToSessionEventsFetch` method to handle
 * authorization headers smoothly inside the Fabric iframe sandbox.
 * Once the auth token is available, it streams events securely.
 */

import { useEffect, useReducer, useRef, useState } from "react";
import * as api from "../../../controller/AgentHubApi";
import {
    missionReducer,
    initialMissionState,
    type MissionState,
} from "./missionReducer";
import type { JobStatusLite, MissionEvent } from "./events";
import { recordMissionEventObservation, recordMissionObservation } from "./missionObservability";

export interface UseMissionStream {
    state: MissionState;
    reconnectCount: number;
    isConnected: boolean;
    error: string | null;
}

const RECONNECT_DELAY_MS = 2_000;
const TERMINAL_STATUSES: JobStatusLite[] = ["completed", "failed", "cancelled"];

function readStoredGithubToken(): string {
    // Keep stream auth resilient when sessionStorage gets wiped on iframe
    // reloads but localStorage still has a valid token.
    try {
        const local = window.localStorage?.getItem("github_token");
        if (local) {
            try { window.sessionStorage?.setItem("github_token", local); } catch { /* ignore */ }
            return local;
        }
    } catch { /* ignore */ }
    try {
        return window.sessionStorage?.getItem("github_token") || "";
    } catch {
        return "";
    }
}

export function useMissionStream(
    sessionId: string | null,
    opts: { enabled?: boolean; getSessionOpts?: { githubToken?: string; fabricToken?: string } } = {},
): UseMissionStream {
    const { enabled = true, getSessionOpts } = opts;
    
    // Store tokens in refs since they update asynchronously 
    // without triggering excessive re-renders of the effect.
    const fabricTokenRef = useRef(getSessionOpts?.fabricToken);
    fabricTokenRef.current = getSessionOpts?.fabricToken;
    const githubTokenRef = useRef(getSessionOpts?.githubToken);
    githubTokenRef.current = getSessionOpts?.githubToken;

    const [state, dispatch] = useReducer(missionReducer, undefined, initialMissionState);
    const stateRef = useRef(state);
    stateRef.current = state;
    const [reconnectCount, setReconnectCount] = useState(0);
    const [isConnected, setIsConnected] = useState(false);
    const [streamUnavailable, setStreamUnavailable] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const aborterRef = useRef<AbortController | null>(null);
    const polledAgentStatusRef = useRef<Record<string, string>>({});

    useEffect(() => {
        polledAgentStatusRef.current = {};
        setStreamUnavailable(false);
    }, [sessionId]);

    useEffect(() => {
        if (!sessionId || !enabled) {
            setIsConnected(false);
            return undefined;
        }

        if (streamUnavailable) {
            setIsConnected(false);
            return undefined;
        }

        let active = true;
        let attempts = 0;
        recordMissionObservation({ kind: "hook_mount", sessionId });

        const scheduleReconnect = () => {
            if (streamUnavailable) return;
            recordMissionObservation({
                kind: "reconnect_scheduled",
                sessionId,
                details: { delayMs: RECONNECT_DELAY_MS, nextAttempt: attempts + 1 },
            });
            reconnectTimerRef.current = setTimeout(() => {
                setReconnectCount((c) => c + 1);
                connect();
            }, RECONNECT_DELAY_MS);
        };

        const syncPersistedTerminalState = async (reason: string): Promise<boolean> => {
            const fabric = fabricTokenRef.current;
            if (!fabric) return false;
            recordMissionObservation({ kind: "terminal_recovery_start", sessionId, details: { reason } });

            try {
                const job: any = await api.getSession(sessionId, {
                    githubToken: undefined,
                    fabricToken: fabric,
                });
                if (!active) return false;

                const rawStatus = String(job?.status || "running").toLowerCase();
                const allowed: JobStatusLite[] = [
                    "planned", "approved", "running", "completed", "failed", "cancelled",
                ];
                const jobStatus: JobStatusLite = allowed.includes(rawStatus as JobStatusLite)
                    ? (rawStatus as JobStatusLite)
                    : "running";

                if (!TERMINAL_STATUSES.includes(jobStatus)) {
                    recordMissionObservation({
                        kind: "terminal_recovery_miss",
                        sessionId,
                        details: { reason, status: jobStatus },
                    });
                    return false;
                }
                recordMissionObservation({
                    kind: "terminal_recovery_ok",
                    sessionId,
                    details: { reason, status: jobStatus, agentCount: Array.isArray(job?.agents) ? job.agents.length : 0 },
                });

                const agents: any[] = Array.isArray(job?.agents) ? job.agents : [];
                const slotProgress = agents
                    .map((a) => {
                        const aid = String(a?.session_id || a?.agent_session_id || a?.agent_id || "");
                        const st = String(a?.status || "").toLowerCase();
                        if (!aid || !st) return null;
                        return {
                            slotId: aid,
                            agentId: aid,
                            agentName: String(a?.role || a?.agent_id || aid),
                            role: a?.role,
                            status: st === "completed" ? "done" : st === "error" ? "failed" : st,
                        };
                    })
                    .filter(Boolean) as Array<{
                        slotId: string;
                        agentId: string;
                        agentName: string;
                        role?: string;
                        status: "queued" | "running" | "done" | "approval_required" | "failed";
                    }>;

                dispatch({
                    type: "run_overview",
                    seq: Math.max(0, stateRef.current.lastSeq) + 1,
                    sessionId,
                    ts: new Date().toISOString(),
                    job: {
                        id: String(job?.id || sessionId),
                        status: jobStatus,
                        startedAt: job?.started_at ?? null,
                        completedAt: job?.completed_at ?? null,
                    },
                    composition: (job?.composition ?? stateRef.current.composition ?? null) as any,
                    activeAgentId: null,
                    artifacts: [],
                    changes: [],
                    slotProgress,
                } as MissionEvent);

                setIsConnected(false);
                setError(null);
                // eslint-disable-next-line no-console
                console.info(`[mc-stream] recovered terminal sessionId=${sessionId} status=${jobStatus} reason=${reason}`);
                return true;
            } catch (e) {
                recordMissionObservation({
                    kind: "terminal_recovery_error",
                    sessionId,
                    message: e instanceof Error ? e.message : String(e),
                    details: { reason },
                });
                // eslint-disable-next-line no-console
                console.warn(`[mc-stream] terminal recovery failed sessionId=${sessionId}:`, e);
                return false;
            }
        };

        const connect = () => {
            if (!active) return;

            const fabric = fabricTokenRef.current;
            const github = githubTokenRef.current || readStoredGithubToken();
            attempts++;
            recordMissionObservation({
                kind: "connect_start",
                sessionId,
                details: { attempt: attempts, hasFabricToken: !!fabric, hasGithubToken: !!github, lastSeq: stateRef.current.lastSeq },
            });

            // eslint-disable-next-line no-console
            console.info(`[mc-stream] connecting sessionId=${sessionId} attempt=${attempts} fabric=${!!fabric} github=${!!github}`);

            if (!fabric) {
                recordMissionObservation({ kind: "connect_wait_token", sessionId, details: { attempt: attempts } });
                setIsConnected(false);
                setError("Waiting for Fabric token...");
                scheduleReconnect();
                return;
            }

            if (aborterRef.current) aborterRef.current.abort();

            const ctl = api.subscribeToSessionEventsFetch(
                sessionId,
                { 
                    githubToken: undefined,
                    fabricToken: fabric, 
                    lastEventId: stateRef.current.lastSeq >= 0 ? String(stateRef.current.lastSeq) : undefined 
                },
                {
                    onOpen: () => {
                        if (!active) return;
                        setStreamUnavailable(false);
                        recordMissionObservation({ kind: "sse_open", sessionId, details: { attempt: attempts } });
                        // eslint-disable-next-line no-console
                        console.info(`[mc-stream] SSE OPEN sessionId=${sessionId}`);
                        setIsConnected(true);
                        setError(null);
                        setReconnectCount(0); // Reset reconnect counter on success
                    },
                    onEvent: (data) => {
                        if (!active) return;
                        const event = data as MissionEvent;
                        recordMissionEventObservation("sse_event", event);
                        const seq = (event as any)?.seq;
                        if (typeof seq === "number" && seq <= stateRef.current.lastSeq) {
                            recordMissionEventObservation("reducer_drop_duplicate", event, { lastSeq: stateRef.current.lastSeq });
                        }
                        if ((event as any)?.logCategory === "trace") {
                            recordMissionEventObservation("reducer_drop_trace", event);
                        }
                        if (window.localStorage.getItem("agenthub_debug_stream") === "1") {
                            // eslint-disable-next-line no-console
                            console.debug("[mc-stream] event", (data as any)?.type, (data as any)?.seq);
                        }
                        dispatch(event);
                    },
                    onError: (err: any) => {
                        if (!active) return;
                        setIsConnected(false);
                        const msg = err instanceof Error ? err.message : String(err);
                        recordMissionObservation({ kind: "sse_error", sessionId, message: msg, details: { attempt: attempts } });
                        // eslint-disable-next-line no-console
                        console.error(`[mc-stream] SSE ERROR sessionId=${sessionId}: ${msg}`);
                        setError(`Stream disconnected: ${msg}`);

                        if (/404|no active execution/i.test(msg)) {
                            void syncPersistedTerminalState("events-404").then((recovered) => {
                                if (!active || recovered) return;
                                recordMissionObservation({
                                    kind: "sse_stream_unavailable",
                                    sessionId,
                                    message: "No active execution for persisted session; stopping live-stream reconnects.",
                                    details: { reason: "events-404", status: stateRef.current.jobStatus },
                                });
                                setStreamUnavailable(true);
                                setIsConnected(false);
                                setError("No live event stream is available for this persisted session.");
                            });
                            return;
                        }

                        scheduleReconnect();
                    },
                    onClose: () => {
                        if (!active) return;
                        recordMissionObservation({
                            kind: "sse_close",
                            sessionId,
                            details: { attempt: attempts, jobStatus: stateRef.current.jobStatus, lastSeq: stateRef.current.lastSeq },
                        });
                        setIsConnected(false);
                        
                        const js = stateRef.current.jobStatus;
                        // Determine terminal state based on latest known state
                        if (js !== "completed" && js !== "failed" && js !== "cancelled") {
                            void syncPersistedTerminalState("stream-close").then((recovered) => {
                                if (!active || recovered) return;
                                scheduleReconnect();
                            });
                        }
                    }
                }
            );
            aborterRef.current = ctl;
        };

        // Initialize connection
        connect();

        return () => {
            active = false;
            recordMissionObservation({ kind: "hook_unmount", sessionId, details: { lastSeq: stateRef.current.lastSeq } });
            setIsConnected(false);
            if (aborterRef.current) {
                aborterRef.current.abort();
                aborterRef.current = null;
            }
            if (reconnectTimerRef.current) {
                clearTimeout(reconnectTimerRef.current);
            }
        };
    }, [sessionId, enabled, streamUnavailable]); // Only change effect on major keys, token changes are picked via ref

    // Fallback path: poll session state and synthesize lightweight timeline
    // updates from agent status changes. Keep this running even while SSE is
    // connected so a missed terminal event can still be recovered from the
    // persisted backend session.
    useEffect(() => {
        if (!sessionId || !enabled) return undefined;
        if (streamUnavailable) {
            recordMissionObservation({
                kind: "poll_full_session_skip",
                sessionId,
                details: { reason: "stream-unavailable", status: stateRef.current.jobStatus },
            });
            return undefined;
        }

        // If the persisted session is already terminal when this effect mounts
        // (e.g. user opened a failed/completed session), do not start polling at
        // all. Otherwise we hammer the backend forever for a session that will
        // never change again.
        if (TERMINAL_STATUSES.includes(stateRef.current.jobStatus)) {
            return undefined;
        }

        let active = true;
        let timer: number | null = null;

        const stopPolling = () => {
            active = false;
            if (timer !== null) {
                window.clearInterval(timer);
                timer = null;
            }
        };

        const pollOnce = async () => {
            if (!active) return;

            if (TERMINAL_STATUSES.includes(stateRef.current.jobStatus)) {
                recordMissionObservation({
                    kind: "poll_full_session_skip",
                    sessionId,
                    details: { reason: "state-terminal", status: stateRef.current.jobStatus },
                });
                stopPolling();
                return;
            }

            if (isConnected) {
                recordMissionObservation({
                    kind: "poll_full_session_skip",
                    sessionId,
                    details: { reason: "sse-connected", status: stateRef.current.jobStatus, lastSeq: stateRef.current.lastSeq },
                });
                return;
            }

            const fabric = fabricTokenRef.current;
            if (!fabric) return;

            try {
                recordMissionObservation({ kind: "poll_full_session_start", sessionId });
                const job: any = await api.getSession(sessionId, {
                    githubToken: undefined,
                    fabricToken: fabric,
                });
                if (!active) return;

                let seq = Math.max(0, stateRef.current.lastSeq) + 1;
                const nowIso = new Date().toISOString();
                const rawStatus = String(job?.status || "running").toLowerCase();
                const allowed: JobStatusLite[] = [
                    "planned", "approved", "running", "completed", "failed", "cancelled",
                ];
                const jobStatus: JobStatusLite = allowed.includes(rawStatus as JobStatusLite)
                    ? (rawStatus as JobStatusLite)
                    : "running";
                recordMissionObservation({
                    kind: "poll_full_session_ok",
                    sessionId,
                    details: { status: jobStatus, agentCount: Array.isArray(job?.agents) ? job.agents.length : 0 },
                });

                const agents: any[] = Array.isArray(job?.agents) ? job.agents : [];
                const slotProgress = agents
                    .map((a) => {
                        const aid = String(a?.session_id || a?.agent_session_id || a?.agent_id || "");
                        const st = String(a?.status || "").toLowerCase();
                        if (!aid || !st) return null;
                        const mappedStatus =
                            st === "completed" ? "done"
                                : st === "error" ? "failed"
                                    : st;
                        const displayName = String(a?.role || a?.agent_id || aid);
                        return {
                            slotId: aid,
                            agentId: aid,
                            agentName: displayName,
                            role: a?.role,
                            status: mappedStatus,
                        };
                    })
                    .filter(Boolean) as Array<{
                        slotId: string;
                        agentId: string;
                        agentName: string;
                        role?: string;
                        status: "queued" | "running" | "done" | "approval_required" | "failed";
                    }>;

                const activeAgent = slotProgress.find(s => s.status === "running")?.agentId || null;

                const isTerminal = TERMINAL_STATUSES.includes(jobStatus);
                const stateIsTerminal = TERMINAL_STATUSES.includes(stateRef.current.jobStatus);

                if (isTerminal && stateIsTerminal) return;

                dispatch({
                    type: "run_overview",
                    seq: seq++,
                    sessionId,
                    ts: nowIso,
                    job: {
                        id: String(job?.id || sessionId),
                        status: jobStatus,
                        startedAt: job?.started_at ?? null,
                        completedAt: job?.completed_at ?? null,
                    },
                    composition: (job?.composition ?? stateRef.current.composition ?? null) as any,
                    activeAgentId: activeAgent,
                    artifacts: [],
                    slotProgress,
                } as MissionEvent);

                if (isTerminal) {
                    setIsConnected(false);
                    setError(null);
                    // eslint-disable-next-line no-console
                    console.info(`[mc-stream] poll recovered terminal sessionId=${sessionId} status=${jobStatus} connected=${isConnected}`);
                    // Stop polling — the session will never change again. Without
                    // this the interval would hammer the backend every 2s forever
                    // for failed/completed/cancelled sessions.
                    stopPolling();
                    return;
                }

                for (const a of agents) {
                    const aid = String(a?.session_id || a?.agent_session_id || a?.agent_id || "");
                    const st = String(a?.status || "").toLowerCase();
                    if (!aid || !st) continue;
                    if (polledAgentStatusRef.current[aid] === st) continue;
                    polledAgentStatusRef.current[aid] = st;
                    dispatch({
                        type: "agent_status",
                        seq: seq++,
                        sessionId,
                        ts: nowIso,
                        agentId: aid,
                        agentName: String(a?.role || a?.agent_id || aid),
                        status: (st === "error" ? "error" : st) as "queued" | "running" | "waiting" | "completed" | "error",
                        currentStep: a?.current_step || `status: ${st}`,
                        role: a?.role,
                    } as MissionEvent);
                }
            } catch (e) {
                recordMissionObservation({
                    kind: "poll_full_session_error",
                    sessionId,
                    message: e instanceof Error ? e.message : String(e),
                });
                // eslint-disable-next-line no-console
                console.warn(`[mc-stream] poll session failed sessionId=${sessionId}:`, e);
                // Keep retrying while stream is disconnected.
            }
        };

        void pollOnce();
        timer = window.setInterval(() => { void pollOnce(); }, 2000);
        return () => {
            stopPolling();
        };
    }, [sessionId, enabled, isConnected, streamUnavailable]);

    return {
        state,
        reconnectCount,
        isConnected,
        error: error,
    };
}
