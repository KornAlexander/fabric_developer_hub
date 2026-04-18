import React, { useMemo, useState, useCallback, useEffect } from "react";
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
    ChevronLeft24Regular,
    ChevronRight24Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { DashboardPage } from "./DashboardPage";
import { OrchestratorPage } from "./OrchestratorPage";
import { AgentsPage } from "./AgentsPage";
import { SettingsPage } from "./SettingsPage";
import { JobDetailPage } from "./JobDetailPage";
import { useGitHubAuth } from "./useGitHubAuth";
import { ItemProvider } from "./ItemContext";
import { PbiFixerPage } from "../PbiFixer";

interface AgentHubLayoutProps {
    workloadClient: WorkloadClientAPI;
    itemObjectId?: string;
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
    else if (currentPath.includes("/job/")) activePage = "sessions";

    function nav(page: string) {
        history.push(`${match.url}/${page}`);
    }

    // ── GitHub auth gate ──────────────────────────────────────────
    if (!auth.githubToken) {
        return (
            <div className="agenthub-root">
                <div className="agenthub-auth-gate">
                    <BrainCircuit24Regular style={{ fontSize: 48, color: "#0078d4" }} />
                    <Text size={700} weight="bold">AgentHub</Text>
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

    function navTo(page: string) {
        nav(page);
        closeSidebar();
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
                    <span className="topbar-brand">AgentHub</span>
                    <span className="topbar-divider" />
                    <div className="topbar-breadcrumb">
                        <BrainCircuit24Regular className="topbar-breadcrumb-icon" />
                        <span>Orchestrator</span>
                        <span className="topbar-breadcrumb-chev">›</span>
                        <span>New Session</span>
                    </div>
                </div>
                <div className="agenthub-topbar-search">
                    <input
                        type="text"
                        placeholder="Search orchestrated jobs..."
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
                    <div className="agenthub-sidebar-brand">
                        <div className="agenthub-brand-icon"><BrainCircuit24Regular /></div>
                        <div className="agenthub-brand-text">
                            <Text weight="bold" size={300}>AgentHub</Text>
                            <Text size={100} className="agenthub-brand-sub">FABRIC ENTERPRISE</Text>
                        </div>
                        <button
                            type="button"
                            className="sidebar-collapse-btn"
                            onClick={toggleCollapsed}
                            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                        >
                            {sidebarCollapsed ? <ChevronRight24Regular /> : <ChevronLeft24Regular />}
                        </button>
                    </div>

                    <nav className="agenthub-sidenav">
                        <div className="sidenav-section-label">Agent Hub</div>
                        <SideNavItem icon={<AddCircle24Regular />} label="New Session" active={activePage === "newsession"} collapsed={sidebarCollapsed} onClick={() => navTo("orchestrator")} />
                        <SideNavItem icon={<ChatMultiple24Regular />} label="Sessions" active={activePage === "sessions"} collapsed={sidebarCollapsed} onClick={() => navTo("home")} />
                        <SideNavItem icon={<Bot24Regular />} label="Agents and Skills" active={activePage === "agents"} collapsed={sidebarCollapsed} onClick={() => navTo("agents")} />

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
                    <Switch>
                        <Route path={`${match.path}/home`}><DashboardPage workloadClient={workloadClient} /></Route>
                        <Route path={`${match.path}/orchestrator`}><OrchestratorPage workloadClient={workloadClient} /></Route>
                        <Route path={`${match.path}/agents`}><AgentsPage workloadClient={workloadClient} /></Route>
                        <Route path={`${match.path}/pbifixer`}><PbiFixerPage workloadClient={workloadClient} /></Route>
                        <Route path={`${match.path}/settings`}><SettingsPage workloadClient={workloadClient} /></Route>
                        <Route path={`${match.path}/job/:jobId`}><JobDetailPage workloadClient={workloadClient} /></Route>
                        <Route path={match.path}><OrchestratorPage workloadClient={workloadClient} /></Route>
                    </Switch>
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
