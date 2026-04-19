import { WorkloadItem } from "./models/AgentHubModel";
import { GetItemResult } from "@ms-fabric/workload-client";

export function convertGetItemResultToWorkloadItem<T>(item: GetItemResult): WorkloadItem<T> {
    let payload: T;
    if (item.workloadPayload) {
        try {
            payload = JSON.parse(item.workloadPayload);
        } catch (payloadParseError) {
            console.error(`Failed parsing payload for item ${item.objectId}`, payloadParseError);
        }
    }

    return {
        id: item.objectId,
        workspaceId: item.folderObjectId,
        type: item.itemType,
        displayName: item.displayName,
        description: item.description,
        extendedMetdata: payload,
        createdBy: item.createdByUser.name,
        createdDate: item.createdDate,
        lastModifiedBy: item.modifiedByUser.name,
        lastModifiedDate: item.lastUpdatedDate
    };
}

