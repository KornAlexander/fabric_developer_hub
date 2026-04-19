import React, { useEffect, useMemo, useState } from "react";
import { Spinner, Button } from "@fluentui/react-components";
import {
    Search20Regular,
    Add20Regular,
    ArrowSync20Regular,
    Bot24Regular,
    DataUsage24Regular,
    DataPie24Regular,
    ShieldCheckmark24Regular,
    Stream24Regular,
    DataBarVertical24Regular,
    Wrench24Regular,
    Code20Regular,
    Database20Regular,
    Edit16Regular,
    DataHistogram16Regular,
    Wrench16Regular,
    Add16Regular,
    AddCircle20Regular,
    Settings16Regular,
    Dismiss16Regular,
    Copy20Regular,
    MoreVertical20Regular,
    CheckmarkCircle16Filled,
    ShieldCheckmark20Regular,
    PlayCircle16Regular,
    Delete20Regular,
    Warning20Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import * as api from "../../controller/AgentHubApi";
import { readPreloaded, setPreloaded } from "./pagePreloadCache";
import { useSearch } from "./SearchContext";
import { fuzzyFilter } from "./fuzzySearch";

interface AgentsPageProps {
    workloadClient: WorkloadClientAPI;
}

type AgentTone = "blue" | "purple" | "teal" | "amber" | "emerald";

interface AgentVisual {
    icon: React.ReactNode;
    tone: AgentTone;
}

function pickVisual(category?: string, name?: string): AgentVisual {
    const lower = `${category || ""} ${name || ""}`.toLowerCase();
    if (lower.includes("engineer") || lower.includes("etl")) {
        return { icon: <DataUsage24Regular />, tone: "blue" };
    }
    if (lower.includes("admin") || lower.includes("govern") || lower.includes("secur")) {
        return { icon: <ShieldCheckmark24Regular />, tone: "purple" };
    }
    if (lower.includes("realtime") || lower.includes("stream") || lower.includes("event")) {
        return { icon: <Stream24Regular />, tone: "teal" };
    }
    if (lower.includes("report") || lower.includes("sales")) {
        return { icon: <DataBarVertical24Regular />, tone: "amber" };
    }
    if (lower.includes("dashboard") || lower.includes("bi") || lower.includes("powerbi") || lower.includes("analy")) {
        return { icon: <DataPie24Regular />, tone: "emerald" };
    }
    return { icon: <Bot24Regular />, tone: "blue" };
}

function skillKind(tag: string): "authoring" | "consumption" | "tool" {
    const t = tag.toLowerCase();
    if (t.includes("author")) return "authoring";
    if (t.includes("consum") || t.includes("query")) return "consumption";
    return "tool";
}

function skillIcon(kind: "authoring" | "consumption" | "tool") {
    if (kind === "authoring") return <Edit16Regular />;
    if (kind === "consumption") return <DataHistogram16Regular />;
    return <Wrench16Regular />;
}

export function AgentsPage({ workloadClient: _workloadClient }: AgentsPageProps) {
    // If navTo() prefetched agent data before route-changing, use it
    // directly so the page mounts fully populated with no skeleton flicker.
    const preloaded = readPreloaded<{ templates: any[]; myConfigs: any[] }>("agents");
    const [templates, setTemplates] = useState<any[]>(preloaded?.templates ?? []);
    const [myConfigs, setMyConfigs] = useState<any[]>(preloaded?.myConfigs ?? []);
    const [loading, setLoading] = useState(preloaded === undefined);
    const [slowLoading, setSlowLoading] = useState(false);
    const [activeTab, setActiveTab] = useState<"my-agents" | "skill-library" | "marketplace">("my-agents");
    // The search term is driven by the global topbar via SearchContext so
    // typing in either the topbar input or the local agents search bar keeps
    // them in sync.
    const { query: search, setQuery: setSearch } = useSearch();
    const [selectedId, setSelectedId] = useState<string | null>(
        () => preloaded?.templates?.[0]?.id ?? null,
    );
    const [savingId, setSavingId] = useState<string | null>(null);
    const [removingId, setRemovingId] = useState<string | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);

    const githubToken = sessionStorage.getItem("github_token") || "";

    useEffect(() => {
        // If we already have preloaded data, skip the initial fetch.
        if (!loading) return;
        loadData();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    async function loadData() {
        setLoading(true);
        setSlowLoading(false);
        setLoadError(null);
        const slowTimer = window.setTimeout(() => setSlowLoading(true), 1200);
        try {
            const [tpls, configs] = await Promise.all([
                api.listAgentTemplates({ githubToken }),
                api.listMyAgents({ githubToken }).catch(() => [] as any[]),
            ]);
            setTemplates(tpls || []);
            setMyConfigs(configs || []);
            setSelectedId(prev => prev || ((tpls || [])[0]?.id ?? null));
            setPreloaded("agents", { templates: tpls || [], myConfigs: configs || [] });
        } catch (e: any) {
            console.error("Failed to load agents:", e);
            const msg = e?.message || String(e) || "Unknown error";
            const isNetwork = /failed to fetch|networkerror|load failed/i.test(msg);
            setLoadError(
                isNetwork
                    ? "Can't reach the Developer Hub backend. Check that it's running, then retry."
                    : `Failed to load agents: ${msg}`,
            );
            setTemplates([]);
        } finally {
            window.clearTimeout(slowTimer);
            setLoading(false);
            setSlowLoading(false);
        }
    }

    const myTemplateIds = useMemo(() => new Set(myConfigs.map((c: any) => c.agent_template_id)), [myConfigs]);

    const visibleAgents = useMemo(() => {
        const term = search.trim();
        let list: any[];
        if (activeTab === "my-agents") {
            list = templates.filter(t => myTemplateIds.has(t.id));
        } else if (activeTab === "marketplace") {
            list = templates.filter(t => !myTemplateIds.has(t.id));
        } else {
            list = templates;
        }
        if (!term) return list;
        // Fuzzy match: tolerant to typos + subsequence, ranked best-first.
        return fuzzyFilter(term, list, (t: any) => [
            t.name,
            t.display_name,
            t.description,
            ...(Array.isArray(t.tags) ? t.tags : []),
        ]);
    }, [templates, myTemplateIds, activeTab, search]);

    const skillsCount = useMemo(() => {
        const s = new Set<string>();
        templates.forEach(t => (t.tags || []).forEach((tag: string) => s.add(tag)));
        return s.size;
    }, [templates]);

    const allSkills = useMemo(() => {
        const s = new Set<string>();
        templates.forEach(t => (t.tags || []).forEach((tag: string) => s.add(tag)));
        return Array.from(s).sort();
    }, [templates]);

    const selected = useMemo(() => {
        if (!selectedId) return null;
        return templates.find(t => t.id === selectedId) || null;
    }, [selectedId, templates]);

    const selectedConfig = useMemo(() => {
        if (!selected) return null;
        return myConfigs.find((c: any) => c.agent_template_id === selected.id) || null;
    }, [selected, myConfigs]);

    async function enableAgent(agent: any) {
        setSavingId(agent.id);
        try {
            await api.configureAgent({
                agent_template_id: agent.id,
                access_levels: { warehouse_read: true, warehouse_write: false },
                tool_integrations: { sql_warehouse: true, data_factory: false, teams: false, outlook: false },
            }, { githubToken });
            await loadData();
        } catch (e) {
            console.error("Failed to enable agent:", e);
        } finally {
            setSavingId(null);
        }
    }

    async function removeAgent(config: any) {
        setRemovingId(config.id);
        try {
            await api.deleteMyAgent(config.id, { githubToken });
            await loadData();
        } catch (e) {
            console.error("Failed to remove agent:", e);
        } finally {
            setRemovingId(null);
        }
    }

    const newInMarketplace = templates.filter(t => !myTemplateIds.has(t.id)).length;
    const enabledCount = myConfigs.length;

    return (
        <div className="agents-page">
            {/* ── Header with tabs ── */}
            <div className="agents-header">
                <div className="agents-header-row">
                    <div>
                        <h1 className="agents-title">Agents &amp; Skills</h1>
                        <p className="agents-subtitle">
                            Build, compose, and manage AI agents from modular skills. Powered by{" "}
                            <span className="agents-subtitle-accent">skills-for-fabric</span>.
                        </p>
                    </div>
                    <div className="agents-header-actions">
                        <button type="button" className="agents-btn-secondary" onClick={loadData}>
                            <ArrowSync20Regular /> Check updates
                        </button>
                        <button type="button" className="agents-btn-primary">
                            <Add20Regular /> Create Agent
                        </button>
                    </div>
                </div>
                <div className="agents-tabs">
                    <button
                        type="button"
                        className={`agents-tab ${activeTab === "my-agents" ? "agents-tab--active" : ""}`}
                        onClick={() => setActiveTab("my-agents")}
                    >
                        My Agents
                    </button>
                    <button
                        type="button"
                        className={`agents-tab ${activeTab === "skill-library" ? "agents-tab--active" : ""}`}
                        onClick={() => setActiveTab("skill-library")}
                    >
                        Skill Library
                    </button>
                    <button
                        type="button"
                        className={`agents-tab ${activeTab === "marketplace" ? "agents-tab--active" : ""}`}
                        onClick={() => setActiveTab("marketplace")}
                    >
                        Marketplace
                        {newInMarketplace > 0 && (
                            <span className="agents-tab-badge">{newInMarketplace} new</span>
                        )}
                    </button>
                </div>
            </div>

            {/* ── Body: list + detail panel ── */}
            <div className="agents-body">
                <div className="agents-list-col">
                    {/* Stats / search bar */}
                    <div className="agents-statsbar">
                        <div className="agents-stat">
                            <Bot24Regular className="agents-stat-icon" />
                            {loading ? (
                                <span className="skeleton skeleton-line skeleton-line--stat" />
                            ) : (
                                <>
                                    <span className="agents-stat-num">{visibleAgents.length}</span>
                                    <span className="agents-stat-label">{activeTab === "skill-library" ? "skills shown" : "agents"}</span>
                                </>
                            )}
                        </div>
                        <div className="agents-stat">
                            <Wrench24Regular className="agents-stat-icon" />
                            {loading ? (
                                <span className="skeleton skeleton-line skeleton-line--stat" />
                            ) : (
                                <>
                                    <span className="agents-stat-num">{skillsCount}</span>
                                    <span className="agents-stat-label">skills installed</span>
                                </>
                            )}
                        </div>
                        <div className="agents-stat">
                            <CheckmarkCircle16Filled className="agents-stat-icon agents-stat-icon--ok" />
                            {loading ? (
                                <span className="skeleton skeleton-line skeleton-line--stat" />
                            ) : (
                                <span className="agents-stat-label agents-stat-label--ok">
                                    {enabledCount > 0 ? "All healthy" : "None enabled"}
                                </span>
                            )}
                        </div>
                        <div className="agents-search-wrap">
                            <Search20Regular className="agents-search-icon" />
                            <input
                                id="agents-search"
                                name="agentsSearch"
                                type="text"
                                placeholder="Search agents, skills, or capabilities…"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                className="agents-search-input"
                                aria-label="Search agents"
                                disabled={loading}
                            />
                        </div>
                    </div>

                    {loading ? (
                        <div className="agent-card-list" aria-busy="true" aria-label="Loading agents">
                            {[0, 1].map(i => (
                                <div key={i} className="agent-card agent-card--skeleton" aria-hidden="true">
                                    <div className="skeleton skeleton-icon skeleton-icon--lg" />
                                    <div className="agent-card-body">
                                        <div className="agent-card-header">
                                            <div className="skeleton skeleton-line skeleton-line--title" />
                                            <div className="skeleton skeleton-pill skeleton-pill--sm" />
                                        </div>
                                        <div className="skeleton skeleton-line skeleton-line--goal" />
                                        <div className="skeleton skeleton-line skeleton-line--goal-short" />
                                        <div className="agent-card-skills">
                                            <div className="skeleton skeleton-pill skeleton-pill--wide" />
                                            <div className="skeleton skeleton-pill skeleton-pill--wide" />
                                            <div className="skeleton skeleton-pill skeleton-pill--wide" />
                                        </div>
                                    </div>
                                    <div className="agent-card-meta">
                                        <div className="skeleton skeleton-line skeleton-line--meta" />
                                        <div className="skeleton skeleton-line skeleton-line--action" />
                                    </div>
                                </div>
                            ))}
                            {slowLoading && (
                                <div className="agents-slow-hint" role="status">
                                    <Spinner size="tiny" />
                                    <span>Still loading—this is taking longer than usual…</span>
                                </div>
                            )}
                        </div>
                    ) : activeTab === "skill-library" ? (
                        <div className="skill-library-grid">
                            {allSkills.map(tag => {
                                const kind = skillKind(tag);
                                return (
                                    <div key={tag} className={`skill-tile skill-tile--${kind}`}>
                                        <div className="skill-tile-icon">{skillIcon(kind)}</div>
                                        <div className="skill-tile-name">{tag}</div>
                                        <div className="skill-tile-kind">{kind}</div>
                                    </div>
                                );
                            })}
                            {allSkills.length === 0 && (
                                <div className="agents-empty">No skills found.</div>
                            )}
                        </div>
                    ) : (
                        <div className="agent-card-list">
                            {visibleAgents.map((agent: any) => {
                                const visual = pickVisual(agent.category, agent.name);
                                const isEnabled = myTemplateIds.has(agent.id);
                                const isSelected = agent.id === selectedId;
                                const tags: string[] = (agent.tags || []).slice(0, 4);
                                return (
                                    <div
                                        key={agent.id}
                                        className={`agent-card ${isSelected ? "agent-card--selected" : ""}`}
                                        onClick={() => setSelectedId(agent.id)}
                                    >
                                        <div className={`agent-card-icon agent-card-icon--${visual.tone}`}>
                                            {visual.icon}
                                        </div>
                                        <div className="agent-card-body">
                                            <div className="agent-card-header">
                                                <h3 className="agent-card-name">{agent.display_name || agent.name}</h3>
                                                <span className="agent-card-publisher">
                                                    {agent.publisher || "Microsoft"}
                                                </span>
                                                {isEnabled ? (
                                                    <span className="agent-card-status agent-card-status--active">
                                                        <span className="status-dot status-dot--green" />
                                                        Active
                                                    </span>
                                                ) : (
                                                    <span className="agent-card-status agent-card-status--available">
                                                        <span className="status-dot" />
                                                        Available
                                                    </span>
                                                )}
                                            </div>
                                            <p className="agent-card-desc">
                                                {(agent.description || "").slice(0, 180)}
                                            </p>
                                            <div className="agent-card-skills">
                                                {tags.map((tag: string) => {
                                                    const kind = skillKind(tag);
                                                    return (
                                                        <span key={tag} className={`skill-pill skill-pill--${kind}`}>
                                                            {skillIcon(kind)}
                                                            {tag}
                                                        </span>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                        <div className="agent-card-meta">
                                            <div className="agent-card-meta-num">
                                                {(agent.tags || []).length} skill{(agent.tags || []).length === 1 ? "" : "s"}
                                            </div>
                                            <div className="agent-card-meta-sub">
                                                v{agent.version || "1.0.0"}
                                            </div>
                                        </div>
                                    </div>
                                );
                            })}

                            {visibleAgents.length === 0 && !loadError && (
                                <div className="agents-empty">
                                    {activeTab === "my-agents"
                                        ? "No agents enabled yet. Switch to Marketplace to add agents."
                                        : "No agents match your search."}
                                </div>
                            )}

                            {loadError && (
                                <div className="sessions-error" role="alert">
                                    <Warning20Regular />
                                    <div className="sessions-error-body">
                                        <div className="sessions-error-title">Couldn't load agents</div>
                                        <div className="sessions-error-msg">{loadError}</div>
                                    </div>
                                    <Button appearance="primary" size="small" onClick={loadData}>
                                        Retry
                                    </Button>
                                </div>
                            )}

                            {/* Create new agent prompt card */}
                            <div className="agent-create-card">
                                <div className="agent-create-icon"><Add20Regular /></div>
                                <div>
                                    <div className="agent-create-title">Create a new agent</div>
                                    <div className="agent-create-sub">Compose from existing skills or start from scratch</div>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* ── Detail panel ── */}
                <aside className="agent-detail">
                    {selected ? (
                        <DetailPanel
                            agent={selected}
                            config={selectedConfig}
                            isEnabled={myTemplateIds.has(selected.id)}
                            saving={savingId === selected.id}
                            removing={selectedConfig ? removingId === selectedConfig.id : false}
                            onEnable={() => enableAgent(selected)}
                            onRemove={() => selectedConfig && removeAgent(selectedConfig)}
                        />
                    ) : (
                        <div className="agent-detail-empty">
                            Select an agent to see details.
                        </div>
                    )}
                </aside>
            </div>
        </div>
    );
}

function DetailPanel({
    agent, config: _config, isEnabled, saving, removing, onEnable, onRemove,
}: {
    agent: any;
    config: any | null;
    isEnabled: boolean;
    saving: boolean;
    removing: boolean;
    onEnable: () => void;
    onRemove: () => void;
}) {
    const visual = pickVisual(agent.category, agent.name);
    const tags: string[] = agent.tags || [];

    return (
        <div className="agent-detail-inner">
            {/* Header */}
            <div className="agent-detail-header">
                <div className="agent-detail-identity">
                    <div className={`agent-card-icon agent-card-icon--${visual.tone} agent-card-icon--lg`}>
                        {visual.icon}
                    </div>
                    <div>
                        <h2 className="agent-detail-name">{agent.display_name || agent.name}</h2>
                        <div className="agent-detail-meta-row">
                            <span className="agent-card-publisher">{agent.publisher || "Microsoft"}</span>
                            <span className="agent-detail-version">v{agent.version || "1.0.0"}</span>
                        </div>
                    </div>
                </div>
                <div className="agent-detail-actions-top">
                    <button type="button" className="icon-btn" title="Clone agent">
                        <Copy20Regular />
                    </button>
                    <button type="button" className="icon-btn" title="More options">
                        <MoreVertical20Regular />
                    </button>
                </div>
            </div>

            <p className="agent-detail-desc">{agent.description}</p>

            {/* Personas */}
            <div className="agent-detail-section">
                <div className="agent-detail-label">Personas</div>
                <div className="agent-detail-pills">
                    {(agent.personas || ["Developer", "Data Engineer"]).map((p: string) => (
                        <span key={p} className="persona-pill">
                            {p.toLowerCase().includes("dev") ? <Code20Regular /> : <Database20Regular />}
                            {p}
                        </span>
                    ))}
                </div>
            </div>

            {/* Skills composition */}
            <div className="agent-detail-section">
                <div className="agent-detail-section-head">
                    <div className="agent-detail-label">Skills ({tags.length})</div>
                    <button type="button" className="agent-detail-add">
                        <Add16Regular /> Add skill
                    </button>
                </div>
                <div className="agent-detail-skill-list">
                    {tags.map(tag => {
                        const kind = skillKind(tag);
                        return (
                            <div key={tag} className={`skill-row skill-row--${kind}`}>
                                <div className="skill-row-icon">{skillIcon(kind)}</div>
                                <div className="skill-row-body">
                                    <div className="skill-row-name">
                                        {tag}
                                        <span className={`skill-row-kind skill-row-kind--${kind}`}>
                                            {kind === "authoring" ? "Authoring" : kind === "consumption" ? "Consumption" : "Tool"}
                                        </span>
                                    </div>
                                </div>
                                <div className="skill-row-actions">
                                    <button type="button" className="icon-btn icon-btn--xs" title="Configure skill">
                                        <Settings16Regular />
                                    </button>
                                    <button type="button" className="icon-btn icon-btn--xs" title="Remove skill">
                                        <Dismiss16Regular />
                                    </button>
                                </div>
                            </div>
                        );
                    })}
                    <button type="button" className="skill-add-dropzone">
                        <AddCircle20Regular /> Add skill from library
                    </button>
                </div>
            </div>

            {/* Authentication */}
            <div className="agent-detail-section">
                <div className="agent-detail-label">Authentication</div>
                <div className="agent-detail-auth">
                    <div className="agent-detail-auth-row">
                        <ShieldCheckmark20Regular className="agent-detail-auth-icon" />
                        <span className="agent-detail-auth-title">Azure AD authenticated</span>
                    </div>
                    <p className="agent-detail-auth-sub">
                        All operations use your Azure AD identity. No secrets or tokens stored by skills.
                    </p>
                </div>
            </div>

            {/* Security & Responsible AI */}
            <div className="agent-detail-section">
                <div className="agent-detail-label">Security &amp; Responsible AI</div>
                <div className="agent-detail-checks">
                    {[
                        "Prompt-injection safe",
                        "No arbitrary execution",
                        "Secret scanning",
                        "OWASP LLM Top 10",
                    ].map(label => (
                        <div key={label} className="agent-detail-check">
                            <CheckmarkCircle16Filled className="agent-detail-check-icon" />
                            {label}
                        </div>
                    ))}
                </div>
            </div>

            {/* Example use cases */}
            <div className="agent-detail-section">
                <div className="agent-detail-label">Example use cases</div>
                <div className="agent-detail-examples">
                    {(agent.examples || ["NYC Taxi Medallion Pipeline", "Document My Workspace", "Analytics PDF Report"]).map((ex: string) => (
                        <div key={ex} className="agent-detail-example">
                            <PlayCircle16Regular />
                            <span>{ex}</span>
                        </div>
                    ))}
                </div>
            </div>

            {/* Footer actions */}
            <div className="agent-detail-footer">
                {isEnabled ? (
                    <>
                        <button
                            type="button"
                            className="agents-btn-primary agent-detail-cta"
                            disabled
                        >
                            <CheckmarkCircle16Filled /> Enabled
                        </button>
                        <button
                            type="button"
                            className="agents-btn-secondary agent-detail-cta"
                            onClick={onRemove}
                            disabled={removing}
                        >
                            {removing ? <Spinner size="tiny" /> : <Delete20Regular />}
                            Remove
                        </button>
                    </>
                ) : (
                    <>
                        <button
                            type="button"
                            className="agents-btn-primary agent-detail-cta"
                            onClick={onEnable}
                            disabled={saving}
                        >
                            {saving ? <Spinner size="tiny" /> : <Copy20Regular />}
                            Enable agent
                        </button>
                        <button
                            type="button"
                            className="agents-btn-secondary agent-detail-cta"
                            disabled
                        >
                            <Copy20Regular /> Clone &amp; Customize
                        </button>
                    </>
                )}
            </div>

        </div>
    );
}
