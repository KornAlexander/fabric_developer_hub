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
    createItem: (name: string, description?: string) => Promise<string>;
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

const ITEM_TYPE = (process.env.WORKLOAD_NAME || "Org.AgentHub") + ".AgentHubItem";
const STORAGE_KEY_ITEM_ID = "agenthub_item_id";

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
    const [settings, setSettings] = useState<AgentHubSettings | null>(null);
    const [itemLoading, setItemLoading] = useState(false);

    const loadItem = useCallback(async (objectId: string) => {
        setItemLoading(true);
        try {
            const result = await callItemGet(objectId, workloadClient);
            const item = convertGetItemResultToWorkloadItem<AgentHubPayload>(result);
            const meta = item.extendedMetdata?.["agenthub-metadata"];
            if (meta) {
                setSettings({
                    defaultModel: meta.defaultModel ?? "gpt-4o",
                    maxRounds: meta.maxRounds ?? 15,
                    verboseDefault: meta.verboseDefault ?? true,
                });
            }
            setItemObjectId(objectId);
            sessionStorage.setItem(STORAGE_KEY_ITEM_ID, objectId);
        } catch (err) {
            console.error("Failed to load item:", err);
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

    const createItem = useCallback(async (name: string, description?: string): Promise<string> => {
        if (!workspaceObjectId) {
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
            workspaceObjectId,
            ITEM_TYPE,
            name,
            description || "",
            defaultPayload,
            workloadClient,
        );
        const newId = created.id;
        setItemObjectId(newId);
        sessionStorage.setItem(STORAGE_KEY_ITEM_ID, newId);
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
            workspaceObjectId,
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
