import {
    ItemCreateContext,
    createWorkloadClient,
    InitParams,
    NotificationType,
} from '@ms-fabric/workload-client';

export async function initialize(params: InitParams) {
    const workloadClient = createWorkloadClient();
    const workloadName = process.env.WORKLOAD_NAME;

    workloadClient.action.onAction(async function ({ action, data }) {
        switch (action) {
            case 'open.agentHub':
                const { workspaceObjectId: agentHubWsId } = data as ItemCreateContext;
                return workloadClient.page.open({
                    workloadName: workloadName,
                    route: {
                        path: agentHubWsId ? `/agent-hub/orchestrator?ws=${agentHubWsId}` : `/agent-hub/orchestrator`,
                    },
                });

            case 'item.onCreationSuccess':
                const { item: createdItem } = data as any;
                // v1.32: eagerly persist a default workload payload so
                // the item shows up in the workspace as a "real" item
                // immediately, mirroring how native Fabric items
                // behave. Otherwise the editor opens an item with no
                // payload and the first ``getItem`` call fails.
                try {
                    await workloadClient.itemCrud.updateItem({
                        objectId: createdItem.objectId,
                        etag: undefined,
                        payload: {
                            workloadPayload: JSON.stringify({
                                "agenthub-metadata": {
                                    defaultModel: "gpt-4o",
                                    maxRounds: 15,
                                    verboseDefault: true,
                                    configuredAgents: [],
                                },
                            }),
                            payloadContentType: "InlineJson",
                        },
                    });
                } catch (e) {
                    console.warn("[onCreationSuccess] initial payload write failed (non-fatal):", e);
                }
                await workloadClient.navigation.navigate('host', {
                    path: `/groups/${createdItem.folderObjectId}/${createdItem.itemType}/${createdItem.objectId}`,
                });
                return { succeeded: true };

            case 'item.onCreationFailure':
                const failureData = data as any;
                await workloadClient.notification.open({
                    title: 'Error creating item',
                    notificationType: NotificationType.Error,
                    message: `Failed to create item, error code: ${failureData.errorCode}, result code: ${failureData.resultCode}`
                });
                return {};

            default:
                console.warn('Unknown action received:', action);
                return {};
        }
    });
}
