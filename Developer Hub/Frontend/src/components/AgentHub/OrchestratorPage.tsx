import React, { useState, useEffect } from "react";
import { useHistory, useRouteMatch } from "react-router-dom";
import {
    Badge,
    Button,
    Card,
    Text,
    Spinner,
    Subtitle1,
    Body1,
    Caption1,
    Divider,
} from "@fluentui/react-components";
import {
    Sparkle24Regular,
    Checkmark24Regular,
    Dismiss24Regular,
    Add20Regular,
    Database20Regular,
    BuildingFactory20Regular,
    PeopleTeam20Regular,
    Attach20Regular,
    History20Regular,
    Flash20Regular,
    ShieldCheckmark20Regular,
    Warning20Regular,
    Money20Regular,
    BranchFork20Regular,
    Info16Regular,
    ChevronDown16Regular,
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
import { callAuthAcquireAccessToken } from "../../controller/AgentHubController";
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

/* ── Prompt starter chips ────────────────────────────────────── */

const PROMPT_STARTERS = [
    "Ingest marketing logs weekly",
    "Audit all agent connections",
    "Rotate API keys",
    "Clean staging data",
    "Sync CRM to Lakehouse",
];

/* ── Discovery cards ─────────────────────────────────────────── */

interface DiscoveryCard {
    title: string;
    description: string;
    cta: string;
    icon: React.ReactNode;
    iconBg: string;
    iconColor: string;
    accent?: boolean;
}

const DISCOVERY_CARDS: DiscoveryCard[] = [
    {
        title: "Optimize Sales Pipeline",
        description: "High latency detected in yesterday's sync job. Recommend re-partitioning the source.",
        cta: "Start Optimization",
        icon: <Flash20Regular />,
        iconBg: "#fff8e1",
        iconColor: "#d97706",
    },
    {
        title: "Review Security Permissions",
        description: "Audit access for the new Gold Layer schema. 12 unauthorized attempts flagged.",
        cta: "Begin Audit",
        icon: <ShieldCheckmark20Regular />,
        iconBg: "#e3f2fd",
        iconColor: "#1565c0",
    },
    {
        title: "Audit Data Lineage",
        description: "Trace 'Customer_ID' across all transforms to ensure GDPR compliance standards.",
        cta: "Map Lineage",
        icon: <BranchFork20Regular />,
        iconBg: "#f3e5f5",
        iconColor: "#7b1fa2",
    },
    {
        title: "Fix Schema Drift",
        description: "Upstream 'Marketing_API' added 3 new fields. Pipelines currently ignoring them.",
        cta: "Remediate Drift",
        icon: <Warning20Regular />,
        iconBg: "#e8f5e9",
        iconColor: "#2e7d32",
    },
    {
        title: "Optimize Tenant Costs",
        description: "Identified $450/day in idle warehouse compute for the dev environment.",
        cta: "Apply Savings",
        icon: <Money20Regular />,
        iconBg: "#ffebee",
        iconColor: "#c62828",
    },
    {
        title: "Create Custom Agent",
        description: "Can't find what you need? Build a specialized agent for your specific domain.",
        cta: "Go to Studio",
        icon: <Add20Regular />,
        iconBg: "#005faa",
        iconColor: "#ffffff",
        accent: true,
    },
];

/* ── Component ────────────────────────────────────────────────── */

export function OrchestratorPage({ workloadClient }: OrchestratorPageProps) {
    const [taskText, setTaskText] = useState("");
    const [planning, setPlanning] = useState(false);
    const [plan, setPlan] = useState<any | null>(null);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [approving, setApproving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Workspace selection
    const [workspaces, setWorkspaces] = useState<{ id: string; name: string }[]>([]);
    const [selectedWorkspace, setSelectedWorkspace] = useState("");
    const [destinationWorkspace, setDestinationWorkspace] = useState("");
    const [loadingWorkspaces, setLoadingWorkspaces] = useState(false);

    // Composer toggles
    const [requireApprovals, setRequireApprovals] = useState(true);
    const [branchOut, setBranchOut] = useState(true);
    const [branchName, setBranchName] = useState("agent/sales-ingestion-pipeline");

    // Context pills (Fabric items + workspaces attached to the prompt)
    const [contextItems, setContextItems] = useState<{ name: string; type: "lakehouse" | "warehouse" | "workspace" }[]>([]);

    const history = useHistory();
    const match = useRouteMatch();
    const githubToken = sessionStorage.getItem("github_token") || "";

    const urlParams = new URLSearchParams(window.location.search);
    const defaultWs = urlParams.get("ws") || "";

    async function getFabricToken(): Promise<string | undefined> {
        try {
            const accessToken = await callAuthAcquireAccessToken(workloadClient);
            return accessToken.token;
        } catch (e) {
            console.warn("Could not acquire Fabric token:", e);
            return undefined;
        }
    }

    useEffect(() => { loadWorkspaces(); }, []);

    async function loadWorkspaces() {
        setLoadingWorkspaces(true);
        try {
            const fabricToken = await getFabricToken();
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
                const initial = (defaultWs && data.some((w: any) => w.id === defaultWs))
                    ? defaultWs
                    : (data[0]?.id || "");
                setSelectedWorkspace(initial);
                setDestinationWorkspace(initial);
            }
        } catch (e) {
            console.warn("Failed to load workspaces:", e);
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
            const job = await api.createSession(
                taskText, selectedWorkspace,
                {
                    workspace_name: wsName,
                    destination_workspace: destinationWorkspace,
                    branch_out: branchOut,
                    branch_name: branchOut ? branchName : undefined,
                    require_approvals: requireApprovals,
                    context_items: contextItems,
                },
                { githubToken, fabricToken },
            );
            setSessionId(job.id);
            setPlan(job.plan);
        } catch (e: any) {
            setError(e.message || "Plan generation failed");
        } finally {
            setPlanning(false);
        }
    }

    async function handleApprove() {
        if (!sessionId) return;
        setApproving(true);
        try {
            const fabricToken = await getFabricToken();
            await api.approvePlan(sessionId, { githubToken, fabricToken });
            history.push(match.url.replace(/\/orchestrator$/, `/session/${sessionId}`));
        } catch (e: any) {
            setError(e.message || "Failed to start job");
        } finally {
            setApproving(false);
        }
    }

    async function handleReject() {
        if (!sessionId) return;
        try { await api.rejectPlan(sessionId, { githubToken }); } catch { /* ok */ }
        setPlan(null);
        setSessionId(null);
    }

    function removeContext(name: string) {
        setContextItems(prev => prev.filter(c => c.name !== name));
    }

    const { nodes, edges } = buildGraph(plan);
    const wsName = workspaces.find(w => w.id === selectedWorkspace)?.name || selectedWorkspace;

    return (
        <div className="compose-page">
            <div className="compose-container">

                {/* ── HERO ── */}
                <section className="compose-hero">
                    <div className="compose-hero-icon">
                        <Sparkle24Regular />
                    </div>
                    <h1 className="compose-hero-title">Orchestrate your vision.</h1>
                    <p className="compose-hero-sub">
                        Describe what you need done. We’ll decompose your goal into steps,
                        select the right agents, and execute—with your approval at every critical decision.
                    </p>
                </section>

                {/* ── COMPOSER ── */}
                <section className="composer-wrap">
                    <div className="composer-card">
                        <div className="composer-glow" />
                        <div className="composer-inner">
                            <div className="composer-label">NEW TASK DESCRIPTION</div>
                            <textarea
                                className="composer-textarea"
                                value={taskText}
                                onChange={(e) => setTaskText(e.target.value)}
                                placeholder="Automate the weekly ingestion of regional sales data from our OneLake raw zone, normalize the schema for Gold-layer reporting, and generate SQL-based views for the Finance dashboard."
                                disabled={planning}
                            />

                            {/* Context pills */}
                            <div className="composer-pills">
                                {contextItems.map(item => (
                                    <span key={item.name} className={`ctx-pill ctx-pill--${item.type}`}>
                                        {item.type === "lakehouse" && <Database20Regular />}
                                        {item.type === "warehouse" && <BuildingFactory20Regular />}
                                        {item.type === "workspace" && <PeopleTeam20Regular />}
                                        {item.name}
                                        <button
                                            type="button"
                                            className="ctx-pill-close"
                                            onClick={() => removeContext(item.name)}
                                            aria-label={`Remove ${item.name}`}
                                        >
                                            <Dismiss24Regular />
                                        </button>
                                    </span>
                                ))}
                                <button
                                    type="button"
                                    className="ctx-pill-add"
                                    onClick={() => setContextItems(prev => [
                                        ...prev,
                                        { name: `item-${prev.length + 1}`, type: "lakehouse" },
                                    ])}
                                >
                                    <Add20Regular /> Add Fabric item
                                </button>
                                <span className="ctx-divider" />
                                <button
                                    type="button"
                                    className="ctx-pill-add"
                                    onClick={() => setContextItems(prev => [
                                        ...prev,
                                        { name: `workspace-${prev.length + 1}`, type: "workspace" },
                                    ])}
                                >
                                    <Add20Regular /> Add workspace
                                </button>
                            </div>

                            <div className="composer-actionbar">
                                {/* Toggles row */}
                                <div className="composer-toggles">
                                    <ComposerToggle
                                        label="Require approvals"
                                        icon={<ShieldCheckmark20Regular />}
                                        on={requireApprovals}
                                        onChange={setRequireApprovals}
                                    />
                                    <ComposerToggle
                                        label="Branch out"
                                        icon={<BranchFork20Regular />}
                                        on={branchOut}
                                        onChange={setBranchOut}
                                    />
                                </div>

                                {/* Source workspace + branch (when branchOut on) */}
                                {branchOut && (
                                    <div className="composer-branchpanel">
                                        <div className="branchpanel-row">
                                            <div className="branchpanel-field">
                                                <label>SOURCE WORKSPACE</label>
                                                <div className="select-wrap">
                                                    <PeopleTeam20Regular className="select-leadicon" />
                                                    {loadingWorkspaces ? (
                                                        <Spinner size="tiny" />
                                                    ) : (
                                                        <select
                                                            value={selectedWorkspace}
                                                            onChange={(e) => setSelectedWorkspace(e.target.value)}
                                                        >
                                                            {workspaces.map(w => (
                                                                <option key={w.id} value={w.id}>{w.name}</option>
                                                            ))}
                                                        </select>
                                                    )}
                                                    <ChevronDown16Regular className="select-trailicon" />
                                                </div>
                                            </div>
                                            <div className="branchpanel-field">
                                                <label>BRANCH NAME</label>
                                                <div className="select-wrap">
                                                    <BranchFork20Regular className="select-leadicon" />
                                                    <input
                                                        type="text"
                                                        value={branchName}
                                                        onChange={(e) => setBranchName(e.target.value)}
                                                    />
                                                </div>
                                            </div>
                                        </div>
                                        <p className="branchpanel-info">
                                            <Info16Regular />
                                            Changes will be applied in a new branch. Merge to source workspace when ready.
                                        </p>
                                    </div>
                                )}

                                {/* Destination workspace (always shown) */}
                                <div className="branchpanel-field branchpanel-field--full">
                                    <label>DESTINATION WORKSPACE</label>
                                    <div className="select-wrap">
                                        <PeopleTeam20Regular className="select-leadicon" />
                                        {loadingWorkspaces ? (
                                            <Spinner size="tiny" />
                                        ) : (
                                            <select
                                                value={destinationWorkspace}
                                                onChange={(e) => setDestinationWorkspace(e.target.value)}
                                            >
                                                {workspaces.map(w => (
                                                    <option key={w.id} value={w.id}>{w.name}</option>
                                                ))}
                                            </select>
                                        )}
                                        <ChevronDown16Regular className="select-trailicon" />
                                    </div>
                                </div>

                                {/* Actions row */}
                                <div className="composer-actions">
                                    <div className="composer-actions-left">
                                        <button type="button" className="composer-link-btn">
                                            <Attach20Regular /> Upload file
                                        </button>
                                        <button type="button" className="composer-link-btn">
                                            <History20Regular /> Recent prompts
                                        </button>
                                    </div>
                                    <Button
                                        appearance="primary"
                                        icon={<Sparkle24Regular />}
                                        iconPosition="after"
                                        size="large"
                                        className="composer-submit-btn"
                                        onClick={handleGeneratePlan}
                                        disabled={planning || !taskText.trim() || !selectedWorkspace}
                                    >
                                        {planning ? <Spinner size="tiny" /> : "Generate Plan"}
                                    </Button>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Prompt starters */}
                    <div className="prompt-starters">
                        <span className="starters-label">TRY:</span>
                        {PROMPT_STARTERS.map(s => (
                            <button
                                key={s}
                                type="button"
                                className="prompt-chip"
                                onClick={() => setTaskText(s)}
                            >
                                {s}
                            </button>
                        ))}
                    </div>
                </section>

                {error && (
                    <div className="compose-error">
                        <Text size={300} style={{ color: "#d13438" }}>{error}</Text>
                    </div>
                )}

                {/* ── PLAN APPROVAL (kept from original) ── */}
                {plan && (
                    <>
                        <Divider />
                        <div className="plan-approval-section">
                            <Subtitle1>Execution Plan</Subtitle1>
                            <Caption1 style={{ color: "#605e5c" }}>
                                Workspace: <Text weight="semibold">{wsName}</Text>
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

                {/* ── DISCOVERY & RECOMMENDATIONS ── */}
                {!plan && (
                    <section className="discovery-section">
                        <div className="discovery-header">
                            <div className="discovery-title">
                                <Flash20Regular />
                                <h3>DISCOVERY &amp; RECOMMENDATIONS</h3>
                            </div>
                            <button type="button" className="discovery-viewall">View all suggestions</button>
                        </div>
                        <div className="discovery-grid">
                            {DISCOVERY_CARDS.map(card => (
                                <button
                                    key={card.title}
                                    type="button"
                                    className={`discovery-card ${card.accent ? "discovery-card--accent" : ""}`}
                                    onClick={() => setTaskText(card.description)}
                                >
                                    <div
                                        className="discovery-card-icon"
                                        style={{ background: card.iconBg, color: card.iconColor }}
                                    >
                                        {card.icon}
                                    </div>
                                    <h4>{card.title}</h4>
                                    <p>{card.description}</p>
                                    <div className="discovery-card-cta">
                                        {card.cta} <span aria-hidden>→</span>
                                    </div>
                                </button>
                            ))}
                        </div>
                    </section>
                )}
            </div>
        </div>
    );
}

/* ── Composer toggle pill ────────────────────────────────────── */

function ComposerToggle({ label, icon, on, onChange }: {
    label: string;
    icon: React.ReactNode;
    on: boolean;
    onChange: (next: boolean) => void;
}) {
    return (
        <button
            type="button"
            className="composer-toggle"
            onClick={() => onChange(!on)}
            aria-pressed={on}
        >
            {icon}
            <span>{label}</span>
            <span className={`composer-switch ${on ? "composer-switch--on" : ""}`}>
                <span className="composer-switch-thumb" />
            </span>
        </button>
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
