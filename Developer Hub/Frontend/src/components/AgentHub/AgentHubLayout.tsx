import React, { useMemo, useState, useCallback, useEffect, Suspense, lazy } from "react";
import { Route, Switch, useHistory, useRouteMatch } from "react-router-dom";
import "../../styles.scss";
import {
    Button,
    Text,
    Spinner,
    Body1,
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
    ChatMultiple24Regular,
    MoreHorizontal24Regular,
    PanelLeftContract24Regular,
    PanelLeftExpand24Regular,
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
    const Component: any = lazy(() =>
        factory().then(m => ({ default: m[exportName] })),
    );
    Component.preload = factory;
    return Component as React.LazyExoticComponent<any> & { preload: () => Promise<any> };
}

const DashboardPage = lazyWithPreload(() => import("./DashboardPage"), "DashboardPage");
const AgentsPage = lazyWithPreload(() => import("./AgentsPage"), "AgentsPage");
const SettingsPage = lazyWithPreload(() => import("./SettingsPage"), "SettingsPage");
const SessionDetailPage = lazyWithPreload(() => import("./SessionDetailPage"), "SessionDetailPage");
const PbiFixerPage = lazyWithPreload(
    () => import("../PbiFixer").then(m => ({ PbiFixerPage: m.PbiFixerPage })),
    "PbiFixerPage",
);
import { useGitHubAuth } from "./useGitHubAuth";
import { ItemProvider } from "./ItemContext";
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
        case "sessions":   return <ChatMultiple24Regular className={cls} />;
        case "newsession": return <AddCircle24Regular className={cls} />;
        case "agents":     return <Bot24Regular className={cls} />;
        case "pbifixer":   return <Wrench24Regular className={cls} />;
        default:           return <BrainCircuit24Regular className={cls} />;
    }
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
    else if (currentPath.includes("/sessions") || currentPath.includes("/home")) activePage = "sessions";
    else if (currentPath.includes("/agents")) activePage = "agents";
    else if (currentPath.includes("/pbifixer")) activePage = "pbifixer";
    else if (currentPath.includes("/session/")) activePage = "sessions";

    function nav(page: string) {
        history.push(`${match.url}/${page}`);
    }

    // ── Background workspace preload (fire-and-forget) ────────────
    // Once GitHub auth lands, acquire a Fabric token and ask the backend to
    // warm the per-user workspace cache so the workspace selector is instant
    // on first navigation. Safe to call without a Fabric token (no-op).
    useEffect(() => {
        if (!auth.githubToken) return;
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

    // When the user clicks a sidebar item, kick off the target page's data
    // fetch *before* we route-change. We wait for it to finish, capped at
    // NAV_PREFETCH_TIMEOUT_MS (1s). Fast loads therefore switch straight
    // from the current page to the fully-populated target with no skeleton
    // in between. Slow loads still flip over after the cap and show the
    // skeleton + "still loading" hint.
    const [navPending, setNavPending] = useState(false);

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
            return fetchPromise.catch(() => undefined);
        },
        [auth.githubToken, workloadClient],
    );

    async function navTo(page: string) {
        const key = preloadKeyFor(page);
        closeSidebar();
        // Kick off the lazy chunk fetch as early as we know the target — in
        // parallel with the data prefetch. `preload` is a no-op if the chunk
        // is already loaded/in-flight.
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

    return (
        <ItemProvider
            workloadClient={workloadClient}
            workspaceObjectId={workspaceObjectId}
            routeItemObjectId={routeItemObjectId || null}
        >
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
                    <input
                        type="text"
                        placeholder="Search Developer Hub…"
                        aria-label="Search"
                    />
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
                {sidebarOpen && <div className="sidebar-backdrop" onClick={closeSidebar} />}

                {/* Sidebar */}
                <aside className={`agenthub-sidebar ${sidebarOpen ? "sidebar--open" : ""} ${sidebarCollapsed ? "agenthub-sidebar--collapsed" : ""}`}>
                    {/* Collapse toggle (design has no brand block in the sidebar — brand lives only in the topbar). */}
                    <div className="agenthub-sidebar-toolbar">
                        <button
                            type="button"
                            className="sidebar-collapse-btn"
                            onClick={toggleCollapsed}
                            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                        >
                            {sidebarCollapsed ? <PanelLeftExpand24Regular /> : <PanelLeftContract24Regular />}
                        </button>
                    </div>

                    <nav className="agenthub-sidenav">
                        <div className="sidenav-section-label">Agent Hub</div>
                        <SideNavItem icon={<AddCircle24Regular />} label="New Session" active={activePage === "newsession"} collapsed={sidebarCollapsed} onClick={() => navTo("orchestrator")} />
                        <SideNavItem icon={<ChatMultiple24Regular />} label="Sessions" active={activePage === "sessions"} collapsed={sidebarCollapsed} onClick={() => navTo("home")} />
                        <SideNavItem icon={<Bot24Regular />} label="Agents and Skills" active={activePage === "agents"} collapsed={sidebarCollapsed} onClick={() => navTo("agents")} />

                        {/* Visible only in collapsed mode — visually separates the two sections
                            when the text labels are hidden. Matches Design/_shared/sidebar.js. */}
                        <div className="sidenav-rail-divider" aria-hidden="true" />

                        <div className="sidenav-section-label sidenav-section-label--spaced">Tools</div>
                        <SideNavItem icon={<Wrench24Regular />} label="Power BI Fixer" active={activePage === "pbifixer"} collapsed={sidebarCollapsed} onClick={() => navTo("pbifixer")} />
                        <SideNavItem icon={<MoreHorizontal24Regular />} label="…" active={false} collapsed={sidebarCollapsed} onClick={() => { /* placeholder */ }} disabled />
                        <SideNavItem icon={<MoreHorizontal24Regular />} label="…" active={false} collapsed={sidebarCollapsed} onClick={() => { /* placeholder */ }} disabled />
                    </nav>

                    <div className="agenthub-sidebar-footer">
                        <div className="sidenav-footer-item" onClick={() => { auth.signOut(); closeSidebar(); }} title={`Sign out (${auth.githubUser})`}>
                            <SignOut24Regular /> <Text size={200}>Sign out ({auth.githubUser})</Text>
                        </div>
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
                    {navPending && <div className="nav-progress" aria-hidden="true" />}
                    <Suspense fallback={<div className="page-suspense-fallback"><Spinner size="small" /></div>}>
                        <Switch>
                            <Route path={`${match.path}/home`}><DashboardPage workloadClient={workloadClient} /></Route>
                            <Route path={`${match.path}/orchestrator`}><OrchestratorPage workloadClient={workloadClient} /></Route>
                            <Route path={`${match.path}/agents`}><AgentsPage workloadClient={workloadClient} /></Route>
                            <Route path={`${match.path}/pbifixer`}><PbiFixerPage workloadClient={workloadClient} /></Route>
                            <Route path={`${match.path}/settings`}><SettingsPage workloadClient={workloadClient} /></Route>
                            <Route path={`${match.path}/session/:sessionId`}><SessionDetailPage workloadClient={workloadClient} /></Route>
                            <Route path={match.path}><OrchestratorPage workloadClient={workloadClient} /></Route>
                        </Switch>
                    </Suspense>
                </main>
            </div>
        </div>
        </ItemProvider>
    );
}

function SideNavItem({ icon, label, active, onClick, disabled, collapsed }: {
    icon: React.ReactNode; label: string; active: boolean; onClick: () => void; disabled?: boolean; collapsed?: boolean;
}) {
    return (
        <div
            className={`sidenav-item ${active ? "sidenav-item--active" : ""} ${disabled ? "sidenav-item--disabled" : ""}`}
            onClick={disabled ? undefined : onClick}
            title={collapsed ? label : undefined}
        >
            {icon}
            <Text size={300} weight={active ? "semibold" : "regular"}>{label}</Text>
        </div>
    );
}
