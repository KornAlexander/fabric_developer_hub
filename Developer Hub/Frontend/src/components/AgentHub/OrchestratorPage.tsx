import React, { useState, useEffect, useRef } from "react";
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
    Menu,
    MenuTrigger,
    MenuPopover,
    MenuList,
    MenuItem,
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
    Dismiss16Regular,
    DocumentPdf20Regular,
    Image20Regular,
    Document20Regular,
    Flash20Regular,
    ShieldCheckmark20Regular,
    Warning20Regular,
    Money20Regular,
    BranchFork20Regular,
    Info16Regular,
    ChevronDown16Regular,
    ArrowClockwise16Regular,
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
import { callAuthAcquireAccessToken, callDatahubOpen } from "../../controller/AgentHubController";
import * as api from "../../controller/AgentHubApi";

// Common Fabric data item types surfaced in the Datahub item picker.
// The picker itself will show workspace navigation so any item the user can
// reach is pickable; this just restricts the initial filter.
const FABRIC_PICKER_SUPPORTED_TYPES = [
    "Lakehouse",
    "Warehouse",
    "KustoEventHouse",
    "KustoDatabase",
    "SemanticModel",
    "Notebook",
    "SynapseNotebook",
    "Pipeline",
    "DataflowFabric",
    "SparkJobDefinition",
    "Report",
    "EventStream",
    "SqlAnalyticsEndpoint",
    "SQLDbNative",
] as any;

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

/** Format a workspace cache timestamp as "just now" / "5 min ago" / "2 hr ago". */
function formatCacheAge(iso: string | null): string {
    if (!iso) return "never";
    const ageMs = Date.now() - new Date(iso).getTime();
    if (ageMs < 60_000) return "just now";
    const mins = Math.floor(ageMs / 60_000);
    if (mins < 60) return `${mins} min ago`;
    const hrs = Math.floor(mins / 60);
    return `${hrs} hr ago`;
}

/* ── Component ────────────────────────────────────────────────── */

export function OrchestratorPage({ workloadClient }: OrchestratorPageProps) {
    const [taskText, setTaskText] = useState("");
    const [planning, setPlanning] = useState(false);
    const [plan, setPlan] = useState<any | null>(null);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [approving, setApproving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Attached files. Sent to the backend as a structured `attachments`
    // array alongside the prompt so multimodal models (GPT-4o via Copilot
    // Chat API) can see images directly, and the server can extract text
    // from PDFs. For each kind:
    //   - text:  `content` is the raw UTF-8 string.
    //   - image: `content` is a data URI (`data:image/...;base64,…`).
    //   - pdf:   `content` is a data URI (`data:application/pdf;base64,…`).
    const fileInputRef = useRef<HTMLInputElement | null>(null);
    type AttachmentKind = "text" | "image" | "pdf";
    interface UiAttachment {
        name: string;
        size: number;
        kind: AttachmentKind;
        mime: string;
        content: string;
        /** Object URL for thumbnail (images only); revoked on removal. */
        previewUrl?: string;
    }
    const [attachedFiles, setAttachedFiles] = useState<UiAttachment[]>([]);
    const [uploadError, setUploadError] = useState<string | null>(null);

    // Recent prompts popover. We fetch on first open (cheap, ~1 roundtrip to
    // /api/sessions) and cache in state for the rest of the component's life.
    // Selecting a prompt only populates the textarea — it does NOT re-apply
    // the old session's workspace/toggles/attachments, because users almost
    // always want to tweak the prompt before submitting.
    const recentBtnRef = useRef<HTMLButtonElement | null>(null);
    const [recentOpen, setRecentOpen] = useState(false);
    const [recentPrompts, setRecentPrompts] = useState<
        { prompt: string; createdAt: string; workspace?: string }[] | null
    >(null);
    const [recentLoading, setRecentLoading] = useState(false);
    const [recentError, setRecentError] = useState<string | null>(null);
    const [recentIndex, setRecentIndex] = useState(0);

    // Workspace selection
    const [workspaces, setWorkspaces] = useState<{ id: string; name: string }[]>([]);
    const [selectedWorkspace, setSelectedWorkspace] = useState("");
    const [destinationWorkspace, setDestinationWorkspace] = useState("");
    const [loadingWorkspaces, setLoadingWorkspaces] = useState(false);
    const [workspacesCachedAt, setWorkspacesCachedAt] = useState<string | null>(null);

    // Composer toggles
    const [requireApprovals, setRequireApprovals] = useState(false);
    const [branchOut, setBranchOut] = useState(false);
    const [branchName, setBranchName] = useState("agent/sales-ingestion-pipeline");

    // Context pills (Fabric items + workspaces attached to the prompt)
    // `type` is the Fabric item type string (e.g. "Lakehouse", "Warehouse",
    // "Notebook") or the special "workspace" marker. `id` / `workspaceId` are
    // carried through to the backend so plan steps can resolve the item.
    const [contextItems, setContextItems] = useState<Array<{
        name: string;
        type: string;
        id?: string;
        workspaceId?: string;
    }>>([]);

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

    useEffect(() => { loadWorkspaces(false); }, []);

    async function loadWorkspaces(forceRefresh: boolean) {
        setLoadingWorkspaces(true);
        try {
            // Always pass the Fabric token so the backend can identify the
            // user via UPN from the JWT (same cache key on first load and on
            // refresh). The workload-client caches the token locally, so this
            // is cheap after the first call. The expensive OBO exchange is
            // only done on cache miss / stale / forced refresh.
            const fabricToken = await getFabricToken();
            const data = await api.getWorkspaces({ githubToken, fabricToken }, forceRefresh);
            const list = data.workspaces || [];
            setWorkspaces(list);
            setWorkspacesCachedAt(data.cached_at);
            if (data.source) {
                console.debug(`[workspaces] source=${data.source} count=${list.length}`);
            }
            // Preserve selection if it still exists, otherwise pick a default.
            setSelectedWorkspace(prev => {
                if (prev && list.some(w => w.id === prev)) return prev;
                if (defaultWs && list.some(w => w.id === defaultWs)) return defaultWs;
                return list[0]?.id || "";
            });
            setDestinationWorkspace(prev => {
                if (prev && list.some(w => w.id === prev)) return prev;
                if (defaultWs && list.some(w => w.id === defaultWs)) return defaultWs;
                return list[0]?.id || "";
            });
        } catch (e) {
            console.warn("Failed to load workspaces:", e);
        } finally {
            setLoadingWorkspaces(false);
        }
    }

    // Per-file cap. Applies to raw file size for binary (images/PDFs) and
    // to encoded-byte length for text. The backend enforces a matching cap.
    const MAX_FILE_BYTES = 10 * 1024 * 1024; // 10 MB
    const MAX_TOTAL_BYTES = 25 * 1024 * 1024; // 25 MB combined

    // ── Recent prompts ────────────────────────────────────────────────
    async function loadRecentPrompts() {
        if (!githubToken) {
            setRecentPrompts([]);
            return;
        }
        setRecentLoading(true);
        setRecentError(null);
        try {
            const sessions: any[] = await api.listSessions({ githubToken });
            // Dedupe by prompt text (keep most recent), cap at 10.
            const seen = new Set<string>();
            const result: { prompt: string; createdAt: string; workspace?: string }[] = [];
            for (const s of sessions) {
                const p: string = (s.task_description || "").trim();
                if (!p || seen.has(p)) continue;
                seen.add(p);
                result.push({
                    prompt: p,
                    createdAt: s.created_at || "",
                    workspace: s.context?.workspace_name,
                });
                if (result.length >= 10) break;
            }
            setRecentPrompts(result);
        } catch (e: any) {
            setRecentError(e.message || "Failed to load recent prompts.");
            setRecentPrompts([]);
        } finally {
            setRecentLoading(false);
        }
    }

    function openRecentPopover() {
        setRecentIndex(0);
        setRecentOpen(true);
        if (recentPrompts === null) loadRecentPrompts();
    }

    function pickRecentPrompt(prompt: string) {
        setTaskText(prompt);
        setRecentOpen(false);
    }

    // Close on outside click / Escape.
    useEffect(() => {
        if (!recentOpen) return;
        function onDown(e: MouseEvent) {
            const t = e.target as Node;
            if (recentBtnRef.current?.contains(t)) return;
            const popover = document.getElementById("recent-prompts-popover");
            if (popover?.contains(t)) return;
            setRecentOpen(false);
        }
        function onKey(e: KeyboardEvent) {
            if (e.key === "Escape") { setRecentOpen(false); return; }
            const n = recentPrompts?.length ?? 0;
            if (!n) return;
            if (e.key === "ArrowDown") { e.preventDefault(); setRecentIndex(i => (i + 1) % n); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setRecentIndex(i => (i - 1 + n) % n); }
            else if (e.key === "Enter") {
                e.preventDefault();
                const p = recentPrompts?.[recentIndex];
                if (p) pickRecentPrompt(p.prompt);
            }
        }
        document.addEventListener("mousedown", onDown);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("mousedown", onDown);
            document.removeEventListener("keydown", onKey);
        };
    }, [recentOpen, recentPrompts, recentIndex]);

    function formatRelativeTime(iso: string): string {
        if (!iso) return "";
        const t = new Date(iso).getTime();
        if (!t) return "";
        const diff = Date.now() - t;
        const m = Math.floor(diff / 60000);
        if (m < 1) return "just now";
        if (m < 60) return `${m}m ago`;
        const h = Math.floor(m / 60);
        if (h < 24) return `${h}h ago`;
        const d = Math.floor(h / 24);
        if (d < 7) return `${d}d ago`;
        return new Date(iso).toLocaleDateString();
    }

    function classifyFile(f: File): { kind: AttachmentKind; mime: string } {
        const mime = f.type || "";
        const name = f.name.toLowerCase();
        if (mime.startsWith("image/")) return { kind: "image", mime };
        if (mime === "application/pdf" || name.endsWith(".pdf")) {
            return { kind: "pdf", mime: "application/pdf" };
        }
        // Anything else — treat as text. Final read-as-text attempt will
        // surface an error if the file isn't valid UTF-8.
        return { kind: "text", mime: mime || "text/plain" };
    }

    function readAsDataUrl(f: File): Promise<string> {
        return new Promise((resolve, reject) => {
            const r = new FileReader();
            r.onerror = () => reject(new Error("read error"));
            r.onload = () => resolve(String(r.result || ""));
            r.readAsDataURL(f);
        });
    }

    async function handleUploadFile(e: React.ChangeEvent<HTMLInputElement>) {
        const input = e.target;
        const files = Array.from(input.files || []);
        input.value = ""; // allow re-selecting the same file later
        if (!files.length) return;
        setUploadError(null);
        const accepted: UiAttachment[] = [];
        let runningTotal = attachedFiles.reduce((acc, a) => acc + a.size, 0);
        for (const f of files) {
            if (f.size > MAX_FILE_BYTES) {
                setUploadError(`"${f.name}" is ${(f.size / (1024 * 1024)).toFixed(1)} MB — max is ${MAX_FILE_BYTES / (1024 * 1024)} MB per file.`);
                continue;
            }
            if (runningTotal + f.size > MAX_TOTAL_BYTES) {
                setUploadError(`Adding "${f.name}" would exceed the ${MAX_TOTAL_BYTES / (1024 * 1024)} MB total attachment limit.`);
                continue;
            }
            const { kind, mime } = classifyFile(f);
            try {
                if (kind === "text") {
                    const content = await f.text();
                    accepted.push({ name: f.name, size: f.size, kind, mime, content });
                } else {
                    const dataUrl = await readAsDataUrl(f);
                    const previewUrl = kind === "image" ? URL.createObjectURL(f) : undefined;
                    accepted.push({ name: f.name, size: f.size, kind, mime, content: dataUrl, previewUrl });
                }
                runningTotal += f.size;
            } catch {
                setUploadError(`Could not read "${f.name}".`);
            }
        }
        if (accepted.length) {
            setAttachedFiles(prev => [...prev, ...accepted]);
        }
    }

    function removeAttachedFile(name: string) {
        setAttachedFiles(prev => {
            const target = prev.find(f => f.name === name);
            if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
            return prev.filter(f => f.name !== name);
        });
    }

    // Revoke preview object URLs on unmount.
    useEffect(() => {
        return () => {
            attachedFiles.forEach(f => {
                if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
            });
        };
    // We intentionally only revoke on unmount, not on every change — the
    // remove-file path already revokes individually.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    async function handleGeneratePlan() {
        if (!taskText.trim() || !selectedWorkspace) return;
        setPlanning(true);
        setError(null);
        setPlan(null);
        try {
            const fabricToken = await getFabricToken();
            const wsName = workspaces.find(w => w.id === selectedWorkspace)?.name || selectedWorkspace;
            // Attachments go as a structured array; the backend extracts PDF
            // text, inlines text files into the prompt, and passes images to
            // the vision model as multi-part image_url content parts.
            const apiAttachments = attachedFiles.map(f => ({
                name: f.name,
                kind: f.kind,
                mime: f.mime,
                content: f.content,
            }));
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
                apiAttachments,
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

    async function addFabricItem() {
        try {
            const picked = await callDatahubOpen(
                FABRIC_PICKER_SUPPORTED_TYPES,
                "Select a Fabric item to attach as context",
                /* multiSelectionEnabled */ false,
                workloadClient,
                /* workspaceNavigationEnabled */ true,
            );
            if (!picked) return; // user cancelled
            setContextItems(prev => {
                if (prev.some(c => c.id === picked.id && c.type !== "workspace")) return prev;
                return [
                    ...prev,
                    {
                        name: picked.displayName || picked.id,
                        type: String(picked.type || "item"),
                        id: picked.id,
                        workspaceId: picked.workspaceId,
                    },
                ];
            });
        } catch (e) {
            console.warn("Datahub picker failed:", e);
        }
    }

    function addWorkspaceContext(ws: { id: string; name: string }) {
        setContextItems(prev => {
            if (prev.some(c => c.type === "workspace" && c.id === ws.id)) return prev;
            return [...prev, { name: ws.name, type: "workspace", id: ws.id }];
        });
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
                                {contextItems.map(item => {
                                    const isWorkspace = item.type === "workspace";
                                    const isLakehouse = item.type === "lakehouse" || item.type === "Lakehouse";
                                    const isWarehouse = item.type === "warehouse" || item.type === "Warehouse";
                                    const pillVariant = isWorkspace
                                        ? "workspace"
                                        : isLakehouse
                                            ? "lakehouse"
                                            : isWarehouse
                                                ? "warehouse"
                                                : "item";
                                    return (
                                        <span
                                            key={`${item.type}:${item.id || item.name}`}
                                            className={`ctx-pill ctx-pill--${pillVariant}`}
                                            title={item.type !== pillVariant ? `${item.name} · ${item.type}` : item.name}
                                        >
                                            {isWorkspace
                                                ? <PeopleTeam20Regular />
                                                : isWarehouse
                                                    ? <BuildingFactory20Regular />
                                                    : <Database20Regular />}
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
                                    );
                                })}
                                <button
                                    type="button"
                                    className="ctx-pill-add"
                                    onClick={addFabricItem}
                                >
                                    <Add20Regular /> Add Fabric item
                                </button>
                                <span className="ctx-divider" />
                                <Menu>
                                    <MenuTrigger disableButtonEnhancement>
                                        <button type="button" className="ctx-pill-add">
                                            <Add20Regular /> Add workspace
                                        </button>
                                    </MenuTrigger>
                                    <MenuPopover>
                                        <MenuList>
                                            {workspaces.length === 0 && (
                                                <MenuItem disabled>
                                                    {loadingWorkspaces ? "Loading workspaces…" : "No workspaces available"}
                                                </MenuItem>
                                            )}
                                            {workspaces.map(w => {
                                                const alreadyAdded = contextItems.some(
                                                    c => c.type === "workspace" && c.id === w.id,
                                                );
                                                return (
                                                    <MenuItem
                                                        key={w.id}
                                                        disabled={alreadyAdded}
                                                        onClick={() => addWorkspaceContext(w)}
                                                    >
                                                        {w.name}
                                                    </MenuItem>
                                                );
                                            })}
                                        </MenuList>
                                    </MenuPopover>
                                </Menu>
                            </div>

                            {(attachedFiles.length > 0 || uploadError) && (
                                <div className="composer-attachments">
                                    {attachedFiles.map(f => {
                                        const sizeLabel = f.size > 1024 * 1024
                                            ? `${(f.size / (1024 * 1024)).toFixed(1)} MB`
                                            : `${(f.size / 1024).toFixed(1)} KB`;
                                        const kindClass =
                                            f.kind === "image" ? "ctx-pill--image"
                                            : f.kind === "pdf" ? "ctx-pill--pdf"
                                            : "ctx-pill--attachment";
                                        return (
                                            <span
                                                key={f.name}
                                                className={`ctx-pill ${kindClass}`}
                                                title={`${f.name} · ${sizeLabel}`}
                                            >
                                                {f.kind === "image" && f.previewUrl ? (
                                                    <img
                                                        src={f.previewUrl}
                                                        alt=""
                                                        className="ctx-pill-thumb"
                                                    />
                                                ) : f.kind === "pdf" ? (
                                                    <DocumentPdf20Regular />
                                                ) : f.kind === "image" ? (
                                                    <Image20Regular />
                                                ) : (
                                                    <Document20Regular />
                                                )}
                                                <span className="ctx-pill-name">{f.name}</span>
                                                <button
                                                    type="button"
                                                    className="ctx-pill-close"
                                                    onClick={() => removeAttachedFile(f.name)}
                                                    aria-label={`Remove ${f.name}`}
                                                >
                                                    <Dismiss16Regular />
                                                </button>
                                            </span>
                                        );
                                    })}
                                    {uploadError && (
                                        <span className="composer-upload-error">{uploadError}</span>
                                    )}
                                </div>
                            )}

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
                                                <label>
                                                    SOURCE WORKSPACE
                                                    <button
                                                        type="button"
                                                        className="workspace-refresh-btn"
                                                        onClick={() => loadWorkspaces(true)}
                                                        disabled={loadingWorkspaces}
                                                        title={workspacesCachedAt
                                                            ? `Updated ${formatCacheAge(workspacesCachedAt)} — click to refresh`
                                                            : "Refresh workspaces"}
                                                        aria-label="Refresh workspaces"
                                                    >
                                                        <ArrowClockwise16Regular className={loadingWorkspaces ? "spin" : undefined} />
                                                    </button>
                                                </label>
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
                                    <label>
                                        DESTINATION WORKSPACE
                                        <button
                                            type="button"
                                            className="workspace-refresh-btn"
                                            onClick={() => loadWorkspaces(true)}
                                            disabled={loadingWorkspaces}
                                            title={workspacesCachedAt
                                                ? `Updated ${formatCacheAge(workspacesCachedAt)} — click to refresh`
                                                : "Refresh workspaces"}
                                            aria-label="Refresh workspaces"
                                        >
                                            <ArrowClockwise16Regular className={loadingWorkspaces ? "spin" : undefined} />
                                        </button>
                                    </label>
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
                                        <input
                                            ref={fileInputRef}
                                            type="file"
                                            multiple
                                            accept="image/*,application/pdf,.txt,.md,.json,.yaml,.yml,.csv,.tsv,.log,.sql,.py,.js,.ts,.tsx,.jsx,.xml,.html,.cfg,.ini,.toml,text/*"
                                            style={{ display: "none" }}
                                            onChange={handleUploadFile}
                                        />
                                        <button
                                            type="button"
                                            className="composer-link-btn"
                                            onClick={() => fileInputRef.current?.click()}
                                            title="Attach images, PDFs, or text files (10 MB per file, 25 MB total)"
                                        >
                                            <Attach20Regular /> Attach files
                                        </button>
                                        <div className="recent-prompts-wrap">
                                            <button
                                                ref={recentBtnRef}
                                                type="button"
                                                className={`composer-link-btn${recentOpen ? " is-active" : ""}`}
                                                onClick={() => recentOpen ? setRecentOpen(false) : openRecentPopover()}
                                                aria-haspopup="listbox"
                                                aria-expanded={recentOpen}
                                            >
                                                <History20Regular /> Recent prompts
                                            </button>
                                            {recentOpen && (
                                                <div
                                                    id="recent-prompts-popover"
                                                    className="recent-prompts-popover"
                                                    role="listbox"
                                                >
                                                    {recentLoading && (
                                                        <div className="recent-prompts-empty">
                                                            <Spinner size="tiny" /> <span>Loading…</span>
                                                        </div>
                                                    )}
                                                    {!recentLoading && recentError && (
                                                        <div className="recent-prompts-empty recent-prompts-error">
                                                            {recentError}
                                                        </div>
                                                    )}
                                                    {!recentLoading && !recentError && recentPrompts && recentPrompts.length === 0 && (
                                                        <div className="recent-prompts-empty">
                                                            No previous tasks yet.<br />
                                                            Your prompts will appear here after your first session.
                                                        </div>
                                                    )}
                                                    {!recentLoading && !recentError && recentPrompts && recentPrompts.length > 0 && (
                                                        <>
                                                            <div className="recent-prompts-header">Click to reuse — you can edit before sending</div>
                                                            <ul className="recent-prompts-list">
                                                                {recentPrompts.map((p, i) => (
                                                                    <li
                                                                        key={`${p.createdAt}-${i}`}
                                                                        role="option"
                                                                        aria-selected={i === recentIndex}
                                                                        className={`recent-prompts-item${i === recentIndex ? " is-active" : ""}`}
                                                                        onMouseEnter={() => setRecentIndex(i)}
                                                                        onClick={() => pickRecentPrompt(p.prompt)}
                                                                        title={p.prompt}
                                                                    >
                                                                        <div className="recent-prompts-text">{p.prompt}</div>
                                                                        <div className="recent-prompts-meta">
                                                                            <span>{formatRelativeTime(p.createdAt)}</span>
                                                                            {p.workspace && <span className="recent-prompts-ws">· {p.workspace}</span>}
                                                                        </div>
                                                                    </li>
                                                                ))}
                                                            </ul>
                                                            <div className="recent-prompts-footer">
                                                                <button
                                                                    type="button"
                                                                    className="recent-prompts-viewall"
                                                                    onClick={() => {
                                                                        setRecentOpen(false);
                                                                        history.push(match.url.replace(/\/orchestrator$/, "/home"));
                                                                    }}
                                                                >
                                                                    View all in Sessions →
                                                                </button>
                                                            </div>
                                                        </>
                                                    )}
                                                </div>
                                            )}
                                        </div>
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
