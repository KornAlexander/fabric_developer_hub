import React, { useEffect, useState } from "react";
import { useHistory, useRouteMatch } from "react-router-dom";
import {
    Button,
    Body1,
    Spinner,
    MessageBar,
    MessageBarBody,
    MessageBarTitle,
    MessageBarActions,
} from "@fluentui/react-components";
import {
    Add20Regular,
    ArrowRight16Regular,
    ArrowSync24Regular,
    PlugConnected24Regular,
    DataUsage24Regular,
    DataPie24Regular,
    ShieldCheckmark24Regular,
    Lightbulb24Regular,
    Gauge24Regular,
    ArrowSwap24Regular,
    Bot24Regular,
    CheckmarkCircle16Filled,
    DismissCircle16Filled,
    ErrorCircle16Filled,
    MoreVertical20Regular,
    Warning20Regular,
    Play16Regular,
    ArrowClockwise16Regular,
    Dismiss16Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import * as api from "../../controller/AgentHubApi";
import { callAuthAcquireAccessToken } from "../../controller/AgentHubController";
import { useItemContext } from "./ItemContext";
import { readPreloaded, setPreloaded } from "./pagePreloadCache";
import { useSearch } from "./SearchContext";
import { fuzzyMatches } from "./fuzzySearch";

interface DashboardPageProps {
    workloadClient: WorkloadClientAPI;
}

type CardTone = "primary" | "secondary" | "error";

interface AgentVisual {
    icon: React.ReactNode;
    tone: CardTone;
}

/** Map an agent role/index to a deterministic icon + accent tone (matches design palette). */
function pickVisual(label: string, fallbackIdx: number): AgentVisual {
    const lower = (label || "").toLowerCase();
    if (lower.includes("data") || lower.includes("engineer")) return { icon: <DataUsage24Regular />, tone: "primary" };
    if (lower.includes("analy")) return { icon: <DataPie24Regular />, tone: "secondary" };
    if (lower.includes("secur") || lower.includes("compli")) return { icon: <ShieldCheckmark24Regular />, tone: "error" };
    if (lower.includes("research") || lower.includes("knowledge")) return { icon: <Lightbulb24Regular />, tone: "primary" };
    if (lower.includes("optim") || lower.includes("cost")) return { icon: <Gauge24Regular />, tone: "primary" };
    if (lower.includes("sync") || lower.includes("integr") || lower.includes("pipeline")) return { icon: <ArrowSwap24Regular />, tone: "primary" };
    const fallbacks: AgentVisual[] = [
        { icon: <Bot24Regular />, tone: "primary" },
        { icon: <DataPie24Regular />, tone: "secondary" },
        { icon: <Lightbulb24Regular />, tone: "primary" },
    ];
    return fallbacks[fallbackIdx % fallbacks.length];
}

/** Map job.status -> design's status-pill variant. */
function statusPillVariant(status: string): "running" | "waiting" | "error" | "completed" {
    switch (status) {
        case "running": return "running";
        case "completed": return "completed";
        case "failed": case "error": return "error";
        case "planned": case "approved": case "queued": case "waiting": default: return "waiting";
    }
}

function statusLabel(status: string): string {
    if (!status) return "";
    return status.charAt(0).toUpperCase() + status.slice(1);
}

export function DashboardPage({ workloadClient }: DashboardPageProps) {
    // If navTo() prefetched the session list before route-changing, use it
    // directly so the page mounts fully populated with no skeleton flicker.
    const preloaded = readPreloaded<any[]>("sessions");
    const [jobs, setJobs] = useState<any[]>(preloaded ?? []);
    const [loading, setLoading] = useState(preloaded === undefined);
    const [slowLoading, setSlowLoading] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [creating, setCreating] = useState(false);
    // Session-cancel confirmation. We render an in-app dialog because
    // window.confirm() is blocked by the browser inside Fabric's cross-origin
    // iframe sandbox — clicking the dismiss button on a session card would
    // otherwise appear to do nothing.
    const [pendingCancel, setPendingCancel] = useState<any | null>(null);
    const [cancelBusy, setCancelBusy] = useState(false);
    const [showAllHistory, setShowAllHistory] = useState(false);
    const history = useHistory();
    const match = useRouteMatch();
    const { itemObjectId, workspaceObjectId, createItem } = useItemContext();

    const githubToken = sessionStorage.getItem("github_token") || "";

    useEffect(() => {
        // If we already have preloaded data, skip the initial fetch.
        if (!loading) return;
        loadJobs();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    async function loadJobs() {
        setLoading(true);
        setSlowLoading(false);
        setLoadError(null);
        const slowTimer = window.setTimeout(() => setSlowLoading(true), 1200);
        try {
            // Need the Fabric token so the backend can identify the user by
            // UPN (same key sessions were written under). Without it the
            // backend falls back to an Authorization hash and returns nothing.
            let fabricToken: string | undefined;
            try {
                const tok = await callAuthAcquireAccessToken(workloadClient, undefined);
                fabricToken = tok?.token;
            } catch (e) {
                console.warn("Could not acquire Fabric token for sessions list:", e);
            }
            const data = await api.listSessions({ githubToken, fabricToken });
            setJobs(data || []);
            setPreloaded("sessions", data || []);
        } catch (e: any) {
            console.error("Failed to load jobs:", e);
            // Surface the error instead of silently showing an empty state —
            // otherwise a dead backend looks identical to a user with no
            // sessions, which is confusing.
            const msg = e?.message || String(e) || "Unknown error";
            // TypeError: Failed to fetch → classic offline-backend signature.
            const isNetwork = /failed to fetch|networkerror|load failed/i.test(msg);
            setLoadError(
                isNetwork
                    ? "Can't reach the Developer Hub backend. Check that it's running, then retry."
                    : `Failed to load sessions: ${msg}`,
            );
            setJobs([]);
        } finally {
            window.clearTimeout(slowTimer);
            setLoading(false);
            setSlowLoading(false);
        }
    }

    const activeJobs = jobs.filter(j => ["running", "approved", "planned", "waiting"].includes(j.status));
    const completedJobs = jobs.filter(j => ["completed", "failed", "cancelled"].includes(j.status));
    const runningCount = jobs.filter(j => j.status === "running").length;
    const waitingCount = jobs.filter(j => ["planned", "approved", "waiting"].includes(j.status)).length;
    const errorCount = jobs.filter(j => ["failed", "error"].includes(j.status)).length;

    // ── Topbar search: filter sessions by task / plan / attachment filename ──
    const { query: searchQuery } = useSearch();
    function matchesSearch(job: any): boolean {
        if (!searchQuery.trim()) return true;
        const haystacks: (string | null | undefined)[] = [
            job.task_description,
            job.id,
            job.status,
            job.plan?.summary,
            job.plan?.title,
            job.agent?.name,
            job.agent?.role_description,
            job.agent?.specialty,
            job.agent?.current_step,
            job.error_message,
            ...(Array.isArray(job.plan?.steps)
                ? job.plan.steps.map((s: any) => s?.description ?? s?.title ?? "")
                : []),
            ...(Array.isArray(job.attachments)
                ? job.attachments.map((a: any) => a?.filename ?? a?.name ?? "")
                : []),
        ];
        return fuzzyMatches(searchQuery, ...haystacks);
    }
    const visibleActiveJobs = activeJobs.filter(matchesSearch);
    const visibleCompletedJobs = completedJobs.filter(matchesSearch);

    function gotoSession(sessionId: string) {
        history.push(match.url.replace(/\/home$/, `/session/${sessionId}`));
    }

    function gotoNew() {
        history.push(match.url.replace(/\/home$/, "/orchestrator"));
    }

    async function dismissSession(job: any) {
        // Open the in-app confirm dialog. We can't use window.confirm()
        // because Chrome/Edge block it inside cross-origin iframes such as
        // the one Fabric hosts us in.
        setPendingCancel(job);
    }

    async function performCancel(job: any) {
        setCancelBusy(true);

        // Optimistic update — flip the card to cancelled locally so the
        // dashboard reacts instantly. If the API call fails we restore.
        const snapshot = jobs;
        const nowIso = new Date().toISOString();
        const optimistic = jobs.map(j =>
            j.id === job.id
                ? {
                    ...j,
                    status: "cancelled",
                    completed_at: nowIso,
                    cancelled_at: nowIso,
                    // Stamp the canceller so the "Cancelled by …" audit
                    // line renders consistently on the optimistic row
                    // (before the backend response arrives). The caller
                    // of performCancel is always the session owner, so
                    // falling back to the job's own user_upn/user_id is
                    // safe.
                    cancelled_by_upn: j.cancelled_by_upn || j.user_upn,
                    cancelled_by_user_id: j.cancelled_by_user_id || j.user_id,
                }
                : j,
        );
        setJobs(optimistic);
        setPreloaded("sessions", optimistic);

        try {
            let fabricToken: string | undefined;
            try {
                const tok = await callAuthAcquireAccessToken(workloadClient, undefined);
                fabricToken = tok?.token;
            } catch { /* best-effort */ }
            await api.cancelSession(job.id, { githubToken, fabricToken });
            setPendingCancel(null);
        } catch (e: any) {
            console.error("Failed to cancel session:", e);
            setJobs(snapshot);
            setPreloaded("sessions", snapshot);
            window.alert(`Could not cancel session: ${e?.message || e}`);
        } finally {
            setCancelBusy(false);
        }
    }

    return (
        <div className="sessions-page">
            {/* Page header */}
            <div className="sessions-header">
                <div>
                    <h1 className="sessions-title">Sessions</h1>
                    <p className="sessions-subtitle">
                        Monitor, orchestrate, and deploy enterprise-grade AI agents across your Fabric ecosystem.
                    </p>
                </div>
                <button className="sessions-cta" onClick={gotoNew}>
                    <Add20Regular />
                    <span>Create New Job</span>
                </button>
            </div>

            {/* Fabric persistence banner */}
            {!itemObjectId && workspaceObjectId && (
                <MessageBar intent="warning" style={{ marginBottom: 16 }}>
                    <MessageBarBody>
                        <MessageBarTitle>Not connected to Fabric</MessageBarTitle>
                        Settings and configuration are stored in this session only. Create a workspace item to persist them.
                    </MessageBarBody>
                    <MessageBarActions>
                        <Button
                            appearance="primary"
                            icon={<PlugConnected24Regular />}
                            size="small"
                            disabled={creating}
                            onClick={async () => {
                                setCreating(true);
                                try {
                                    await createItem("AgentHub", "AgentHub configuration and settings");
                                } catch (e) {
                                    console.error("Failed to create item:", e);
                                } finally {
                                    setCreating(false);
                                }
                            }}
                        >
                            {creating ? "Creating..." : "Create AgentHub Item"}
                        </Button>
                    </MessageBarActions>
                </MessageBar>
            )}
            {itemObjectId && (
                <MessageBar intent="success" style={{ marginBottom: 16 }}>
                    <MessageBarBody>
                        <MessageBarTitle>Connected to Fabric</MessageBarTitle>
                        Settings are persisted as a workspace item.
                    </MessageBarBody>
                </MessageBar>
            )}

            {/* ── Active Sessions ── */}
            <section className="sessions-section">
                <div className="sessions-section-head">
                    <h2 className="sessions-h2">Active Sessions</h2>
                    <div className="sessions-status-pills">
                        {runningCount > 0 && <span className="status-count status-count--neutral">{runningCount} Running</span>}
                        {waitingCount > 0 && <span className="status-count status-count--neutral">{waitingCount} Waiting</span>}
                        {errorCount > 0 && <span className="status-count status-count--error">{errorCount} Error</span>}
                    </div>
                    <button className="sessions-icon-btn" onClick={loadJobs} title="Refresh">
                        <ArrowSync24Regular />
                    </button>
                </div>

                {loading ? (
                    <div className="agent-cards-scroller" aria-busy="true" aria-label="Loading active sessions">
                        <div className="agent-cards-row">
                            {[0, 1, 2].map(i => (
                                <article key={i} className="session-card session-card--skeleton" aria-hidden="true">
                                    <header className="session-card-head">
                                        <div className="session-card-identity">
                                            <div className="skeleton skeleton-icon" />
                                            <div className="skeleton-lines">
                                                <div className="skeleton skeleton-line skeleton-line--title" />
                                                <div className="skeleton skeleton-line skeleton-line--sub" />
                                            </div>
                                        </div>
                                        <div className="skeleton skeleton-pill" />
                                    </header>
                                    <div className="session-card-body">
                                        <div className="skeleton skeleton-line skeleton-line--eyebrow" />
                                        <div className="skeleton skeleton-line skeleton-line--goal" />
                                        <div className="skeleton skeleton-line skeleton-line--goal-short" />
                                    </div>
                                    <footer className="session-card-foot">
                                        <div className="skeleton skeleton-line skeleton-line--meta" />
                                        <div className="skeleton skeleton-line skeleton-line--action" />
                                    </footer>
                                </article>
                            ))}
                        </div>
                        {slowLoading && (
                            <div className="agents-slow-hint" role="status">
                                <Spinner size="tiny" />
                                <span>Still loading—this is taking longer than usual…</span>
                            </div>
                        )}
                    </div>
                ) : loadError ? (
                    <div className="sessions-error" role="alert">
                        <Warning20Regular />
                        <div className="sessions-error-body">
                            <div className="sessions-error-title">Couldn't load sessions</div>
                            <div className="sessions-error-msg">{loadError}</div>
                        </div>
                        <Button appearance="primary" size="small" onClick={loadJobs}>
                            Retry
                        </Button>
                    </div>
                ) : activeJobs.length === 0 ? (
                    <div className="sessions-empty">
                        <Body1>No active sessions. Submit a task to get started.</Body1>
                    </div>
                ) : visibleActiveJobs.length === 0 ? (
                    <div className="sessions-empty">
                        <Body1>No active sessions match “{searchQuery}”.</Body1>
                    </div>
                ) : (
                    <div className="agent-cards-scroller">
                        <div className="agent-cards-row">
                            {visibleActiveJobs.map((job, idx) => {
                                const agent = job.agents?.[0];
                                const agentName = agent?.role || agent?.agent_id || "Orchestrator";
                                const agentSubtitle = agent?.specialty || agent?.role_description || "Workload";
                                const visual = pickVisual(agentName, idx);
                                const variant = statusPillVariant(job.status);

                                return (
                                    <article
                                        key={job.id}
                                        className={`session-card session-card--${variant}`}
                                        onClick={() => gotoSession(job.id)}
                                    >
                                        <header className="session-card-head">
                                            <div className="session-card-identity">
                                                <div className={`session-card-icon session-card-icon--${visual.tone}`}>
                                                    {visual.icon}
                                                </div>
                                                <div>
                                                    <div className="session-card-name">{agentName}</div>
                                                    <div className="session-card-role">{agentSubtitle}</div>
                                                </div>
                                            </div>
                                            <div className="session-card-head-right">
                                                <span className={`status-pill status-pill--${variant}`}>
                                                    {variant === "running" && <span className="status-dot status-dot--running" />}
                                                    {variant === "waiting" && <span className="status-dot status-dot--waiting" />}
                                                    {variant === "error" && <Warning20Regular />}
                                                    <span>{statusLabel(job.status)}</span>
                                                </span>
                                                <button
                                                    type="button"
                                                    className="session-card-dismiss"
                                                    aria-label="Cancel session"
                                                    title="Cancel session"
                                                    onClick={(e) => { e.stopPropagation(); dismissSession(job); }}
                                                >
                                                    <Dismiss16Regular />
                                                </button>
                                            </div>
                                        </header>

                                        <div className="session-card-body">
                                            <div className="session-card-label">CURRENT GOAL</div>
                                            <p className="session-card-goal">
                                                {job.task_description?.slice(0, 120) || "—"}
                                            </p>
                                            {variant !== "error" && agent?.current_step && (
                                                <div className="session-card-step">
                                                    <div className="session-card-label session-card-label--muted">CURRENT STEP</div>
                                                    <p>{agent.current_step}</p>
                                                </div>
                                            )}
                                            {variant === "error" && (agent?.last_error || job.error_message) && (
                                                <div className="session-card-error">
                                                    <div className="session-card-label session-card-label--error">EXCEPTION</div>
                                                    <code>{(agent?.last_error || job.error_message).slice(0, 140)}</code>
                                                </div>
                                            )}
                                        </div>

                                        <footer className="session-card-foot">
                                            <span className="session-card-meta">
                                                {variant === "waiting" && "Awaiting input"}
                                                {variant === "error" && "Action required"}
                                                {variant === "running" && (job.started_at ? `Started ${shortAgo(job.started_at)}` : "Running")}
                                            </span>
                                            <button
                                                className={`session-card-action session-card-action--${variant}`}
                                                onClick={(e) => { e.stopPropagation(); gotoSession(job.id); }}
                                            >
                                                {variant === "waiting" ? <>Resume <Play16Regular /></>
                                                  : variant === "error" ? <>Restart <ArrowClockwise16Regular /></>
                                                  : <>Details <ArrowRight16Regular /></>}
                                            </button>
                                        </footer>
                                    </article>
                                );
                            })}
                        </div>
                    </div>
                )}
            </section>

            {/* ── Recent Jobs ── (hidden while loading so layout doesn't shift) */}
            {!loading && !loadError && (
            <section className="sessions-section">
                <h2 className="sessions-h2">Recent Sessions</h2>
                {completedJobs.length === 0 ? (
                    <div className="sessions-empty">
                        <Body1>No completed tasks yet.</Body1>
                    </div>
                ) : (
                    <div className="recent-jobs-card">
                        <table className="recent-jobs-table">
                            <thead>
                                <tr>
                                    <th>Task</th>
                                    <th>Created</th>
                                    <th>Duration</th>
                                    <th>Status</th>
                                    <th aria-label="actions" />
                                </tr>
                            </thead>
                            <tbody>
                                {(showAllHistory ? visibleCompletedJobs : visibleCompletedJobs.slice(0, 10)).map((job) => {
                                    const dur = job.started_at && job.completed_at
                                        ? formatDuration(job.started_at, job.completed_at)
                                        : "—";
                                    const isOk = job.status === "completed";
                                    const isCancelled = job.status === "cancelled";
                                    const isFailed = job.status === "failed";
                                    // Distinct visual per outcome — cancelled is user-initiated
                                    // and neutral, NOT an error (which was the old behavior).
                                    const statusKind: "ok" | "cancelled" | "err" = isOk
                                        ? "ok"
                                        : isCancelled
                                            ? "cancelled"
                                            : "err";
                                    const StatusIcon = isOk
                                        ? CheckmarkCircle16Filled
                                        : isCancelled
                                            ? DismissCircle16Filled
                                            : ErrorCircle16Filled;
                                    const statusLabelText = isOk
                                        ? "100% SUCCESS"
                                        : isCancelled
                                            ? "CANCELLED"
                                            : (job.status || "").toUpperCase();
                                    const cancelledWho = job.cancelled_by_upn || job.cancelled_by_user_id;
                                    const cancelledAtIso = job.cancelled_at || (isCancelled ? job.completed_at : undefined);
                                    const createdFull = job.created_at
                                        ? new Date(job.created_at).toLocaleString()
                                        : undefined;
                                    const createdShort = job.created_at ? shortAgo(job.created_at) : "—";
                                    const cancelTooltip = isCancelled && cancelledAtIso
                                        ? `Cancelled ${new Date(cancelledAtIso).toLocaleString()}${cancelledWho ? ` by ${cancelledWho}` : ""}`
                                        : undefined;
                                    const createdTooltip = createdFull ? `Created ${createdFull}` : undefined;
                                    const rowTooltip = [createdTooltip, cancelTooltip].filter(Boolean).join(" · ");
                                    return (
                                        <tr key={job.id} className="recent-jobs-row" title={rowTooltip || undefined} onClick={() => gotoSession(job.id)}>
                                            <td className="recent-jobs-task">
                                                <div className="recent-jobs-task-main">
                                                    <StatusIcon className={`recent-jobs-check recent-jobs-check--${statusKind}`} />
                                                    <span>{job.task_description?.slice(0, 80) || "—"}</span>
                                                </div>
                                                {isCancelled && (cancelledWho || cancelledAtIso) && (
                                                    <div className="recent-jobs-audit">
                                                        Cancelled
                                                        {cancelledWho ? <> by <b>{cancelledWho}</b></> : null}
                                                        {cancelledAtIso ? <> · {shortAgo(cancelledAtIso)}</> : null}
                                                    </div>
                                                )}
                                            </td>
                                            <td className="recent-jobs-created" title={createdTooltip || undefined}>
                                                {createdShort}
                                            </td>
                                            <td className="recent-jobs-dur">{dur}</td>
                                            <td>
                                                <span className={`recent-jobs-status recent-jobs-status--${statusKind}`}>
                                                    {statusLabelText}
                                                </span>
                                            </td>
                                            <td className="recent-jobs-more">
                                                <button onClick={(e) => { e.stopPropagation(); gotoSession(job.id); }} title="More">
                                                    <MoreVertical20Regular />
                                                </button>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                        <div className="recent-jobs-foot">
                            {visibleCompletedJobs.length > 10 ? (
                                <button
                                    className="recent-jobs-viewall"
                                    onClick={() => setShowAllHistory((v) => !v)}
                                >
                                    {showAllHistory
                                        ? `Show Less (showing all ${visibleCompletedJobs.length})`
                                        : `View All History (${visibleCompletedJobs.length})`}
                                </button>
                            ) : (
                                <span className="recent-jobs-viewall-note">
                                    Showing all {visibleCompletedJobs.length}{" "}
                                    {visibleCompletedJobs.length === 1 ? "session" : "sessions"}
                                </span>
                            )}
                        </div>
                    </div>
                )}
            </section>
            )}

            {/* Cancel-session confirm dialog. Rendered inline because
                window.confirm() is blocked inside the Fabric iframe. */}
            {pendingCancel && (
                <div
                    className="cancel-dialog-backdrop"
                    role="presentation"
                    onClick={() => { if (!cancelBusy) setPendingCancel(null); }}
                >
                    <div
                        className="cancel-dialog"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="cancel-dialog-title"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="cancel-dialog-icon" aria-hidden="true">
                            <Warning20Regular />
                        </div>
                        <div className="cancel-dialog-body">
                            <h2 id="cancel-dialog-title" className="cancel-dialog-title">
                                Cancel this session?
                            </h2>
                            <p className="cancel-dialog-text">
                                {pendingCancel.status === "running"
                                    ? "Running agents will be stopped immediately."
                                    : "The session has not started yet."}
                                {" "}
                                It will be kept in Recent Sessions as <strong>cancelled</strong>
                                {" "}with an audit of who cancelled it and when.
                            </p>
                            {pendingCancel.task_description && (
                                <p className="cancel-dialog-task" title={pendingCancel.task_description}>
                                    “{pendingCancel.task_description.slice(0, 140)}
                                    {pendingCancel.task_description.length > 140 ? "…" : ""}”
                                </p>
                            )}
                        </div>
                        <div className="cancel-dialog-actions">
                            <Button
                                appearance="secondary"
                                disabled={cancelBusy}
                                onClick={() => setPendingCancel(null)}
                            >
                                Keep session
                            </Button>
                            <Button
                                appearance="primary"
                                disabled={cancelBusy}
                                icon={cancelBusy ? <Spinner size="tiny" /> : <Dismiss16Regular />}
                                onClick={() => performCancel(pendingCancel)}
                            >
                                {cancelBusy ? "Cancelling…" : "Cancel session"}
                            </Button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function formatDuration(start: string, end: string): string {
    const ms = new Date(end).getTime() - new Date(start).getTime();
    const secs = Math.floor(ms / 1000);
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    const remSecs = secs % 60;
    if (mins < 60) return `${mins}m ${remSecs}s`;
    const hrs = Math.floor(mins / 60);
    const remMins = mins % 60;
    return `${hrs}h ${remMins}m`;
}

function shortAgo(iso: string): string {
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 0) return "just now";
    const mins = Math.floor(ms / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
}

