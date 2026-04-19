import { ItemLikeV2, WorkloadClientAPI } from '@ms-fabric/workload-client';

// Represents an item as defined in the frontend manifest.
export interface ItemManifest {
    name: string;
    displayName: string;
    editor: {
        path: string;
    };
}

// Represents a reference to a fabric item.
export interface ItemReference {
    workspaceId: string;
    id: string;
}

// Represents a generic fabric item with common properties.
export interface GenericItem extends ItemReference {
    type: string;
    displayName: string;
    description: string;
    createdBy?: string;
    createdDate?: Date;
    lastModifiedBy?: string;
    lastModifiedDate?: Date;
}

// Represents a workload item with extended metadata.
export interface WorkloadItem<T> extends GenericItem {
    extendedMetdata?: T;
}

// Represents the item-specific payload passed with the CreateItem request.
// AgentHub items currently have no custom metadata; reserved for future use.
export interface CreateItemPayload {
    // Intentionally empty — AgentHub item has no custom create-time payload.
}

// Represents the item-specific payload passed with the UpdateItem request.
export interface UpdateItemPayload {
    // Intentionally empty — AgentHub item has no custom update-time payload.
}

// Represents the item-specific payload returned by the GetItemPayload request.
export interface ItemPayload {
    // Intentionally empty — AgentHub item has no custom server-side payload.
}

export interface TabContentProps {
    workloadClient: WorkloadClientAPI;
    workloadName?: string;
    item?: WorkloadItem<ItemPayload>;
}

export interface ItemCreationFailureData {
    errorCode?: string;
    resultCode?: string;
}

export interface ItemCreationSuccessData {
    item: ItemLikeV2;
}
