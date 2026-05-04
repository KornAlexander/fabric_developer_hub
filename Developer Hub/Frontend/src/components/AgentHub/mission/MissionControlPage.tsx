/**
 * Mission Control — Step 3 of the new-session flow.
 *
 * A single evolving surface that replaces the old ``SessionDetailPage``
 * for live runs. The surface is fed by ``useMissionStream`` (reducer
 * over the SSE event vocabulary) and reuses the existing design-system
 * primitives: ``TaskPromptRecap``, ``TeamPanel`` (which in turn drives
 * ``OrchCanvas`` / ``TeamStrip``), and ``ApprovalCard``.
 *
 * The page structure mirrors prototype screens 3–6 of
 * ``Design/agent-ux/prototypes/03-mission-control/index.html``:
 *
 *   ┌─ Header: status pill · elapsed timer · Pause · Terminate ─┐
 *   │  ╭───────── Task prompt recap (sticky) ─────────╮         │
 *   │  ╰──────────────────────────────────────────────╯         │
 *   │  ╭──────── Team panel (strip / expanded graph) ───────╮   │
 *   │  ╰────────────────────────────────────────────────────╯   │
 *   │  ┌─── Live log (left) ───┬── Run overview + artifacts ──┐ │
 *   │  │                       │                              │ │
 *   │  └───────────────────────┴──────────────────────────────┘ │
 *   └──────────────────────────────────────────────────────────┘
 *
 * The header's Pause CTA is a disabled stub (no backend support) so the
 * UI can surface it without inventing behaviour. Terminate wires to
 * ``cancelSession`` and triggers optimistic UI → CANCELLED.
 */

import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useHistory } from "react-router-dom";
import {
    Button,
    Badge,
    Card,
    Caption1,
    Subtitle2,
    Spinner,
    Tooltip,
    TabList,
    Tab,
} from "@fluentui/react-components";
import {
    Stop24Regular,
    Pause24Regular,
    ArrowClockwise16Regular,
    DocumentBulletList20Regular,
    CheckmarkCircle20Filled,
    ErrorCircle20Filled,
    Warning20Filled,
    Circle20Regular,
} from "@fluentui/react-icons";
import type { WorkloadClientAPI } from "@ms-fabric/workload-client";

import * as api from "../../../controller/AgentHubApi";
import { getFabricTokenCached } from "../../../controller/AgentHubController";
import { TaskPromptRecap } from "../TaskPromptRecap";
import { TeamPanel } from "../team/TeamPanel";
import { ApprovalCard } from "../approvals/ApprovalCard";
import { useMissionStream } from "./useMissionStream";
import { teamFromComposition } from "./types";
import type { LogEntry, PendingApproval, MissionState } from "./missionReducer";
import type { JobStatusLite } from "./events";
import type { PlanStep, RecoveryAction } from "../plan/types";

export interface MissionControlPageProps {
    workloadClient: WorkloadClientAPI;
    sessionId: string;
    // Initial job snapshot from createSession / approvePlan. Used to
    // seed the recap + header before the first SSE event arrives.
    initialJob?: {
        task_description?: string;
        workspace_id?: string;
        workspace_name?: string | null;
        started_at?: string | null;
        status?: string;
        context?: Record<string, any> | null;
    } | null;
}

const STATUS_COLOR: Record<JobStatusLite, "brand" | "danger" | "warning" | "success" | "subtle"> = {
    planned: "subtle",
    approved: "brand",
    running: "brand",
    completed: "success",
    failed: "danger",
    cancelled: "warning",
};

const STATUS_LABEL: Record<JobStatusLite, string> = {
    planned: "PLANNED",
    approved: "STARTING",
    running: "RUNNING",
    completed: "COMPLETE",
    failed: "FAILED",
    cancelled: "CANCELLED",
};

function formatElapsed(sec: number): string {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
}

export function MissionControlPage({
    workloadClient,
    sessionId,
    initialJob,
}: MissionControlPageProps) {
    // Tokens for API calls.
    const githubToken = sessionStorage.getItem("github_token") || "";
    const [fabricToken, setFabricToken] = useState<string | undefined>(undefined);
    // Keep the URL in sync so the surface is deep-linkable even though
    // the user never navigates — ``history.replace`` avoids a history
    // entry per session to prevent back-button churn.
    const history = useHistory();
    useEffect(() => {
        if (!sessionId) return;
        const target = `/agent-hub/session/${sessionId}`;
        if (history.location.pathname !== target) {
            history.replace(target);
        }
    }, [sessionId, history]);
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const tk = await getFabricTokenCached(workloadClient);
                if (!cancelled) setFabricToken(tk);
            } catch { /* best-effort */ }
        })();
        return () => { cancelled = true; };
    }, [workloadClient]);

    // Subscribe to the mission-control event stream.
    const { state, isConnected, reconnectCount, error } = useMissionStream(sessionId, {
        getSessionOpts: { githubToken, fabricToken },
    });

    // Deep-link case: when ``initialJob`` isn't supplied (user hit the
    // /agent-hub/session/:id permalink directly), fetch the session
    // once so the recap + elapsed timer have a task description and
    // a ``started_at`` anchor. The SSE stream will still be the
    // source of truth for everything else.
    const [fetchedJob, setFetchedJob] = useState<MissionControlPageProps["initialJob"]>(null);
    useEffect(() => {
        if (initialJob || !sessionId || !fabricToken) return undefined;
        let cancelled = false;
        (async () => {
            try {
                const j = await api.getSession(sessionId, { githubToken, fabricToken });
                if (cancelled) return;
                setFetchedJob({
                    task_description: j.task_description,
                    workspace_id: j.workspace_id,
                    workspace_name: j.context?.workspace_name ?? null,
                    started_at: j.started_at ?? null,
                    status: j.status,
                    context: j.context ?? null,
                });
            } catch { /* best-effort; reducer will still render */ }
        })();
        return () => { cancelled = true; };
    }, [initialJob, sessionId, fabricToken, githubToken]);
    const job = initialJob ?? fetchedJob;

    // Local-clock elapsed timer — driven by a tick, not the network, so
    // the UI ticks smoothly regardless of event density. The clock
    // freezes when the run reaches a terminal state.
    const startedAtMs = useMemo(() => {
        const iso = job?.started_at ?? null;
        return iso ? new Date(iso).getTime() : Date.now();
    }, [job?.started_at]);
    const [elapsedSec, setElapsedSec] = useState(0);
    useEffect(() => {
        if (state.terminalType) return undefined;
        const tick = () => setElapsedSec(Math.max(0, Math.floor((Date.now() - startedAtMs) / 1000)));
        tick();
        const id = window.setInterval(tick, 1000);
        return () => window.clearInterval(id);
    }, [startedAtMs, state.terminalType]);

    // Terminate CTA with optimistic UI.
    const [terminating, setTerminating] = useState(false);
    const [optimisticCancelled, setOptimisticCancelled] = useState(false);
    const handleTerminate = useCallback(async () => {
        if (terminating) return;
        setTerminating(true);
        setOptimisticCancelled(true);
        try {
            await api.cancelSession(sessionId, { githubToken, fabricToken });
        } catch (e) {
            // eslint-disable-next-line no-console
            console.warn("[mission-control] cancel failed", e);
        } finally {
            setTerminating(false);
        }
    }, [sessionId, githubToken, fabricToken, terminating]);

    // Approval handling.
    const [approvalBusy, setApprovalBusy] = useState<string | null>(null);
    const handleApprovalAction = useCallback(async (approval: PendingApproval, action: RecoveryAction) => {
        setApprovalBusy(approval.approvalId);
        try {
            await api.resolveApproval(sessionId, approval.approvalId, action, null,
                { githubToken, fabricToken });
        } catch (e) {
            // eslint-disable-next-line no-console
            console.error("[mission-control] approval failed", e);
        } finally {
            setApprovalBusy(null);
        }
    }, [sessionId, githubToken, fabricToken]);

    // Effective status: show CANCELLED immediately when the user hits
    // Terminate, even before the backend confirms via job_cancelled.
    const effectiveStatus: JobStatusLite = optimisticCancelled && !state.terminalType
        ? "cancelled"
        : state.jobStatus;

    const team = useMemo(() => teamFromComposition(state.composition as any), [state.composition]);

    return (
        <div className="mission-control">
            <MissionHeader
                status={effectiveStatus}
                elapsed={elapsedSec}
                isConnected={isConnected}
                reconnectCount={reconnectCount}
                onTerminate={handleTerminate}
                terminating={terminating}
                terminated={!!state.terminalType || optimisticCancelled}
                totalDuration={state.totalDuration}
                error={error}
            />

            <TaskPromptRecap
                task={job?.task_description || ""}
                workspaceName={(job?.workspace_name as string) || null}
                workspaceId={(job?.workspace_id as string) || null}
                workspaceItems={(job?.context?.context_items as any) || null}
                attachments={(job?.context?.prompt_attachments as any) || null}
                defaultOpen={false}
            />

            {team && (
                <TeamPanel
                    team={team}
                    activeAgentId={state.activeAgentId}
                />
            )}

            <div className="mission-control__body">
                <LogColumn
                    state={state}
                    onApprovalAction={handleApprovalAction}
                    approvalBusy={approvalBusy}
                />
                <RightRail state={state} />
            </div>
        </div>
    );
}

// ── Header ───────────────────────────────────────────────────────────

interface MissionHeaderProps {
    status: JobStatusLite;
    elapsed: number;
    isConnected: boolean;
    reconnectCount: number;
    onTerminate: () => void;
    terminating: boolean;
    terminated: boolean;
    totalDuration?: string;
    error: string | null;
}

function MissionHeader({
    status, elapsed, isConnected, reconnectCount, onTerminate, terminating, terminated, totalDuration, error,
}: MissionHeaderProps) {
    const { t } = useTranslation();
    const terminal = status === "completed" || status === "failed" || status === "cancelled";
    return (
        <div className="mission-control__header">
            <Badge
                appearance="filled"
                color={STATUS_COLOR[status]}
                className={`mission-control__status mission-control__status--${status}`}
            >
                {t(`MissionControl_Status_${status}`, STATUS_LABEL[status])}
            </Badge>
            <Caption1 className="mission-control__elapsed">
                {terminal && totalDuration ? totalDuration : formatElapsed(elapsed)}
            </Caption1>
            {!isConnected && !terminal && (
                <Caption1 className="mission-control__reconnecting">
                    {reconnectCount > 0
                        ? t("MissionControl_Reconnecting", { count: reconnectCount })
                        : t("MissionControl_Connecting")}
                </Caption1>
            )}
            {error && <Caption1 className="mission-control__error">{error}</Caption1>}
            <div className="mission-control__spacer" />
            <Tooltip
                content={t("MissionControl_Pause_Tooltip")}
                relationship="label"
            >
                {/* Disabled placeholder per spec §1. */}
                <Button
                    icon={<Pause24Regular />}
                    appearance="subtle"
                    disabled
                    aria-label={t("MissionControl_Pause")}
                >
                    {t("MissionControl_Pause")}
                </Button>
            </Tooltip>
            <Button
                icon={<Stop24Regular />}
                appearance="secondary"
                onClick={onTerminate}
                disabled={terminating || terminated}
            >
                {terminating
                    ? t("MissionControl_Terminating")
                    : terminated ? t("MissionControl_Terminated") : t("MissionControl_Terminate")}
            </Button>
        </div>
    );
}

// ── Left column: live log (with inline approval card) ────────────────

interface LogColumnProps {
    state: MissionState;
    onApprovalAction: (approval: PendingApproval, action: RecoveryAction) => void;
    approvalBusy: string | null;
}

function LogColumn({ state, onApprovalAction, approvalBusy }: LogColumnProps) {
    const { t } = useTranslation();
    const scrollRef = useRef<HTMLDivElement>(null);
    const pinnedRef = useRef(true);
    const [activeFilter, setActiveFilter] = useState<string>("all");

    // Track whether the user is pinned to the bottom so incoming events
    // only auto-scroll when the user hasn't scrolled away manually.
    const onScroll = useCallback(() => {
        const el = scrollRef.current;
        if (!el) return;
        pinnedRef.current = (el.scrollHeight - el.scrollTop - el.clientHeight) < 32;
    }, []);

    useEffect(() => {
        if (!pinnedRef.current) return;
        const el = scrollRef.current;
        if (el) el.scrollTop = el.scrollHeight;
    }, [state.logs.length]);

    const terminal = !!state.terminalType;
    const agents = useMemo(() => {
        const ids = new Set<string>();
        for (const l of state.logs) {
            if (l.agentId) ids.add(l.agentId);
        }
        return Array.from(ids);
    }, [state.logs]);

    const visibleLogs = useMemo(() => {
        if (activeFilter === "all") return state.logs;
        return state.logs.filter((l) => l.agentId === activeFilter);
    }, [state.logs, activeFilter]);

    // Inline approval cards are interleaved between log lines right
    // where they were raised so the user sees them in context.
    const pendingApprovals = Object.values(state.approvals).filter((a) => !a.resolved);

    return (
        <section className="mission-control__log-column">
            <div className="mission-control__log-header">
                <Subtitle2>{t("MissionControl_LiveLog")}</Subtitle2>
                {state.activeAgentId && (
                    <Badge appearance="outline" color="brand">
                        {agentNameFor(state, state.activeAgentId)}
                    </Badge>
                )}
            </div>
            {terminal && agents.length >= 2 && (
                <TabList
                    selectedValue={activeFilter}
                    onTabSelect={(_, d) => setActiveFilter(String(d.value))}
                    size="small"
                >
                    <Tab value="all">{t("RunLog_Filter_All")}</Tab>
                    {agents.map((a) => (
                        <Tab key={a} value={a}>
                            {agentNameFor(state, a)}
                        </Tab>
                    ))}
                </TabList>
            )}
            <div
                className="mission-control__log"
                ref={scrollRef}
                onScroll={onScroll}
                role="log"
                aria-live="polite"
            >
                {visibleLogs.map((l) => (
                    <LogRow key={l.seq} entry={l} />
                ))}
                {pendingApprovals.map((ap) => (
                    <ApprovalInlineCard
                        key={ap.approvalId}
                        approval={ap}
                        busy={approvalBusy === ap.approvalId}
                        onAction={(a) => onApprovalAction(ap, a)}
                    />
                ))}
                {!terminal && visibleLogs.length === 0 && (
                    <Caption1 className="mission-control__log-placeholder">
                        {t("MissionControl_LogPlaceholder")}
                    </Caption1>
                )}
            </div>
        </section>
    );
}

function agentNameFor(state: MissionState, agentId: string): string {
    const s = state.slotProgress[agentId];
    if (s?.agentName) return s.agentName;
    const comp: any = state.composition;
    if (comp?.slots) {
        const slot = comp.slots.find((x: any) => x.id === agentId || x.agentId === agentId);
        if (slot) return slot.role || slot.agentId || agentId;
    }
    return agentId.slice(0, 8);
}

function LogRow({ entry }: { entry: LogEntry }) {
    return (
        <div className={`mission-control__log-row mission-control__log-row--${entry.kind} mission-control__log-row--${entry.level}`}>
            <span className="mission-control__log-ts">{new Date(entry.ts).toLocaleTimeString()}</span>
            {entry.agentName && <Badge appearance="outline" size="small">{entry.agentName}</Badge>}
            <span className="mission-control__log-message">{entry.message}</span>
            {entry.durationMs !== undefined && (
                <Caption1 className="mission-control__log-duration">{entry.durationMs}ms</Caption1>
            )}
        </div>
    );
}

// ── Inline approval card ─────────────────────────────────────────────

interface ApprovalInlineCardProps {
    approval: PendingApproval;
    busy: boolean;
    onAction: (action: RecoveryAction) => void;
}

function ApprovalInlineCard({ approval, busy, onAction }: ApprovalInlineCardProps) {
    // Feed a minimal PlanStep-shaped object to the existing ApprovalCard
    // so we can reuse its visuals wholesale.
    const stubStep: PlanStep = {
        id: approval.approvalId,
        order: 0,
        title: approval.summary,
        action: "configure",
        target: { itemType: "", displayName: "", workspaceId: "" },
        inputs: [],
        dependsOn: [],
        rationale: approval.summary,
        risk: "medium",
        reversible: approval.reversible ?? true,
        blastRadius: (approval.blastRadius as any) || undefined,
        toolCallPreview: approval.toolCallPreview || undefined,
        recoveryActions: (approval.recoveryActions as any) || undefined,
    };
    return (
        <ApprovalCard
            step={stubStep}
            summary={approval.summary}
            blastRadius={(approval.blastRadius as any) || undefined}
            reversible={approval.reversible ?? undefined}
            toolCallPreview={approval.toolCallPreview || undefined}
            recoveryActions={(approval.recoveryActions as any) || undefined}
            busy={busy}
            onAction={onAction}
        />
    );
}

// ── Right rail: run overview + artifacts + completion summary ────────

function RightRail({ state }: { state: MissionState }) {
    const { t } = useTranslation();
    const slots = useMemo(() => {
        const comp: any = state.composition;
        const list = (comp?.slots || []) as Array<{ id: string; agentId: string; role: string }>;
        return list.map((s) => {
            const progress = state.slotProgress[s.id] || state.slotProgress[s.agentId];
            const status = progress?.status || "queued";
            const isActive = state.activeAgentId === s.id || state.activeAgentId === s.agentId;
            return { id: s.id, role: s.role, agent: s.agentId, status, isActive };
        });
    }, [state.composition, state.slotProgress, state.activeAgentId]);

    const doneCount = slots.filter((s) => s.status === "done").length;
    const total = slots.length;
    const artifacts = state.artifactOrder.map((id) => state.artifacts[id]).filter(Boolean);
    const terminal = !!state.terminalType;

    return (
        <aside className="mission-control__right-rail">
            <Card className="mission-control__rail-card">
                <div className="mission-control__rail-header">
                    <DocumentBulletList20Regular />
                    <Subtitle2>{t("MissionControl_RunOverview")}</Subtitle2>
                    {total > 0 && (
                        <Caption1 className="mission-control__rail-meta">
                            {t("MissionControl_StepOf", {
                                current: Math.min(doneCount + (slots.some((s) => s.status === "running") ? 1 : 0), total),
                                total,
                            })}
                        </Caption1>
                    )}
                </div>
                <ol className="mission-control__overview-list">
                    {slots.map((s) => (
                        <li
                            key={s.id}
                            className={`mission-control__overview-item mission-control__overview-item--${s.status} ${s.isActive ? "is-active" : ""}`}
                        >
                            <SlotStatusIcon status={s.status} />
                            <span className="mission-control__overview-role">{s.role}</span>
                            {s.status === "approval_required" && (
                                <Badge appearance="outline" color="warning" size="small">
                                    {t("MissionControl_AwaitingYou")}
                                </Badge>
                            )}
                        </li>
                    ))}
                    {slots.length === 0 && (
                        <li className="mission-control__overview-placeholder">
                            <Caption1>{t("MissionControl_OverviewPlaceholder")}</Caption1>
                        </li>
                    )}
                </ol>
            </Card>

            <Card className="mission-control__rail-card">
                <div className="mission-control__rail-header">
                    <Subtitle2>{t("MissionControl_Artifacts")}</Subtitle2>
                    {artifacts.length > 0 && (
                        <Caption1 className="mission-control__rail-meta">{artifacts.length}</Caption1>
                    )}
                </div>
                <ul className="mission-control__artifact-list">
                    {artifacts.map((a) => (
                        <li key={a.artifactId} className="mission-control__artifact-row">
                            <Badge
                                appearance={a.state === "written" ? "filled" : "outline"}
                                color={a.state === "written" ? "success" : "subtle"}
                                size="small"
                            >
                                {a.state}
                            </Badge>
                            <span className="mission-control__artifact-name">{a.name}</span>
                            <Caption1 className="mission-control__artifact-kind">{a.kind}</Caption1>
                            {a.webUrl && (
                                <a className="mission-control__artifact-link" href={a.webUrl} target="_blank" rel="noreferrer">{t("MissionControl_Artifact_Open")}</a>
                            )}
                        </li>
                    ))}
                    {artifacts.length === 0 && (
                        <li>
                            <Caption1 className="mission-control__artifact-placeholder">{t("MissionControl_ArtifactsPlaceholder")}</Caption1>
                        </li>
                    )}
                </ul>
            </Card>

            {terminal && <CompletionPanel state={state} />}
        </aside>
    );
}

function SlotStatusIcon({ status }: { status: string }) {
    switch (status) {
        case "done": return <CheckmarkCircle20Filled style={{ color: "#107c10" }} />;
        case "running": return <Spinner size="tiny" />;
        case "approval_required": return <Warning20Filled style={{ color: "#b38a00" }} />;
        case "failed": return <ErrorCircle20Filled style={{ color: "#d13438" }} />;
        default: return <Circle20Regular style={{ color: "#8a8886" }} />;
    }
}

// ── Completion summary ───────────────────────────────────────────────

function CompletionPanel({ state }: { state: MissionState }) {
    const { t } = useTranslation();
    // Aggregate "who did what" from accumulated log entries. Pure
    // client-side — no extra fetch.
    const perAgent = useMemo(() => {
        const map = new Map<string, { actions: number; phases: number; tools: number; lastMessage?: string; name?: string }>();
        for (const l of state.logs) {
            if (!l.agentId) continue;
            const entry = map.get(l.agentId) || { actions: 0, phases: 0, tools: 0 };
            if (l.kind === "action") entry.actions += 1;
            if (l.kind === "phase") entry.phases += 1;
            if (l.kind === "tool_end") entry.tools += 1;
            if (l.agentName) entry.name = l.agentName;
            entry.lastMessage = l.message;
            map.set(l.agentId, entry);
        }
        return Array.from(map.entries()).map(([id, v]) => ({ id, ...v }));
    }, [state.logs]);

    const handleExport = () => {
        const blob = new Blob([JSON.stringify({
            jobStatus: state.jobStatus,
            totalDuration: state.totalDuration,
            artifacts: Object.values(state.artifacts),
            logs: state.logs,
        }, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `mission-${new Date().toISOString()}.json`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const handleSaveTemplate = () => {
        // Stub per spec §1 — just audit locally for now; server-side
        // template-save is deferred work.
        // eslint-disable-next-line no-console
        console.info("[mission-control] save-as-template (stub)", { jobStatus: state.jobStatus });
    };

    const handleStartAnother = () => {
        window.location.assign("/agent-hub/orchestrator");
    };

    return (
        <Card className="mission-control__rail-card mission-control__completion">
            <Subtitle2>{t("MissionControl_Contributions")}</Subtitle2>
            <ul className="mission-control__contribution-list">
                {perAgent.map((a) => (
                    <li key={a.id} className="mission-control__contribution-row">
                        <Badge appearance="outline" size="small">{a.name || a.id.slice(0, 8)}</Badge>
                        <Caption1>
                            {t("MissionControl_Contribution_Summary", {
                                actions: a.actions, phases: a.phases, tools: a.tools,
                            })}
                        </Caption1>
                    </li>
                ))}
                {perAgent.length === 0 && (
                    <li><Caption1>{t("MissionControl_Contribution_Empty")}</Caption1></li>
                )}
            </ul>
            <div className="mission-control__completion-actions">
                <Button appearance="primary" onClick={handleExport}>{t("MissionControl_Export")}</Button>
                <Button appearance="secondary" onClick={handleSaveTemplate}>{t("MissionControl_SaveTemplate")}</Button>
                <Button appearance="outline" icon={<ArrowClockwise16Regular />} onClick={handleStartAnother}>
                    {t("MissionControl_StartAnother")}
                </Button>
            </div>
        </Card>
    );
}
