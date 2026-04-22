import React, { useMemo, useState, useCallback, useEffect, Suspense, lazy } from "react";
import { useHistory, useRouteMatch } from "react-router-dom";
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
} from "@fluentui/react-components";
import {
    BrainCircuit24Regular,
    Bot24Regular,
    SignOut24Regular,
    QuestionCircle24Regular,
    Chat24Regular,
    Alert24Regular,
    PersonCircle32Regular,
    Navigation24Regular,
    Dismiss24Regular,
    Wrench24Regular,
    AddCircle24Regular,
    Sparkle24Regular,
    ChatMultiple24Regular,
    MoreHorizontal24Regular,
    PanelLeftContract24Regular,
    PanelLeftExpand24Regular,
    Search20Regular,
    Filter20Regular,
    Dismiss16Regular,
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
// Direct prop-driven variant of MissionControlPage used by the tabs
// system — lets non-active editor groups render a session by id without
// needing to own the URL via ``useParams``.
const MissionControlPageLazy = lazyWithPreload(() => import("./mission/MissionControlPage"), "MissionControlPage");
const PbiFixerPage = lazyWithPreload(
    () => import("../PbiFixer").then(m => ({ PbiFixerPage: m.PbiFixerPage })),
    "PbiFixerPage",
);
import { useGitHubAuth } from "./useGitHubAuth";
import { ItemProvider } from "./ItemContext";
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

export function AgentHubLayout({ workloadClient, itemObjectId: routeItemObjectId }: AgentHubLayoutProps) {
    const history = useHistory();
    const match = useRouteMatch();
    const auth = useGitHubAuth();

    // Extract workspaceObjectId from ?ws= query param (set by index.worker.ts)
    const workspaceObjectId = useMemo(() => {
        const params = new URLSearchParams(window.location.search);
        return params.get("ws") || sessionStorage.getItem("workspace_id") || null;
    }, []);

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
    // Once GitHub auth lands, acquire a Fabric token and ask the backend to
    // warm the per-user workspace cache so the workspace selector is instant
    // on first navigation. Safe to call without a Fabric token (no-op).
    useEffect(() => {
        if (!auth.githubToken) return undefined;
        let cancelled = false;
        (async () => {
            let fabricToken: string | undefined;
            try {
                const t = await callAuthAcquireAccessToken(workloadClient);
                fabricToken = t.token;
            } catch { /* ignore — preload is best-effort */ }
            if (cancelled) return;
            await api.preloadWorkspaces({ githubToken: auth.githubToken!, fabricToken });
        })();
        return () => { cancelled = true; };
    }, [auth.githubToken, workloadClient]);

    // ── GitHub auth gate ──────────────────────────────────────────
    if (!auth.githubToken) {
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
            case "pbifixer": return <PbiFixerPage workloadClient={workloadClient} />;
            case "settings": return <SettingsPage workloadClient={workloadClient} />;
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
}: AgentHubShellProps) {
    const { openTab, openTabInNewGroup, replaceActiveTab } = useEditorTabs();
    const { prefs, setPrefs } = useNavPreferences();

    // Right-click context menu state — a single instance handles
    // whichever nav item was last right-clicked. Tracked here rather
    // than per-item so we only ever mount one popover at a time.
    const [ctxMenu, setCtxMenu] = useState<{ item: NavItemId; pos: { left: number; top: number } } | null>(null);
    const dismissCtxMenu = useCallback(() => setCtxMenu(null), []);

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
                        <SideNavItem
                            icon={<Wrench24Regular />}
                            label="Power BI Fixer"
                            active={activePage === "pbifixer"}
                            collapsed={sidebarCollapsed}
                            draggable
                            onClick={() => handleNavClick("pbifixer")}
                            onContextMenu={(e) => handleNavContextMenu("pbifixer", e)}
                            onDragStart={(e) => handleNavDragStart("pbifixer", e)}
                        />
                        <SideNavItem icon={<MoreHorizontal24Regular />} label="…" active={false} collapsed={sidebarCollapsed} onClick={() => { /* placeholder */ }} disabled />
                        <SideNavItem icon={<MoreHorizontal24Regular />} label="…" active={false} collapsed={sidebarCollapsed} onClick={() => { /* placeholder */ }} disabled />
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
