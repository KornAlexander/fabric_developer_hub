import React, { useEffect, useState } from "react";
import { useHistory, useRouteMatch } from "react-router-dom";
import {
    Button,
    Spinner,
    Body1,
    MessageBar,
    MessageBarBody,
    MessageBarTitle,
    MessageBarActions,
} from "@fluentui/react-components";
import {
    Add24Regular,
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
    MoreVertical20Regular,
    Warning20Regular,
    Play16Regular,
    ArrowClockwise16Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import * as api from "../../controller/AgentHubApi";
import { useItemContext } from "./ItemContext";

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

export function DashboardPage({ workloadClient: _workloadClient }: DashboardPageProps) {
    const [jobs, setJobs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [creating, setCreating] = useState(false);
    const history = useHistory();
    const match = useRouteMatch();
    const { itemObjectId, workspaceObjectId, createItem } = useItemContext();

    const githubToken = sessionStorage.getItem("github_token") || "";

    useEffect(() => {
        loadJobs();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    async function loadJobs() {
        setLoading(true);
        try {
            const data = await api.listJobs({ githubToken });
            setJobs(data || []);
        } catch (e) {
            console.error("Failed to load jobs:", e);
        } finally {
            setLoading(false);
        }
    }

    const activeJobs = jobs.filter(j => ["running", "approved", "planned", "waiting"].includes(j.status));
    const completedJobs = jobs.filter(j => ["completed", "failed", "cancelled"].includes(j.status));
    const runningCount = jobs.filter(j => j.status === "running").length;
    const waitingCount = jobs.filter(j => ["planned", "approved", "waiting"].includes(j.status)).length;
    const errorCount = jobs.filter(j => ["failed", "error"].includes(j.status)).length;

    function gotoJob(jobId: string) {
        history.push(match.url.replace(/\/home$/, `/job/${jobId}`));
    }

    function gotoNew() {
        history.push(match.url.replace(/\/home$/, "/orchestrator"));
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
                    <Add24Regular />
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

            {/* ── Active Agents ── */}
            <section className="sessions-section">
                <div className="sessions-section-head">
                    <h2 className="sessions-h2">Active Agents</h2>
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
                    <div className="sessions-loading"><Spinner label="Loading agents..." /></div>
                ) : activeJobs.length === 0 ? (
                    <div className="sessions-empty">
                        <Body1>No active agents. Submit a task to get started.</Body1>
                    </div>
                ) : (
                    <div className="agent-cards-scroller">
                        <div className="agent-cards-row">
                            {activeJobs.map((job, idx) => {
                                const agent = job.agents?.[0];
                                const agentName = agent?.role || agent?.agent_id || "Orchestrator";
                                const agentSubtitle = agent?.specialty || agent?.role_description || "Workload";
                                const visual = pickVisual(agentName, idx);
                                const variant = statusPillVariant(job.status);

                                return (
                                    <article
                                        key={job.id}
                                        className={`session-card session-card--${variant}`}
                                        onClick={() => gotoJob(job.id)}
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
                                            <span className={`status-pill status-pill--${variant}`}>
                                                {variant === "running" && <span className="status-dot status-dot--running" />}
                                                {variant === "waiting" && <span className="status-dot status-dot--waiting" />}
                                                {variant === "error" && <Warning20Regular />}
                                                <span>{statusLabel(job.status)}</span>
                                            </span>
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
                                                onClick={(e) => { e.stopPropagation(); gotoJob(job.id); }}
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

            {/* ── Recent Jobs ── */}
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
                                    <th>Agent Name</th>
                                    <th>Completed Task</th>
                                    <th>Duration</th>
                                    <th>Status</th>
                                    <th aria-label="actions" />
                                </tr>
                            </thead>
                            <tbody>
                                {completedJobs.slice(0, 10).map((job) => {
                                    const dur = job.started_at && job.completed_at
                                        ? formatDuration(job.started_at, job.completed_at)
                                        : "—";
                                    const isOk = job.status === "completed";
                                    const agentName = job.agents?.[0]?.role || job.agents?.[0]?.agent_id || "Agent";
                                    return (
                                        <tr key={job.id} className="recent-jobs-row" onClick={() => gotoJob(job.id)}>
                                            <td>
                                                <span className="recent-jobs-agent">
                                                    <CheckmarkCircle16Filled className={isOk ? "recent-jobs-check" : "recent-jobs-check--err"} />
                                                    {agentName}
                                                </span>
                                            </td>
                                            <td className="recent-jobs-task">{job.task_description?.slice(0, 80) || "—"}</td>
                                            <td className="recent-jobs-dur">{dur}</td>
                                            <td>
                                                <span className={`recent-jobs-status ${isOk ? "" : "recent-jobs-status--err"}`}>
                                                    {isOk ? "100% SUCCESS" : (job.status || "").toUpperCase()}
                                                </span>
                                            </td>
                                            <td className="recent-jobs-more">
                                                <button onClick={(e) => { e.stopPropagation(); gotoJob(job.id); }} title="More">
                                                    <MoreVertical20Regular />
                                                </button>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                        <div className="recent-jobs-foot">
                            <button className="recent-jobs-viewall">View All History</button>
                        </div>
                    </div>
                )}
            </section>
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

