import logging
from uuid import UUID

from fabric_api.apis.item_lifecycle_api_base import BaseItemLifecycleApi
from fabric_api.models.create_item_request import CreateItemRequest
from fabric_api.models.get_item_payload_response import GetItemPayloadResponse
from fabric_api.models.update_item_request import UpdateItemRequest
from services.auth.authentication import get_authentication_service
from services.fabric.item_factory import get_item_factory

logger = logging.getLogger(__name__)

class ItemLifecycleController(BaseItemLifecycleApi):
    """Implementation of the Item Lifecycle API"""

    async def item_lifecycle_create_item(
        self,
        workspaceId: UUID,
        itemType: str,
        itemId: UUID,
        activity_id: str = None,
        request_id: str = None,
        authorization: str = None,
        x_ms_client_tenant_id: str = None,
        create_item_request: CreateItemRequest = None
    ) -> None:
        """
        Called by Microsoft Fabric for creating a new item.

        This endpoint is triggered when the frontend calls callItemCreate,
        which happens during handleCreateAgentHubItem in AgentHubItemCreateDialog.
        """
        logger.info(
            "Creating item: %s with ID %s in workspace %s", itemType, itemId, workspaceId
        )
        # The full create_item_request can contain the user payload — keep at
        # debug-only to avoid logging potentially sensitive item definitions
        # at INFO level in production.
        logger.debug("Create item request: %s", create_item_request)

        # Get required services
        auth_service = get_authentication_service()
        item_factory = get_item_factory()

        # Authenticate the call. The tenant header is cross-checked against
        # the bearer-token tenant claim inside authenticate_control_plane_call.
        logger.debug(
            "Authenticating control plane call with x_ms_client_tenant_id: %s",
            x_ms_client_tenant_id,
        )
        auth_context = await auth_service.authenticate_control_plane_call(
            authorization,
            x_ms_client_tenant_id
        )

        # Create the item
        item = item_factory.create_item(itemType, auth_context)
        await item.create(workspaceId, itemId, create_item_request)

        logger.info("Successfully created item %s", itemId)
        return None

    async def item_lifecycle_update_item(
        self,
        workspaceId: UUID,
        itemType: str,
        itemId: UUID,
        activity_id: str = None,
        request_id: str = None,
        authorization: str = None,
        x_ms_client_tenant_id: str = None,
        update_item_request: UpdateItemRequest = None
    ) -> None:
        """Called by Microsoft Fabric for updating an existing item."""
        logger.info(
            "Updating item: %s with ID %s in workspace %s", itemType, itemId, workspaceId
        )
        logger.debug("Update item request: %s", update_item_request)

        auth_service = get_authentication_service()
        item_factory = get_item_factory()

        auth_context = await auth_service.authenticate_control_plane_call(
            authorization,
            x_ms_client_tenant_id
        )

        item = item_factory.create_item(itemType, auth_context)
        # item.load() enforces tenant isolation by cross-checking the stored
        # tenant_object_id against the authenticated auth_context.
        await item.load(itemId)
        await item.update(update_item_request)

        logger.info("Successfully updated item %s", itemId)
        return None

    async def item_lifecycle_delete_item(
        self,
        workspaceId: UUID,
        itemType: str,
        itemId: UUID,
        activity_id: str = None,
        request_id: str = None,
        authorization: str = None,
        x_ms_client_tenant_id: str = None
    ) -> None:
        """Called by Microsoft Fabric for deleting an existing item."""
        logger.info(
            "Deleting item: %s with ID %s in workspace %s", itemType, itemId, workspaceId
        )

        auth_service = get_authentication_service()
        item_factory = get_item_factory()

        # Per Fabric workload contract, the subject token may be missing on
        # delete (e.g. workspace-level cleanup); allow it but log when absent.
        auth_context = await auth_service.authenticate_control_plane_call(
            authorization,
            tenant_id=x_ms_client_tenant_id,
            require_subject_token=False
        )
        if not auth_context.has_subject_context:
            logger.warning("Subject token not provided for item deletion: %s", itemId)

        item = item_factory.create_item(itemType, auth_context)
        await item.load(itemId)
        await item.delete()

        logger.info("Successfully deleted item %s", itemId)
        return None

    async def item_lifecycle_get_item_payload(
        self,
        workspaceId: UUID,
        itemType: str,
        itemId: UUID,
        activity_id: str = None,
        request_id: str = None,
        authorization: str = None,
        x_ms_client_tenant_id: str = None
    ) -> GetItemPayloadResponse:
        """
        Called by Microsoft Fabric for retrieving the workload payload for an item.

        This endpoint is called when the editor loads via loadDataFromUrl.
        """
        logger.info(
            "Getting payload for item: %s with ID %s in workspace %s",
            itemType,
            itemId,
            workspaceId,
        )

        auth_service = get_authentication_service()
        item_factory = get_item_factory()

        auth_context = await auth_service.authenticate_control_plane_call(
            authorization,
            x_ms_client_tenant_id
        )

        item = item_factory.create_item(itemType, auth_context)
        await item.load(itemId)
        item_payload = await item.get_item_payload()

        logger.debug("Retrieved payload for item %s: %s", itemId, item_payload)
        return GetItemPayloadResponse(item_payload=item_payload)
