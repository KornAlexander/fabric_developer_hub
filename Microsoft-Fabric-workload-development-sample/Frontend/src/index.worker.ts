import {
    ItemCreateContext,
    createWorkloadClient,
    InitParams,
    NotificationType,
} from '@ms-fabric/workload-client';

export async function initialize(params: InitParams) {
    const workloadClient = createWorkloadClient();
    const sampleWorkloadName = process.env.WORKLOAD_NAME;

    workloadClient.action.onAction(async function ({ action, data }) {
        switch (action) {
            case 'open.agentHub':
                const { workspaceObjectId: agentHubWsId } = data as ItemCreateContext;
                return workloadClient.page.open({
                    workloadName: sampleWorkloadName,
                    route: {
                        path: agentHubWsId ? `/agent-hub/home?ws=${agentHubWsId}` : `/agent-hub/home`,
                    },
                });

            case 'item.onCreationSuccess':
                const { item: createdItem } = data as any;
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
