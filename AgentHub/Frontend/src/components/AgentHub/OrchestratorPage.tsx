import React, { useState, useEffect } from "react";
import { useHistory, useRouteMatch } from "react-router-dom";
import {
    Badge,
    Button,
    Card,
    Text,
    Textarea,
    Spinner,
    Subtitle1,
    Body1,
    Caption1,
    Divider,
    Dropdown,
    Option,
    Field,
} from "@fluentui/react-components";
import {
    Sparkle24Regular,
    Checkmark24Regular,
    Dismiss24Regular,
} from "@fluentui/react-icons";
import ReactFlow, {
    Background,
    Controls,
    Node,
    Edge,
    Position,
    Handle,
    NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { callAuthAcquireAccessToken } from "../../controller/SampleWorkloadController";
import * as api from "../../controller/AgentHubApi";

interface OrchestratorPageProps {
    workloadClient: WorkloadClientAPI;
}

/* ── Custom ReactFlow agent node ─────────────────────────────── */

function AgentNode({ data }: NodeProps) {
    const dotColor =
        data.status === "running" ? "#0ea50e" :
        data.status === "waiting" ? "#daa520" :
        data.status === "error" ? "#d13438" : "#8a8886";
    return (
        <div className="rf-agent-node">
            <Handle type="target" position={Position.Left} />
            <div className="rf-agent-header">
                <Text weight="semibold" size={300}>{data.name}</Text>
                <span className="rf-status-dot" style={{ background: dotColor }} />
            </div>
            <Caption1 className="rf-agent-role">{data.role}</Caption1>
            {data.goal && <Text size={200} className="rf-agent-goal">{data.goal}</Text>}
            <Handle type="source" position={Position.Right} />
        </div>
    );
}

const nodeTypes = { agentNode: AgentNode };

/* ── Component ────────────────────────────────────────────────── */

export function OrchestratorPage({ workloadClient }: OrchestratorPageProps) {
    const [taskText, setTaskText] = useState("");
    const [planning, setPlanning] = useState(false);
    const [plan, setPlan] = useState<any | null>(null);
    const [jobId, setJobId] = useState<string | null>(null);
    const [approving, setApproving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Workspace selection
    const [workspaces, setWorkspaces] = useState<{ id: string; name: string }[]>([]);
    const [selectedWorkspace, setSelectedWorkspace] = useState("");
    const [loadingWorkspaces, setLoadingWorkspaces] = useState(false);

    const history = useHistory();
    const match = useRouteMatch();
    const githubToken = sessionStorage.getItem("github_token") || "";

    // Default workspace from URL or Fabric context
    const urlParams = new URLSearchParams(window.location.search);
    const defaultWs = urlParams.get("ws") || "";

    /** Acquire a fresh Fabric token for OBO tool execution. */
    async function getFabricToken(): Promise<string | undefined> {
        try {
            const accessToken = await callAuthAcquireAccessToken(workloadClient);
            return accessToken.token;
        } catch (e) {
            console.warn("Could not acquire Fabric token:", e);
            return undefined;
        }
    }

    // Load workspaces on mount
    useEffect(() => {
        loadWorkspaces();
    }, []);

    async function loadWorkspaces() {
        setLoadingWorkspaces(true);
        try {
            const fabricToken = await getFabricToken();
            // Use the workspaces endpoint
            const resp = await fetch(
                `${(process as any).env.WORKLOAD_BE_URL || 'http://localhost:5000'}/api/workspaces`,
                {
                    headers: {
                        'Authorization': `Bearer ${githubToken}`,
                        ...(fabricToken ? { 'X-Fabric-Token': `Bearer ${fabricToken}` } : {}),
                    },
                }
            );
            if (resp.ok) {
                const data = await resp.json();
                setWorkspaces(data || []);
                // Auto-select the current workspace if available
                if (defaultWs && data.some((w: any) => w.id === defaultWs)) {
                    setSelectedWorkspace(defaultWs);
                } else if (data.length > 0) {
                    setSelectedWorkspace(data[0].id);
                }
            }
        } catch (e) {
            console.warn("Failed to load workspaces, using manual input:", e);
        } finally {
            setLoadingWorkspaces(false);
        }
    }

    async function handleGeneratePlan() {
        if (!taskText.trim() || !selectedWorkspace) return;
        setPlanning(true);
        setError(null);
        setPlan(null);
        try {
            const fabricToken = await getFabricToken();
            const wsName = workspaces.find(w => w.id === selectedWorkspace)?.name || selectedWorkspace;
            const job = await api.createJob(
                taskText, selectedWorkspace,
                { workspace_name: wsName },
                { githubToken, fabricToken },
            );
            setJobId(job.id);
            setPlan(job.plan);
        } catch (e: any) {
            setError(e.message || "Plan generation failed");
        } finally {
            setPlanning(false);
        }
    }

    async function handleApprove() {
        if (!jobId) return;
        setApproving(true);
        try {
            const fabricToken = await getFabricToken();
            await api.approvePlan(jobId, { githubToken, fabricToken });
            history.push(match.url.replace(/\/orchestrator$/, `/job/${jobId}`));
        } catch (e: any) {
            setError(e.message || "Failed to start job");
        } finally {
            setApproving(false);
        }
    }

    async function handleReject() {
        if (!jobId) return;
        try {
            await api.rejectPlan(jobId, { githubToken });
        } catch { /* ok */ }
        setPlan(null);
        setJobId(null);
    }

    // Build ReactFlow nodes/edges from plan
    const { nodes, edges } = buildGraph(plan);

    return (
        <div className="orchestrator-page">
            {/* Task input */}
            <div className="orchestrator-input-section">
                <Text size={700} weight="bold" as="h2">Plan your next task.</Text>
                <Body1 className="orchestrator-subtitle">
                    The Orchestrator agent will decompose your goal into executable agent workflows.
                </Body1>

                <div className="task-input-card">
                    <Field label="Workspace" className="workspace-field">
                        {loadingWorkspaces ? (
                            <Spinner size="tiny" label="Loading workspaces..." />
                        ) : workspaces.length > 0 ? (
                            <Dropdown
                                value={workspaces.find(w => w.id === selectedWorkspace)?.name || selectedWorkspace}
                                onOptionSelect={(_, d) => setSelectedWorkspace(d.optionValue || "")}
                                placeholder="Select a workspace"
                            >
                                {workspaces.map(w => (
                                    <Option key={w.id} value={w.id} text={w.name}>{w.name}</Option>
                                ))}
                            </Dropdown>
                        ) : (
                            <input
                                type="text"
                                value={selectedWorkspace}
                                onChange={(e) => setSelectedWorkspace(e.target.value)}
                                placeholder="Enter workspace ID (e.g. 8bdca8af-...)"
                                className="workspace-manual-input"
                            />
                        )}
                    </Field>

                    <Caption1 className="task-label" style={{ color: "#d13438" }}>TASK DESCRIPTION</Caption1>
                    <Textarea
                        value={taskText}
                        onChange={(_, d) => setTaskText(d.value)}
                        placeholder="Describe what you want to accomplish..."
                        resize="vertical"
                        className="task-textarea"
                        disabled={planning}
                    />
                    <div className="task-actions">
                        <Button
                            appearance="primary"
                            icon={<Sparkle24Regular />}
                            size="large"
                            onClick={handleGeneratePlan}
                            disabled={planning || !taskText.trim() || !selectedWorkspace}
                        >
                            {planning ? <Spinner size="tiny" /> : "Generate Plan"}
                        </Button>
                    </div>
                </div>

                {error && (
                    <div className="orchestrator-error">
                        <Text size={300} style={{ color: "#d13438" }}>{error}</Text>
                    </div>
                )}
            </div>

            {/* Plan approval */}
            {plan && (
                <>
                    <Divider />
                    <div className="plan-approval-section">
                        <Subtitle1>Execution Plan</Subtitle1>
                        <Caption1 style={{ color: "#605e5c" }}>
                            Workspace: <Text weight="semibold">{workspaces.find(w => w.id === selectedWorkspace)?.name || selectedWorkspace}</Text>
                        </Caption1>
                        <Card className="plan-summary-card">
                            <Body1>{plan.summary}</Body1>
                            <div className="plan-agents-list">
                                {plan.agents?.map((a: any, i: number) => (
                                    <div key={i} className="plan-agent-row">
                                        <Badge appearance="outline" color="informative" size="small">
                                            {a.role}
                                        </Badge>
                                        <Text size={200}>{a.goal}</Text>
                                    </div>
                                ))}
                            </div>
                            {plan.estimated_duration && (
                                <Caption1>Estimated duration: {plan.estimated_duration}</Caption1>
                            )}
                        </Card>
                        <div className="plan-actions">
                            <Button
                                appearance="primary"
                                icon={<Checkmark24Regular />}
                                onClick={handleApprove}
                                disabled={approving}
                            >
                                {approving ? <Spinner size="tiny" /> : "Approve & Start"}
                            </Button>
                            <Button
                                appearance="secondary"
                                icon={<Dismiss24Regular />}
                                onClick={handleReject}
                            >
                                Reject
                            </Button>
                        </div>
                    </div>

                    {/* Orchestration graph */}
                    <div className="orchestration-graph-section">
                        <div className="graph-header">
                            <Subtitle1>Orchestration Graph</Subtitle1>
                            <Badge appearance="outline" color="informative">PREVIEW</Badge>
                        </div>
                        <div className="orchestration-graph" style={{ height: 350 }}>
                            <ReactFlow
                                nodes={nodes}
                                edges={edges}
                                nodeTypes={nodeTypes}
                                fitView
                                proOptions={{ hideAttribution: true }}
                            >
                                <Background />
                                <Controls />
                            </ReactFlow>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}

/* ── Graph builder ────────────────────────────────────────────── */

function buildGraph(plan: any | null): { nodes: Node[]; edges: Edge[] } {
    if (!plan?.agents?.length) return { nodes: [], edges: [] };

    const agents = plan.agents as any[];
    const graph = plan.communication_graph || {};
    const spacing = 250;

    const nodes: Node[] = agents.map((a, i) => ({
        id: a.agent_template_id,
        type: "agentNode",
        position: { x: (i % 3) * spacing + 50, y: Math.floor(i / 3) * 180 + 50 },
        data: {
            name: a.agent_template_id.split("-")[0]?.replace(/^\w/, (c: string) => c.toUpperCase()) || a.role,
            role: a.role,
            goal: a.goal?.slice(0, 60),
            status: "queued",
        },
    }));

    const edges: Edge[] = [];
    for (const [from, tos] of Object.entries(graph)) {
        for (const to of tos as string[]) {
            edges.push({
                id: `${from}-${to}`,
                source: from,
                target: to,
                animated: true,
                style: { stroke: "#0078d4" },
            });
        }
    }

    return { nodes, edges };
}
