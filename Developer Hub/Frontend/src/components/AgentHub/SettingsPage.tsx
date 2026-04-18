import React, { useState, useEffect } from "react";
import {
    Button,
    Card,
    Input,
    Text,
    Subtitle1,
    Body1,
    Field,
    Dropdown,
    Option,
    Switch,
    Spinner,
    Badge,
} from "@fluentui/react-components";
import { Save24Regular } from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { useItemContext } from "./ItemContext";

declare const process: { env: Record<string, string | undefined> };
const BE = process.env.WORKLOAD_BE_URL || 'http://localhost:5000';

interface ModelInfo {
    id: string;
    name: string;
}

interface SettingsPageProps {
    workloadClient: WorkloadClientAPI;
}

const PREFERRED_DEFAULT = "claude-opus-4.6";

export function SettingsPage({ workloadClient }: SettingsPageProps) {
    const { itemObjectId, settings: itemSettings, saveSettings, itemLoading } = useItemContext();

    const [workspaceId] = useState(() => {
        const urlParams = new URLSearchParams(window.location.search);
        return urlParams.get("ws") || sessionStorage.getItem("workspace_id") || "(auto-detected from Fabric)";
    });
    const [defaultModel, setDefaultModel] = useState(
        itemSettings?.defaultModel || sessionStorage.getItem("default_model") || "gpt-4o"
    );
    const [maxRounds, setMaxRounds] = useState(
        itemSettings ? String(itemSettings.maxRounds) : (sessionStorage.getItem("max_rounds") || "15")
    );
    const [verboseDefault, setVerboseDefault] = useState(
        itemSettings ? itemSettings.verboseDefault : (sessionStorage.getItem("verbose_default") !== "false")
    );
    const [saved, setSaved] = useState(false);
    const [saving, setSaving] = useState(false);
    const [models, setModels] = useState<ModelInfo[]>([]);
    const [loadingModels, setLoadingModels] = useState(false);

    const githubToken = sessionStorage.getItem("github_token") || "";

    // Sync local state when item settings load
    useEffect(() => {
        if (itemSettings) {
            setDefaultModel(itemSettings.defaultModel);
            setMaxRounds(String(itemSettings.maxRounds));
            setVerboseDefault(itemSettings.verboseDefault);
        }
    }, [itemSettings]);

    useEffect(() => {
        if (githubToken) loadModels();
    }, [githubToken]);

    async function loadModels() {
        setLoadingModels(true);
        try {
            const resp = await fetch(`${BE}/api/github/models`, {
                headers: { Authorization: `Bearer ${githubToken}` },
            });
            if (resp.ok) {
                const data = await resp.json();
                const list: ModelInfo[] = (data.models || []).map((m: any) => ({
                    id: m.id,
                    name: m.name || m.id,
                }));
                setModels(list);

                // Auto-select preferred default if available and no saved preference
                const savedModel = sessionStorage.getItem("default_model");
                if (!savedModel || !list.some(m => m.id === savedModel)) {
                    const preferred = list.find(m => m.id === PREFERRED_DEFAULT);
                    if (preferred) {
                        setDefaultModel(preferred.id);
                    } else if (list.length > 0) {
                        setDefaultModel(list[0].id);
                    }
                }
            }
        } catch (e) {
            console.error("Failed to load models:", e);
        } finally {
            setLoadingModels(false);
        }
    }

    function handleSave() {
        setSaving(true);
        saveSettings({
            defaultModel,
            maxRounds: parseInt(maxRounds, 10) || 15,
            verboseDefault,
        }).then(() => {
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        }).finally(() => setSaving(false));
    }

    return (
        <div className="settings-page">
            <Text size={700} weight="bold" as="h2">Settings</Text>
            <Body1 className="settings-subtitle">Configure Developer Hub defaults and workspace preferences.</Body1>

            <Card className="settings-card">
                <Subtitle1>Workspace</Subtitle1>
                <Body1>Current workspace: <Text weight="semibold">{workspaceId}</Text></Body1>
                <Body1 style={{ color: "#605e5c", fontSize: 12 }}>
                    The workspace is automatically set from the Fabric context. You can override it per task in New Session.
                </Body1>
            </Card>

            <Card className="settings-card">
                <Subtitle1>Agent Defaults</Subtitle1>

                <Field label="Default Model">
                    {loadingModels ? (
                        <Spinner size="tiny" label="Loading models..." />
                    ) : (
                        <Dropdown
                            value={models.find(m => m.id === defaultModel)?.name || defaultModel}
                            onOptionSelect={(_, d) => setDefaultModel(d.optionValue || "gpt-4o")}
                        >
                            {models.map(m => (
                                <Option key={m.id} value={m.id} text={m.name}>{m.name}</Option>
                            ))}
                        </Dropdown>
                    )}
                </Field>

                <Field label="Max Agent Rounds" hint="Maximum tool-call iterations per agent">
                    <Input
                        type="number"
                        value={maxRounds}
                        onChange={(_, d) => setMaxRounds(d.value)}
                    />
                </Field>

                <div className="settings-toggle-row">
                    <Body1>Verbose reasoning log by default</Body1>
                    <Switch checked={verboseDefault} onChange={(_, d) => setVerboseDefault(d.checked)} />
                </div>
            </Card>

            <div className="settings-actions">
                <Button
                    appearance="primary"
                    icon={<Save24Regular />}
                    onClick={handleSave}
                    disabled={saving || itemLoading}
                >
                    {saving ? "Saving..." : "Save Settings"}
                </Button>
                {saved && <Text size={200} style={{ color: "#0ea50e" }}>Settings saved!</Text>}
                <Badge
                    appearance="outline"
                    color={itemObjectId ? "success" : "warning"}
                    size="small"
                    style={{ marginLeft: 8 }}
                >
                    {itemObjectId ? "Persisted to Fabric item" : "Session only"}
                </Badge>
            </div>
        </div>
    );
}
