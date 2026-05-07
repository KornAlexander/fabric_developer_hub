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
const FAST_ATTACH_RECONNECT_DELAY_MS = 100;
const FAST_ATTACH_ATTEMPTS = 4;
const SSE_WATCHDOG_INTERVAL_MS = 5_000;
const SSE_STALL_RECONNECT_MS = 35_000;
const EVENT_LEDGER_POLL_MS = 1_000;
const EVENT_LEDGER_POLL_TIMEOUT_MS = 4_000;
const TERMINAL_STATUSES: JobStatusLite[] = ["completed", "failed", "cancelled"];
const STARTUP_AGENT_ID = "generalist";

interface InitialMissionJobSnapshot {
    status?: string;
    started_at?: string | null;
    composition?: any | null;
}

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
    opts: {
        enabled?: boolean;
        getSessionOpts?: { githubToken?: string; fabricToken?: string };
        initialJob?: InitialMissionJobSnapshot | null;
    } = {},
): UseMissionStream {
    const { enabled = true, getSessionOpts, initialJob } = opts;
    
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
    const [snapshotFallbackActive, setSnapshotFallbackActive] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const aborterRef = useRef<AbortController | null>(null);
    const sseWatchdogTimerRef = useRef<number | null>(null);
    const lastSseFrameAtRef = useRef<number>(0);
    const optimisticStartupSessionRef = useRef<string | null>(null);

    useEffect(() => {
        if (!sessionId || !enabled || !initialJob) return;
        if (optimisticStartupSessionRef.current === sessionId) return;
        const rawStatus = String(initialJob.status || "running").toLowerCase();
        if (TERMINAL_STATUSES.includes(rawStatus as JobStatusLite)) return;

        optimisticStartupSessionRef.current = sessionId;
        const nowIso = new Date().toISOString();
        const startedAt = initialJob.started_at || nowIso;
        dispatch({
            type: "run_overview",
            seq: 0.1,
            sessionId,
            ts: nowIso,
            job: { id: sessionId, status: "running", startedAt, completedAt: null },
            composition: initialJob.composition ?? null,
            activeAgentId: STARTUP_AGENT_ID,
            artifacts: [],
            changes: [],
            slotProgress: [{
                slotId: STARTUP_AGENT_ID,
                agentId: STARTUP_AGENT_ID,
                agentName: "Generalist",
                role: "Mission controller",
                status: "running",
                currentStep: "Starting mission controller and attaching live events",
            }],
        } as MissionEvent);
        dispatch({
            type: "log_line",
            seq: 0.2,
            sessionId,
            ts: nowIso,
            logCategory: "high_level",
            agentId: STARTUP_AGENT_ID,
            agentName: "Generalist",
            level: "info",
            message: "Mission accepted. Starting the mission controller and attaching live events.",
            tags: ["startup"],
        } as MissionEvent);
    }, [enabled, initialJob, sessionId]);

    useEffect(() => {
        setStreamUnavailable(false);
        setSnapshotFallbackActive(false);
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

        const stopSseWatchdog = () => {
            if (sseWatchdogTimerRef.current !== null) {
                window.clearInterval(sseWatchdogTimerRef.current);
                sseWatchdogTimerRef.current = null;
            }
        };

        const startSseWatchdog = () => {
            stopSseWatchdog();
            lastSseFrameAtRef.current = Date.now();
            sseWatchdogTimerRef.current = window.setInterval(() => {
                if (!active || TERMINAL_STATUSES.includes(stateRef.current.jobStatus)) return;
                const idleMs = Date.now() - lastSseFrameAtRef.current;
                if (idleMs < SSE_STALL_RECONNECT_MS) return;
                recordMissionObservation({
                    kind: "sse_watchdog_reconnect",
                    sessionId,
                    message: "The SSE reader stopped receiving frames; reconnecting and relying on persisted-event catch-up.",
                    details: { idleMs, lastSeq: stateRef.current.lastSeq },
                });
                setIsConnected(false);
                setError("Live stream stalled; reconnecting and catching up from saved events.");
                aborterRef.current?.abort();
                stopSseWatchdog();
            }, SSE_WATCHDOG_INTERVAL_MS);
        };

        const scheduleReconnect = (delayMs?: number) => {
            if (streamUnavailable) return;
            const resolvedDelay = delayMs ?? (attempts <= FAST_ATTACH_ATTEMPTS ? FAST_ATTACH_RECONNECT_DELAY_MS : RECONNECT_DELAY_MS);
            recordMissionObservation({
                kind: "reconnect_scheduled",
                sessionId,
                details: { delayMs: resolvedDelay, nextAttempt: attempts + 1 },
            });
            reconnectTimerRef.current = setTimeout(() => {
                setReconnectCount((c) => c + 1);
                connect();
            }, resolvedDelay);
        };

        const normalizeJobStatus = (job: any): JobStatusLite => {
            const rawStatus = String(job?.status || "running").toLowerCase();
            const allowed: JobStatusLite[] = [
                "planned", "approved", "running", "completed", "failed", "cancelled",
            ];
            return allowed.includes(rawStatus as JobStatusLite)
                ? (rawStatus as JobStatusLite)
                : "running";
        };

        const slotProgressFromJob = (job: any) => {
            const agents: any[] = Array.isArray(job?.agents) ? job.agents : [];
            return agents
                .map((a) => {
                    const aid = String(a?.session_id || a?.agent_session_id || a?.agent_id || "");
                    const st = String(a?.status || "").toLowerCase();
                    if (!aid || !st) return null;
                    const mappedStatus = st === "completed" ? "done"
                        : st === "error" ? "failed"
                            : st;
                    return {
                        slotId: aid,
                        agentId: aid,
                        agentName: String(a?.agentName || a?.agent_name || a?.agent_id || a?.role || aid),
                        role: a?.role,
                        status: mappedStatus,
                        currentStep: a?.current_step || a?.currentStep || undefined,
                    };
                })
                .filter(Boolean) as Array<{
                    slotId: string;
                    agentId: string;
                    agentName: string;
                    role?: string;
                    status: "queued" | "running" | "waiting" | "done" | "approval_required" | "failed";
                    currentStep?: string;
                }>;
        };

        const dispatchJobSnapshot = (job: any, jobStatus: JobStatusLite) => {
            const slotProgress = slotProgressFromJob(job);
            dispatch({
                type: "run_overview",
                sessionId,
                ts: new Date().toISOString(),
                job: {
                    id: String(job?.id || sessionId),
                    status: jobStatus,
                    startedAt: job?.started_at ?? null,
                    completedAt: job?.completed_at ?? null,
                },
                composition: (job?.composition ?? stateRef.current.composition ?? null) as any,
                activeAgentId: slotProgress.find(s => s.status === "running")?.agentId
                    || slotProgress.find(s => s.status === "waiting" || s.status === "approval_required")?.agentId
                    || null,
                artifacts: [],
                changes: [],
                slotProgress,
            } as MissionEvent);
        };

        const classifyEmptyReplayClose = async (reason: string): Promise<boolean> => {
            const fabric = fabricTokenRef.current;
            if (!fabric) return false;
            recordMissionObservation({ kind: "sse_empty_replay_close", sessionId, details: { reason } });

            try {
                const [job, eventBody]: [any, any] = await Promise.all([
                    api.getSession(sessionId, {
                        githubToken: undefined,
                        fabricToken: fabric,
                    }),
                    api.getSessionEvents(sessionId, {
                        githubToken: undefined,
                        fabricToken: fabric,
                        agentHubSessionId: sessionId,
                        limit: 1,
                    }),
                ]);
                if (!active) return true;

                const jobStatus = normalizeJobStatus(job);
                const slotProgress = slotProgressFromJob(job);
                const hasActiveSnapshot = slotProgress.some((slot) => ["queued", "running", "waiting", "approval_required"].includes(slot.status));
                dispatchJobSnapshot(job, jobStatus);

                const eventCount = typeof eventBody?.persistedTotal === "number"
                    ? eventBody.persistedTotal
                    : Array.isArray(eventBody?.events) ? eventBody.events.length : Number(eventBody?.count || 0);
                const hasLiveExecution = eventBody?.liveExecution === true || eventBody?.source === "live";
                if (!hasLiveExecution && eventCount === 0 && !TERMINAL_STATUSES.includes(jobStatus)) {
                    if (hasActiveSnapshot) {
                        const waitingMessage = "Live event stream is attaching; showing current agent progress while retrying.";
                        recordMissionObservation({
                            kind: "snapshot_fallback_active",
                            sessionId,
                            message: waitingMessage,
                            details: { reason, status: jobStatus, activeAgentCount: slotProgress.length, source: eventBody?.source || "unknown" },
                        });
                        setStreamUnavailable(false);
                        setSnapshotFallbackActive(true);
                        setIsConnected(false);
                        setError(waitingMessage);
                        return false;
                    }
                    if (jobStatus === "planned" || jobStatus === "approved" || jobStatus === "running") {
                        const waitingMessage = jobStatus === "running"
                            ? "Waiting for live mission events to attach; retrying stream."
                            : "Mission launch is attaching; waiting for the first agent event.";
                        recordMissionObservation({
                            kind: "sse_waiting_for_run_attach",
                            sessionId,
                            message: waitingMessage,
                            details: { reason, status: jobStatus, source: eventBody?.source || "unknown", eventCount },
                        });
                        setStreamUnavailable(false);
                        setSnapshotFallbackActive(false);
                        setIsConnected(false);
                        setError(waitingMessage);
                        return false;
                    }
                    recordMissionObservation({
                        kind: "sse_stream_unavailable",
                        sessionId,
                        message: "The backend reported no live execution and no persisted mission events; stopping live reconnects.",
                        details: { reason, status: jobStatus, source: eventBody?.source || "unknown", eventCount },
                    });
                    setStreamUnavailable(true);
                    setSnapshotFallbackActive(false);
                    setIsConnected(false);
                    setError("No live mission process is running for this session yet; the event log is empty.");
                    return true;
                }

                if (TERMINAL_STATUSES.includes(jobStatus)) {
                    setIsConnected(false);
                    setError(null);
                    return true;
                }
                return false;
            } catch (e) {
                recordMissionObservation({
                    kind: "terminal_recovery_error",
                    sessionId,
                    message: e instanceof Error ? e.message : String(e),
                    details: { reason },
                });
                return false;
            }
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
                            agentName: String(a?.agentName || a?.agent_name || a?.agent_id || a?.role || aid),
                            role: a?.role,
                            status: st === "completed" ? "done" : st === "error" ? "failed" : st,
                            currentStep: a?.current_step || a?.currentStep || undefined,
                        };
                    })
                    .filter(Boolean) as Array<{
                        slotId: string;
                        agentId: string;
                        agentName: string;
                        role?: string;
                        status: "queued" | "running" | "waiting" | "done" | "approval_required" | "failed";
                        currentStep?: string;
                    }>;

                dispatch({
                    type: "run_overview",
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
                    agentHubSessionId: sessionId,
                    lastEventId: stateRef.current.lastSeq >= 1 ? String(stateRef.current.lastSeq) : undefined
                },
                {
                    onOpen: () => {
                        if (!active) return;
                        setStreamUnavailable(false);
                        startSseWatchdog();
                        recordMissionObservation({ kind: "sse_open", sessionId, details: { attempt: attempts } });
                        // eslint-disable-next-line no-console
                        console.info(`[mc-stream] SSE OPEN sessionId=${sessionId}`);
                        setIsConnected(true);
                        setError(null);
                        setReconnectCount(0); // Reset reconnect counter on success
                    },
                    onEvent: (data) => {
                        if (!active) return;
                        lastSseFrameAtRef.current = Date.now();
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
                        stopSseWatchdog();
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
                    onClose: (info) => {
                        if (!active) return;
                        stopSseWatchdog();
                        recordMissionObservation({
                            kind: "sse_close",
                            sessionId,
                            details: { attempt: attempts, eventCount: info.eventCount, jobStatus: stateRef.current.jobStatus, lastSeq: stateRef.current.lastSeq },
                        });
                        setIsConnected(false);

                        if (info.eventCount === 0) {
                            void classifyEmptyReplayClose("empty-stream-close").then((classified) => {
                                if (!active || classified) return;
                                scheduleReconnect(FAST_ATTACH_RECONNECT_DELAY_MS);
                            });
                            return;
                        }
                        
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
            stopSseWatchdog();
            if (reconnectTimerRef.current) {
                clearTimeout(reconnectTimerRef.current);
            }
        };
    }, [sessionId, enabled, streamUnavailable]); // Only change effect on major keys, token changes are picked via ref

    // Safety net for Fabric iframe / proxy buffering: the backend persists every
    // public mission event as it is emitted. Poll that compact event ledger so
    // the UI catches up even if the fetch-SSE reader stalls or reconnects late.
    // Duplicates are harmless because missionReducer dedupes by backend seq.
    useEffect(() => {
        if (!sessionId || !enabled || (streamUnavailable && !snapshotFallbackActive)) return undefined;
        let active = true;
        let timer: number | null = null;
        let inFlight = false;

        const pollEvents = async () => {
            if (!active || TERMINAL_STATUSES.includes(stateRef.current.jobStatus)) return;
            if (inFlight) return;
            const fabric = fabricTokenRef.current;
            if (!fabric) return;

            inFlight = true;
            const timeoutController = new AbortController();
            const timeout = window.setTimeout(() => timeoutController.abort(), EVENT_LEDGER_POLL_TIMEOUT_MS);

            try {
                const body = await api.getSessionEvents(sessionId, {
                    githubToken: undefined,
                    fabricToken: fabric,
                    agentHubSessionId: sessionId,
                    limit: 1000,
                    afterSeq: stateRef.current.lastSeq,
                    signal: timeoutController.signal,
                });
                if (!active || !Array.isArray(body.events)) return;

                const lastSeq = stateRef.current.lastSeq;
                const missed = body.events
                    .filter((event: any) => typeof event?.seq === "number" && event.seq > lastSeq && event.logCategory !== "trace")
                    .sort((a: any, b: any) => a.seq - b.seq) as MissionEvent[];
                if (missed.length === 0) return;

                recordMissionObservation({
                    kind: "poll_events_catchup",
                    sessionId,
                    details: { source: body.source, count: missed.length, fromSeq: lastSeq, toSeq: (missed[missed.length - 1] as any).seq },
                });
                for (const event of missed) {
                    recordMissionEventObservation("poll_event", event);
                    if (window.localStorage.getItem("agenthub_debug_stream") === "1") {
                        // eslint-disable-next-line no-console
                        console.debug("[mc-stream] event", (event as any)?.type, (event as any)?.seq, "poll");
                    }
                    dispatch(event);
                }
            } catch (e) {
                const isAbort = (e as DOMException)?.name === "AbortError";
                recordMissionObservation({
                    kind: isAbort ? "poll_events_timeout" : "poll_events_error",
                    sessionId,
                    message: e instanceof Error ? e.message : String(e),
                });
            } finally {
                window.clearTimeout(timeout);
                inFlight = false;
            }
        };

        void pollEvents();
        timer = window.setInterval(() => { void pollEvents(); }, EVENT_LEDGER_POLL_MS);
        return () => {
            active = false;
            if (timer !== null) window.clearInterval(timer);
        };
    }, [sessionId, enabled, streamUnavailable, snapshotFallbackActive]);

    // Fallback path: poll session state and synthesize lightweight timeline
    // updates from agent status changes. Keep this running even while SSE is
    // connected so a missed terminal event can still be recovered from the
    // persisted backend session.
    useEffect(() => {
        if (!sessionId || !enabled) return undefined;
        if (streamUnavailable && !snapshotFallbackActive) {
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

            if (isConnected && stateRef.current.logs.length > 0) {
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
                        const displayName = String(a?.agentName || a?.agent_name || a?.agent_id || a?.role || aid);
                        return {
                            slotId: aid,
                            agentId: aid,
                            agentName: displayName,
                            role: a?.role,
                            status: mappedStatus,
                            currentStep: a?.current_step || a?.currentStep || undefined,
                        };
                    })
                    .filter(Boolean) as Array<{
                        slotId: string;
                        agentId: string;
                        agentName: string;
                        role?: string;
                        status: "queued" | "running" | "waiting" | "done" | "approval_required" | "failed";
                        currentStep?: string;
                    }>;

                const activeAgent = slotProgress.find(s => s.status === "running")?.agentId
                    || slotProgress.find(s => s.status === "waiting" || s.status === "approval_required")?.agentId
                    || null;

                const isTerminal = TERMINAL_STATUSES.includes(jobStatus);
                const stateIsTerminal = TERMINAL_STATUSES.includes(stateRef.current.jobStatus);

                if (isTerminal && stateIsTerminal) return;

                dispatch({
                    type: "run_overview",
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
    }, [sessionId, enabled, isConnected, streamUnavailable, snapshotFallbackActive]);

    return {
        state,
        reconnectCount,
        isConnected,
        error: error,
    };
}
