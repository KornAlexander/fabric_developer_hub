import logging
from uuid import UUID
import json
from typing import Dict, Any

from fabric_api.apis.item_lifecycle_api_base import BaseItemLifecycleApi
from fabric_api.models.create_item_request import CreateItemRequest
from fabric_api.models.update_item_request import UpdateItemRequest
from fabric_api.models.get_item_payload_response import GetItemPayloadResponse

from services.authentication import get_authentication_service, AuthenticationService
from services.item_factory import get_item_factory, ItemFactory

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
        which happens during handleCreateSampleItem in SampleWorkloadCreateDialog.
        """
        logger.info(f"Creating item: {itemType} with ID {itemId} in workspace {workspaceId}")
        
        logger.debug(f"Create item request: {create_item_request}")
        
        # Get required services
        auth_service = get_authentication_service()
        item_factory = get_item_factory()
        
        logger.debug(f"Authenticating control plane call with x_ms_client_tenant_id ID: {x_ms_client_tenant_id}")
        # Authenticate the call
        auth_context = await auth_service.authenticate_control_plane_call(
            authorization, 
            x_ms_client_tenant_id
        )
        
        # Create the item
        item = item_factory.create_item(itemType, auth_context)
        await item.create(workspaceId, itemId, create_item_request)
        
        logger.info(f"Successfully created item {itemId}")
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
        logger.info(f"Updating item: {itemType} with ID {itemId} in workspace {workspaceId}")
        logger.debug(f"Update item request: {update_item_request}")
        
        auth_service = get_authentication_service()
        item_factory = get_item_factory()
        
        auth_context = await auth_service.authenticate_control_plane_call(
            authorization, 
            x_ms_client_tenant_id
        )
        
        item = item_factory.create_item(itemType, auth_context)
        await item.load(itemId)
        await item.update(update_item_request)
        
        logger.info(f"Successfully updated item {itemId}")
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
        logger.info(f"Deleting item: {itemType} with ID {itemId} in workspace {workspaceId}")
        
        auth_service = get_authentication_service()
        item_factory = get_item_factory()
        
        auth_context = await auth_service.authenticate_control_plane_call(
            authorization, 
            tenant_id=x_ms_client_tenant_id,
            require_subject_token=False
        )
        if not auth_context.has_subject_context:
            logger.warning(f"Subject token not provided for item deletion: {itemId}")

        
        item = item_factory.create_item(itemType, auth_context)
        await item.load(itemId)
        await item.delete()
        
        logger.info(f"Successfully deleted item {itemId}")
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
        logger.info(f"Getting payload for item: {itemType} with ID {itemId} in workspace {workspaceId}")
        
        auth_service = get_authentication_service()
        item_factory = get_item_factory()
        
        auth_context = await auth_service.authenticate_control_plane_call(
            authorization, 
            x_ms_client_tenant_id
        )
        
        item = item_factory.create_item(itemType, auth_context)
        await item.load(itemId)
        item_payload = await item.get_item_payload()
        
        logger.debug(f"Retrieved payload for item {itemId}: {item_payload}")
        return GetItemPayloadResponse(item_payload=item_payload)