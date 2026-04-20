import React, { useState, useEffect, useRef, useMemo } from "react";
import { useHistory, useRouteMatch } from "react-router-dom";
import {
    Button,
    Text,
    Spinner,
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
    ArrowDownload20Regular,
    ChatMultiple24Regular,
    Bot24Regular,
    DataUsage24Regular,
    Search20Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { callAuthAcquireAccessToken, callDatahubOpen } from "../../controller/AgentHubController";
import * as api from "../../controller/AgentHubApi";
import type { Workspace } from "../../controller/AgentHubApi";
import { PlanView } from "./plan";
import type { Plan } from "./plan";
import { PdfPreview } from "./PdfPreview";
import { useSearch } from "./SearchContext";
import { fuzzyFilter } from "./fuzzySearch";
import { WorkspacePreviewModal } from "./WorkspacePreviewModal";

const BE = process.env.WORKLOAD_BE_URL || "http://127.0.0.1:5000";

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

/**
 * Map a session status string to a compact visual descriptor for the
 * Recent prompts pill. Aggregates the backend's many in-flight sub-states
 * (planned/approved/queued/waiting/executing/generating_plan) under a
 * single "in progress" bucket so the pill stays legible.
 */
type RecentStatusVariant = "running" | "completed" | "failed" | "cancelled" | "waiting";

// Human-friendly label for a raw Fabric item type string. The REST API
// returns PascalCase (e.g. ``DataPipeline``, ``KustoDatabase``,
// ``SemanticModel``) which reads awkwardly in tooltips. Known types get
// curated names; unknown ones fall back to a PascalCase → spaced split.
function humanizeItemType(raw: unknown): string {
    // Defensive: the Fabric Datahub picker occasionally returns a numeric
    // enum in ``itemType`` instead of the PascalCase string (e.g. ``12``
    // for DataPipeline). Anything that isn't a proper identifier string
    // is surfaced as a generic "Item" so tooltips never show "Pipeline_1 12".
    if (typeof raw !== "string" || !/^[A-Za-z][A-Za-z0-9]*$/.test(raw)) return "Item";
    const t = raw.toLowerCase();
    const map: Record<string, string> = {
        workspace: "Workspace",
        lakehouse: "Lakehouse",
        warehouse: "Warehouse",
        notebook: "Notebook",
        datapipeline: "Data Pipeline",
        pipeline: "Pipeline",
        semanticmodel: "Semantic Model",
        dataset: "Semantic Model",
        report: "Report",
        dashboard: "Dashboard",
        dataflow: "Dataflow",
        dataflowgen2: "Dataflow Gen2",
        sparkjobdefinition: "Spark Job Definition",
        kustodatabase: "KQL Database",
        kqldatabase: "KQL Database",
        kustoeventhouse: "Eventhouse",
        eventhouse: "Eventhouse",
        eventstream: "Eventstream",
        kqlqueryset: "KQL Queryset",
        mirroreddatabase: "Mirrored Database",
        dataversemirroredDatabase: "Dataverse Mirror",
        sqlendpoint: "SQL Endpoint",
        sqlanalyticsendpoint: "SQL Endpoint",
        sqldb: "SQL Database",
        mlmodel: "ML Model",
        mlexperiment: "ML Experiment",
        environment: "Environment",
        graphqlapi: "GraphQL API",
        apiformlmodel: "ML Model API",
        reflex: "Data Activator",
        datamart: "Datamart",
        paginatedreport: "Paginated Report",
        variablelibrary: "Variable Library",
        copyjob: "Copy Job",
        exploration: "Exploration",
        map: "Map",
    };
    if (map[t]) return map[t];
    // Fallback: split PascalCase / camelCase into spaced words.
    return raw.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/([A-Z])([A-Z][a-z])/g, "$1 $2");
}


function recentStatusInfo(status?: string): { variant: RecentStatusVariant; label: string } | null {
    if (!status) return null;
    const s = status.toLowerCase();
    if (s === "completed" || s === "success" || s === "succeeded") return { variant: "completed", label: "Completed" };
    if (s === "failed" || s === "error") return { variant: "failed", label: "Failed" };
    if (s === "cancelled" || s === "canceled") return { variant: "cancelled", label: "Cancelled" };
    if (s === "running" || s === "executing") return { variant: "running", label: "Running" };
    if (s === "planned" || s === "approved" || s === "queued" || s === "waiting" || s === "generating_plan") {
        return { variant: "waiting", label: "In progress" };
    }
    return null;
}

/* ── Component ────────────────────────────────────────────────── */

export function OrchestratorPage({ workloadClient }: OrchestratorPageProps) {
    const [taskText, setTaskText] = useState("");
    const [planning, setPlanning] = useState(false);
    // Serialized snapshot of every input that feeds plan generation, taken
    // the instant planning starts. Used to tell whether the user has changed
    // anything while a plan is in flight — if so we show the "won't be
    // reflected" warning; if nothing changed, we stay quiet.
    const [planningSnapshot, setPlanningSnapshot] = useState<string | null>(null);
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
    // Anchor for the generated-plan section. We auto-scroll to this once a
    // plan arrives so the user isn't left staring at the prompt form.
    const planSectionRef = useRef<HTMLDivElement | null>(null);
    // Focus the compose textarea when the New Session page first mounts so
    // users can start typing immediately. One-shot — subsequent re-renders
    // don't steal focus from whatever the user is doing.
    const composerRef = useRef<HTMLTextAreaElement | null>(null);
    useEffect(() => {
        const id = window.requestAnimationFrame(() => {
            composerRef.current?.focus();
        });
        return () => window.cancelAnimationFrame(id);
    }, []);
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
    // Which attachment (if any) is currently shown in the preview overlay.
    // Clicking a pill opens the overlay; it closes on Escape or backdrop
    // click. Images render inline, PDFs in an iframe, and text files in a
    // pre block (content is already held in memory as data URI / utf-8).
    const [previewAttachment, setPreviewAttachment] = useState<UiAttachment | null>(null);
    // Workspace preview modal — opened by clicking a workspace ctx-pill.
    // Shows the items inside the workspace in a Fabric-like table. The
    // backend caches results per (user, workspace) for ~60s; we track
    // ``previewWsCapturedAt`` alongside items so the modal can render
    // "Loaded HH:MM:SS" and offer a Refresh button that force-busts
    // the cache (``refresh: true``).
    const [previewWorkspace, setPreviewWorkspace] = useState<{ id: string; name: string } | null>(null);
    // When the user clicks a Fabric item pill we open the parent
    // workspace's preview and ask the modal to highlight+scroll to
    // the specific row so the "click to preview" interaction points
    // at exactly what was clicked.
    const [previewHighlightItemId, setPreviewHighlightItemId] = useState<string | null>(null);
    const [previewWsItems, setPreviewWsItems] = useState<api.WorkspaceItem[] | null>(null);
    const [previewWsLoading, setPreviewWsLoading] = useState(false);
    const [previewWsError, setPreviewWsError] = useState<string | null>(null);
    const [previewWsCapturedAt, setPreviewWsCapturedAt] = useState<string | null>(null);
    // Fallback dialog for when Fabric's `openBrowserTab` rejects a
    // download URL (e.g. in dev when the backend runs on localhost, not
    // on a host Fabric's allowlist trusts). Holds the minted URL so the
    // user can copy+paste it into a new tab themselves — the browser
    // then honours `Content-Disposition: attachment` at the top level.
    const [manualDownloadUrl, setManualDownloadUrl] = useState<string | null>(null);
    const [manualDownloadCopied, setManualDownloadCopied] = useState(false);

    // Recent prompts popover. Opens a scrollable list of the caller's past
    // sessions (newest first) with lazy pagination — we fetch a page of
    // summaries, then load more when the sentinel row scrolls into view.
    // Clicking an entry restores the full compose form (prompt text,
    // workspace, toggles, context pills, and attached files). Attachment
    // bytes are fetched on-demand (not included in the list payload) via
    // ``GET /api/sessions/{id}``.
    const recentBtnRef = useRef<HTMLButtonElement | null>(null);
    const recentScrollRef = useRef<HTMLDivElement | null>(null);
    const recentSentinelRef = useRef<HTMLDivElement | null>(null);
    // True when ``recentIndex`` just changed because of keyboard navigation
    // (Arrow keys). Only in that case do we auto-scroll the active item into
    // view — hovering an already-visible item with the mouse must NOT cause
    // the list to jump.
    const recentIndexFromKeyboardRef = useRef(false);
    interface RecentSessionLite {
        id: string;
        prompt: string;
        createdAt: string;
        status?: string;
        workspaceId?: string;
        workspaceName?: string;
        contextItems: Array<{ name: string; type: string; id?: string; workspaceId?: string }>;
        attachments: Array<{ name: string; kind: AttachmentKind; mime?: string }>;
    }
    const RECENT_PAGE_SIZE = 20;
    const [recentOpen, setRecentOpen] = useState(false);
    const [recentSessions, setRecentSessions] = useState<RecentSessionLite[] | null>(null);
    const [recentLoading, setRecentLoading] = useState(false);
    const [recentLoadingMore, setRecentLoadingMore] = useState(false);
    const [recentHasMore, setRecentHasMore] = useState(true);
    const [recentError, setRecentError] = useState<string | null>(null);
    const [recentIndex, setRecentIndex] = useState(0);
    const [recentRestoring, setRecentRestoring] = useState<string | null>(null);
    // In-popover search: lets the user filter across every loaded prompt
    // (independent of the global topbar search). Scrolling the popover also
    // auto-pages so the filter reaches entries that haven't been fetched
    // yet.
    const [recentFilter, setRecentFilter] = useState("");

    // Ephemeral "unsaved draft" slot. Populated when the user has typed
    // something and then clicks a Recent prompt — we snapshot their current
    // composer so they can restore it in one click. Shown at the top of the
    // popover with a distinct style so it's obvious it isn't a persisted
    // session. Cleared after the draft is consumed (restored) or the user
    // successfully generates a plan (at which point it's persisted as a
    // real recent prompt instead).
    const DRAFT_ID = "__draft__";
    interface DraftEntry extends RecentSessionLite {
        // Full in-memory attachments (with bytes + preview URLs) so we can
        // restore them without an API fetch.
        _fullAttachments: UiAttachment[];
    }
    const [draftEntry, setDraftEntry] = useState<DraftEntry | null>(null);
    // Tracks the id of the last recent prompt the user restored into the
    // composer. Used to avoid re-snapshotting a draft that itself came from
    // another Recent pick (i.e. the user is hopping between recents without
    // typing). Cleared when the user types in the textarea — any further
    // edit counts as a fresh unsaved draft worth preserving.
    const [lastRecentPickId, setLastRecentPickId] = useState<string | null>(null);
    // Viewport-relative position for the popover. Rendered with
    // `position: fixed` so it escapes the composer card's `overflow: hidden`
    // clipping and stays fully visible regardless of page scroll or how
    // many recent prompts are loaded. Recomputed on open and whenever the
    // user scrolls / resizes while it's open. `placement: "above"` means
    // the popover opens upward from the button (preferred); `"below"` is
    // the fallback when there isn't enough space above.
    const [recentPos, setRecentPos] = useState<{
        top: number; left: number; width: number; maxHeight: number;
        placement: "above" | "below";
    } | null>(null);

    function computeRecentPopoverPos(): typeof recentPos {
        const btn = recentBtnRef.current;
        if (!btn) return null;
        const r = btn.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const MARGIN = 8;
        const GAP = 6;
        const PREFERRED_WIDTH = 440;
        const MAX_HEIGHT_CAP = 520;
        const width = Math.min(PREFERRED_WIDTH, vw - MARGIN * 2);
        // Left-align to the button, but clamp so the popover stays on screen.
        let left = r.left;
        if (left + width > vw - MARGIN) left = vw - MARGIN - width;
        if (left < MARGIN) left = MARGIN;
        const spaceAbove = r.top - MARGIN - GAP;
        const spaceBelow = vh - r.bottom - MARGIN - GAP;
        // Prefer opening upward (button sits at the composer footer). Only
        // flip if there's meaningfully more room below.
        const placement: "above" | "below" = spaceAbove >= 200 || spaceAbove >= spaceBelow ? "above" : "below";
        const maxHeight = Math.max(
            160,
            Math.min(MAX_HEIGHT_CAP, placement === "above" ? spaceAbove : spaceBelow),
        );
        const top = placement === "above" ? r.top - GAP - maxHeight : r.bottom + GAP;
        return { top, left, width, maxHeight, placement };
    }

    // Workspace selection
    const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
    const [selectedWorkspace, setSelectedWorkspace] = useState("");
    const [destinationWorkspace, setDestinationWorkspace] = useState("");
    const [loadingWorkspaces, setLoadingWorkspaces] = useState(false);
    const [workspacesCachedAt, setWorkspacesCachedAt] = useState<string | null>(null);
    const [workspacesError, setWorkspacesError] = useState<string | null>(null);
    // Inline "create new workspace" form — only available when branch-out
    // is OFF. Opens below the dropdown, POSTs to /api/workspaces, then
    // inserts the new workspace and selects it.
    const [createWsOpen, setCreateWsOpen] = useState(false);
    const [createWsName, setCreateWsName] = useState("");
    const [creatingWs, setCreatingWs] = useState(false);
    const [createWsError, setCreateWsError] = useState<string | null>(null);

    // Composer toggles
    const [requireApprovals, setRequireApprovals] = useState(false);
    const [branchOut, setBranchOut] = useState(false);
    const [branchName, setBranchName] = useState("");
    // Destination child workspace name shown in the branch-out tree.
    // Both this and ``branchName`` are populated by an LLM call (see
    // the debounced effect below) — there is no client-side heuristic.
    // While the LLM call is in flight, the UI shows a "generating
    // name…" skeleton in place of the input. Once the user hand-edits
    // either field, the matching ``*Touched`` flag freezes it so
    // subsequent AI suggestions don't overwrite their edit.
    const [childWsName, setChildWsName] = useState("");
    const [branchNameTouched, setBranchNameTouched] = useState(false);
    const [childWsNameTouched, setChildWsNameTouched] = useState(false);
    // When enabled, non-git-connected workspaces appear (disabled) in the
    // source dropdown so users can see the full list and understand why
    // those workspaces can't be used. Fabric's branch-out operation
    // requires a git-connected source workspace
    // (https://learn.microsoft.com/en-us/fabric/cicd/git-integration/manage-branches).
    const [showUnsupportedSources, setShowUnsupportedSources] = useState(false);

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

    // --- LLM-generated suggestions (debounced) ----------------------
    // Both ``branchName`` and ``childWsName`` start empty. 700 ms after
    // the user stops typing in the task description we ask Copilot
    // (gpt-4o-mini) for sensible names and fill the fields. While the
    // request is in flight, the inputs are replaced by a "generating
    // name…" skeleton (see render code). If the user has hand-edited
    // either field (``*Touched`` set) we respect that and do not
    // overwrite. Any LLM failure (timeout, malformed JSON, no token)
    // silently leaves the field empty so the user can type their own.
    const [suggestLoading, setSuggestLoading] = useState(false);
    // We cache the raw AI suggestions so the user can restore them
    // after manual edits without triggering another LLM call. The
    // workspace suffix is also used when the user switches source
    // workspaces (we re-prefix it instead of re-prompting the LLM).
    const [childWsSuffix, setChildWsSuffix] = useState("");
    const [aiBranchName, setAiBranchName] = useState("");

    // Run the actual LLM request. Extracted so both the debounced
    // effect (on task-text change) AND the "Regenerate with AI" button
    // can call it. ``targets`` picks which field(s) to overwrite; the
    // button uses "branch" / "workspace" to regenerate just one even
    // if the other is touched.
    async function fetchBranchSuggestions(
        targets: { branch: boolean; workspace: boolean },
        signal?: AbortSignal,
    ): Promise<void> {
        const task = taskText.trim();
        if (task.length < 10) return;
        const githubToken = sessionStorage.getItem("github_token") || "";
        if (!githubToken) return;
        setSuggestLoading(true);
        try {
            const src = workspaces.find(w => w.id === selectedWorkspace);
            const resp = await fetch(`${BE}/api/github/suggest-branch-names`, {
                method: "POST",
                signal,
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${githubToken}`,
                },
                body: JSON.stringify({
                    task_text: task,
                    source_workspace_name: src?.name || null,
                    context_names: contextItems.map(c => c.name),
                    file_names: attachedFiles.map(f => f.name),
                }),
            });
            if (!resp.ok) return;
            const data = await resp.json();
            const aiBranch = String(data.branch_name || "").trim();
            const aiWs = String(data.workspace_name || "").trim();
            if (aiBranch) setAiBranchName(aiBranch);
            if (aiWs) setChildWsSuffix(aiWs);
            if (aiBranch && targets.branch) {
                setBranchName(aiBranch);
                setBranchNameTouched(false);
            }
            if (aiWs && targets.workspace) {
                const srcName = src?.name || "Workspace";
                setChildWsName(`${srcName} — ${aiWs}`.slice(0, 200));
                setChildWsNameTouched(false);
            }
        } catch { /* network / abort — silently leave fields empty */ }
        finally { setSuggestLoading(false); }
    }

    useEffect(() => {
        if (!branchOut) return undefined;
        if (branchNameTouched && childWsNameTouched) return undefined; // both frozen
        if (taskText.trim().length < 10) return undefined; // too short for a good suggestion
        // Flip the loading flag synchronously so the skeleton appears
        // the instant the user toggles branch-out on (or edits the
        // task text) — otherwise the empty input flashes for the 700 ms
        // debounce window before we show "Generating name…".
        setSuggestLoading(true);
        const ctrl = new AbortController();
        const handle = window.setTimeout(() => {
            fetchBranchSuggestions(
                { branch: !branchNameTouched, workspace: !childWsNameTouched },
                ctrl.signal,
            );
        }, 700);
        return () => {
            window.clearTimeout(handle);
            ctrl.abort();
            // If the effect is torn down before the request fires
            // (e.g. user keeps typing), we leave ``suggestLoading``
            // ``true`` — the next effect run will re-assert it and
            // the skeleton stays visible without flicker.
        };
        // NOTE: ``selectedWorkspace`` is intentionally omitted from
        // deps — changing the source workspace re-prefixes the
        // destination name via the effect below, without a fresh LLM
        // round-trip.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [branchOut, taskText, contextItems, attachedFiles]);

    // Re-prefix the destination workspace name when the source
    // changes. Uses the cached AI suffix so we don't hit the LLM again.
    useEffect(() => {
        if (!branchOut) return;
        if (childWsNameTouched) return;
        if (!childWsSuffix) return;
        const src = workspaces.find(w => w.id === selectedWorkspace);
        const srcName = src?.name || "Workspace";
        setChildWsName(`${srcName} — ${childWsSuffix}`.slice(0, 200));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedWorkspace, workspaces, childWsSuffix, branchOut]);

    // Branch-out now creates a *child* workspace from the source (no
    // separate destination picker), so the destination always mirrors
    // the source. Keeping the state in sync means any downstream code
    // that still reads destinationWorkspace sees a consistent value.
    useEffect(() => {
        if (destinationWorkspace !== selectedWorkspace) {
            setDestinationWorkspace(selectedWorkspace);
        }
    }, [selectedWorkspace, destinationWorkspace]);

    async function loadWorkspaces(forceRefresh: boolean) {
        setLoadingWorkspaces(true);
        setWorkspacesError(null);
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
        } catch (e: any) {
            console.warn("Failed to load workspaces:", e);
            // Surface the failure so users don't stare at an empty dropdown
            // wondering why they can't pick a workspace.
            const msg = e?.message || String(e) || "Unknown error";
            const isNetwork = /failed to fetch|networkerror|load failed/i.test(msg);
            setWorkspacesError(
                isNetwork
                    ? "Can't reach the Developer Hub backend. Check that it's running, then retry."
                    : `Failed to load workspaces: ${msg}`,
            );
        } finally {
            setLoadingWorkspaces(false);
        }
    }

    async function handleCreateWorkspace() {
        const name = createWsName.trim();
        if (!name || creatingWs) return;
        setCreatingWs(true);
        setCreateWsError(null);
        try {
            const fabricToken = await getFabricToken();
            const created = await api.createWorkspace(
                { display_name: name },
                { githubToken, fabricToken },
            );
            setWorkspaces(prev => {
                if (prev.some(w => w.id === created.id)) return prev;
                return [...prev, created].sort(
                    (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
                );
            });
            setSelectedWorkspace(created.id);
            setCreateWsOpen(false);
            setCreateWsName("");
        } catch (e: any) {
            setCreateWsError(friendlyCreateWorkspaceError(e));
        } finally {
            setCreatingWs(false);
        }
    }

    /**
     * Turn the backend's raw error text (which wraps the Fabric API's 403
     * JSON) into a compact, human-readable message. The most common
     * failure is ``InsufficientScopes`` — that means this workload's
     * Entra app registration hasn't been granted admin consent for
     * ``Workspace.ReadWrite.All``. We surface that plainly so admins
     * know what to fix, and offer an escape hatch (open Fabric in a new
     * tab to create the workspace there).
     */
    function friendlyCreateWorkspaceError(e: any): string {
        const raw = e?.message || String(e) || "Workspace creation failed";
        // The raw message often looks like:
        //   {"detail":"Error creating workspace: 403 — {\"requestId\":...,
        //    \"errorCode\":\"InsufficientScopes\",...}"}
        try {
            const outer = JSON.parse(raw);
            const inner = outer?.detail ?? raw;
            const m = String(inner).match(/\{[\s\S]*\}$/);
            if (m) {
                const body = JSON.parse(m[0]);
                if (body?.errorCode === "InsufficientScopes") {
                    return (
                        "This workload isn't permitted to create workspaces on your "
                        + "behalf. Ask an admin to grant ``Workspace.ReadWrite.All`` "
                        + "to the AgentHub app registration, or use “Open in Fabric” "
                        + "to create the workspace directly."
                    );
                }
                if (body?.message) return String(body.message);
            }
        } catch {
            /* fall through */
        }
        if (/403/.test(raw)) {
            return "Creating workspaces isn't permitted for this workload. Use “Open in Fabric” instead.";
        }
        return raw;
    }

    // Per-file cap. Applies to raw file size for binary (images/PDFs) and
    // to encoded-byte length for text. The backend enforces a matching cap.
    const MAX_FILE_BYTES = 10 * 1024 * 1024; // 10 MB
    const MAX_TOTAL_BYTES = 25 * 1024 * 1024; // 25 MB combined

    // ── Recent prompts ────────────────────────────────────────────────
    function toRecentLite(s: any): RecentSessionLite {
        const ctx = (s && typeof s === "object" ? s.context : null) || {};
        const rawAtts = Array.isArray(ctx.prompt_attachments) ? ctx.prompt_attachments : [];
        const rawItems = Array.isArray(ctx.context_items) ? ctx.context_items : [];
        return {
            id: String(s.id),
            prompt: String(s.task_description || "").trim(),
            createdAt: String(s.created_at || ""),
            status: s.status ? String(s.status) : undefined,
            workspaceId: s.workspace_id || undefined,
            workspaceName: ctx.workspace_name || undefined,
            contextItems: rawItems.map((c: any) => ({
                name: String(c.name || ""),
                type: String(c.type || "item"),
                id: c.id ? String(c.id) : undefined,
                workspaceId: c.workspaceId ? String(c.workspaceId) : undefined,
            })),
            attachments: rawAtts.map((a: any) => ({
                name: String(a.name || ""),
                kind: (a.kind === "image" || a.kind === "pdf" ? a.kind : "text") as AttachmentKind,
                mime: a.mime ? String(a.mime) : undefined,
            })),
        };
    }

    async function loadRecentSessions(reset: boolean) {
        if (!githubToken) {
            setRecentSessions([]);
            setRecentHasMore(false);
            return;
        }
        if (reset) {
            setRecentLoading(true);
            setRecentError(null);
        } else {
            if (recentLoadingMore || !recentHasMore) return;
            setRecentLoadingMore(true);
        }
        try {
            const fabricToken = await getFabricToken();
            const offset = reset ? 0 : (recentSessions?.length || 0);
            const raw: any[] = await api.listSessions(
                { githubToken, fabricToken },
                undefined,
                { limit: RECENT_PAGE_SIZE, offset },
            );
            const page = (raw || [])
                .filter(s => (s?.task_description || "").trim().length > 0)
                .map(toRecentLite);
            setRecentHasMore((raw || []).length >= RECENT_PAGE_SIZE);
            setRecentSessions(prev => (reset || !prev ? page : [...prev, ...page]));
        } catch (e: any) {
            setRecentError(e.message || "Failed to load recent prompts.");
            if (reset) setRecentSessions([]);
        } finally {
            setRecentLoading(false);
            setRecentLoadingMore(false);
        }
    }

    function openRecentPopover() {
        setRecentIndex(0);
        setRecentFilter("");
        setRecentPos(computeRecentPopoverPos());
        setRecentOpen(true);
        if (recentSessions === null) loadRecentSessions(true);
    }

    async function pickRecentPrompt(session: RecentSessionLite) {
        // ── Restore the cached unsaved draft ─────────────────────────
        // The draft entry holds an in-memory snapshot with full attachment
        // bytes, so we can restore without any API call.
        if (session.id === DRAFT_ID && draftEntry) {
            setTaskText(draftEntry.prompt === "(no prompt text)" ? "" : draftEntry.prompt);
            if (draftEntry.workspaceId && workspaces.some(w => w.id === draftEntry.workspaceId)) {
                setSelectedWorkspace(draftEntry.workspaceId);
                setDestinationWorkspace(draftEntry.workspaceId);
            }
            setContextItems(draftEntry.contextItems as any);
            setAttachedFiles(prev => {
                // Don't revoke the draft's previewUrls — they are the same
                // object references we're about to put back. Only revoke
                // URLs owned by attachments being replaced that aren't the
                // draft's.
                const draftSet = new Set(draftEntry._fullAttachments.map(a => a.previewUrl).filter(Boolean) as string[]);
                prev.forEach(f => {
                    if (f.previewUrl && !draftSet.has(f.previewUrl)) URL.revokeObjectURL(f.previewUrl);
                });
                return draftEntry._fullAttachments;
            });
            setRecentOpen(false);
            setDraftEntry(null);
            setLastRecentPickId(null);
            return;
        }

        // ── Capture current composer as a draft before overwriting ──
        // Only if the user has unsaved content AND that content wasn't
        // itself pulled in from a previous Recent pick (we don't want to
        // re-stash the same content back and forth as the user hops).
        const hasContent = taskText.trim().length > 0 || attachedFiles.length > 0 || contextItems.length > 0;
        if (hasContent && lastRecentPickId === null) {
            const wsName = workspaces.find(w => w.id === selectedWorkspace)?.name;
            const promptText = taskText.trim() || "(no prompt text)";
            setDraftEntry({
                id: DRAFT_ID,
                prompt: promptText,
                createdAt: new Date().toISOString(),
                workspaceId: selectedWorkspace || undefined,
                workspaceName: wsName,
                contextItems: contextItems.map(c => ({
                    name: c.name, type: c.type, id: c.id, workspaceId: c.workspaceId,
                })),
                attachments: attachedFiles.map(a => ({ name: a.name, kind: a.kind, mime: a.mime })),
                _fullAttachments: attachedFiles,
            });
        }
        setLastRecentPickId(session.id);

        // Populate text + metadata immediately from the lite row so the UI
        // feels snappy, then fetch the full session to pull back attachment
        // bytes (stripped from the list response to keep it cheap).
        setTaskText(session.prompt);
        if (session.workspaceId && workspaces.some(w => w.id === session.workspaceId)) {
            setSelectedWorkspace(session.workspaceId);
            setDestinationWorkspace(session.workspaceId);
        }
        setContextItems(session.contextItems);
        // Clear any existing attachments' preview URLs — we're replacing them.
        setAttachedFiles(prev => {
            prev.forEach(f => { if (f.previewUrl) URL.revokeObjectURL(f.previewUrl); });
            return [];
        });
        setRecentOpen(false);

        if (session.attachments.length === 0) return;
        setRecentRestoring(session.id);
        try {
            const fabricToken = await getFabricToken();
            const full: any = await api.getSession(session.id, { githubToken, fabricToken });
            const fullCtx = (full && typeof full === "object" ? full.context : null) || {};
            const rawAtts: any[] = Array.isArray(fullCtx.prompt_attachments)
                ? fullCtx.prompt_attachments
                : [];
            const restored: UiAttachment[] = [];
            for (const a of rawAtts) {
                const kind: AttachmentKind =
                    a.kind === "image" || a.kind === "pdf" ? a.kind : "text";
                const content = String(a.content || "");
                if (!content) continue;
                // For images, synthesize a preview URL from the data URI so
                // the existing thumbnail code keeps working without re-reading
                // a File object.
                let previewUrl: string | undefined;
                if (kind === "image" && content.startsWith("data:")) {
                    previewUrl = content;
                }
                // Size is best-effort: approximate from content length for
                // binaries (base64 expands ~4/3×); text is exact.
                const approxSize =
                    kind === "text"
                        ? new Blob([content]).size
                        : Math.max(0, Math.floor(content.length * 0.75));
                restored.push({
                    name: String(a.name || "attachment"),
                    size: approxSize,
                    kind,
                    mime: String(a.mime || (kind === "pdf" ? "application/pdf" : "")),
                    content,
                    previewUrl,
                });
            }
            setAttachedFiles(restored);
        } catch (e: any) {
            setUploadError(e.message || "Could not restore attachments from the selected prompt.");
        } finally {
            setRecentRestoring(null);
        }
    }

    // Close on outside click / Escape.
    useEffect(() => {
        if (!recentOpen) return undefined;
        function onDown(e: MouseEvent) {
            const t = e.target as Node;
            if (recentBtnRef.current?.contains(t)) return;
            const popover = document.getElementById("recent-prompts-popover");
            if (popover?.contains(t)) return;
            setRecentOpen(false);
        }
        function onKey(e: KeyboardEvent) {
            if (e.key === "Escape") { setRecentOpen(false); return; }
            const n = recentSessions?.length ?? 0;
            if (!n) return;
            if (e.key === "ArrowDown") { e.preventDefault(); recentIndexFromKeyboardRef.current = true; setRecentIndex(i => (i + 1) % n); }
            else if (e.key === "ArrowUp") { e.preventDefault(); recentIndexFromKeyboardRef.current = true; setRecentIndex(i => (i - 1 + n) % n); }
            else if (e.key === "Enter") {
                e.preventDefault();
                const s = recentSessions?.[recentIndex];
                if (s) pickRecentPrompt(s);
            }
        }
        document.addEventListener("mousedown", onDown);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("mousedown", onDown);
            document.removeEventListener("keydown", onKey);
        };
    }, [recentOpen, recentSessions, recentIndex]);

    // Close attachment preview overlay on Escape. Backdrop click is
    // handled inline on the backdrop element itself.
    useEffect(() => {
        if (!previewAttachment) return undefined;
        function onKey(e: KeyboardEvent) {
            if (e.key === "Escape") setPreviewAttachment(null);
        }
        document.addEventListener("keydown", onKey);
        return () => document.removeEventListener("keydown", onKey);
    }, [previewAttachment]);

    // Workspace preview modal: close on Escape, fetch items on open.
    useEffect(() => {
        if (!previewWorkspace) return undefined;
        function onKey(e: KeyboardEvent) {
            if (e.key === "Escape") setPreviewWorkspace(null);
        }
        document.addEventListener("keydown", onKey);
        return () => document.removeEventListener("keydown", onKey);
    }, [previewWorkspace]);

    // Fetch (or re-fetch) workspace items. Passed to WorkspacePreviewModal
    // as ``onRefresh`` so the header button forces a cache-busting re-fetch.
    async function loadPreviewItems(workspaceId: string, opts: { refresh?: boolean } = {}) {
        setPreviewWsLoading(true);
        setPreviewWsError(null);
        try {
            const githubToken = sessionStorage.getItem("github_token") || "";
            const fabricToken = await getFabricToken();
            const resp = await api.listWorkspaceItems(
                workspaceId,
                { githubToken, fabricToken, refresh: opts.refresh },
            );
            setPreviewWsItems(resp.items);
            setPreviewWsCapturedAt(resp.capturedAt);
        } catch (e: any) {
            setPreviewWsError(e?.message || "Failed to load workspace items.");
        } finally {
            setPreviewWsLoading(false);
        }
    }

    useEffect(() => {
        if (!previewWorkspace) {
            setPreviewWsItems(null);
            setPreviewWsError(null);
            setPreviewWsLoading(false);
            setPreviewWsCapturedAt(null);
            return undefined;
        }
        let cancelled = false;
        (async () => {
            setPreviewWsLoading(true);
            setPreviewWsError(null);
            setPreviewWsItems(null);
            setPreviewWsCapturedAt(null);
            try {
                const githubToken = sessionStorage.getItem("github_token") || "";
                const fabricToken = await getFabricToken();
                const resp = await api.listWorkspaceItems(
                    previewWorkspace.id,
                    { githubToken, fabricToken },
                );
                if (cancelled) return;
                setPreviewWsItems(resp.items);
                setPreviewWsCapturedAt(resp.capturedAt);
            } catch (e: any) {
                if (cancelled) return;
                setPreviewWsError(e?.message || "Failed to load workspace items.");
            } finally {
                if (!cancelled) setPreviewWsLoading(false);
            }
        })();
        return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [previewWorkspace]);

    /**
     * Trigger a browser download of the currently previewed attachment.
     *
     * Works for text/image/pdf by converting the attachment's content
     * (data-URI or raw text) into a ``Blob`` and driving a programmatic
     * ``<a download>`` click. Because the blob lives in our own origin,
     * this survives Fabric's sandboxed iframe: as long as the host frame
     * grants ``allow-downloads`` (which the Fabric workload host does)
     * the browser saves the file normally. If the host frame disallows
     * downloads we fall back to opening the blob in a new tab so the user
     * can save it from there.
     */
    async function downloadPreviewAttachment() {
        if (!previewAttachment) return;
        const src = previewAttachment.content;
        const name = previewAttachment.name || "download";
        const mime = previewAttachment.mime
            || (previewAttachment.kind === "text" ? "text/plain"
                : previewAttachment.kind === "image" ? "image/*"
                : previewAttachment.kind === "pdf" ? "application/pdf"
                : "application/octet-stream");
        console.log("[attachment-download] click", { name, kind: previewAttachment.kind });

        // The Fabric workload iframe sandbox blocks every in-frame save
        // path (``<a download>``, ``window.open``, ``showSaveFilePicker``
        // all fail with a SecurityError or silent "allow-downloads not
        // set" warning). The only path that escapes the sandbox is
        // ``workloadClient.navigation.openBrowserTab`` — and even that
        // has an allowlist and refuses URLs outside the workload's
        // trusted hosts (e.g. ``localhost`` in dev).
        //
        // Strategy:
        //   1. Mint a short-lived backend URL that serves the bytes
        //      with ``Content-Disposition: attachment`` (works from any
        //      top-level tab).
        //   2. Try ``openBrowserTab`` — works in production where the
        //      backend is on a trusted HTTPS domain.
        //   3. If Fabric refuses (dev, or any domain not on the
        //      tenant's allowlist), surface a small "Download ready"
        //      dialog. The user can copy-paste the URL into a fresh
        //      browser tab; the browser saves via Content-Disposition.
        let url: string;
        try {
            const fabricToken = await getFabricToken();
            url = await api.mintAttachmentDownloadUrl(
                name, mime, src, { fabricToken },
            );
            console.log("[attachment-download] minted url", url);
        } catch (e) {
            console.error("[attachment-download] mint failed", e);
            return;
        }

        try {
            const result = await workloadClient.navigation.openBrowserTab({ url });
            console.log("[attachment-download] openBrowserTab result", result);
            if (!result?.success) {
                throw new Error("openBrowserTab returned success=false");
            }
            // Success — native download flow is underway.
            return;
        } catch (e) {
            console.warn("[attachment-download] openBrowserTab failed, falling back to manual URL", e);
            setManualDownloadUrl(url);
        }
    }

    // Keep the popover anchored to the trigger button as the user scrolls
    // the page or resizes the window. Using `position: fixed` avoids being
    // clipped by the composer card's `overflow: hidden`; this listener just
    // keeps the popover visually stuck to the button.
    useEffect(() => {
        if (!recentOpen) return undefined;
        const onReflow = () => {
            const next = computeRecentPopoverPos();
            if (next) setRecentPos(next);
        };
        window.addEventListener("scroll", onReflow, true);
        window.addEventListener("resize", onReflow);
        return () => {
            window.removeEventListener("scroll", onReflow, true);
            window.removeEventListener("resize", onReflow);
        };
    }, [recentOpen]);

    // Keep the active item visible as the user arrow-navigates a long list.
    // Gated on ``recentIndexFromKeyboardRef`` so that hovering an item with
    // the mouse does NOT scroll the popover — the hovered item is already
    // under the cursor by definition.
    useEffect(() => {
        if (!recentOpen) return;
        if (!recentIndexFromKeyboardRef.current) return;
        recentIndexFromKeyboardRef.current = false;
        const el = document.getElementById(`recent-prompt-item-${recentIndex}`);
        el?.scrollIntoView({ block: "nearest" });
    }, [recentIndex, recentOpen]);

    // Lazy-load more rows when the sentinel enters the popover viewport.
    useEffect(() => {
        if (!recentOpen) return undefined;
        const root = recentScrollRef.current;
        const sentinel = recentSentinelRef.current;
        if (!root || !sentinel) return undefined;
        const io = new IntersectionObserver(
            (entries) => {
                for (const ent of entries) {
                    if (ent.isIntersecting && recentHasMore && !recentLoadingMore && !recentLoading) {
                        loadRecentSessions(false);
                    }
                }
            },
            { root, rootMargin: "64px", threshold: 0.01 },
        );
        io.observe(sentinel);
        return () => io.disconnect();
    // We rely on state deps rather than function identity — loadRecentSessions
    // is defined fresh each render but only reads state via closures, which is
    // safe because the observer is re-created whenever any of these change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [recentOpen, recentHasMore, recentLoadingMore, recentLoading, recentSessions?.length]);

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

    // When a plan arrives, scroll it into view. Uses a short rAF delay so
    // the section has actually rendered before we measure its position; a
    // second frame is cheap insurance against layout jank from late-loading
    // agent avatars / badges. Respects the user's reduced-motion preference.
    useEffect(() => {
        if (!plan || !planSectionRef.current) return undefined;
        const prefersReduced =
            typeof window !== "undefined" &&
            window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        const behavior: ScrollBehavior = prefersReduced ? "auto" : "smooth";
        const id = window.requestAnimationFrame(() => {
            planSectionRef.current?.scrollIntoView({ behavior, block: "start" });
        });
        return () => window.cancelAnimationFrame(id);
    }, [plan]);

    async function handleGeneratePlan() {
        if (!taskText.trim() || !selectedWorkspace) return;
        // Branch-out requires the source workspace to be git-connected.
        // The destination is always a child workspace derived from the
        // source + branch name — the user doesn't pick one separately.
        if (branchOut) {
            const src = workspaces.find(w => w.id === selectedWorkspace);
            if (src && src.git_connected === false) {
                setError("Branch-out requires the source workspace to have Fabric git integration enabled. Connect it to a repository, or pick a git-connected workspace.");
                return;
            }
        }
        setPlanning(true);
        setPlanningSnapshot(computePlanInputSignature());
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
                    destination_workspace_name: branchOut ? childWsName : undefined,
                    require_approvals: requireApprovals,
                    context_items: contextItems,
                },
                { githubToken, fabricToken },
                apiAttachments,
            );
            setSessionId(job.id);
            setPlan(job.plan);
            // Surface the just-created prompt in the Recent-prompts popover
            // immediately so the user sees their task reflected without
            // having to wait for a list reload. Dedup by id in case a
            // background refresh already inserted it.
            try {
                const lite = toRecentLite(job);
                setRecentSessions(prev => {
                    const base = prev ?? [];
                    return [lite, ...base.filter(s => s.id !== lite.id)];
                });
            } catch { /* best-effort — never break the main flow */ }
            // The saved session replaces the transient draft slot. Clearing
            // both prevents a stale draft from shadowing the freshly
            // persisted entry in the popover.
            setDraftEntry(null);
            setLastRecentPickId(null);
        } catch (e: any) {
            setError(e.message || "Plan generation failed");
        } finally {
            setPlanning(false);
            setPlanningSnapshot(null);
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
        // Warm the backend's per-(user, workspace) cache in the background
        // so the first chip-click feels instant. Best-effort — any failure
        // is surfaced only when the user actually opens the preview.
        (async () => {
            try {
                const githubToken = sessionStorage.getItem("github_token") || "";
                const fabricToken = await getFabricToken();
                api.warmWorkspaceItems(ws.id, { githubToken, fabricToken });
            } catch { /* ignore — best effort */ }
        })();
    }

    const wsName = workspaces.find(w => w.id === selectedWorkspace)?.name || selectedWorkspace;

    // Serialize every input that feeds the planner into a stable string.
    // Used to take a snapshot when planning starts and to detect whether the
    // user has since changed anything (see `planningDirty`).
    function computePlanInputSignature(): string {
        return JSON.stringify({
            taskText,
            selectedWorkspace,
            destinationWorkspace,
            branchOut,
            branchName: branchOut ? branchName : "",
            requireApprovals,
            contextItems: contextItems.map(c => ({
                id: c.id ?? "",
                name: c.name,
                type: c.type,
                workspaceId: c.workspaceId ?? "",
            })),
            attachedFiles: attachedFiles.map(f => ({
                name: f.name,
                size: f.size,
                kind: f.kind,
                mime: f.mime,
            })),
        });
    }

    // Only show the "plan in flight" warning when the user has actually
    // touched something since generation started.
    const planningDirty = useMemo(() => {
        if (!planning || planningSnapshot === null) return false;
        return computePlanInputSignature() !== planningSnapshot;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        planning, planningSnapshot,
        taskText, selectedWorkspace, destinationWorkspace,
        branchOut, branchName, requireApprovals,
        contextItems, attachedFiles,
    ]);

    // ── Global topbar search → cross-entity quick results ──
    // Active on the New Session page only. Matches recent sessions, workspaces
    // and (lazily-loaded) agent templates against the current search query.
    const { query: globalSearch, setQuery: setGlobalSearchQuery } = useSearch();
    const [agentsForSearch, setAgentsForSearch] = useState<any[] | null>(null);
    useEffect(() => {
        if (globalSearch.trim().length < 2) return undefined;
        if (agentsForSearch !== null) return undefined;
        let cancelled = false;
        (async () => {
            try {
                const data = await api.listAgentTemplates({ githubToken });
                if (!cancelled) setAgentsForSearch(Array.isArray(data) ? data : (data?.templates ?? []));
            } catch {
                if (!cancelled) setAgentsForSearch([]);
            }
        })();
        return () => { cancelled = true; };
    }, [globalSearch, agentsForSearch, githubToken]);

    // Recent sessions are normally only fetched when the user opens the
    // "Recent prompts" popover. As soon as they start typing in the global
    // search bar we kick off that fetch too so sessions appear in results.
    useEffect(() => {
        if (globalSearch.trim().length < 2) return;
        if (recentSessions !== null) return;
        if (recentLoading) return;
        loadRecentSessions(true);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [globalSearch]);

    const quickResults = useMemo(() => {
        const term = globalSearch.trim();
        if (term.length < 2) return null;
        // Fuzzy rank each category independently so we keep the top-N of
        // each. Unmatched entries are dropped by `fuzzyFilter`.
        const sessions = fuzzyFilter(term, recentSessions ?? [], s => [
            s.prompt,
            s.workspaceName,
            ...s.contextItems.flatMap(c => [c.name, c.type]),
            ...s.attachments.map(a => a.name),
        ]).slice(0, 6);
        const wsMatches = fuzzyFilter(term, workspaces, w => [w.name, w.id]).slice(0, 6);
        const agentMatches = fuzzyFilter(term, agentsForSearch ?? [], (a: any) => [
            a.name,
            a.display_name,
            a.description,
            ...(Array.isArray(a.tags) ? a.tags : []),
        ]).slice(0, 6);
        const total = sessions.length + wsMatches.length + agentMatches.length;
        return { term, sessions, workspaces: wsMatches, agents: agentMatches, total };
    }, [globalSearch, recentSessions, workspaces, agentsForSearch]);

    // ── Search-dropdown positioning ──
    // The dropdown is anchored to the topbar input (`#agenthub-topbar-search`)
    // so the UX matches any standard browser/app search bar: the input stays
    // put and results float beneath it instead of pushing page content down.
    const [searchPos, setSearchPos] = useState<{ top: number; left: number; width: number } | null>(null);
    useEffect(() => {
        if (!quickResults) { setSearchPos(null); return undefined; }
        function update() {
            const input = document.getElementById("agenthub-topbar-search");
            if (!input) return;
            const r = input.getBoundingClientRect();
            setSearchPos({ top: r.bottom + 6, left: r.left, width: r.width });
        }
        update();
        window.addEventListener("resize", update);
        window.addEventListener("scroll", update, true);
        return () => {
            window.removeEventListener("resize", update);
            window.removeEventListener("scroll", update, true);
        };
    }, [quickResults]);

    // Clear the search query when the dropdown item is picked or when the
    // user clicks outside / presses Escape — matches standard search UX.
    useEffect(() => {
        if (!quickResults) return undefined;
        function onKey(e: KeyboardEvent) {
            if (e.key === "Escape") setGlobalSearchQuery("");
        }
        function onClick(e: MouseEvent) {
            const t = e.target as HTMLElement | null;
            if (!t) return;
            if (t.closest(".compose-search-results")) return;
            if (t.closest("#agenthub-topbar-search")) return;
            setGlobalSearchQuery("");
        }
        document.addEventListener("keydown", onKey);
        document.addEventListener("mousedown", onClick);
        return () => {
            document.removeEventListener("keydown", onKey);
            document.removeEventListener("mousedown", onClick);
        };
    }, [quickResults, setGlobalSearchQuery]);

    return (
        <div className="compose-page">
            {/* ── Global search quick-results (topbar-driven, floating dropdown) ── */}
            {quickResults && searchPos && (
                <div
                    className="compose-search-results"
                    role="listbox"
                    aria-label="Search results"
                    style={{
                        position: "fixed",
                        top: `${searchPos.top}px`,
                        left: `${searchPos.left}px`,
                        width: `${searchPos.width}px`,
                    }}
                >
                    <header className="compose-search-results-head">
                        <span>
                            Search results for <strong>&ldquo;{quickResults.term}&rdquo;</strong>
                        </span>
                        <span className="compose-search-results-count">
                            {quickResults.total} match{quickResults.total === 1 ? "" : "es"}
                        </span>
                    </header>

                    {quickResults.total === 0 && (
                        <div className="compose-search-empty">
                            No matches in recent sessions, workspaces or agents.
                        </div>
                    )}

                    {quickResults.sessions.length > 0 && (
                        <div className="compose-search-group">
                            <div className="compose-search-group-title">Sessions</div>
                            {quickResults.sessions.map(s => (
                                <button
                                    key={`sess-${s.id}`}
                                    type="button"
                                    className="compose-search-row"
                                    onClick={() => {
                                        history.push(match.url.replace(/\/orchestrator$/, `/session/${s.id}`));
                                        setGlobalSearchQuery("");
                                    }}
                                    title={s.prompt}
                                >
                                    <ChatMultiple24Regular className="compose-search-row-icon" />
                                    <span className="compose-search-row-main">{s.prompt.slice(0, 90) || "(no prompt)"}</span>
                                    {s.workspaceName && (
                                        <span className="compose-search-row-meta">{s.workspaceName}</span>
                                    )}
                                </button>
                            ))}
                        </div>
                    )}

                    {quickResults.workspaces.length > 0 && (
                        <div className="compose-search-group">
                            <div className="compose-search-group-title">Workspaces</div>
                            {quickResults.workspaces.map(w => (
                                <button
                                    key={`ws-${w.id}`}
                                    type="button"
                                    className="compose-search-row"
                                    onClick={() => {
                                        setSelectedWorkspace(w.id);
                                        addWorkspaceContext({ id: w.id, name: w.name });
                                        setGlobalSearchQuery("");
                                    }}
                                    title={w.name}
                                >
                                    <DataUsage24Regular className="compose-search-row-icon" />
                                    <span className="compose-search-row-main">{w.name}</span>
                                </button>
                            ))}
                        </div>
                    )}

                    {quickResults.agents.length > 0 && (
                        <div className="compose-search-group">
                            <div className="compose-search-group-title">Agents &amp; Skills</div>
                            {quickResults.agents.map((a: any) => (
                                <button
                                    key={`agent-${a.id}`}
                                    type="button"
                                    className="compose-search-row"
                                    onClick={() => {
                                        history.push(match.url.replace(/\/orchestrator$/, "/agents"));
                                        setGlobalSearchQuery("");
                                    }}
                                    title={a.description || a.name}
                                >
                                    <Bot24Regular className="compose-search-row-icon" />
                                    <span className="compose-search-row-main">{a.display_name || a.name}</span>
                                    {a.description && (
                                        <span className="compose-search-row-meta">
                                            {String(a.description).slice(0, 60)}
                                        </span>
                                    )}
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            )}

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
                            {planning && planningDirty && (
                                // Shown while the plan is being generated in the background.
                                // We intentionally leave the composer editable so the user can
                                // keep refining their task while they wait — but any edits made
                                // here won't reach the in-flight plan generator. They'll take
                                // effect on the next "Generate Plan" click.
                                <div
                                    className="composer-planning-warn"
                                    role="status"
                                    aria-live="polite"
                                >
                                    <Warning20Regular />
                                    <span>
                                        A plan is being generated from your current input. Any
                                        edits you make now <strong>won't be reflected</strong> in
                                        the in-flight plan — regenerate to apply them.
                                    </span>
                                </div>
                            )}
                            <label className="composer-label" htmlFor="composer-task-text">NEW TASK DESCRIPTION</label>
                            <textarea
                                ref={composerRef}
                                id="composer-task-text"
                                name="taskText"
                                className="composer-textarea"
                                value={taskText}
                                onChange={(e) => { setTaskText(e.target.value); setLastRecentPickId(null); }}
                                placeholder="Automate the weekly ingestion of regional sales data from our OneLake raw zone, normalize the schema for Gold-layer reporting, and generate SQL-based views for the Finance dashboard."
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
                                    // Every pill is clickable. Workspace
                                    // pills open the workspace preview
                                    // modal. Fabric-item pills open the
                                    // same modal for their parent
                                    // workspace and auto-scroll to / flash
                                    // the specific row — giving items the
                                    // same "click to preview" experience
                                    // without ever leaving the sandbox.
                                    const canOpen = isWorkspace
                                        ? !!item.id
                                        : !!item.workspaceId;
                                    const openPreview = () => {
                                        if (isWorkspace && item.id) {
                                            setPreviewHighlightItemId(null);
                                            setPreviewWorkspace({ id: item.id, name: item.name });
                                            return;
                                        }
                                        if (item.workspaceId) {
                                            // Find the friendliest workspace
                                            // name we know about.
                                            const wsCtx = contextItems.find(c => c.type === "workspace" && c.id === item.workspaceId);
                                            const wsMeta = workspaces.find(w => w.id === item.workspaceId);
                                            setPreviewHighlightItemId(item.id || null);
                                            setPreviewWorkspace({
                                                id: item.workspaceId,
                                                name: wsCtx?.name || wsMeta?.name || "Workspace",
                                            });
                                        }
                                    };
                                    const humanType = isWorkspace ? "Workspace" : humanizeItemType(item.type);
                                    const tooltip = isWorkspace && item.id
                                        ? `${item.name} · Workspace · click to preview items`
                                        : canOpen
                                            ? `${item.name} · ${humanType} · click to preview`
                                            : `${item.name} · ${humanType}`;
                                    return (
                                        <span
                                            key={`${item.type}:${item.id || item.name}`}
                                            className={`ctx-pill ctx-pill--${pillVariant}${canOpen ? " ctx-pill--clickable" : ""}`}
                                            title={tooltip}
                                            role={canOpen ? "button" : undefined}
                                            tabIndex={canOpen ? 0 : undefined}
                                            onClick={canOpen ? openPreview : undefined}
                                            onKeyDown={
                                                canOpen
                                                    ? (e) => {
                                                        if (e.key === "Enter" || e.key === " ") {
                                                            e.preventDefault();
                                                            openPreview();
                                                        }
                                                    }
                                                    : undefined
                                            }
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
                                                onClick={(e) => { e.stopPropagation(); removeContext(item.name); }}
                                                aria-label={`Remove ${item.name}`}
                                            >
                                                <Dismiss24Regular />
                                            </button>
                                        </span>
                                    );
                                })}
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
                                            className={`ctx-pill ctx-pill--clickable ${kindClass}`}
                                            title={`${f.name} · ${sizeLabel} · click to preview`}
                                            role="button"
                                            tabIndex={0}
                                            onClick={() => setPreviewAttachment(f)}
                                            onKeyDown={(e) => {
                                                if (e.key === "Enter" || e.key === " ") {
                                                    e.preventDefault();
                                                    setPreviewAttachment(f);
                                                }
                                            }}
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
                                                onClick={(e) => { e.stopPropagation(); removeAttachedFile(f.name); }}
                                                aria-label={`Remove ${f.name}`}
                                            >
                                                <Dismiss16Regular />
                                            </button>
                                        </span>
                                    );
                                })}
                            </div>

                            {/* Add-buttons row — always rendered below the
                                pills so it stays put (doesn't get pushed
                                around) as the user adds more context. */}
                            <div className="composer-add-actions">
                                <button
                                    type="button"
                                    className="ctx-pill-add"
                                    onClick={addFabricItem}
                                    title="Attach a Fabric item as context"
                                >
                                    <Add20Regular /> Add item
                                </button>
                                <Menu>
                                    <MenuTrigger disableButtonEnhancement>
                                        <button
                                            type="button"
                                            className="ctx-pill-add"
                                            title="Attach a workspace as context"
                                        >
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
                                <input
                                    ref={fileInputRef}
                                    id="composer-file-input"
                                    name="attachments"
                                    type="file"
                                    multiple
                                    accept="image/*,application/pdf,.txt,.md,.json,.yaml,.yml,.csv,.tsv,.log,.sql,.py,.js,.ts,.tsx,.jsx,.xml,.html,.cfg,.ini,.toml,text/*"
                                    style={{ display: "none" }}
                                    aria-label="Attach files"
                                    onChange={handleUploadFile}
                                />
                                <button
                                    type="button"
                                    className="ctx-pill-add"
                                    onClick={() => fileInputRef.current?.click()}
                                    title="Attach images, PDFs, or text files (10 MB per file, 25 MB total)"
                                >
                                    <Attach20Regular /> Attach files
                                </button>
                            </div>
                            {uploadError && (
                                <div className="composer-upload-error-row">
                                    <span className="composer-upload-error">{uploadError}</span>
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

                                {/* Workspace picker — always shown, relabeled when branch-out on */}
                                <div className="branchpanel-field branchpanel-field--full">
                                    <label htmlFor="composer-workspace-select">
                                        {branchOut ? "SOURCE WORKSPACE" : "WORKSPACE"}
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
                                                id="composer-workspace-select"
                                                name="workspace"
                                                value={selectedWorkspace}
                                                onChange={(e) => {
                                                    const v = e.target.value;
                                                    if (v === "__create__") {
                                                        // Sentinel — open the inline form instead
                                                        // of selecting a non-existent workspace.
                                                        setCreateWsOpen(true);
                                                        setCreateWsError(null);
                                                        return;
                                                    }
                                                    setSelectedWorkspace(v);
                                                }}
                                            >
                                                {workspaces
                                                    // When branch-out is on we filter to git-connected
                                                    // workspaces (Fabric branch-out requires git
                                                    // integration on the source). Off: show everything.
                                                    // Always keep the currently-selected workspace in
                                                    // the list, even if it wouldn't otherwise pass the
                                                    // filter — a native <select value={id}> whose
                                                    // value isn't among its <option>s silently falls
                                                    // back to the first option WITHOUT firing
                                                    // ``onChange``, which would drift the state from
                                                    // the display. Keeping the selected option
                                                    // rendered (but ``disabled``) lets the user see
                                                    // exactly which workspace is selected + why it
                                                    // isn't actionable.
                                                    .filter(w =>
                                                        !branchOut
                                                        || w.git_connected === true
                                                        || w.git_connected === null
                                                        || showUnsupportedSources
                                                        || w.id === selectedWorkspace,
                                                    )
                                                    .map(w => {
                                                        const notGit = w.git_connected === false;
                                                        const label = branchOut && w.git_branch
                                                            ? `${w.name} — ${w.git_branch}`
                                                            : branchOut && notGit
                                                                ? `${w.name} (no git integration)`
                                                                : w.name;
                                                        // Don't ``disabled`` the currently-selected
                                                        // option — browsers silently fall back to the
                                                        // first non-disabled <option> as the visible
                                                        // value when the selected one is disabled,
                                                        // which desynchronises the dropdown from React
                                                        // state. Picking is still blocked for other
                                                        // non-git options; the selected row carries
                                                        // the visual warning in the tree below.
                                                        const isSelected = w.id === selectedWorkspace;
                                                        return (
                                                            <option
                                                                key={w.id}
                                                                value={w.id}
                                                                disabled={branchOut && notGit && !isSelected}
                                                            >
                                                                {label}
                                                            </option>
                                                        );
                                                    })}
                                                {/* Sentinel option — acts like a dropdown command
                                                    “Create new workspace…”. Branch-out mode has its
                                                    own child-workspace flow so the sentinel is hidden
                                                    there. */}
                                                {!branchOut && (
                                                    <>
                                                        <option disabled>───────────</option>
                                                        <option value="__create__">
                                                            + Create new workspace…
                                                        </option>
                                                    </>
                                                )}
                                            </select>
                                        )}
                                        <ChevronDown16Regular className="select-trailicon" />
                                    </div>
                                    {branchOut && (() => {
                                        const hiddenCount = workspaces.filter(w => w.git_connected === false).length;
                                        if (hiddenCount === 0) return null;
                                        return (
                                            <button
                                                type="button"
                                                className={`workspace-hidden-toggle${showUnsupportedSources ? " is-on" : ""}`}
                                                onClick={() => setShowUnsupportedSources(v => !v)}
                                                title={showUnsupportedSources
                                                    ? "Hide workspaces that aren't git-connected"
                                                    : "Show workspaces without git integration (they'll appear disabled)"}
                                                aria-pressed={showUnsupportedSources}
                                            >
                                                {showUnsupportedSources
                                                    ? `Hide ${hiddenCount} non-git`
                                                    : `+${hiddenCount} hidden (no git)`}
                                            </button>
                                        );
                                    })()}
                                    {/* Inline create-workspace form. Opens when the user
                                        picks the “+ Create new workspace…” sentinel in the
                                        dropdown above. Branch-out has its own child-workspace
                                        flow so we hide it there. The old standalone “Create new
                                        workspace” link was removed — it cluttered the card and
                                        duplicated the dropdown affordance. */}
                                    {!branchOut && createWsOpen && (
                                        <div className="workspace-create-form" role="group" aria-label="Create workspace">
                                            <div className="select-wrap workspace-create-input">
                                                <PeopleTeam20Regular className="select-leadicon" />
                                                <input
                                                    id="composer-new-workspace-name"
                                                    name="newWorkspaceName"
                                                    type="text"
                                                    autoFocus
                                                    value={createWsName}
                                                    onChange={(e) => setCreateWsName(e.target.value)}
                                                    onKeyDown={(e) => {
                                                        if (e.key === "Enter") { e.preventDefault(); handleCreateWorkspace(); }
                                                        if (e.key === "Escape") { e.preventDefault(); setCreateWsOpen(false); setCreateWsError(null); }
                                                    }}
                                                    placeholder="New workspace name"
                                                    maxLength={200}
                                                    disabled={creatingWs}
                                                    spellCheck={false}
                                                    aria-label="New workspace name"
                                                />
                                            </div>
                                            <div className="workspace-create-actions">
                                                <button
                                                    type="button"
                                                    className="workspace-create-btn workspace-create-btn--primary"
                                                    onClick={handleCreateWorkspace}
                                                    disabled={creatingWs || !createWsName.trim()}
                                                >
                                                    {creatingWs ? <Spinner size="tiny" /> : <Checkmark24Regular />}
                                                    {creatingWs ? "Creating…" : "Create"}
                                                </button>
                                                <button
                                                    type="button"
                                                    className="workspace-create-btn"
                                                    onClick={() => { setCreateWsOpen(false); setCreateWsError(null); }}
                                                    disabled={creatingWs}
                                                >
                                                    Cancel
                                                </button>
                                            </div>
                                            {createWsError && (
                                                <div className="workspace-create-error" role="alert">
                                                    <Warning20Regular />
                                                    <span>{createWsError}</span>
                                                    <a
                                                        href="https://app.fabric.microsoft.com/groups/me/list"
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        className="workspace-create-openfabric"
                                                        onClick={(e) => {
                                                            // Try the workload SDK first (opens in the
                                                            // host's tab-group without popup-blocker
                                                            // issues). If it works, prevent the default
                                                            // anchor navigation. If it throws or the SDK
                                                            // isn't available, let the browser follow
                                                            // the href normally — sandboxed iframes with
                                                            // allow-popups honour target="_blank".
                                                            try {
                                                                const p = workloadClient?.navigation?.openBrowserTab?.({
                                                                    url: "https://app.fabric.microsoft.com/groups/me/list",
                                                                });
                                                                if (p && typeof (p as any).then === "function") {
                                                                    e.preventDefault();
                                                                    (p as Promise<unknown>).catch(() => {
                                                                        window.open("https://app.fabric.microsoft.com/groups/me/list", "_blank", "noopener,noreferrer");
                                                                    });
                                                                }
                                                            } catch {
                                                                /* fall through to default anchor nav */
                                                            }
                                                        }}
                                                    >
                                                        Open in Fabric
                                                    </a>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                    {workspacesError && (
                                        <div className="workspaces-error" role="alert">
                                            <Warning20Regular />
                                            <span className="workspaces-error-msg">{workspacesError}</span>
                                            <button
                                                type="button"
                                                className="workspaces-error-retry"
                                                onClick={() => loadWorkspaces(true)}
                                                disabled={loadingWorkspaces}
                                            >
                                                Retry
                                            </button>
                                        </div>
                                    )}
                                </div>

                                {/* Branch-out tree — child workspace under source (explorer-inspired) */}
                                {branchOut && (() => {
                                    const src = workspaces.find(w => w.id === selectedWorkspace);
                                    const srcName = src?.name || "Source workspace";
                                    const srcNotGit = src?.git_connected === false;
                                    return (
                                    <div className={`composer-branchtree${srcNotGit ? " is-unsupported" : ""}`} role="tree" aria-label="Branch-out child workspace">
                                        <div className="branchtree-root" role="treeitem" aria-expanded="true">
                                            <span className="branchtree-chevron" aria-hidden="true">
                                                <ChevronDown16Regular />
                                            </span>
                                            <PeopleTeam20Regular className="branchtree-icon" />
                                            <span className={`branchtree-label${srcNotGit ? " is-disabled" : ""}`}>
                                                {srcName}
                                                {srcNotGit && (
                                                    <span className="branchtree-badge" aria-label="Not supported">
                                                        no git
                                                    </span>
                                                )}
                                            </span>
                                        </div>
                                        <div className="branchtree-children" role="group">
                                            <div className="branchtree-child" role="treeitem">
                                                <span className="branchtree-connector" aria-hidden="true" />
                                                <PeopleTeam20Regular className="branchtree-icon branchtree-icon--child" />
                                                <div className="branchtree-field">
                                                    <div className="branchtree-field-labelrow">
                                                        <span className="branchtree-field-label">Destination workspace</span>
                                                        {childWsNameTouched && (
                                                            <div className="branchtree-field-meta">
                                                                <span
                                                                    className="branchtree-ai-hint branchtree-ai-hint--manual"
                                                                    title="You edited this name. AI suggestions won't overwrite it."
                                                                >
                                                                    manually edited
                                                                </span>
                                                                {childWsSuffix && (
                                                                    <button
                                                                        type="button"
                                                                        className="branchtree-reset-btn"
                                                                        onClick={() => {
                                                                            // Always re-ask the LLM using the
                                                                            // current prompt + context so the
                                                                            // regenerated name reflects any
                                                                            // changes the user has made since
                                                                            // the last suggestion.
                                                                            void fetchBranchSuggestions({ branch: false, workspace: true });
                                                                        }}
                                                                        disabled={suggestLoading}
                                                                        title="Regenerate this name with AI using the latest prompt"
                                                                        aria-label="Regenerate destination workspace name with AI"
                                                                    >
                                                                        <ArrowClockwise16Regular /> Regenerate with AI
                                                                    </button>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                    {suggestLoading && !childWsNameTouched ? (
                                                        <div
                                                            className="branchtree-child-name branchtree-child-name--loading"
                                                            role="status"
                                                            aria-live="polite"
                                                        >
                                                            <Sparkle24Regular className="branchtree-loading-icon" />
                                                            <span className="branchtree-loading-text">Generating name…</span>
                                                        </div>
                                                    ) : (
                                                        <input
                                                            id="composer-branch-child-workspace"
                                                            name="branchChildWorkspace"
                                                            type="text"
                                                            className="branchtree-child-name branchtree-child-name--editable"
                                                            value={childWsName}
                                                            onChange={(e) => {
                                                                setChildWsName(e.target.value);
                                                                setChildWsNameTouched(true);
                                                            }}
                                                            placeholder="child-workspace-name"
                                                            aria-label="Destination workspace name"
                                                            spellCheck={false}
                                                            disabled={srcNotGit}
                                                        />
                                                    )}
                                                </div>
                                            </div>
                                            <div className="branchtree-child" role="treeitem">
                                                <span className="branchtree-connector" aria-hidden="true" />
                                                <BranchFork20Regular className="branchtree-icon branchtree-icon--child" />
                                                <div className="branchtree-field">
                                                    <div className="branchtree-field-labelrow">
                                                        <span className="branchtree-field-label">Git branch</span>
                                                        {branchNameTouched && (
                                                            <div className="branchtree-field-meta">
                                                                <span
                                                                    className="branchtree-ai-hint branchtree-ai-hint--manual"
                                                                    title="You edited this name. AI suggestions won't overwrite it."
                                                                >
                                                                    manually edited
                                                                </span>
                                                                {aiBranchName && (
                                                                    <button
                                                                        type="button"
                                                                        className="branchtree-reset-btn"
                                                                        onClick={() => {
                                                                            void fetchBranchSuggestions({ branch: true, workspace: false });
                                                                        }}
                                                                        disabled={suggestLoading}
                                                                        title="Regenerate this name with AI using the latest prompt"
                                                                        aria-label="Regenerate git branch name with AI"
                                                                    >
                                                                        <ArrowClockwise16Regular /> Regenerate with AI
                                                                    </button>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                    {suggestLoading && !branchNameTouched ? (
                                                        <div
                                                            className="branchtree-child-name branchtree-child-name--loading"
                                                            role="status"
                                                            aria-live="polite"
                                                        >
                                                            <Sparkle24Regular className="branchtree-loading-icon" />
                                                            <span className="branchtree-loading-text">Generating name…</span>
                                                        </div>
                                                    ) : (
                                                        <input
                                                            id="composer-branch-child-name"
                                                            name="branchChildName"
                                                            type="text"
                                                            className="branchtree-child-name branchtree-child-name--editable"
                                                            value={branchName}
                                                            onChange={(e) => {
                                                                setBranchName(e.target.value);
                                                                setBranchNameTouched(true);
                                                            }}
                                                            placeholder="feature/short-description"
                                                            aria-label="Git branch name"
                                                            spellCheck={false}
                                                            disabled={srcNotGit}
                                                        />
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                        {srcNotGit ? (
                                            <p className="branchtree-info branchtree-info--warn" role="alert">
                                                <Warning20Regular />
                                                <span>
                                                    <b>{srcName}</b> isn't git-connected, so branch-out
                                                    isn't supported. Connect this workspace to a Fabric
                                                    git repository, or pick a git-connected source.
                                                </span>
                                            </p>
                                        ) : (
                                            <p className="branchtree-info">
                                                <Info16Regular />
                                                A new child workspace will be created from the source. Merge back when ready.
                                            </p>
                                        )}
                                    </div>
                                    );
                                })()}

                                {/* Actions row */}
                                <div className="composer-actions">
                                    <div className="composer-actions-left">
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
                                                    className={`recent-prompts-popover recent-prompts-popover--${recentPos?.placement ?? "above"}`}
                                                    role="listbox"
                                                    style={recentPos ? {
                                                        top: `${recentPos.top}px`,
                                                        left: `${recentPos.left}px`,
                                                        width: `${recentPos.width}px`,
                                                        maxHeight: `${recentPos.maxHeight}px`,
                                                    } : undefined}
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
                                                    {!recentLoading && !recentError && recentSessions && recentSessions.length === 0 && !draftEntry && (
                                                        <div className="recent-prompts-empty">
                                                            No previous tasks yet.<br />
                                                            Your prompts will appear here after your first session.
                                                        </div>
                                                    )}
                                                    {!recentLoading && !recentError && ((recentSessions && recentSessions.length > 0) || draftEntry) && (
                                                        <>
                                                            <div className="recent-prompts-search">
                                                                <Search20Regular className="recent-prompts-search-icon" />
                                                                <input
                                                                    id="recent-prompts-search-input"
                                                                    name="recentPromptsSearch"
                                                                    type="text"
                                                                    className="recent-prompts-search-input"
                                                                    placeholder="Search your prompts, workspaces, attachments…"
                                                                    value={recentFilter}
                                                                    onChange={(e) => setRecentFilter(e.target.value)}
                                                                    autoComplete="off"
                                                                    autoFocus
                                                                />
                                                                {recentFilter && (
                                                                    <button
                                                                        type="button"
                                                                        className="recent-prompts-search-clear"
                                                                        onClick={() => setRecentFilter("")}
                                                                        aria-label="Clear filter"
                                                                        title="Clear"
                                                                    >
                                                                        <Dismiss16Regular />
                                                                    </button>
                                                                )}
                                                            </div>
                                                            <div className="recent-prompts-header">
                                                                {recentFilter.trim()
                                                                    ? `Showing prompts matching “${recentFilter.trim()}”`
                                                                    : "Click a prompt to reuse it — workspace, items and files are restored"}
                                                            </div>
                                                            <div className="recent-prompts-scroll" ref={recentScrollRef}>
                                                                <ul className="recent-prompts-list">
                                                                    {draftEntry && (
                                                                        // Transient in-memory snapshot of whatever the
                                                                        // user had typed before picking a Recent prompt.
                                                                        // Visually distinct (dashed border + badge) so
                                                                        // it's obvious this isn't a persisted session.
                                                                        // Lives only in component state — disappears on
                                                                        // restore, on successful plan generation, or on
                                                                        // page reload.
                                                                        <li
                                                                            key={draftEntry.id}
                                                                            role="option"
                                                                            aria-selected={false}
                                                                            className="recent-prompts-item recent-prompts-item--draft"
                                                                            onClick={() => pickRecentPrompt(draftEntry)}
                                                                            title={draftEntry.prompt}
                                                                        >
                                                                            <div className="recent-prompts-draft-badge">
                                                                                Unsaved draft · restore what you had
                                                                            </div>
                                                                            <div className="recent-prompts-text">{draftEntry.prompt}</div>
                                                                            <div className="recent-prompts-meta">
                                                                                <span>not stored — this session only</span>
                                                                                {draftEntry.workspaceName && (
                                                                                    <span className="recent-prompts-ws">· {draftEntry.workspaceName}</span>
                                                                                )}
                                                                            </div>
                                                                            {(draftEntry.contextItems.length > 0 || draftEntry.attachments.length > 0) && (
                                                                                <div className="recent-prompts-chips">
                                                                                    {draftEntry.contextItems.slice(0, 6).map((c, ci) => {
                                                                                        const t = (c.type || "").toLowerCase();
                                                                                        const variant = t === "workspace" ? "workspace"
                                                                                            : t === "lakehouse" ? "lakehouse"
                                                                                            : t === "warehouse" ? "warehouse"
                                                                                            : "item";
                                                                                        const Icon = variant === "workspace" ? PeopleTeam20Regular
                                                                                            : variant === "warehouse" ? BuildingFactory20Regular
                                                                                            : Database20Regular;
                                                                                        return (
                                                                                            <span
                                                                                                key={`dci-${ci}`}
                                                                                                className={`ctx-pill ctx-pill--${variant} recent-prompts-chip`}
                                                                                                title={variant === "workspace" ? `Workspace · ${c.name}` : `${c.name} · ${c.type}`}
                                                                                            >
                                                                                                <Icon />
                                                                                                <span>{c.name}</span>
                                                                                            </span>
                                                                                        );
                                                                                    })}
                                                                                    {draftEntry.attachments.slice(0, 4).map((a, ai) => {
                                                                                        const variant = a.kind === "image" ? "image"
                                                                                            : a.kind === "pdf" ? "pdf"
                                                                                            : "attachment";
                                                                                        return (
                                                                                            <span
                                                                                                key={`dat-${ai}`}
                                                                                                className={`ctx-pill ctx-pill--${variant} recent-prompts-chip`}
                                                                                                title={a.name}
                                                                                            >
                                                                                                {a.kind === "image" ? <Image20Regular /> :
                                                                                                 a.kind === "pdf" ? <DocumentPdf20Regular /> :
                                                                                                 <Document20Regular />}
                                                                                                <span>{a.name}</span>
                                                                                            </span>
                                                                                        );
                                                                                    })}
                                                                                </div>
                                                                            )}
                                                                        </li>
                                                                    )}
                                                                    {(() => {
                                                                        const sorted = (recentSessions ?? [])
                                                                            .slice()
                                                                            .sort((a, b) => {
                                                                                // Newest first (by created_at). Sessions
                                                                                // without a timestamp sink to the bottom.
                                                                                const ta = a.createdAt ? Date.parse(a.createdAt) : 0;
                                                                                const tb = b.createdAt ? Date.parse(b.createdAt) : 0;
                                                                                return tb - ta;
                                                                            });
                                                                        const filtered = recentFilter.trim()
                                                                            ? fuzzyFilter(recentFilter, sorted, s => [
                                                                                s.prompt,
                                                                                s.workspaceName,
                                                                                s.status,
                                                                                ...s.contextItems.flatMap(c => [c.name, c.type]),
                                                                                ...s.attachments.map(a => a.name),
                                                                            ])
                                                                            : sorted;
                                                                        if (recentFilter.trim() && filtered.length === 0) {
                                                                            return (
                                                                                <li className="recent-prompts-empty recent-prompts-empty--nomatch">
                                                                                    No prompts match “{recentFilter.trim()}”.
                                                                                    {recentHasMore && (
                                                                                        <> Scroll to load more, or try another term.</>
                                                                                    )}
                                                                                </li>
                                                                            );
                                                                        }
                                                                        return filtered.map((s, i) => {
                                                                        const statusInfo = recentStatusInfo(s.status);
                                                                        return (
                                                                        <li
                                                                            key={s.id}
                                                                            id={`recent-prompt-item-${i}`}
                                                                            role="option"
                                                                            aria-selected={i === recentIndex}
                                                                            className={`recent-prompts-item${i === recentIndex ? " is-active" : ""}${recentRestoring === s.id ? " is-restoring" : ""}`}
                                                                            onMouseEnter={() => setRecentIndex(i)}
                                                                            onClick={() => pickRecentPrompt(s)}
                                                                            title={s.prompt}
                                                                        >
                                                                            <div className="recent-prompts-text">{s.prompt}</div>
                                                                            <div className="recent-prompts-meta">
                                                                                {statusInfo && (
                                                                                    <span
                                                                                        className={`recent-prompts-status recent-prompts-status--${statusInfo.variant}`}
                                                                                        title={statusInfo.label}
                                                                                    >
                                                                                        {statusInfo.variant === "running" && (
                                                                                            <span className="recent-prompts-status-dot recent-prompts-status-dot--pulse" />
                                                                                        )}
                                                                                        {statusInfo.variant !== "running" && (
                                                                                            <span className="recent-prompts-status-dot" />
                                                                                        )}
                                                                                        <span>{statusInfo.label}</span>
                                                                                    </span>
                                                                                )}
                                                                                <span>{formatRelativeTime(s.createdAt)}</span>
                                                                                {s.workspaceName && (
                                                                                    <span className="recent-prompts-ws">· {s.workspaceName}</span>
                                                                                )}
                                                                                {recentRestoring === s.id && (
                                                                                    <span className="recent-prompts-restoring">
                                                                                        · <Spinner size="tiny" /> restoring…
                                                                                    </span>
                                                                                )}
                                                                            </div>
                                                                            {(s.contextItems.length > 0 || s.attachments.length > 0) && (
                                                                                <div className="recent-prompts-chips">
                                                                                    {s.contextItems.slice(0, 6).map((c, ci) => {
                                                                                        const t = (c.type || "").toLowerCase();
                                                                                        const variant = t === "workspace" ? "workspace"
                                                                                            : t === "lakehouse" ? "lakehouse"
                                                                                            : t === "warehouse" ? "warehouse"
                                                                                            : "item";
                                                                                        const Icon = variant === "workspace" ? PeopleTeam20Regular
                                                                                            : variant === "warehouse" ? BuildingFactory20Regular
                                                                                            : Database20Regular;
                                                                                        return (
                                                                                            <span
                                                                                                key={`ci-${ci}`}
                                                                                                className={`ctx-pill ctx-pill--${variant} recent-prompts-chip`}
                                                                                                title={variant === "workspace" ? `Workspace · ${c.name}` : `${c.name} · ${c.type}`}
                                                                                            >
                                                                                                <Icon />
                                                                                                <span>{c.name}</span>
                                                                                            </span>
                                                                                        );
                                                                                    })}
                                                                                    {s.contextItems.length > 6 && (
                                                                                        <span className="ctx-pill ctx-pill--item recent-prompts-chip">
                                                                                            +{s.contextItems.length - 6} more
                                                                                        </span>
                                                                                    )}
                                                                                    {s.attachments.slice(0, 4).map((a, ai) => {
                                                                                        const variant = a.kind === "image" ? "image"
                                                                                            : a.kind === "pdf" ? "pdf"
                                                                                            : "attachment";
                                                                                        return (
                                                                                            <span
                                                                                                key={`at-${ai}`}
                                                                                                className={`ctx-pill ctx-pill--${variant} recent-prompts-chip`}
                                                                                                title={a.name}
                                                                                            >
                                                                                                {a.kind === "image" ? <Image20Regular /> :
                                                                                                 a.kind === "pdf" ? <DocumentPdf20Regular /> :
                                                                                                 <Document20Regular />}
                                                                                                <span>{a.name}</span>
                                                                                            </span>
                                                                                        );
                                                                                    })}
                                                                                    {s.attachments.length > 4 && (
                                                                                        <span className="ctx-pill ctx-pill--attachment recent-prompts-chip">
                                                                                            +{s.attachments.length - 4} file{s.attachments.length - 4 === 1 ? "" : "s"}
                                                                                        </span>
                                                                                    )}
                                                                                </div>
                                                                            )}
                                                                        </li>
                                                                        );
                                                                    });
                                                                    })()}
                                                                </ul>
                                                                <div
                                                                    ref={recentSentinelRef}
                                                                    className="recent-prompts-sentinel"
                                                                    aria-hidden="true"
                                                                >
                                                                    {recentLoadingMore && (
                                                                        <>
                                                                            <Spinner size="tiny" /> <span>Loading more…</span>
                                                                        </>
                                                                    )}
                                                                    {!recentLoadingMore && !recentHasMore && recentSessions.length > RECENT_PAGE_SIZE && (
                                                                        <span>End of history</span>
                                                                    )}
                                                                </div>
                                                            </div>
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
                                        disabled={
                                            planning
                                            || !taskText.trim()
                                            || !selectedWorkspace
                                            || (
                                                branchOut
                                                && workspaces.find(w => w.id === selectedWorkspace)?.git_connected === false
                                            )
                                        }
                                    >
                                        {planning ? <Spinner size="tiny" /> : "Generate Plan"}
                                    </Button>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                {error && (
                    <div className="compose-error">
                        <Text size={300} style={{ color: "#d13438" }}>{error}</Text>
                    </div>
                )}

                {/* ── PLAN V2 REVIEW ── */}
                {plan && (
                    <>
                        <Divider />
                        <div ref={planSectionRef}>
                            <PlanView
                                plan={plan as Plan}
                                workspaceName={wsName}
                                approving={approving}
                                requireApprovals={requireApprovals}
                                onApprove={handleApprove}
                                onReject={handleReject}
                            />
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

            {previewAttachment && (
                <div
                    className="attachment-preview-backdrop"
                    onClick={() => setPreviewAttachment(null)}
                    role="presentation"
                >
                    <div
                        className="attachment-preview-dialog"
                        onClick={(e) => e.stopPropagation()}
                        role="dialog"
                        aria-modal="true"
                        aria-label={`Preview of ${previewAttachment.name}`}
                    >
                        <header className="attachment-preview-head">
                            <span className="attachment-preview-name" title={previewAttachment.name}>
                                {previewAttachment.name}
                            </span>
                            <button
                                type="button"
                                className="attachment-preview-download"
                                onClick={downloadPreviewAttachment}
                                aria-label={`Download ${previewAttachment.name}`}
                                title="Download"
                            >
                                <ArrowDownload20Regular />
                                <span>Download</span>
                            </button>
                            <button
                                type="button"
                                className="attachment-preview-close"
                                onClick={() => setPreviewAttachment(null)}
                                aria-label="Close preview"
                            >
                                <Dismiss24Regular />
                            </button>
                        </header>
                        <div className={`attachment-preview-body attachment-preview-body--${previewAttachment.kind}`}>
                            {previewAttachment.kind === "image" && (
                                <img
                                    src={previewAttachment.previewUrl || previewAttachment.content}
                                    alt={previewAttachment.name}
                                />
                            )}
                            {previewAttachment.kind === "pdf" && (
                                // PDF.js renders to a canvas inside the current origin, so
                                // Fabric's sandboxed iframe policy (which blocks inline
                                // `<iframe>` / `<object>` PDFs as well as `target=_blank`
                                // and `download=…` links) doesn't apply.
                                <PdfPreview
                                    source={previewAttachment.content}
                                    filename={previewAttachment.name}
                                />
                            )}
                            {previewAttachment.kind === "text" && (
                                <pre>{previewAttachment.content}</pre>
                            )}
                        </div>
                    </div>
                </div>
            )}

            {previewWorkspace && (
                <WorkspacePreviewModal
                    workspace={previewWorkspace}
                    items={previewWsItems}
                    capturedAt={previewWsCapturedAt}
                    loading={previewWsLoading}
                    error={previewWsError}
                    onRefresh={() => loadPreviewItems(previewWorkspace.id, { refresh: true })}
                    onClose={() => { setPreviewWorkspace(null); setPreviewHighlightItemId(null); }}
                    workloadClient={workloadClient}
                    highlightItemId={previewHighlightItemId}
                    autoOpenHighlighted={!!previewHighlightItemId}
                />
            )}

            {manualDownloadUrl && (
                // Fabric blocks `openBrowserTab` for URLs that aren't on
                // its per-workload allowlist (notably localhost in dev).
                // When that happens we can't trigger the browser's
                // download flow ourselves — the sandbox forbids every
                // in-frame path. Fall back to showing the user the
                // URL: they click copy, open a new tab, paste, and the
                // browser handles the rest via `Content-Disposition`.
                <div
                    className="attachment-preview-backdrop"
                    onClick={() => { setManualDownloadUrl(null); setManualDownloadCopied(false); }}
                    role="presentation"
                >
                    <div
                        className="manual-download-dialog"
                        onClick={(e) => e.stopPropagation()}
                        role="dialog"
                        aria-modal="true"
                        aria-label="Manual download"
                    >
                        <header className="attachment-preview-head">
                            <span className="attachment-preview-name">Download ready</span>
                            <button
                                type="button"
                                className="attachment-preview-close"
                                onClick={() => { setManualDownloadUrl(null); setManualDownloadCopied(false); }}
                                aria-label="Close"
                            >
                                <Dismiss24Regular />
                            </button>
                        </header>
                        <div className="manual-download-body">
                            <p>
                                Fabric's sandbox prevents this page from starting a
                                download directly. Copy the one-time link below and
                                paste it into a new browser tab — your browser will
                                save the file automatically.
                            </p>
                            <p className="manual-download-hint">
                                Link expires in 60 seconds and can only be used once.
                            </p>
                            <div className="manual-download-url-row">
                                <code className="manual-download-url">{manualDownloadUrl}</code>
                                <button
                                    type="button"
                                    className={`attachment-preview-download${manualDownloadCopied ? " is-copied" : ""}`}
                                    onClick={async () => {
                                        try {
                                            await navigator.clipboard.writeText(manualDownloadUrl);
                                            setManualDownloadCopied(true);
                                            window.setTimeout(() => setManualDownloadCopied(false), 2000);
                                        } catch (e) {
                                            console.warn("[manual-download] clipboard write failed", e);
                                        }
                                    }}
                                    aria-live="polite"
                                >
                                    {manualDownloadCopied ? <Checkmark24Regular /> : <ArrowDownload20Regular />}
                                    <span>{manualDownloadCopied ? "Copied!" : "Copy link"}</span>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
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

