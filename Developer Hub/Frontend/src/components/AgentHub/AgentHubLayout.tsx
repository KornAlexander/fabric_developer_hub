import React, { useMemo, useState, useCallback, useEffect, Suspense, lazy } from "react";
import { useHistory, useLocation, useRouteMatch } from "react-router-dom";
import "../../styles.scss";
import { EditorTabsProvider, useEditorTabs, type TabDescriptor, descriptorFromPath, makeNewSessionDescriptor, isReloadNavigation } from "./EditorTabs/EditorTabsContext";
import { EditorGroupsRoot } from "./EditorTabs/EditorGroupsRoot";
import { DRAG_NAVITEM_MIME } from "./EditorTabs/EditorTabsBar";
import { SideNavContextMenu } from "./EditorTabs/SideNavContextMenu";
import { useNavPreferences, resolveBehaviour, type NavItemId, type NavBehaviour } from "./EditorTabs/navPreferences";
import {
    Button,
    Text,
    Spinner,
    Body1,
    Tooltip,
    Dialog,
    DialogSurface,
    DialogTitle,
    DialogBody,
    DialogContent,
    DialogActions,
    Field,
    Input,
    Combobox,
    Option,
} from "@fluentui/react-components";
import {
    BrainCircuit24Regular,
    Bot24Regular,
    SignOut24Regular,
    QuestionCircle24Regular,
    Chat24Regular,
    Info24Regular,
    Alert24Regular,
    PersonCircle32Regular,
    Navigation24Regular,
    Dismiss24Regular,
    Wrench24Regular,
    AddCircle24Regular,
    Sparkle24Regular,
    ChatMultiple24Regular,
    PanelLeftContract24Regular,
    PanelLeftExpand24Regular,
    Search20Regular,
    Filter20Regular,
    Dismiss16Regular,
    ChevronRight16Regular,
    ChevronDown16Regular,
    Save24Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
// OrchestratorPage stays eager — it's the default landing route, so lazy
// loading it just adds a Suspense flash on first paint. Everything else is
// code-split into its own chunk via React.lazy below.
import { OrchestratorPage } from "./OrchestratorPage";

// ─── Lazy-loaded pages ────────────────────────────────────────────
// Each page becomes its own webpack chunk. `preload()` is attached so the
// existing nav-intent logic (hover / click-before-navigate) can trigger the
// chunk fetch before we actually mount the component — hiding latency.
function lazyWithPreload<T extends Record<string, any>>(
    factory: () => Promise<T>,
    exportName: keyof T,
) {
    const Component = lazy(() =>
        factory().then(m => ({ default: m[exportName] })),
    ) as React.LazyExoticComponent<React.ComponentType<any>> & {
        preload: () => Promise<T>;
    };
    Component.preload = factory;
    return Component;
}

const DashboardPage = lazyWithPreload(() => import("./DashboardPage"), "DashboardPage");
const AgentsPage = lazyWithPreload(() => import("./AgentsPage"), "AgentsPage");
const SettingsPage = lazyWithPreload(() => import("./SettingsPage"), "SettingsPage");
const AboutPage = lazyWithPreload(() => import("./AboutPage"), "AboutPage");
// Direct prop-driven variant of MissionControlPage used by the tabs
// system — lets non-active editor groups render a session by id without
// needing to own the URL via ``useParams``.
const MissionControlPageLazy = lazyWithPreload(() => import("./mission/MissionControlPage"), "MissionControlPage");
const PbiFixerPage = lazyWithPreload(
    () => import("../PbiFixer").then(m => ({ PbiFixerPage: m.PbiFixerPage })),
    "PbiFixerPage",
);
import { NAV_ITEMS as PBIFIXER_NAV_ITEMS, NAV_GROUPS as PBIFIXER_NAV_GROUPS, DEFAULT_NAV_KEY as PBIFIXER_DEFAULT_NAV, type NavKey as PbiFixerNavKey, type NavGroup as PbiFixerNavGroup } from "../PbiFixer";
import { useGitHubAuth, GitHubAuth } from "./useGitHubAuth";
import { ItemProvider, useItemContext } from "./ItemContext";
import { WORKLOAD_VERSION } from "../../version";
import { SearchProvider, useSearch, searchPlaceholderFor, isFilterScope, type SearchScope } from "./SearchContext";
import { callAuthAcquireAccessToken } from "../../controller/AgentHubController";
import * as api from "../../controller/AgentHubApi";
import {
    setPreloaded,
    setPending,
    getPending,
    type PreloadKey,
} from "./pagePreloadCache";

/** Max time we linger on the current page while prefetching the target. */
const NAV_PREFETCH_TIMEOUT_MS = 1000;

/** True on macOS — used to render the correct modifier glyph for keyboard
 *  shortcuts (⌘ on Apple, Ctrl everywhere else). Prefers the modern
 *  ``navigator.userAgentData`` API and falls back to ``navigator.platform``
 *  for older browsers. Evaluated once at module load; `navigator` is
 *  guaranteed in browser bundles. */
const IS_MAC: boolean = (() => {
    try {
        const uaData = (navigator as unknown as { userAgentData?: { platform?: string } }).userAgentData;
        const platform = uaData?.platform || navigator.platform || "";
        if (/mac/i.test(platform)) return true;
        // iPadOS 13+ masquerades as Mac; treat it like mac for shortcut glyphs.
        return /Mac|iPad|iPhone|iPod/i.test(navigator.userAgent || "");
    } catch {
        return false;
    }
})();

/** Human-readable "⌘B" / "Ctrl+B" string for the given base key. */
function modShortcut(key: string): string {
    return IS_MAC ? `⌘${key.toUpperCase()}` : `Ctrl+${key.toUpperCase()}`;
}

/** Maps a nav page id to the preload key for its data dependency. */
function preloadKeyFor(page: string): PreloadKey | null {
    if (page === "home" || page === "sessions") return "sessions";
    if (page === "agents") return "agents";
    return null;
}

/** Kick off the webpack chunk for a page so it's warm by the time we navigate. */
function preloadChunkFor(page: string): void {
    try {
        if (page === "home" || page === "sessions") DashboardPage.preload();
        else if (page === "agents") AgentsPage.preload();
        else if (page === "pbifixer") PbiFixerPage.preload();
        else if (page === "settings") SettingsPage.preload();
    } catch { /* preload failures are recoverable — Suspense will retry */ }
}

interface AgentHubLayoutProps {
    workloadClient: WorkloadClientAPI;
    itemObjectId?: string;
}

/** Breadcrumb label for the topbar — mirrors the design's per-page page title. */
function topbarBreadcrumbLabel(activePage: string): string {
    switch (activePage) {
        case "sessions": return "Sessions";
        case "sessiondetail": return "Session";
        case "newsession": return "New Session";
        case "agents": return "Agents and Skills";
        case "pbifixer": return "Power BI Fixer";
        default: return "Developer Hub";
    }
}

/** Breadcrumb icon for the topbar — mirrors the per-page sidebar icon. */
function TopbarBreadcrumbIcon({ activePage }: { activePage: string }) {
    const cls = "topbar-breadcrumb-icon";
    switch (activePage) {
        case "sessions":
        case "sessiondetail": return <ChatMultiple24Regular className={cls} />;
        case "newsession": return <Sparkle24Regular className={cls} />;
        case "agents":     return <Bot24Regular className={cls} />;
        case "pbifixer":   return <Wrench24Regular className={cls} />;
        default:           return <BrainCircuit24Regular className={cls} />;
    }
}

/**
 * Controlled topbar search input. Sits inside `SearchProvider` so it can call
 * `useSearch()`. Factored out of the main layout so the provider value can be
 * consumed without a second component layer.
 *
 * Two visual modes:
 *   - **search** (New Session): plain search with a magnifier icon. Opens
 *     a cross-entity results dropdown.
 *   - **filter** (Sessions / Agents): the input *filters the page in
 *     place*. Renders with a filter icon, a lightly tinted background and
 *     a "Filter" pill so users immediately see it behaves differently.
 */
function TopbarSearchInput() {
    const { query, setQuery, scope } = useSearch();
    const inputRef = React.useRef<HTMLInputElement | null>(null);
    // Auto-focus the filter bar when the user lands on a page where the
    // topbar acts as an in-page filter. Skipped on the New Session page so
    // we don't steal focus from the composer textarea.
    useEffect(() => {
        if (scope === "sessions" || scope === "agents") {
            // Defer one frame so the page's mount-time focus (if any) wins
            // for explicit inputs, and we only claim focus if nothing else
            // currently has it.
            const id = window.requestAnimationFrame(() => {
                const active = document.activeElement;
                const bodyIsActive = !active || active === document.body;
                if (bodyIsActive && inputRef.current) inputRef.current.focus();
            });
            return () => window.cancelAnimationFrame(id);
        }
        return undefined;
    }, [scope]);
    // Session detail is a focused single-item view (like GitHub/Linear
    // ticket pages) — no search/filter makes sense here, so we render
    // nothing and let the topbar collapse around it.
    if (scope === "sessiondetail") return null;
    const filterMode = isFilterScope(scope);
    const placeholder = searchPlaceholderFor(scope);
    return (
        <div className={`topbar-search-wrap${filterMode ? " topbar-search-wrap--filter" : ""}`}>
            {filterMode
                ? <Filter20Regular className="topbar-search-icon" aria-hidden="true" />
                : <Search20Regular className="topbar-search-icon" aria-hidden="true" />
            }
            {filterMode && (
                <span className="topbar-search-badge" aria-hidden="true">Filter</span>
            )}
            <input
                ref={inputRef}
                id="agenthub-topbar-search"
                name="topbarSearch"
                type="text"
                placeholder={placeholder}
                aria-label={placeholder}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoComplete="off"
            />
            {query && (
                <button
                    type="button"
                    className="topbar-search-clear"
                    onClick={() => setQuery("")}
                    aria-label="Clear"
                    title="Clear"
                >
                    <Dismiss16Regular />
                </button>
            )}
        </div>
    );
}

/**
 * Topbar Save / Close action group. Lives inside ``ItemProvider`` so it can
 * read + persist the AgentHub item via ``useItemContext``. Save persists to
 * the workspace (creating the item on first save with a name dialog); Close
 * navigates the host back to the workspace listing. Once saved, the item
 * shows up in the Fabric workspace alongside reports / lakehouses / etc.
 */
function TopbarItemActions({
    workloadClient,
    workspaceObjectId,
}: {
    workloadClient: WorkloadClientAPI;
    workspaceObjectId: string | null;
}) {
    const { itemObjectId, workspaceObjectId: ctxWorkspaceId, settings, createItem, saveSettings } = useItemContext();
    const auth = useGitHubAuth();
    const [saveOpen, setSaveOpen] = useState(false);
    const [name, setName] = useState("AgentHub");
    const [description, setDescription] = useState("");
    const [busy, setBusy] = useState(false);
    const [savedFlash, setSavedFlash] = useState(false);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);

    // v0.36 Option B: when there's no ?ws= URL param the dialog asks
    // the user to pick a workspace. We lazy-fetch the workspace list on
    // first dialog open so we don't waste a Fabric round-trip on every
    // page load.
    const [workspaces, setWorkspaces] = useState<Array<{ id: string; name: string }>>([]);
    const [workspacesLoading, setWorkspacesLoading] = useState(false);
    const [pickedWorkspaceId, setPickedWorkspaceId] = useState<string>("");
    const [pickedWorkspaceText, setPickedWorkspaceText] = useState<string>("");

    const effectiveWorkspaceId = workspaceObjectId || pickedWorkspaceId || "";

    const flashSaved = useCallback(() => {
        setSavedFlash(true);
        window.setTimeout(() => setSavedFlash(false), 1800);
    }, []);

    const ensureWorkspaces = useCallback(async () => {
        if (workspaceObjectId) return;
        if (workspaces.length || workspacesLoading) return;
        if (!auth.githubToken) return;
        setWorkspacesLoading(true);
        try {
            // The /api/workspaces backend endpoint requires a Fabric OBO
            // token (GitHub token alone returns "Invalid Fabric token").
            // Acquire one via the workload SDK — this runs in response
            // to the user clicking "Save to workspace…", so the consent
            // overlay (if any) shows in a context the user expects.
            const fabricScopes = [
                "https://api.fabric.microsoft.com/Workspace.Read.All",
                "https://api.fabric.microsoft.com/Item.Read.All",
            ];
            let fabricToken: string | undefined;
            try {
                const res = await workloadClient.auth.acquireAccessToken({
                    additionalScopesToConsent: fabricScopes,
                    claimsForConditionalAccessPolicy: "",
                });
                fabricToken = res?.token;
            } catch (e) {
                // Fall through — backend will reject with a clear error
                // surfaced via errorMsg below.
            }
            const data = await api.getWorkspaces({ githubToken: auth.githubToken, fabricToken });
            const list = (data?.workspaces ?? []).map((w: any) => ({ id: w.id, name: w.name }));
            list.sort((a, b) => a.name.localeCompare(b.name));
            setWorkspaces(list);
        } catch (e: any) {
            setErrorMsg(`Failed to load workspaces: ${e?.message || e}`);
        } finally {
            setWorkspacesLoading(false);
        }
    }, [workspaceObjectId, workspaces.length, workspacesLoading, auth.githubToken, workloadClient]);

    const handleSave = useCallback(async () => {
        setErrorMsg(null);
        if (!itemObjectId) {
            setSaveOpen(true);
            // Lazy-fetch workspaces only when the dialog actually opens
            // and only when we don't already have workspace context.
            void ensureWorkspaces();
            return;
        }
        if (!settings) return;
        setBusy(true);
        try {
            await saveSettings(settings);
            flashSaved();
        } catch (e: any) {
            setErrorMsg(String(e?.message || e));
        } finally {
            setBusy(false);
        }
    }, [itemObjectId, settings, saveSettings, flashSaved, ensureWorkspaces]);

    const formatErr = useCallback((e: any): string => {
        if (!e) return "Unknown error";
        if (typeof e === "string") return e;
        // Workload SDK rejections often have non-enumerable properties
        // (errorCode, message buried in nested objects, etc). First try
        // common shapes, then fall back to a deep dump including
        // non-enumerable own props.
        const tryFields = (o: any): string => {
            if (!o || typeof o !== "object") return "";
            if (typeof o.message === "string" && o.message) return o.message;
            if (o.error) {
                const r = tryFields(o.error);
                if (r) return r;
            }
            if (o.detail) return typeof o.detail === "string" ? o.detail : tryFields(o.detail);
            if (o.errorCode) return `[${o.errorCode}]`;
            return "";
        };
        const direct = tryFields(e);
        if (direct) return direct;
        try {
            // Include both enumerable and non-enumerable own props so we
            // surface the actual SDK exception payload.
            const dump: Record<string, any> = {};
            for (const k of Object.getOwnPropertyNames(e)) {
                try { dump[k] = (e as any)[k]; } catch { /* skip */ }
            }
            return JSON.stringify(dump);
        } catch {
            return String(e);
        }
    }, []);

    const handleConfirmCreate = useCallback(async () => {
        const trimmed = name.trim();
        if (!trimmed || !effectiveWorkspaceId) return;
        setBusy(true);
        setErrorMsg(null);
        try {
            await createItem(trimmed, description.trim() || undefined, effectiveWorkspaceId);
            setSaveOpen(false);
            flashSaved();
        } catch (e: any) {
            console.error("[AgentHub Save] createItem failed", e);
            setErrorMsg(formatErr(e));
        } finally {
            setBusy(false);
        }
    }, [name, description, effectiveWorkspaceId, createItem, flashSaved, formatErr]);

    const handleClose = useCallback(async () => {
        // Prefer the workspace id known to ItemContext (which remembers
        // the workspace picked in the Save dialog) over the prop, which
        // is null when AgentHub was opened from the generic launcher.
        const targetWs = ctxWorkspaceId || workspaceObjectId;
        try {
            if (targetWs) {
                await workloadClient.navigation.navigate("host", {
                    path: `/groups/${targetWs}/list`,
                });
                return;
            }
            // No workspace context at all — go to the workspaces list.
            await workloadClient.navigation.navigate("host", { path: "/groups" });
        } catch {
            try { window.history.back(); } catch { /* no-op */ }
        }
    }, [workloadClient, workspaceObjectId, ctxWorkspaceId]);

    return (
        <div
            className="agenthub-topbar-actions"
            style={{ display: "flex", alignItems: "center", gap: 8, marginRight: 8 }}
        >
            {savedFlash && (
                <span
                    aria-live="polite"
                    style={{
                        fontSize: 12,
                        color: "#107c10",
                        fontWeight: 600,
                        whiteSpace: "nowrap",
                    }}
                >
                    Saved ✓
                </span>
            )}
            <Button
                appearance="primary"
                size="small"
                icon={<Save24Regular />}
                disabled={busy}
                onClick={handleSave}
                title={itemObjectId ? "Save settings to the workspace item" : "Save this AgentHub session as a workspace item"}
            >
                {itemObjectId ? "Save" : "Save to workspace…"}
            </Button>
            <Button
                appearance="subtle"
                size="small"
                icon={<Dismiss24Regular />}
                onClick={handleClose}
                title="Close and return to the workspace"
            >
                Close
            </Button>
            <Dialog
                open={saveOpen}
                onOpenChange={(_, d) => { if (!busy) setSaveOpen(d.open); }}
            >
                <DialogSurface>
                    <DialogBody>
                        <DialogTitle>Save to workspace</DialogTitle>
                        <DialogContent>
                            <div style={{ display: "flex", flexDirection: "column", gap: 12, paddingTop: 4 }}>
                                <Field label="Name" required>
                                    <Input
                                        value={name}
                                        onChange={(_, d) => setName(d.value)}
                                        disabled={busy}
                                        autoFocus
                                    />
                                </Field>
                                <Field label="Description">
                                    <Input
                                        value={description}
                                        onChange={(_, d) => setDescription(d.value)}
                                        disabled={busy}
                                    />
                                </Field>
                                {!workspaceObjectId && (
                                    <Field
                                        label="Workspace"
                                        required
                                        hint={workspacesLoading ? "Loading workspaces…" : "AgentHub will be saved as an item in this workspace."}
                                    >
                                        <Combobox
                                            placeholder={workspacesLoading ? "Loading…" : "Pick a workspace"}
                                            value={pickedWorkspaceText}
                                            selectedOptions={pickedWorkspaceId ? [pickedWorkspaceId] : []}
                                            disabled={busy || workspacesLoading}
                                            onOptionSelect={(_, d) => {
                                                if (d.optionValue) {
                                                    setPickedWorkspaceId(d.optionValue);
                                                    setPickedWorkspaceText(d.optionText || "");
                                                }
                                            }}
                                            onChange={(e) => setPickedWorkspaceText((e.target as HTMLInputElement).value)}
                                        >
                                            {workspaces.map((w) => (
                                                <Option key={w.id} value={w.id} text={w.name}>{w.name}</Option>
                                            ))}
                                        </Combobox>
                                    </Field>
                                )}
                                {errorMsg && (
                                    <Text size={200} style={{ color: "#a4262c" }}>{errorMsg}</Text>
                                )}
                            </div>
                        </DialogContent>
                        <DialogActions>
                            <Button
                                appearance="secondary"
                                onClick={() => setSaveOpen(false)}
                                disabled={busy}
                            >
                                Cancel
                            </Button>
                            <Button
                                appearance="primary"
                                onClick={handleConfirmCreate}
                                disabled={busy || !name.trim() || !effectiveWorkspaceId}
                                icon={busy ? <Spinner size="tiny" /> : <Save24Regular />}
                            >
                                Save
                            </Button>
                        </DialogActions>
                    </DialogBody>
                </DialogSurface>
            </Dialog>
        </div>
    );
}

// ── Wrapper component ────────────────────────────────────────────
// Splits the layout into an unauthenticated **gate** and an authenticated
// **body**. Without this split, the body's ~20 hooks were declared AFTER
// an early ``if (!auth.githubToken) return <gate/>``. When the GitHub
// device-flow completed, ``auth.githubToken`` flipped null → string,
// making React run far MORE hooks on the second render than the first
// (Rules of Hooks violation → minified React error #310). React then
// unmounted the entire tree, leaving an empty white iframe ("grey
// screen") that only recovered after a full page reload — exactly the
// bug B5 in the PBI Fixer PLAN.md. By moving the gate into its own
// component, the body never mounts until auth is present, so its hook
// list is invariant for the lifetime of the component.
export function AgentHubLayout(props: AgentHubLayoutProps) {
    const auth = useGitHubAuth();
    if (!auth.githubToken) {
        return <AgentHubAuthGate auth={auth} workloadClient={props.workloadClient} />;
    }
    return <AgentHubLayoutAuthed {...props} auth={auth} />;
}

function AgentHubAuthGate({ auth, workloadClient }: { auth: GitHubAuth; workloadClient: WorkloadClientAPI }) {
    return (
        <div className="agenthub-root">
            <div className="agenthub-auth-gate">
                <BrainCircuit24Regular style={{ fontSize: 48, color: "#0078d4" }} />
                <Text size={700} weight="bold">Developer Hub</Text>
                <Body1 style={{ color: "#605e5c", textAlign: "center" }}>
                    Sign in with GitHub to access the Agent Dashboard.
                    <br />A GitHub Copilot subscription is required.
                </Body1>

                {auth.deviceFlow ? (
                    <div className="agenthub-device-flow">
                        <Body1>Enter this code at GitHub:</Body1>
                        <code className="device-code-display">{auth.deviceFlow.userCode}</code>
                        {auth.codeCopied && (
                            <Text size={200} style={{ color: "#0ea50e" }}>Code copied to clipboard automatically</Text>
                        )}
                        <Button appearance="subtle" size="small" onClick={auth.copyCode}>
                            Copy code again
                        </Button>
                        <a
                            href={auth.deviceFlow.verificationUri}
                            target="_blank"
                            rel="noreferrer noopener"
                            className="github-verify-link"
                            onClick={async (e) => {
                                e.preventDefault();
                                try {
                                    await workloadClient.navigation.openBrowserTab({
                                        url: auth.deviceFlow!.verificationUri,
                                        queryParams: {},
                                    });
                                } catch {
                                    window.open(auth.deviceFlow!.verificationUri, '_blank', 'noopener,noreferrer');
                                }
                            }}
                        >
                            Open {auth.deviceFlow.verificationUri}
                        </a>
                        {auth.isPolling && (
                            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
                                <Spinner size="tiny" />
                                <Text size={200} style={{ color: "#605e5c" }}>Waiting for authorization...</Text>
                            </div>
                        )}
                    </div>
                ) : (
                    <Button appearance="primary" size="large" onClick={auth.startDeviceFlow}>
                        Sign in with GitHub
                    </Button>
                )}
            </div>
        </div>
    );
}

function AgentHubLayoutAuthed({ workloadClient, itemObjectId: routeItemObjectId, auth }: AgentHubLayoutProps & { auth: GitHubAuth }) {
    const history = useHistory();
    const location = useLocation();
    const match = useRouteMatch();

    // Extract workspaceObjectId from ?ws= query param (set by index.worker.ts).
    // Recompute on every URL change because the workload SDK navigates to
    // the bootstrap path (which carries ?ws=...) AFTER the React app mounts.
    // Also tolerate a malformed double-? URL (Fabric host occasionally appends
    // ?experience=... to a URL that already has a query string), by stripping
    // anything after a stray '?' inside the ws value.
    const workspaceObjectId = useMemo(() => {
        const search = window.location.search || "";
        const params = new URLSearchParams(search);
        let raw = params.get("ws")
            || sessionStorage.getItem("agenthub_workspace_id")
            || sessionStorage.getItem("workspace_id")
            || "";
        if (raw.includes("?")) raw = raw.split("?")[0];
        if (raw.includes("&")) raw = raw.split("&")[0];
        const trimmed = raw.trim();
        return trimmed || null;
        // location.search & location.pathname are intentional deps so the value
        // refreshes on every navigation.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [location.search, location.pathname]);

    const currentPath = history.location.pathname;
    let activePage = "newsession";
    if (currentPath.includes("/orchestrator")) activePage = "newsession";
    else if (currentPath.includes("/session/")) activePage = "sessiondetail";
    else if (currentPath.includes("/sessions") || currentPath.includes("/home")) activePage = "sessions";
    else if (currentPath.includes("/agents")) activePage = "agents";
    else if (currentPath.includes("/pbifixer")) activePage = "pbifixer";

    function nav(page: string) {
        // Legacy history-based navigator retained for callers that
        // haven't migrated to the tabs API yet. Kept internal to the
        // layout — prefer ``handleNavClick`` in ``AgentHubShell``.
        history.push(`${match.url}/${page}`);
    }
    void nav;

    // ── Background workspace preload (fire-and-forget) ────────────
    // Once GitHub auth lands, ask the backend to warm the per-user workspace
    // cache so the workspace selector is instant on first navigation.
    //
    // IMPORTANT: do NOT call ``acquireAccessToken`` here. Doing so kicks the
    // workload host into showing its consent / "Additional authentication
    // required" overlay the very moment the GitHub device-flow completes —
    // which the user perceives as the page going grey and unresponsive. The
    // overlay only resolves after a manual reload, when it re-shows in
    // proper context. By skipping the Fabric token in the preload we let the
    // first real Fabric-backed navigation surface the consent dialog at the
    // right time. ``preloadWorkspaces`` is a no-op without a Fabric token.
    useEffect(() => {
        if (!auth.githubToken) return undefined;
        let cancelled = false;
        (async () => {
            if (cancelled) return;
            await api.preloadWorkspaces({ githubToken: auth.githubToken! });
        })();
        return () => { cancelled = true; };
    }, [auth.githubToken]);

    // ── Main layout (sidebar + top bar + content) ─────────────────
    const [sidebarOpen, setSidebarOpen] = useState(false);
    const toggleSidebar = useCallback(() => setSidebarOpen(prev => !prev), []);
    const closeSidebar = useCallback(() => setSidebarOpen(false), []);

    // Collapsed (icon-rail) state — persisted in localStorage so it carries across pages.
    const SIDEBAR_COLLAPSE_KEY = "agenthub.sidebar.collapsed";
    const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
        try { return localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === "1"; } catch { return false; }
    });
    const toggleCollapsed = useCallback(() => {
        setSidebarCollapsed(prev => {
            const next = !prev;
            try { localStorage.setItem(SIDEBAR_COLLAPSE_KEY, next ? "1" : "0"); } catch { /* ignore */ }
            return next;
        });
    }, []);
    useEffect(() => {
        const onStorage = (e: StorageEvent) => {
            if (e.key === SIDEBAR_COLLAPSE_KEY) setSidebarCollapsed(e.newValue === "1");
        };
        window.addEventListener("storage", onStorage);
        return () => window.removeEventListener("storage", onStorage);
    }, []);
    // Ctrl/Cmd+B — standard editor-style shortcut (VS Code, GitHub, Notion
    // all ship this). Lets power users collapse the rail without leaving
    // the keyboard. We ignore the shortcut when a text field is focused so
    // it doesn't fire while someone is typing "b" in the composer.
    useEffect(() => {
        function onKey(e: KeyboardEvent) {
            const mod = e.ctrlKey || e.metaKey;
            if (!mod || e.key.toLowerCase() !== "b") return;
            const tgt = e.target as HTMLElement | null;
            const tag = tgt?.tagName;
            if (tag === "INPUT" || tag === "TEXTAREA" || tgt?.isContentEditable) return;
            e.preventDefault();
            toggleCollapsed();
        }
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [toggleCollapsed]);

    // When the user clicks a sidebar item, kick off the target page's data
    // fetch *before* we route-change. We wait for it to finish, capped at
    // NAV_PREFETCH_TIMEOUT_MS (1s). Fast loads therefore switch straight
    // from the current page to the fully-populated target with no skeleton
    // in between. Slow loads still flip over after the cap and show the
    // skeleton + "still loading" hint.
    //
    // The state flag itself is kept (the setter is used to gate the
    // transition) even though the visual progress indicator has been
    // removed per UX preference. The leading underscore tells the linter
    // the binding is intentionally unread.
    const [_navPending, setNavPending] = useState(false);
    void _navPending;

    const startPreload = useCallback(
        (key: PreloadKey): Promise<unknown> => {
            const existing = getPending(key);
            if (existing) return existing;
            const githubToken = auth.githubToken!;
            let fetchPromise: Promise<unknown>;
            if (key === "sessions") {
                fetchPromise = (async () => {
                    let fabricToken: string | undefined;
                    try {
                        const t = await callAuthAcquireAccessToken(workloadClient);
                        fabricToken = t.token;
                    } catch { /* best-effort */ }
                    const data = await api.listSessions({ githubToken, fabricToken });
                    setPreloaded(key, data);
                    return data;
                })();
            } else {
                fetchPromise = (async () => {
                    const [tpls, configs] = await Promise.all([
                        api.listAgentTemplates({ githubToken }),
                        api.listMyAgents({ githubToken }).catch(() => [] as unknown),
                    ]);
                    const payload = { templates: tpls, myConfigs: configs };
                    setPreloaded(key, payload);
                    return payload;
                })();
            }
            setPending(key, fetchPromise);
            return fetchPromise.catch((): undefined => undefined);
        },
        [auth.githubToken, workloadClient],
    );

    async function navTo(page: string) {
        // Legacy navigator retained for the auth-gate fallback path and
        // for programmatic redirects that bypass the tabs API.
        const key = preloadKeyFor(page);
        closeSidebar();
        preloadChunkFor(page);
        if (!key || !auth.githubToken) {
            nav(page);
            return;
        }
        setNavPending(true);
        const fetchPromise = startPreload(key);
        const timeoutPromise = new Promise<void>((resolve) =>
            window.setTimeout(resolve, NAV_PREFETCH_TIMEOUT_MS),
        );
        await Promise.race([fetchPromise, timeoutPromise]);
        setNavPending(false);
        nav(page);
    }
    void navTo;

    return (
        <ItemProvider
            workloadClient={workloadClient}
            workspaceObjectId={workspaceObjectId}
            routeItemObjectId={routeItemObjectId || null}
        >
        <SearchProvider scope={activePage as SearchScope}>
        {/* EditorTabsProvider wraps *both* sidebar and main so the sidebar
            click handlers (opening new tabs, replacing active, splitting
            into groups) can reach the tabs API via ``useEditorTabs``. */}
        <EditorTabsProvider>
        <AgentHubShell
            workloadClient={workloadClient}
            matchPath={match.path}
            activePage={activePage}
            auth={auth}
            sidebarOpen={sidebarOpen}
            sidebarCollapsed={sidebarCollapsed}
            toggleSidebar={toggleSidebar}
            closeSidebar={closeSidebar}
            toggleCollapsed={toggleCollapsed}
            startPreload={startPreload}
            setNavPending={setNavPending}
            workspaceObjectId={workspaceObjectId}
        />
        </EditorTabsProvider>
        </SearchProvider>
        </ItemProvider>
    );
}

function SideNavItem({
    icon, label, active, onClick, onContextMenu, onDragStart, disabled, collapsed, draggable,
}: {
    icon: React.ReactNode;
    label: string;
    active: boolean;
    onClick: () => void;
    onContextMenu?: (e: React.MouseEvent<HTMLDivElement>) => void;
    onDragStart?: (e: React.DragEvent<HTMLDivElement>) => void;
    disabled?: boolean;
    collapsed?: boolean;
    /** Whether this item supports drag-to-editor. Non-draggable items
     *  (placeholders, disabled items) omit the native draggable handler. */
    draggable?: boolean;
}) {
    const row = (
        <div
            className={`sidenav-item ${active ? "sidenav-item--active" : ""} ${disabled ? "sidenav-item--disabled" : ""}`}
            onClick={disabled ? undefined : onClick}
            onContextMenu={disabled ? undefined : onContextMenu}
            draggable={!!draggable && !disabled}
            onDragStart={disabled ? undefined : onDragStart}
            role="button"
            tabIndex={disabled ? -1 : 0}
            aria-current={active ? "page" : undefined}
            aria-disabled={disabled || undefined}
            onKeyDown={(e) => {
                if (disabled) return;
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); }
            }}
        >
            {icon}
            {/* Label is always in the DOM — hidden via CSS opacity in the
                collapsed rail so the transition is smooth instead of
                snapping. */}
            <span className="sidenav-item__label">{label}</span>
        </div>
    );
    // In the collapsed rail we lean on Fluent's themed Tooltip (positioned
    // to the right, with arrow) so hover discovery feels native rather
    // than the browser's default yellow chrome tooltip.
    if (collapsed && !disabled) {
        return (
            <Tooltip content={label} relationship="label" positioning="after" withArrow>
                {row}
            </Tooltip>
        );
    }
    return row;
}

/**
 * Renders the editor-group tree inside `EditorTabsProvider`.
 *
 * This component lives inside the provider so it can call
 * `useEditorTabs()`. Responsibilities:
 *   1. Redirect bare ``/agent-hub`` → ``/agent-hub/orchestrator`` so the
 *      default route still lands on New Session (matches prior behaviour).
 *   2. Provide the `renderTab` factory that maps a ``TabDescriptor``
 *      to its page component. The factory is memoised so lazy chunks
 *      are not re-requested on every render.
 *   3. Render an `emptyFallback` — the OrchestratorPage — when the only
 *      group has no tabs (e.g. very first visit or after closing the
 *      last tab).
 */
function AgentHubContent({ workloadClient, matchPath }: { workloadClient: WorkloadClientAPI; matchPath: string }) {
    const history = useHistory();
    const { state } = useEditorTabs();

    // If we're at the bare base path, land on a fresh New Session draft
    // (the old default route). We use ``replace`` so it doesn't add a
    // history entry the back button has to step over. Each first visit
    // gets a unique draft id — successive New Session clicks then open
    // additional tabs rather than reusing this one.
    //
    // On a *reload* (F5 / Ctrl+Shift+R) we also bounce the URL back to
    // the base path so the user always lands on the default view. The
    // persisted tab state in `sessionStorage` has already been cleared
    // in `EditorTabsContext` at module-load time, so the tab bar starts
    // fresh to match.
    useEffect(() => {
        const p = history.location.pathname;
        const atBase = p === matchPath || p === matchPath + "/";
        if (atBase || isReloadNavigation()) {
            const desc = makeNewSessionDescriptor(`${matchPath}/orchestrator`);
            history.replace(desc.path);
        }
    }, [history, matchPath]);

    const renderTab = useCallback((tab: TabDescriptor): React.ReactNode => {
        switch (tab.kind) {
            case "home":     return <DashboardPage workloadClient={workloadClient} />;
            case "new":      return <OrchestratorPage workloadClient={workloadClient} />;
            case "agents":   return <AgentsPage workloadClient={workloadClient} />;
            case "pbifixer": {
                // Extract per-tab `?nav=` so each editor tab renders
                // its own sub-page. Without this, every PBI Fixer tab
                // would default to the value last persisted in
                // sessionStorage (because all tabs share the outer
                // `window.location`).
                let initialNav: string | undefined;
                try {
                    const q = tab.path.split("?")[1];
                    if (q) {
                        const raw = new URLSearchParams(q).get("nav") ?? undefined;
                        // Defensive: strip any nested ``?…``/``&…`` segment
                        // that leaked in via the iframe bootstrap URL.
                        const m = raw?.match(/^[A-Za-z0-9_-]+/);
                        initialNav = m ? m[0] : raw;
                    }
                } catch { /* ignore */ }
                return <PbiFixerPage workloadClient={workloadClient} initialNav={initialNav as never} />;
            }
            case "settings": return <SettingsPage workloadClient={workloadClient} />;
            case "about":    return <AboutPage />;
            case "session": {
                const m = tab.path.match(/\/session\/([^/?#]+)/);
                const sid = m?.[1] ?? "";
                return <MissionControlPageLazy workloadClient={workloadClient} sessionId={sid} />;
            }
            default: return null;
        }
    }, [workloadClient]);

    // When nothing is open yet (e.g. sessionStorage was wiped), show the
    // default landing surface. Once URL-sync opens a tab, this branch
    // goes away automatically.
    const nothingOpen = state.groups.length === 1 && state.groups[0].tabs.length === 0;
    if (nothingOpen) {
        return <OrchestratorPage workloadClient={workloadClient} />;
    }

    return (
        <EditorGroupsRoot
            renderTab={renderTab}
            emptyFallback={<OrchestratorPage workloadClient={workloadClient} />}
        />
    );
}

/**
 * AgentHubShell — the routed body of AgentHubLayout.
 *
 * Hoisted out of the outer component so it can live *inside* the
 * ``EditorTabsProvider`` and therefore call ``useEditorTabs()`` +
 * ``useNavPreferences()`` directly. The outer layout owns auth,
 * sidebar state, and workspace preload; the shell owns navigation
 * semantics (which click does what — activate, new tab, replace,
 * split) and renders the visual tree.
 */
interface AgentHubShellProps {
    workloadClient: WorkloadClientAPI;
    matchPath: string;
    activePage: string;
    auth: ReturnType<typeof useGitHubAuth>;
    sidebarOpen: boolean;
    sidebarCollapsed: boolean;
    toggleSidebar: () => void;
    closeSidebar: () => void;
    toggleCollapsed: () => void;
    startPreload: (key: PreloadKey) => Promise<unknown>;
    setNavPending: (v: boolean) => void;
    workspaceObjectId: string | null;
}

/** Map a nav item id → its sidebar target page slug (used by preload). */
function pageSlugForNavItem(item: NavItemId): string {
    switch (item) {
        case "newsession": return "orchestrator";
        case "sessions":   return "home";
        case "agents":     return "agents";
        case "pbifixer":   return "pbifixer";
        case "settings":   return "settings";
    }
}

/** Build a tab descriptor for a nav item. "New Session" gets a fresh
 *  draft nonce every call; everything else uses the stable descriptor
 *  produced by ``descriptorFromPath``. */
function descriptorForNavItem(item: NavItemId, matchPath: string): TabDescriptor {
    if (item === "newsession") {
        return makeNewSessionDescriptor(`${matchPath}/orchestrator`);
    }
    const path = `${matchPath}/${pageSlugForNavItem(item)}`;
    const desc = descriptorFromPath(path);
    return desc ?? { id: item, kind: "home", path, title: item };
}

function AgentHubShell({
    workloadClient,
    matchPath,
    activePage,
    auth,
    sidebarOpen,
    sidebarCollapsed,
    toggleSidebar,
    closeSidebar,
    toggleCollapsed,
    startPreload,
    setNavPending,
    workspaceObjectId,
}: AgentHubShellProps) {
    const { openTab, openTabInNewGroup, replaceActiveTab } = useEditorTabs();
    const { prefs, setPrefs } = useNavPreferences();

    // Right-click context menu state — a single instance handles
    // whichever nav item was last right-clicked. Tracked here rather
    // than per-item so we only ever mount one popover at a time.
    const [ctxMenu, setCtxMenu] = useState<{ item: NavItemId; pos: { left: number; top: number } } | null>(null);
    const dismissCtxMenu = useCallback(() => setCtxMenu(null), []);

    // PBI Fixer sub-nav state — the Power BI Fixer sidebar row expands
    // into a flat tree of 14 pages (Model, Report, Others > 12 stubs).
    // We keep the selection + expand state here so navigation from the
    // outer sidebar drives the PBI Fixer page rendering via a window
    // event + sessionStorage handshake. `PbiFixerPage` listens to both.
    const [pbiFixerNavKey, setPbiFixerNavKey] = useState<PbiFixerNavKey>(() => {
        try {
            const raw = sessionStorage.getItem("pbiFixer.activeNav");
            if (raw) return raw as PbiFixerNavKey;
        } catch { /* ignore */ }
        return PBIFIXER_DEFAULT_NAV;
    });
    // v0.34: themed sub-groups (Model tools / Report tools / Automation)
    // replace the single catch-all "Others" branch. State is a per-group
    // boolean map persisted as JSON in sessionStorage.
    type GroupKey = Exclude<PbiFixerNavGroup, "peer">;
    const [pbiFixerExpandedGroups, setPbiFixerExpandedGroups] = useState<Record<GroupKey, boolean>>(() => {
        const empty: Record<GroupKey, boolean> = { modelTools: false, reportTools: false, automation: false };
        try {
            const raw = sessionStorage.getItem("pbiFixer.expandedGroups");
            if (!raw) return empty;
            const parsed = JSON.parse(raw);
            return { ...empty, ...parsed };
        } catch { return empty; }
    });
    // Auto-expand the group that owns the active sub-nav so deep links
    // (e.g. ``?nav=delta``) land on a visible row instead of a closed
    // "Model tools" header.
    useEffect(() => {
        const activeItem = PBIFIXER_NAV_ITEMS.find((i) => i.key === pbiFixerNavKey);
        if (!activeItem || activeItem.group === "peer") return;
        const g = activeItem.group as GroupKey;
        setPbiFixerExpandedGroups((prev) => {
            if (prev[g]) return prev;
            const next = { ...prev, [g]: true };
            try { sessionStorage.setItem("pbiFixer.expandedGroups", JSON.stringify(next)); } catch { /* ignore */ }
            return next;
        });
    }, [pbiFixerNavKey]);
    // Whether the Power BI Fixer group itself is expanded in the sidebar.
    // Auto-expanded while the pbifixer page is active so the user can see
    // where they are; otherwise collapsed to keep the sidebar compact.
    const [pbiFixerGroupExpanded, setPbiFixerGroupExpanded] = useState<boolean>(activePage === "pbifixer");
    useEffect(() => {
        if (activePage === "pbifixer") setPbiFixerGroupExpanded(true);
    }, [activePage]);
    // Pick up nav changes that originate inside the PBI Fixer page
    // itself (e.g. a page calling `onNavigate`).
    useEffect(() => {
        const handler = (e: Event) => {
            const ce = e as CustomEvent<PbiFixerNavKey>;
            if (ce.detail) setPbiFixerNavKey(ce.detail);
        };
        window.addEventListener("pbifixer:navchange", handler as EventListener);
        return () => window.removeEventListener("pbifixer:navchange", handler as EventListener);
    }, []);

    /** Apply a behaviour to a nav item click. Prefetches the target
     *  chunk + data in parallel, then opens/replaces/splits as requested. */
    const applyBehaviour = useCallback((item: NavItemId, behaviour: NavBehaviour) => {
        closeSidebar();
        const slug = pageSlugForNavItem(item);
        preloadChunkFor(slug);
        const key = preloadKeyFor(slug);
        if (key && auth.githubToken) {
            // Best-effort prefetch — we don't block on it here since the
            // tabs model shows a skeleton while the data arrives and the
            // tab slot is already committed.
            setNavPending(true);
            Promise.race([
                startPreload(key),
                new Promise((r) => window.setTimeout(r, NAV_PREFETCH_TIMEOUT_MS)),
            ]).finally(() => setNavPending(false));
        }
        const desc = descriptorForNavItem(item, matchPath);
        switch (behaviour) {
            case "new-group":
                openTabInNewGroup(desc, "right");
                break;
            case "replace":
                replaceActiveTab(desc);
                break;
            case "new-tab":
                // For stable-id items (Sessions, Agents, etc.) ``openTab``
                // still dedups — which is the right behaviour: you can't
                // have two "Sessions" tabs. For duplicable drafts we get
                // a fresh tab as intended.
                openTab(desc);
                break;
            case "smart":
            default:
                openTab(desc);
                break;
        }
    }, [auth.githubToken, closeSidebar, matchPath, openTab, openTabInNewGroup, replaceActiveTab, setNavPending, startPreload]);

    const handleNavClick = useCallback((item: NavItemId) => {
        applyBehaviour(item, resolveBehaviour(prefs, item));
    }, [applyBehaviour, prefs]);

    /** Sub-nav click on any PBI Fixer page (Model / Report / 12 stubs).
     *  Opens a NEW tab dedicated to the selected sub-page (or focuses
     *  it if a tab with that nav key is already open). The PbiFixerPage
     *  reads the initial activeNav from the URL query so each tab
     *  remembers its own page independently. */
    const handlePbiFixerSubNav = useCallback((key: PbiFixerNavKey) => {
        try { sessionStorage.setItem("pbiFixer.activeNav", key); } catch { /* ignore */ }
        setPbiFixerNavKey(key);
        const navItem = PBIFIXER_NAV_ITEMS.find((i) => i.key === key);
        const label = navItem?.label ?? key;
        const path = `${matchPath}/pbifixer?nav=${encodeURIComponent(key)}`;
        const desc: TabDescriptor = {
            id: `pbifixer:${key}`,
            kind: "pbifixer",
            path,
            title: `PBI Fixer · ${label}`,
        };
        // Best-effort prefetch of the PBI Fixer chunk + workspaces.
        preloadChunkFor("pbifixer");
        const pkey = preloadKeyFor("pbifixer");
        if (pkey && auth.githubToken) {
            setNavPending(true);
            Promise.race([
                startPreload(pkey),
                new Promise((r) => window.setTimeout(r, NAV_PREFETCH_TIMEOUT_MS)),
            ]).finally(() => setNavPending(false));
        }
        closeSidebar();
        openTab(desc);
    }, [auth.githubToken, closeSidebar, matchPath, openTab, setNavPending, startPreload]);

    const togglePbiFixerGroup = useCallback((group: GroupKey) => {
        setPbiFixerExpandedGroups((prev) => {
            const next = { ...prev, [group]: !prev[group] };
            try { sessionStorage.setItem("pbiFixer.expandedGroups", JSON.stringify(next)); } catch { /* ignore */ }
            return next;
        });
    }, []);

    // WS-N integration sweep — cross-tab BPA "Fix it" wiring.
    //
    // Background: with multi-tab v1.1, each PBI Fixer sub-nav lives in
    // its own editor tab. Model BPA / Report BPA dispatch a global
    // ``pbifixer:bpa-fix`` CustomEvent and call their local
    // ``onNavigate('fixer')``, but the latter only mutates THAT tab's
    // ``activeNav`` — it doesn't open the Fixer tab. So FixerPage's own
    // ``addEventListener('pbifixer:bpa-fix')`` never fires unless the
    // user already had the Fixer tab open before clicking Fix-it.
    //
    // Fix: AgentHubLayout listens at the shell level. On a BPA fix
    // event we (1) stash the payload in sessionStorage so a freshly
    // mounted FixerPage can drain it, (2) open / focus the Fixer tab
    // via ``handlePbiFixerSubNav('fixer')``, and (3) re-dispatch the
    // event after a short delay so an already-mounted FixerPage still
    // picks it up via its existing handler.
    useEffect(() => {
        const handler = (e: Event) => {
            const ce = e as CustomEvent<unknown> & { __relayed?: boolean };
            if (ce.__relayed) return; // avoid re-entrant loops
            try {
                sessionStorage.setItem(
                    "pbiFixer.pendingBpaFix",
                    JSON.stringify(ce.detail ?? {}),
                );
            } catch { /* ignore */ }
            handlePbiFixerSubNav("fixer");
            // Re-fire so an already-mounted FixerPage's listener still
            // catches the event. Tag the relayed event so this handler
            // ignores it on the second pass.
            window.setTimeout(() => {
                const relay = new CustomEvent("pbifixer:bpa-fix", { detail: ce.detail });
                (relay as unknown as { __relayed: boolean }).__relayed = true;
                window.dispatchEvent(relay);
            }, 50);
        };
        window.addEventListener("pbifixer:bpa-fix", handler);
        return () => window.removeEventListener("pbifixer:bpa-fix", handler);
    }, [handlePbiFixerSubNav]);

    const handleNavContextMenu = useCallback((item: NavItemId, e: React.MouseEvent<HTMLDivElement>) => {
        e.preventDefault();
        setCtxMenu({ item, pos: { left: e.clientX, top: e.clientY } });
    }, []);

    const handleNavDragStart = useCallback((item: NavItemId, e: React.DragEvent<HTMLDivElement>) => {
        const desc = descriptorForNavItem(item, matchPath);
        e.dataTransfer.setData(DRAG_NAVITEM_MIME, JSON.stringify(desc));
        e.dataTransfer.effectAllowed = "copyMove";
    }, [matchPath]);

    return (
        <div className="agenthub-root">
            {/* Top bar — matches design: brand text + breadcrumb, search, utility icons */}
            <div className="agenthub-topbar">
                <div className="agenthub-topbar-left">
                    <button className="hamburger-btn" onClick={toggleSidebar} aria-label="Toggle navigation">
                        {sidebarOpen ? <Dismiss24Regular /> : <Navigation24Regular />}
                    </button>
                    <span className="topbar-brand">Developer Hub</span>
                    <span
                        className="topbar-version"
                        title="AgentHub workload version"
                        style={{
                            fontSize: "11px",
                            color: "#666",
                            marginLeft: "6px",
                            padding: "2px 6px",
                            border: "1px solid #ddd",
                            borderRadius: "4px",
                            fontFamily: "monospace",
                        }}
                    >
                        {WORKLOAD_VERSION}
                    </span>
                    <span className="topbar-divider" />
                    <div className="topbar-breadcrumb">
                        <TopbarBreadcrumbIcon activePage={activePage} />
                        <span>{topbarBreadcrumbLabel(activePage)}</span>
                    </div>
                </div>
                <div className="agenthub-topbar-search">
                    <TopbarSearchInput />
                </div>
                <div className="agenthub-topbar-right">
                    <TopbarItemActions
                        workloadClient={workloadClient}
                        workspaceObjectId={workspaceObjectId}
                    />
                    <Alert24Regular className="topbar-icon" />
                    <QuestionCircle24Regular className="topbar-icon" />
                    <div className="agenthub-avatar" title={auth.githubUser || "User"}>
                        <PersonCircle32Regular />
                    </div>
                </div>
            </div>

            <div className="agenthub-body">
                {/* Sidebar overlay backdrop (mobile only) */}
                {sidebarOpen && (
                    <div
                        className="sidebar-backdrop"
                        role="button"
                        tabIndex={-1}
                        aria-label="Close sidebar"
                        onClick={closeSidebar}
                    />
                )}

                {/* Sidebar */}
                <aside className={`agenthub-sidebar ${sidebarOpen ? "sidebar--open" : ""} ${sidebarCollapsed ? "agenthub-sidebar--collapsed" : ""}`}>
                    <div className="agenthub-sidebar-toolbar">
                        <Tooltip
                            content={sidebarCollapsed
                                ? `Expand sidebar (${modShortcut("B")})`
                                : `Collapse sidebar (${modShortcut("B")})`}
                            relationship="label"
                            positioning="after"
                            withArrow
                        >
                            <button
                                type="button"
                                className="sidebar-collapse-btn"
                                onClick={toggleCollapsed}
                                aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                                aria-expanded={!sidebarCollapsed}
                            >
                                <PanelLeftContract24Regular />
                            </button>
                        </Tooltip>
                    </div>

                    <nav className="agenthub-sidenav">
                        <div className="sidenav-section-label">Agent Hub</div>
                        <SideNavItem
                            icon={<AddCircle24Regular />}
                            label="New Session"
                            active={activePage === "newsession"}
                            collapsed={sidebarCollapsed}
                            draggable
                            onClick={() => handleNavClick("newsession")}
                            onContextMenu={(e) => handleNavContextMenu("newsession", e)}
                            onDragStart={(e) => handleNavDragStart("newsession", e)}
                        />
                        <SideNavItem
                            icon={<ChatMultiple24Regular />}
                            label="Sessions"
                            active={activePage === "sessions"}
                            collapsed={sidebarCollapsed}
                            draggable
                            onClick={() => handleNavClick("sessions")}
                            onContextMenu={(e) => handleNavContextMenu("sessions", e)}
                            onDragStart={(e) => handleNavDragStart("sessions", e)}
                        />
                        <SideNavItem
                            icon={<Bot24Regular />}
                            label="Agents and Skills"
                            active={activePage === "agents"}
                            collapsed={sidebarCollapsed}
                            draggable
                            onClick={() => handleNavClick("agents")}
                            onContextMenu={(e) => handleNavContextMenu("agents", e)}
                            onDragStart={(e) => handleNavDragStart("agents", e)}
                        />

                        <div className="sidenav-rail-divider" aria-hidden="true" />

                        <div className="sidenav-section-label sidenav-section-label--spaced">Tools</div>
                        <div className="sidenav-row-with-toggle">
                            <SideNavItem
                                icon={<Wrench24Regular />}
                                label="Power BI Fixer"
                                active={activePage === "pbifixer"}
                                collapsed={sidebarCollapsed}
                                draggable
                                onClick={() => {
                                    setPbiFixerGroupExpanded(true);
                                    handleNavClick("pbifixer");
                                }}
                                onContextMenu={(e) => handleNavContextMenu("pbifixer", e)}
                                onDragStart={(e) => handleNavDragStart("pbifixer", e)}
                            />
                            {!sidebarCollapsed && (
                                <button
                                    type="button"
                                    className="sidenav-group-toggle"
                                    aria-label={pbiFixerGroupExpanded ? "Collapse Power BI Fixer pages" : "Expand Power BI Fixer pages"}
                                    aria-expanded={pbiFixerGroupExpanded}
                                    onClick={(e) => { e.stopPropagation(); setPbiFixerGroupExpanded((v) => !v); }}
                                >
                                    {pbiFixerGroupExpanded ? <ChevronDown16Regular /> : <ChevronRight16Regular />}
                                </button>
                            )}
                        </div>

                        {/* Flat-tree sub-nav for the PBI Fixer — shown only when
                            the group is expanded and the sidebar isn't collapsed
                            into the narrow rail. v0.34: top peers (Model, Report)
                            → 3 themed collapsible groups (Model tools, Report tools,
                            Automation) → bottom peer (About). */}
                        {pbiFixerGroupExpanded && !sidebarCollapsed && (
                            <div
                                className="pbifixer-subnav"
                                role="group"
                                aria-label="PBI Fixer pages"
                                onKeyDown={(e) => {
                                    // ArrowUp/Down move focus between tabbable rows;
                                    // ArrowRight/Left expand or collapse the group
                                    // header that currently has focus.
                                    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" &&
                                        e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
                                    const root = e.currentTarget;
                                    const items = Array.from(
                                        root.querySelectorAll<HTMLDivElement>('[role="button"]'),
                                    );
                                    const active = document.activeElement as HTMLElement | null;
                                    const idx = active ? items.indexOf(active as HTMLDivElement) : -1;
                                    if (idx < 0) return;
                                    if (e.key === "ArrowDown") {
                                        e.preventDefault();
                                        items[(idx + 1) % items.length]?.focus();
                                    } else if (e.key === "ArrowUp") {
                                        e.preventDefault();
                                        items[(idx - 1 + items.length) % items.length]?.focus();
                                    } else if (e.key === "ArrowRight" && active?.classList.contains("pbifixer-subnav-item--others")) {
                                        const g = active.getAttribute("data-group") as GroupKey | null;
                                        if (g && !pbiFixerExpandedGroups[g]) { e.preventDefault(); togglePbiFixerGroup(g); }
                                    } else if (e.key === "ArrowLeft" && active?.classList.contains("pbifixer-subnav-item--others")) {
                                        const g = active.getAttribute("data-group") as GroupKey | null;
                                        if (g && pbiFixerExpandedGroups[g]) { e.preventDefault(); togglePbiFixerGroup(g); }
                                    }
                                }}
                            >
                                {/* Top peers: Model + Report (anything before the
                                    first non-peer item). */}
                                {PBIFIXER_NAV_ITEMS.filter((i, idx) => i.group === "peer" && idx < PBIFIXER_NAV_ITEMS.findIndex((x) => x.group !== "peer")).map((item) => (
                                    <div
                                        key={item.key}
                                        role="button"
                                        tabIndex={0}
                                        className={`pbifixer-subnav-item ${activePage === "pbifixer" && pbiFixerNavKey === item.key ? "pbifixer-subnav-item--active" : ""}`}
                                        onClick={() => handlePbiFixerSubNav(item.key)}
                                        onKeyDown={(e) => {
                                            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handlePbiFixerSubNav(item.key); }
                                        }}
                                        aria-current={activePage === "pbifixer" && pbiFixerNavKey === item.key ? "page" : undefined}
                                    >
                                        <span className="pbifixer-subnav-icon" aria-hidden>{item.icon}</span>
                                        <span className="pbifixer-subnav-label">{item.label}</span>
                                    </div>
                                ))}

                                {/* Three themed collapsible groups. */}
                                {PBIFIXER_NAV_GROUPS.map((g) => {
                                    const expanded = pbiFixerExpandedGroups[g.key];
                                    const items = PBIFIXER_NAV_ITEMS.filter((i) => i.group === g.key);
                                    return (
                                        <React.Fragment key={g.key}>
                                            <div
                                                role="button"
                                                tabIndex={0}
                                                data-group={g.key}
                                                className="pbifixer-subnav-item pbifixer-subnav-item--others"
                                                onClick={() => togglePbiFixerGroup(g.key)}
                                                onKeyDown={(e) => {
                                                    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); togglePbiFixerGroup(g.key); }
                                                }}
                                                aria-expanded={expanded}
                                            >
                                                <span className="pbifixer-subnav-chevron" aria-hidden>
                                                    {expanded ? <ChevronDown16Regular /> : <ChevronRight16Regular />}
                                                </span>
                                                <span className="pbifixer-subnav-label">{g.label}</span>
                                                <span className="pbifixer-subnav-count">{items.length}</span>
                                            </div>
                                            {expanded && items.map((item) => (
                                                <div
                                                    key={item.key}
                                                    role="button"
                                                    tabIndex={0}
                                                    className={`pbifixer-subnav-item pbifixer-subnav-item--nested ${!item.ready ? "pbifixer-subnav-item--pending" : ""} ${activePage === "pbifixer" && pbiFixerNavKey === item.key ? "pbifixer-subnav-item--active" : ""}`}
                                                    onClick={() => handlePbiFixerSubNav(item.key)}
                                                    onKeyDown={(e) => {
                                                        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handlePbiFixerSubNav(item.key); }
                                                    }}
                                                    title={item.ready ? item.label : `${item.label} — Coming soon`}
                                                    aria-current={activePage === "pbifixer" && pbiFixerNavKey === item.key ? "page" : undefined}
                                                >
                                                    <span className="pbifixer-subnav-icon" aria-hidden>{item.icon}</span>
                                                    <span className="pbifixer-subnav-label">{item.label}</span>
                                                </div>
                                            ))}
                                        </React.Fragment>
                                    );
                                })}

                                {/* Bottom peers: anything after the last non-peer
                                    item (currently just About). */}
                                {(() => {
                                    const lastNonPeer = (() => {
                                        for (let i = PBIFIXER_NAV_ITEMS.length - 1; i >= 0; i--) {
                                            if (PBIFIXER_NAV_ITEMS[i].group !== "peer") return i;
                                        }
                                        return -1;
                                    })();
                                    return PBIFIXER_NAV_ITEMS.slice(lastNonPeer + 1).map((item) => (
                                        <div
                                            key={item.key}
                                            role="button"
                                            tabIndex={0}
                                            className={`pbifixer-subnav-item ${!item.ready ? "pbifixer-subnav-item--pending" : ""} ${activePage === "pbifixer" && pbiFixerNavKey === item.key ? "pbifixer-subnav-item--active" : ""}`}
                                            onClick={() => handlePbiFixerSubNav(item.key)}
                                            onKeyDown={(e) => {
                                                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handlePbiFixerSubNav(item.key); }
                                            }}
                                            title={item.ready ? item.label : `${item.label} — Coming soon`}
                                            aria-current={activePage === "pbifixer" && pbiFixerNavKey === item.key ? "page" : undefined}
                                        >
                                            <span className="pbifixer-subnav-icon" aria-hidden>{item.icon}</span>
                                            <span className="pbifixer-subnav-label">{item.label}</span>
                                        </div>
                                    ));
                                })()}
                            </div>
                        )}
                    </nav>

                    <div className="agenthub-sidebar-footer">
                        <button
                            type="button"
                            className="sidenav-footer-item"
                            onClick={() => { auth.signOut(); closeSidebar(); }}
                            title={`Sign out (${auth.githubUser})`}
                        >
                            <SignOut24Regular /> <Text size={200}>Sign out ({auth.githubUser})</Text>
                        </button>
                        <button
                            type="button"
                            className="sidenav-footer-item"
                            onClick={() => {
                                openTab({ id: "about", kind: "about", path: `${matchPath}/about`, title: "About" });
                                closeSidebar();
                            }}
                            title="About Developer Hub"
                        >
                            <Info24Regular /> <Text size={200}>About</Text>
                        </button>
                        <div className="sidenav-footer-item" title="Support">
                            <QuestionCircle24Regular /> <Text size={200}>Support</Text>
                        </div>
                        <div className="sidenav-footer-item" title="Feedback">
                            <Chat24Regular /> <Text size={200}>Feedback</Text>
                        </div>
                    </div>
                </aside>

                {/* Main content */}
                <main className="agenthub-main">
                    <Suspense fallback={<div className="page-suspense-fallback"><Spinner size="small" /></div>}>
                        <AgentHubContent
                            workloadClient={workloadClient}
                            matchPath={matchPath}
                        />
                    </Suspense>
                </main>
            </div>

            {/* Single floating context menu instance — positioned at the
                cursor when any nav item is right-clicked. */}
            {ctxMenu && (
                <SideNavContextMenu
                    position={ctxMenu.pos}
                    itemId={ctxMenu.item}
                    currentDefault={resolveBehaviour(prefs, ctxMenu.item)}
                    onOpenAs={(b) => applyBehaviour(ctxMenu.item, b)}
                    onSetDefault={(b) => setPrefs({
                        ...prefs,
                        perItem: { ...prefs.perItem, [ctxMenu.item]: b },
                    })}
                    onDismiss={dismissCtxMenu}
                />
            )}
        </div>
    );
}
