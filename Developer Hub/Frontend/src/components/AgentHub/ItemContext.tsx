import React, { createContext, useContext, useState, useCallback, useEffect } from "react";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { callItemCreate, callItemGet, callItemUpdate } from "../../controller/AgentHubController";
import { convertGetItemResultToWorkloadItem } from "../../utils";

export interface AgentHubSettings {
    defaultModel: string;
    maxRounds: number;
    verboseDefault: boolean;
}

interface AgentHubPayload {
    "agenthub-metadata": AgentHubSettings & { configuredAgents?: any[] };
}

interface ItemContextValue {
    itemObjectId: string | null;
    workspaceObjectId: string | null;
    settings: AgentHubSettings | null;
    itemLoading: boolean;
    createItem: (name: string, description?: string, workspaceObjectIdOverride?: string) => Promise<string>;
    saveSettings: (settings: AgentHubSettings) => Promise<void>;
    loadItem: (objectId: string) => Promise<void>;
}

const ItemContext = createContext<ItemContextValue>({
    itemObjectId: null,
    workspaceObjectId: null,
    settings: null,
    itemLoading: false,
    createItem: async () => "",
    saveSettings: async () => {},
    loadItem: async () => {},
});

export function useItemContext() {
    return useContext(ItemContext);
}

const ITEM_TYPE = (process.env.WORKLOAD_NAME || "Org.DeveloperHub") + ".DeveloperHubDashboard";
const STORAGE_KEY_ITEM_ID = "agenthub_item_id";
const STORAGE_KEY_WORKSPACE_ID = "agenthub_workspace_id";

interface ItemProviderProps {
    workloadClient: WorkloadClientAPI;
    workspaceObjectId: string | null;
    routeItemObjectId: string | null;
    children: React.ReactNode;
}

export function ItemProvider({ workloadClient, workspaceObjectId, routeItemObjectId, children }: ItemProviderProps) {
    const [itemObjectId, setItemObjectId] = useState<string | null>(
        routeItemObjectId || sessionStorage.getItem(STORAGE_KEY_ITEM_ID)
    );
    // v1.11: track effective workspace id. Prop value (from ?ws= URL
    // param) is null when AgentHub is launched from the generic
    // launcher; in that case we remember the workspace the user picked
    // in the Save dialog so the Close button can navigate back to it.
    const [effectiveWorkspaceId, setEffectiveWorkspaceId] = useState<string | null>(
        workspaceObjectId || sessionStorage.getItem(STORAGE_KEY_WORKSPACE_ID)
    );
    const [settings, setSettings] = useState<AgentHubSettings | null>(null);
    const [itemLoading, setItemLoading] = useState(false);

    const loadItem = useCallback(async (objectId: string) => {
        setItemLoading(true);
        const defaultSettings: AgentHubSettings = {
            defaultModel: "gpt-4o",
            maxRounds: 15,
            verboseDefault: true,
        };
        try {
            const result = await callItemGet(objectId, workloadClient);
            if (!result) {
                // SDK call failed (auth / network / item not yet
                // visible to the workload backend). Fall back to
                // defaults so the UI still renders cleanly — settings
                // will get persisted on the next user-initiated save.
                console.warn(`[ItemContext] callItemGet returned null for ${objectId}; using defaults`);
                setSettings(defaultSettings);
                setItemObjectId(objectId);
                sessionStorage.setItem(STORAGE_KEY_ITEM_ID, objectId);
                return;
            }
            const item = convertGetItemResultToWorkloadItem<AgentHubPayload>(result);
            const meta = item.extendedMetdata?.["agenthub-metadata"];
            const loadedSettings: AgentHubSettings = meta
                ? {
                    defaultModel: meta.defaultModel ?? defaultSettings.defaultModel,
                    maxRounds: meta.maxRounds ?? defaultSettings.maxRounds,
                    verboseDefault: meta.verboseDefault ?? defaultSettings.verboseDefault,
                }
                : defaultSettings;
            setSettings(loadedSettings);
            setItemObjectId(objectId);
            sessionStorage.setItem(STORAGE_KEY_ITEM_ID, objectId);
            // v1.11: persist the item's workspace id so the Close
            // button can navigate back to it even when the iframe
            // wasn't bootstrapped with ?ws=.
            if (item.workspaceId) {
                setEffectiveWorkspaceId(item.workspaceId);
                sessionStorage.setItem(STORAGE_KEY_WORKSPACE_ID, item.workspaceId);
            }
            // v1.32: if the item has no payload yet (just got created
            // by the host's "+ New" wizard), eagerly persist the
            // default payload so the workload backend has something
            // to work with — mirrors how native Fabric items behave.
            if (!meta) {
                try {
                    const payload: AgentHubPayload = {
                        "agenthub-metadata": { ...defaultSettings, configuredAgents: [] },
                    };
                    await callItemUpdate(objectId, payload, workloadClient);
                } catch (err) {
                    console.warn("[ItemContext] initial payload write failed (non-fatal):", err);
                }
            }
        } catch (err) {
            console.error("Failed to load item:", err);
            // Don't leave the UI in a broken state — fall back to
            // defaults so the user can keep working. They'll see a
            // Power BI Fixer / Settings page, not a TypeError.
            setSettings(defaultSettings);
            setItemObjectId(objectId);
            sessionStorage.setItem(STORAGE_KEY_ITEM_ID, objectId);
        } finally {
            setItemLoading(false);
        }
    }, [workloadClient]);

    // Auto-load item if we have an ID
    useEffect(() => {
        if (itemObjectId && !settings) {
            loadItem(itemObjectId);
        }
    }, [itemObjectId]);

    const createItem = useCallback(async (name: string, description?: string, workspaceObjectIdOverride?: string): Promise<string> => {
        // v0.36: when AgentHub is opened from the generic launcher (no
        // ?ws= URL param), the URL-derived ``workspaceObjectId`` is
        // null. The Save dialog then asks the user to pick a workspace
        // and passes it through here as ``workspaceObjectIdOverride``.
        const targetWorkspaceId = workspaceObjectIdOverride || workspaceObjectId;
        if (!targetWorkspaceId) {
            throw new Error("No workspace context — cannot create item.");
        }
        const defaultPayload: AgentHubPayload = {
            "agenthub-metadata": {
                defaultModel: "gpt-4o",
                maxRounds: 15,
                verboseDefault: true,
                configuredAgents: [],
            },
        };
        const created = await callItemCreate(
            targetWorkspaceId,
            ITEM_TYPE,
            name,
            description || "",
            defaultPayload,
            workloadClient,
        );
        const newId = created.id;
        setItemObjectId(newId);
        sessionStorage.setItem(STORAGE_KEY_ITEM_ID, newId);
        setEffectiveWorkspaceId(targetWorkspaceId);
        sessionStorage.setItem(STORAGE_KEY_WORKSPACE_ID, targetWorkspaceId);
        setSettings(defaultPayload["agenthub-metadata"]);
        return newId;
    }, [workspaceObjectId, workloadClient]);

    const saveSettings = useCallback(async (newSettings: AgentHubSettings) => {
        // Always persist to sessionStorage as fallback
        sessionStorage.setItem("default_model", newSettings.defaultModel);
        sessionStorage.setItem("max_rounds", String(newSettings.maxRounds));
        sessionStorage.setItem("verbose_default", String(newSettings.verboseDefault));

        setSettings(newSettings);

        // If we have an item, persist to Fabric
        if (itemObjectId) {
            try {
                const payload: AgentHubPayload = {
                    "agenthub-metadata": { ...newSettings, configuredAgents: [] },
                };
                await callItemUpdate(itemObjectId, payload, workloadClient);
            } catch (err) {
                console.error("Failed to save settings to Fabric item:", err);
            }
        }
    }, [itemObjectId, workloadClient]);

    return (
        <ItemContext.Provider value={{
            itemObjectId,
            workspaceObjectId: effectiveWorkspaceId,
            settings,
            itemLoading,
            createItem,
            saveSettings,
            loadItem,
        }}>
            {children}
        </ItemContext.Provider>
    );
}
