import React, { useEffect, useState } from "react";
import { useHistory, useRouteMatch } from "react-router-dom";
import {
    Badge,
    Button,
    Card,
    CardHeader,
    Text,
    Spinner,
    Subtitle1,
    Caption1,
    Body1,
    Divider,
    Table,
    TableHeader,
    TableRow,
    TableHeaderCell,
    TableBody,
    TableCell,
    TableCellLayout,
} from "@fluentui/react-components";
import {
    Add24Regular,
    ArrowRight16Regular,
    ArrowSync24Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import * as api from "../../controller/AgentHubApi";

interface DashboardPageProps {
    workloadClient: WorkloadClientAPI;
}

function statusColor(status: string): "success" | "warning" | "danger" | "informative" | "important" {
    switch (status) {
        case "running": return "success";
        case "waiting": case "queued": return "warning";
        case "error": case "failed": return "danger";
        case "completed": return "informative";
        default: return "important";
    }
}

function statusLabel(status: string): string {
    return status.charAt(0).toUpperCase() + status.slice(1);
}

export function DashboardPage({ workloadClient }: DashboardPageProps) {
    const [jobs, setJobs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const history = useHistory();
    const match = useRouteMatch();

    const githubToken = sessionStorage.getItem("github_token") || "";

    useEffect(() => {
        loadJobs();
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

    const activeJobs = jobs.filter(j => ["running", "approved", "planned"].includes(j.status));
    const completedJobs = jobs.filter(j => ["completed", "failed", "cancelled"].includes(j.status));
    const runningCount = jobs.filter(j => j.status === "running").length;
    const waitingCount = jobs.filter(j => ["planned", "approved"].includes(j.status)).length;

    return (
        <div className="dashboard-page">
            <div className="dashboard-header-row">
                <div>
                    <Text size={800} weight="bold" as="h1">Agent Dashboard</Text>
                    <Body1 className="dashboard-subtitle">
                        Monitor, orchestrate, and deploy enterprise-grade AI agents across your Fabric ecosystem.
                    </Body1>
                </div>
                <Button
                    appearance="primary"
                    icon={<Add24Regular />}
                    size="large"
                    onClick={() => history.push(match.url.replace(/\/home$/, "/orchestrator"))}
                >
                    New Task
                </Button>
            </div>

            {/* Active Agents */}
            <div className="dashboard-section">
                <div className="section-header">
                    <Subtitle1>Active Agents</Subtitle1>
                    <div className="status-counters">
                        {runningCount > 0 && (
                            <Badge appearance="filled" color="success">{runningCount} Running</Badge>
                        )}
                        {waitingCount > 0 && (
                            <Badge appearance="filled" color="warning">{waitingCount} Waiting</Badge>
                        )}
                    </div>
                    <Button
                        appearance="subtle"
                        icon={<ArrowSync24Regular />}
                        onClick={loadJobs}
                    />
                </div>

                {loading ? (
                    <div className="dashboard-loading"><Spinner label="Loading agents..." /></div>
                ) : activeJobs.length === 0 ? (
                    <div className="dashboard-empty">
                        <Body1>No active agents. Submit a task to get started.</Body1>
                    </div>
                ) : (
                    <div className="agent-cards-grid">
                        {activeJobs.map((job) => (
                            <Card
                                key={job.id}
                                className={`agent-card agent-card--${job.status}`}
                                onClick={() => history.push(match.url.replace(/\/home$/, `/job/${job.id}`))}
                            >
                                <CardHeader
                                    header={
                                        <div className="agent-card-header">
                                            <Text weight="semibold" size={400}>
                                                {job.agents?.[0]
                                                    ? (job.agents[0].role || job.agents[0].agent_id)
                                                    : "Orchestrator"}
                                            </Text>
                                            <Badge
                                                appearance="filled"
                                                color={statusColor(job.status)}
                                                size="small"
                                            >
                                                {statusLabel(job.status)}
                                            </Badge>
                                        </div>
                                    }
                                />
                                <div className="agent-card-body">
                                    <Caption1 className="agent-card-label">CURRENT GOAL</Caption1>
                                    <Body1 className="agent-card-goal">
                                        {job.task_description?.slice(0, 100)}
                                    </Body1>
                                    {job.agents?.[0]?.current_step && (
                                        <>
                                            <Caption1 className="agent-card-label">CURRENT STEP</Caption1>
                                            <Text size={200} className="agent-card-step">
                                                {job.agents[0].current_step}
                                            </Text>
                                        </>
                                    )}
                                </div>
                                <div className="agent-card-footer">
                                    <Text size={200} className="agent-card-detail-link">
                                        Details <ArrowRight16Regular />
                                    </Text>
                                </div>
                            </Card>
                        ))}
                    </div>
                )}
            </div>

            <Divider />

            {/* Recent Successes */}
            <div className="dashboard-section">
                <Subtitle1>Recent Successes</Subtitle1>
                {completedJobs.length === 0 ? (
                    <div className="dashboard-empty">
                        <Body1>No completed tasks yet.</Body1>
                    </div>
                ) : (
                    <Table className="successes-table">
                        <TableHeader>
                            <TableRow>
                                <TableHeaderCell>Agent</TableHeaderCell>
                                <TableHeaderCell>Task</TableHeaderCell>
                                <TableHeaderCell>Duration</TableHeaderCell>
                                <TableHeaderCell>Status</TableHeaderCell>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {completedJobs.slice(0, 10).map((job) => {
                                const dur = job.started_at && job.completed_at
                                    ? formatDuration(job.started_at, job.completed_at)
                                    : "-";
                                return (
                                    <TableRow
                                        key={job.id}
                                        className="clickable-row"
                                        onClick={() => history.push(match.url.replace(/\/home$/, `/job/${job.id}`))}
                                    >
                                        <TableCell>
                                            <TableCellLayout>
                                                {job.agents?.[0]?.role || "Agent"}
                                            </TableCellLayout>
                                        </TableCell>
                                        <TableCell>{job.task_description?.slice(0, 80)}</TableCell>
                                        <TableCell>{dur}</TableCell>
                                        <TableCell>
                                            <Badge
                                                appearance="filled"
                                                color={job.status === "completed" ? "success" : "danger"}
                                                size="small"
                                            >
                                                {job.status === "completed" ? "SUCCESS" : job.status.toUpperCase()}
                                            </Badge>
                                        </TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                )}
            </div>
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
