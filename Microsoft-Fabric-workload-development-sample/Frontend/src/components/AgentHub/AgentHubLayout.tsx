import React from "react";
import { Route, Switch, useHistory, useRouteMatch } from "react-router-dom";
import "../../styles.scss";
import {
    Button,
    Text,
    Spinner,
    Body1,
} from "@fluentui/react-components";
import {
    Home24Regular,
    BrainCircuit24Regular,
    Bot24Regular,
    Settings24Regular,
    SignOut24Regular,
    QuestionCircle24Regular,
    Chat24Regular,
    Alert24Regular,
    PersonCircle32Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { DashboardPage } from "./DashboardPage";
import { OrchestratorPage } from "./OrchestratorPage";
import { AgentsPage } from "./AgentsPage";
import { SettingsPage } from "./SettingsPage";
import { JobDetailPage } from "./JobDetailPage";
import { useGitHubAuth } from "./useGitHubAuth";

interface AgentHubLayoutProps {
    workloadClient: WorkloadClientAPI;
}

export function AgentHubLayout({ workloadClient }: AgentHubLayoutProps) {
    const history = useHistory();
    const match = useRouteMatch();
    const auth = useGitHubAuth();

    const currentPath = history.location.pathname;
    let activePage = "home";
    if (currentPath.includes("/orchestrator")) activePage = "orchestrator";
    else if (currentPath.includes("/agents")) activePage = "agents";
    else if (currentPath.includes("/settings")) activePage = "settings";
    else if (currentPath.includes("/job/")) activePage = "orchestrator";

    function nav(page: string) {
        history.push(`${match.url}/${page}`);
    }

    // ── GitHub auth gate ──────────────────────────────────────────
    if (!auth.githubToken) {
        return (
            <div className="agenthub-root">
                <div className="agenthub-auth-gate">
                    <BrainCircuit24Regular style={{ fontSize: 48, color: "#0078d4" }} />
                    <Text size={700} weight="bold">ClawHub</Text>
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
    return (
        <div className="agenthub-root">
            {/* Top bar */}
            <div className="agenthub-topbar">
                <div className="agenthub-topbar-left">
                    <BrainCircuit24Regular style={{ color: "#0078d4" }} />
                    <Text weight="bold" size={400}>AgentHub</Text>
                    <nav className="agenthub-topnav">
                        <span className={activePage === "home" ? "is-active" : ""} onClick={() => nav("home")}>Home</span>
                        <span className={activePage === "orchestrator" ? "is-active" : ""} onClick={() => nav("orchestrator")}>Orchestrator</span>
                        <span className={activePage === "agents" ? "is-active" : ""} onClick={() => nav("agents")}>Agents</span>
                    </nav>
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
                {/* Sidebar */}
                <aside className="agenthub-sidebar">
                    <div className="agenthub-sidebar-brand">
                        <div className="agenthub-brand-icon"><BrainCircuit24Regular /></div>
                        <div>
                            <Text weight="bold" size={300}>AgentHub</Text>
                            <Text size={100} className="agenthub-brand-sub">FABRIC ENTERPRISE</Text>
                        </div>
                    </div>

                    <nav className="agenthub-sidenav">
                        <SideNavItem icon={<Home24Regular />} label="Home" active={activePage === "home"} onClick={() => nav("home")} />
                        <SideNavItem icon={<BrainCircuit24Regular />} label="Orchestrator" active={activePage === "orchestrator"} onClick={() => nav("orchestrator")} />
                        <SideNavItem icon={<Bot24Regular />} label="Agents" active={activePage === "agents"} onClick={() => nav("agents")} />
                        <SideNavItem icon={<Settings24Regular />} label="Settings" active={activePage === "settings"} onClick={() => nav("settings")} />
                    </nav>

                    <div className="agenthub-sidebar-footer">
                        <div className="sidenav-footer-item" onClick={auth.signOut}>
                            <SignOut24Regular /> <Text size={200}>Sign out ({auth.githubUser})</Text>
                        </div>
                        <div className="sidenav-footer-item">
                            <QuestionCircle24Regular /> <Text size={200}>Support</Text>
                        </div>
                        <div className="sidenav-footer-item">
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
                        <Route path={`${match.path}/settings`}><SettingsPage workloadClient={workloadClient} /></Route>
                        <Route path={`${match.path}/job/:jobId`}><JobDetailPage workloadClient={workloadClient} /></Route>
                        <Route path={match.path}><DashboardPage workloadClient={workloadClient} /></Route>
                    </Switch>
                </main>
            </div>
        </div>
    );
}

function SideNavItem({ icon, label, active, onClick }: {
    icon: React.ReactNode; label: string; active: boolean; onClick: () => void;
}) {
    return (
        <div className={`sidenav-item ${active ? "sidenav-item--active" : ""}`} onClick={onClick}>
            {icon}
            <Text size={300} weight={active ? "semibold" : "regular"}>{label}</Text>
        </div>
    );
}
