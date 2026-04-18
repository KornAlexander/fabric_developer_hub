import React, { useEffect, useState } from "react";
import {
    Badge,
    Button,
    Card,
    CardHeader,
    Text,
    Spinner,
    Subtitle1,
    Body1,
    Caption1,
    Tab,
    TabList,
    SelectTabEvent,
    SelectTabData,
    Input,
    Checkbox,
    Divider,
    DrawerBody,
    DrawerHeader,
    DrawerHeaderTitle,
    DrawerFooter,
    OverlayDrawer,
    CheckboxOnChangeData,
} from "@fluentui/react-components";
import {
    Search24Regular,
    Dismiss24Regular,
    Bot24Regular,
    Wrench24Regular,
    Shield24Regular,
    DataBarVertical24Regular,
} from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import * as api from "../../controller/AgentHubApi";

interface AgentsPageProps {
    workloadClient: WorkloadClientAPI;
}

function categoryColor(cat: string): "informative" | "success" | "warning" | "important" | "danger" {
    switch (cat) {
        case "ENGINEERING": return "informative";
        case "ANALYTICS": return "success";
        case "ADMIN": return "warning";
        default: return "important";
    }
}

function categoryIcon(cat: string) {
    switch (cat) {
        case "ENGINEERING": return <Wrench24Regular />;
        case "ANALYTICS": return <DataBarVertical24Regular />;
        case "ADMIN": return <Shield24Regular />;
        default: return <Bot24Regular />;
    }
}

export function AgentsPage({ workloadClient }: AgentsPageProps) {
    const [templates, setTemplates] = useState<any[]>([]);
    const [myConfigs, setMyConfigs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState("marketplace");
    const [search, setSearch] = useState("");
    const [selectedAgent, setSelectedAgent] = useState<any | null>(null);
    const [drawerOpen, setDrawerOpen] = useState(false);
    const [configState, setConfigState] = useState<Record<string, boolean>>({});
    const [saving, setSaving] = useState(false);

    const githubToken = sessionStorage.getItem("github_token") || "";

    useEffect(() => {
        loadData();
    }, []);

    async function loadData() {
        setLoading(true);
        try {
            const [tpls, configs] = await Promise.all([
                api.listAgentTemplates({ githubToken }),
                api.listMyAgents({ githubToken }).catch(() => [] as any[]),
            ]);
            setTemplates(tpls || []);
            setMyConfigs(configs || []);
        } catch (e) {
            console.error("Failed to load agents:", e);
        } finally {
            setLoading(false);
        }
    }

    function openConfig(agent: any) {
        setSelectedAgent(agent);
        setConfigState({
            warehouse_read: true,
            warehouse_write: false,
            sql_warehouse: true,
            data_factory: false,
            teams: false,
            outlook: false,
        });
        setDrawerOpen(true);
    }

    async function handleDeploy() {
        if (!selectedAgent) return;
        setSaving(true);
        try {
            await api.configureAgent({
                agent_template_id: selectedAgent.id,
                access_levels: {
                    warehouse_read: configState.warehouse_read ?? true,
                    warehouse_write: configState.warehouse_write ?? false,
                },
                tool_integrations: {
                    sql_warehouse: configState.sql_warehouse ?? true,
                    data_factory: configState.data_factory ?? false,
                    teams: configState.teams ?? false,
                    outlook: configState.outlook ?? false,
                },
            }, { githubToken });
            setDrawerOpen(false);
            await loadData();
        } catch (e) {
            console.error("Failed to configure agent:", e);
        } finally {
            setSaving(false);
        }
    }

    const filtered = templates.filter(t =>
        !search || t.name.toLowerCase().includes(search.toLowerCase()) ||
        t.description.toLowerCase().includes(search.toLowerCase()) ||
        t.tags.some((tag: string) => tag.toLowerCase().includes(search.toLowerCase()))
    );

    const myTemplateIds = new Set(myConfigs.map((c: any) => c.agent_template_id));

    return (
        <div className="agents-page">
            <div className="agents-page-header">
                <TabList
                    selectedValue={activeTab}
                    onTabSelect={(_: SelectTabEvent, d: SelectTabData) => setActiveTab(d.value as string)}
                >
                    <Tab value="marketplace">Marketplace</Tab>
                    <Tab value="my-agents">My Agents</Tab>
                    <Tab value="analytics">Analytics</Tab>
                </TabList>
            </div>

            {activeTab === "marketplace" && (
                <div className="marketplace-content">
                    <Text size={700} weight="bold" as="h2">Agent Marketplace</Text>
                    <Body1 className="marketplace-subtitle">
                        Discover and deploy specialized AI agents across your Fabric environment.
                        Scale your data engineering and analysis workflows automatically.
                    </Body1>

                    <Input
                        contentBefore={<Search24Regular />}
                        placeholder="Search agents..."
                        value={search}
                        onChange={(_, d) => setSearch(d.value)}
                        className="agents-search"
                    />

                    {loading ? (
                        <div className="agents-loading"><Spinner label="Loading agents..." /></div>
                    ) : (
                        <div className="agent-marketplace-grid">
                            {filtered.map((agent) => (
                                <Card key={agent.id} className="marketplace-card">
                                    <CardHeader
                                        image={<div className="marketplace-card-icon">{categoryIcon(agent.category)}</div>}
                                        header={
                                            <div className="marketplace-card-header">
                                                <Text weight="semibold" size={400}>{agent.display_name || agent.name}</Text>
                                                <Badge appearance="filled" color={categoryColor(agent.category)} size="small">
                                                    {agent.category}
                                                </Badge>
                                            </div>
                                        }
                                    />
                                    <Body1 className="marketplace-card-desc">
                                        {agent.description?.slice(0, 120)}
                                    </Body1>
                                    <div className="marketplace-card-tags">
                                        {agent.tags?.map((tag: string) => (
                                            <Badge key={tag} appearance="outline" size="small">{tag}</Badge>
                                        ))}
                                    </div>
                                    <div className="marketplace-card-footer">
                                        <Caption1>v{agent.version}</Caption1>
                                        {myTemplateIds.has(agent.id) ? (
                                            <Badge appearance="filled" color="success" size="small">Enabled</Badge>
                                        ) : (
                                            <Button
                                                appearance="transparent"
                                                size="small"
                                                onClick={() => openConfig(agent)}
                                            >
                                                Configure →
                                            </Button>
                                        )}
                                    </div>
                                </Card>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {activeTab === "my-agents" && (
                <div className="my-agents-content">
                    <Subtitle1>My Agents</Subtitle1>
                    {myConfigs.length === 0 ? (
                        <Body1>No agents configured yet. Visit the Marketplace to add agents.</Body1>
                    ) : (
                        <div className="agent-marketplace-grid">
                            {myConfigs.map((config: any) => {
                                const tpl = config.template;
                                return (
                                    <Card key={config.id} className="marketplace-card">
                                        <CardHeader
                                            header={
                                                <Text weight="semibold" size={400}>
                                                    {tpl?.display_name || config.agent_template_id}
                                                </Text>
                                            }
                                        />
                                        <Body1>{tpl?.description?.slice(0, 100)}</Body1>
                                        <div className="marketplace-card-footer">
                                            <Caption1>Configured</Caption1>
                                            <Button
                                                appearance="subtle"
                                                size="small"
                                                icon={<Dismiss24Regular />}
                                                onClick={async () => {
                                                    await api.deleteMyAgent(config.id, { githubToken });
                                                    await loadData();
                                                }}
                                            >
                                                Remove
                                            </Button>
                                        </div>
                                    </Card>
                                );
                            })}
                        </div>
                    )}
                </div>
            )}

            {activeTab === "analytics" && (
                <div className="analytics-content">
                    <Subtitle1>Analytics</Subtitle1>
                    <Body1>Usage analytics will be available in a future update.</Body1>
                </div>
            )}

            {/* Agent Configuration Drawer */}
            <OverlayDrawer
                open={drawerOpen}
                onOpenChange={(_, { open }) => setDrawerOpen(open)}
                position="end"
                size="medium"
            >
                <DrawerHeader>
                    <DrawerHeaderTitle
                        action={
                            <Button
                                appearance="subtle"
                                icon={<Dismiss24Regular />}
                                onClick={() => setDrawerOpen(false)}
                            />
                        }
                    >
                        Agent Configuration
                    </DrawerHeaderTitle>
                </DrawerHeader>
                <DrawerBody>
                    {selectedAgent && (
                        <div className="agent-config-body">
                            <div className="agent-config-identity">
                                <div className="agent-config-icon">{categoryIcon(selectedAgent.category)}</div>
                                <div>
                                    <Text weight="bold" size={500}>{selectedAgent.display_name || selectedAgent.name}</Text>
                                    <Caption1>{selectedAgent.category} SPECIALIST</Caption1>
                                </div>
                            </div>

                            <Divider />

                            <Subtitle1>Access Levels</Subtitle1>
                            <div className="config-checkboxes">
                                <Checkbox
                                    label="Read-Only Warehouse — View access to production data"
                                    checked={configState.warehouse_read ?? true}
                                    onChange={(_: any, d: CheckboxOnChangeData) => setConfigState(s => ({ ...s, warehouse_read: !!d.checked }))}
                                />
                                <Checkbox
                                    label="Write Permissions — Ability to update staging tables"
                                    checked={configState.warehouse_write ?? false}
                                    onChange={(_: any, d: CheckboxOnChangeData) => setConfigState(s => ({ ...s, warehouse_write: !!d.checked }))}
                                />
                            </div>

                            <Divider />

                            <Subtitle1>Tool Integrations</Subtitle1>
                            <div className="config-toggles">
                                {[
                                    { key: "sql_warehouse", label: "SQL Warehouse" },
                                    { key: "data_factory", label: "Data Factory" },
                                    { key: "teams", label: "Teams" },
                                    { key: "outlook", label: "Outlook" },
                                ].map(({ key, label }) => (
                                    <Button
                                        key={key}
                                        appearance={configState[key] ? "primary" : "outline"}
                                        size="small"
                                        onClick={() => setConfigState(s => ({ ...s, [key]: !s[key] }))}
                                    >
                                        {configState[key] ? "✓ " : ""}{label}
                                    </Button>
                                ))}
                            </div>
                        </div>
                    )}
                </DrawerBody>
                <DrawerFooter>
                    <Button appearance="primary" onClick={handleDeploy} disabled={saving}>
                        {saving ? <Spinner size="tiny" /> : "Enable Agent"}
                    </Button>
                    <Button appearance="secondary" onClick={() => setDrawerOpen(false)}>
                        Discard
                    </Button>
                </DrawerFooter>
            </OverlayDrawer>
        </div>
    );
}
