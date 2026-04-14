import logging
from fastapi import APIRouter, Depends, Request
from typing import Optional
from uuid import UUID

from constants.onelake_constants import OneLakeConstants
from constants.workload_scopes import WorkloadScopes
from services.authentication import AuthenticationService, get_authentication_service
from services.onelake_client_service import OneLakeClientService, get_onelake_client_service

router = APIRouter(tags=["OneLake"])
logger = logging.getLogger(__name__)

@router.get("/{workspace_object_id}/{item_object_id}/isOneLakeSupported")
async def is_onelake_supported(
    workspace_object_id: UUID,
    item_object_id: UUID,
    request: Request,
    auth_service: AuthenticationService = Depends(get_authentication_service),
    onelake_service: OneLakeClientService = Depends(get_onelake_client_service)
):
    """
    Returns a flag indicating whether OneLake storage is supported for this item.
    OneLake is supported if the workload opts in via the "CreateOneLakeFoldersOnArtifactCreation" flag
    
    Returns:
        bool: true if OneLake is supported for this item, false otherwise
    """

    # Extract authorization header from request
    authorization = request.headers.get("authorization")

    # Authenticate the data plane call with allowed scopes
    auth_context = await auth_service.authenticate_data_plane_call(
        authorization,
        allowed_scopes=[WorkloadScopes.ITEM1_READ_WRITE_ALL]
    )
    
    # Get token for OneLake access
    token = await auth_service.get_access_token_on_behalf_of(
        auth_context, 
        OneLakeConstants.ONELAKE_SCOPES
    )
    
    # Get OneLake folder names
    folder_names = await onelake_service.get_onelake_folder_names(
        token, 
        workspace_object_id, 
        item_object_id
    )
    
    # OneLake is supported if there are any folders
    is_supported = bool(folder_names)
    
    return is_supported
