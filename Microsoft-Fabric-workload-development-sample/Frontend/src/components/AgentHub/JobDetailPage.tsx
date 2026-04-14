import React, { useEffect, useState, useRef } from "react";
import { useParams, useHistory } from "react-router-dom";
import {
    Badge,
    Button,
    Card,
    Input,
    Text,
    Spinner,
    Subtitle1,
    Body1,
    Caption1,
    Tab,
    TabList,
    SelectTabEvent,
    SelectTabData,
    Switch,
    Table,
    TableHeader,
    TableRow,
    TableHeaderCell,
    TableBody,
    TableCell,
    TableCellLayout,
    MessageBar,
    MessageBarBody,
} from "@fluentui/react-components";
import {
    ArrowLeft24Regular,
    Send24Regular,
    Stop24Regular,
    BrainCircuit24Regular,
    Checkmark16Regular,
    ErrorCircle16Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import * as api from "../../controller/AgentHubApi";

interface JobDetailPageProps {
    workloadClient: WorkloadClientAPI;
}

interface Phase {
    number: number;
    title: string;
    timestamp: string;
    details: string[];
    decisions: string[];
    status: string;
}

interface Action {
    id: string;
    action_type: string;
    entity_name: string;
    entity_type: string;
    web_url?: string;
    timestamp: string;
}

export function JobDetailPage({ workloadClient }: JobDetailPageProps) {
    const { jobId } = useParams<{ jobId: string }>();
    const history = useHistory();

    const [job, setJob] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [phases, setPhases] = useState<Phase[]>([]);
    const [actions, setActions] = useState<Action[]>([]);
    const [agentStatuses, setAgentStatuses] = useState<Record<string, any>>({});
    const [verbose, setVerbose] = useState(true);
    const [chatInput, setChatInput] = useState("");
    const [filterTab, setFilterTab] = useState("all");
    const [elapsedSeconds, setElapsedSeconds] = useState(0);
    const [error, setError] = useState<string | null>(null);

    const githubToken = sessionStorage.getItem("github_token") || "";
    const logEndRef = useRef<HTMLDivElement>(null);
    const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

    // Load job
    useEffect(() => {
        loadJob();
    }, [jobId]);

    async function loadJob() {
        setLoading(true);
        try {
            const data = await api.getJob(jobId, { githubToken });
            setJob(data);

            // Populate from stored data
            const allPhases: Phase[] = [];
            const allActions: Action[] = [];
            for (const agent of data.agents || []) {
                for (const p of agent.phases || []) {
                    allPhases.push({
                        number: p.phase_number,
                        title: p.title,
                        timestamp: p.started_at,
                        details: p.details || [],
                        decisions: (p.decisions || []).map((d: any) => d.summary || d),
                        status: p.status,
                    });
                }
                for (const a of agent.actions || []) {
                    allActions.push(a);
                }
            }
            setPhases(allPhases);
            setActions(allActions);
        } catch (e: any) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    }

    // SSE subscription for running jobs
    useEffect(() => {
        if (!job || job.status !== "running") return undefined;

        const es = api.subscribeToJobEvents(jobId);
        es.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleSSEEvent(data);
            } catch { /* skip bad events */ }
        };
        es.onerror = () => {
            // Reconnection is automatic for EventSource
        };

        return () => es.close();
    }, [job?.status]);

    // Polling fallback: refresh job data periodically while running
    useEffect(() => {
        if (!job || job.status !== "running") return undefined;
        const pollInterval = setInterval(() => {
            loadJob();
        }, 5000);
        return () => clearInterval(pollInterval);
    }, [job?.status]);

    // Elapsed time counter
    useEffect(() => {
        if (job?.status === "running" && job?.started_at) {
            const start = new Date(job.started_at).getTime();
            timerRef.current = setInterval(() => {
                setElapsedSeconds(Math.floor((Date.now() - start) / 1000));
            }, 1000);
        }
        return () => {
            if (timerRef.current) clearInterval(timerRef.current);
        };
    }, [job?.status, job?.started_at]);

    // Auto-scroll reasoning log
    useEffect(() => {
        logEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [phases]);

    function handleSSEEvent(ev: any) {
        switch (ev.type) {
            case "agent_status":
                setAgentStatuses(prev => ({
                    ...prev,
                    [ev.agentId]: { name: ev.agentName, status: ev.status, currentStep: ev.currentStep, role: ev.role, goal: ev.goal },
                }));
                if (ev.status === "completed" || ev.status === "error") {
                    // Refresh job data
                    loadJob();
                }
                break;
            case "phase_start":
                setPhases(prev => {
                    // Mark all previous executing phases as completed
                    const updated = prev.map(p =>
                        p.status === "executing" ? { ...p, status: "completed" } : p
                    );
                    return [...updated, {
                        number: ev.phase?.number || updated.length + 1,
                        title: ev.phase?.title || "Phase",
                        timestamp: ev.phase?.timestamp || new Date().toISOString(),
                        details: [],
                        decisions: [],
                        status: "executing",
                    }];
                });
                break;
            case "phase_complete":
                setPhases(prev => {
                    const copy = [...prev];
                    const idx = copy.findIndex(p => p.number === ev.phaseNumber);
                    if (idx >= 0) copy[idx] = { ...copy[idx], status: "completed" };
                    return copy;
                });
                break;
            case "phase_detail":
                setPhases(prev => {
                    const copy = [...prev];
                    const idx = copy.findIndex(p => p.number === ev.phaseNumber);
                    if (idx >= 0) copy[idx] = { ...copy[idx], details: [...copy[idx].details, ev.detail] };
                    return copy;
                });
                break;
            case "agent_decision":
                setPhases(prev => {
                    const copy = [...prev];
                    const idx = copy.findIndex(p => p.number === ev.phaseNumber);
                    if (idx >= 0) copy[idx] = { ...copy[idx], decisions: [...copy[idx].decisions, ev.decision] };
                    return copy;
                });
                break;
            case "action":
                if (ev.action) {
                    setActions(prev => [...prev, ev.action]);
                }
                break;
            case "agent_error":
                setError(`Agent ${ev.agentName || ev.agentId}: ${ev.error}`);
                break;
            case "job_complete":
            case "job_failed":
                loadJob();
                if (timerRef.current) clearInterval(timerRef.current);
                break;
        }
    }

    async function handleSendMessage() {
        if (!chatInput.trim()) return;
        try {
            await api.sendMessage(jobId, chatInput, null, { githubToken });
            setChatInput("");
        } catch (e) {
            console.error("Failed to send message:", e);
        }
    }

    async function handleTerminate() {
        try {
            await api.cancelJob(jobId, { githubToken });
            if (timerRef.current) clearInterval(timerRef.current);
            loadJob();
        } catch (e) {
            console.error("Failed to cancel:", e);
        }
    }

    function formatElapsed(secs: number): string {
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }

    function actionIcon(type: string) {
        if (type === "Created") return <span className="action-dot action-dot--created" />;
        if (type === "Modified") return <span className="action-dot action-dot--modified" />;
        if (type === "Deleted") return <span className="action-dot action-dot--deleted" />;
        if (type === "Optimized") return <span className="action-dot action-dot--optimized" />;
        if (type === "Queried") return <span className="action-dot action-dot--queried" />;
        if (type === "Read") return <span className="action-dot action-dot--read" />;
        if (type === "Failed") return <span className="action-dot action-dot--failed" />;
        return <span className="action-dot" />;
    }

    function typeBadgeColor(t: string): "informative" | "success" | "warning" | "important" | "danger" {
        const lower = t.toLowerCase();
        if (lower.includes("sql") || lower.includes("script")) return "informative";
        if (lower.includes("pipeline")) return "important";
        if (lower.includes("schema") || lower.includes("lakehouse")) return "success";
        if (lower.includes("api") || lower.includes("hook")) return "warning";
        return "informative";
    }

    const filteredActions = filterTab === "all"
        ? actions
        : filterTab === "files"
        ? actions.filter(a => a.entity_type.toLowerCase().includes("file"))
        : actions.filter(a => !a.entity_type.toLowerCase().includes("file"));

    if (loading) {
        return <div className="job-detail-loading"><Spinner label="Loading job..." size="large" /></div>;
    }

    if (!job) {
        return <div className="job-detail-error"><Body1>Job not found.</Body1></div>;
    }

    const primaryAgent = Object.values(agentStatuses)[0] as any || job.agents?.[0];
    const isRunning = job.status === "running";

    return (
        <div className="job-detail-page">
            {/* Header */}
            <div className="job-detail-header">
                <Button
                    appearance="subtle"
                    icon={<ArrowLeft24Regular />}
                    onClick={() => history.goBack()}
                />
                <div className="job-header-info">
                    <div className="job-header-title-row">
                        <BrainCircuit24Regular />
                        <Text weight="bold" size={600}>
                            {primaryAgent?.name || primaryAgent?.role || "Agent"}
                        </Text>
                        <Badge
                            appearance="filled"
                            color={job.status === "running" ? "success" : job.status === "completed" ? "informative" : "danger"}
                        >
                            {job.status.toUpperCase()}
                        </Badge>
                    </div>
                    <Body1 className="job-goal-text">{job.task_description}</Body1>
                </div>
                <div className="job-header-actions">
                    {isRunning && (
                        <>
                            <div className="execution-time">
                                <Caption1>EXECUTION TIME</Caption1>
                                <Text weight="bold" size={500} className="time-display">
                                    {formatElapsed(elapsedSeconds)}
                                </Text>
                            </div>
                            <Button
                                appearance="primary"
                                icon={<Stop24Regular />}
                                onClick={handleTerminate}
                                style={{ backgroundColor: "#d13438" }}
                            >
                                Terminate Task
                            </Button>
                        </>
                    )}
                </div>
            </div>

            {/* Collaborating agents */}
            {Object.keys(agentStatuses).length > 1 && (
                <div className="collaborators-bar">
                    <Caption1>Working on this: </Caption1>
                    {Object.entries(agentStatuses).map(([id, st]: [string, any]) => (
                        <Badge key={id} appearance="outline" size="small" color={st.status === "running" ? "success" : "informative"}>
                            {st.name || st.role} ({st.status})
                        </Badge>
                    ))}
                </div>
            )}

            {error && (
                <MessageBar intent="error" className="job-error-bar">
                    <MessageBarBody>{error}</MessageBarBody>
                </MessageBar>
            )}

            {/* Main split pane */}
            <div className="job-split-pane">
                {/* Left: Reasoning Log */}
                <div className="reasoning-log-pane">
                    <div className="reasoning-header">
                        <div className="reasoning-title">
                            <BrainCircuit24Regular />
                            <Subtitle1>Reasoning Log</Subtitle1>
                        </div>
                        <div className="verbose-toggle">
                            <Caption1>VERBOSE MODE</Caption1>
                            <Switch checked={verbose} onChange={(_, d) => setVerbose(d.checked)} />
                        </div>
                    </div>

                    <div className="reasoning-log-content">
                        {phases.length === 0 && isRunning && (
                            <div className="reasoning-waiting">
                                <Spinner size="small" />
                                <Body1>Waiting for agent to begin...</Body1>
                            </div>
                        )}
                        {phases.map((phase) => (
                            <div key={phase.number} className="phase-entry">
                                <div className="phase-header">
                                    {phase.status === "completed" ? (
                                        <Checkmark16Regular className="phase-icon phase-icon--done" />
                                    ) : phase.status === "failed" ? (
                                        <ErrorCircle16Regular className="phase-icon phase-icon--error" />
                                    ) : (
                                        <Spinner size="tiny" className="phase-icon phase-icon--running" />
                                    )}
                                    <Text weight="semibold" size={300}>
                                        Phase {phase.number}: {phase.title}
                                    </Text>
                                    <Caption1 className="phase-timestamp">
                                        {new Date(phase.timestamp).toLocaleTimeString()}
                                    </Caption1>
                                </div>
                                {verbose && phase.details.length > 0 && (
                                    <div className="phase-details">
                                        {phase.details.map((d, i) => (
                                            <Text key={i} size={200} className="phase-detail-line">
                                                {">"} {d}
                                            </Text>
                                        ))}
                                    </div>
                                )}
                                {phase.decisions.map((dec, i) => (
                                    <Card key={i} className="decision-card">
                                        <div className="decision-header">
                                            <BrainCircuit24Regular />
                                            <Caption1 style={{ color: "#0078d4", fontWeight: 600 }}>AGENT DECISION</Caption1>
                                        </div>
                                        <Body1 className="decision-text">"{dec}"</Body1>
                                    </Card>
                                ))}
                            </div>
                        ))}
                        <div ref={logEndRef} />
                    </div>
                </div>

                {/* Right: Changes & Actions */}
                <div className="changes-pane">
                    <div className="changes-header">
                        <Subtitle1>Changes & Actions</Subtitle1>
                        <TabList
                            selectedValue={filterTab}
                            onTabSelect={(_: SelectTabEvent, d: SelectTabData) => setFilterTab(d.value as string)}
                            size="small"
                        >
                            <Tab value="all">All</Tab>
                            <Tab value="files">Files</Tab>
                            <Tab value="metadata">Metadata</Tab>
                        </TabList>
                    </div>

                    {filteredActions.length === 0 ? (
                        <div className="changes-empty">
                            <Body1>{isRunning ? "Waiting for actions..." : "No changes recorded."}</Body1>
                        </div>
                    ) : (
                        <Table className="changes-table" size="small">
                            <TableHeader>
                                <TableRow>
                                    <TableHeaderCell style={{ width: 90 }}>Action</TableHeaderCell>
                                    <TableHeaderCell>Entity</TableHeaderCell>
                                    <TableHeaderCell style={{ width: 100 }}>Type</TableHeaderCell>
                                    <TableHeaderCell style={{ width: 60 }}>Time</TableHeaderCell>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filteredActions.map((a, i) => (
                                    <TableRow key={a.id || i}>
                                        <TableCell>
                                            <TableCellLayout media={actionIcon(a.action_type)}>
                                                {a.action_type}
                                            </TableCellLayout>
                                        </TableCell>
                                        <TableCell>
                                            {a.web_url ? (
                                                <a
                                                    href="#"
                                                    className="entity-link"
                                                    onClick={(e) => {
                                                        e.preventDefault();
                                                        workloadClient?.navigation?.openBrowserTab?.({ url: a.web_url! });
                                                    }}
                                                >
                                                    {a.entity_name}
                                                </a>
                                            ) : (
                                                <Text size={200}>{a.entity_name}</Text>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            <Badge appearance="filled" color={typeBadgeColor(a.entity_type)} size="small">
                                                {a.entity_type}
                                            </Badge>
                                        </TableCell>
                                        <TableCell>
                                            <Caption1>
                                                {a.timestamp ? new Date(a.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}
                                            </Caption1>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </div>
            </div>

            {/* Agent chat input */}
            {isRunning && (
                <div className="agent-chat-bar">
                    <Input
                        value={chatInput}
                        onChange={(_, d) => setChatInput(d.value)}
                        placeholder="Ask the agent about its current reasoning..."
                        className="agent-chat-input"
                        onKeyDown={(e) => e.key === "Enter" && handleSendMessage()}
                    />
                    <Button
                        appearance="primary"
                        icon={<Send24Regular />}
                        onClick={handleSendMessage}
                        disabled={!chatInput.trim()}
                    />
                </div>
            )}
        </div>
    );
}
