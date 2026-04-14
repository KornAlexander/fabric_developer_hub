import logging
from fastapi import APIRouter, Depends, Header, Path, HTTPException, Query, Body
from typing import Optional, List, Dict, Any, Union
from uuid import UUID

from constants.onelake_constants import OneLakeConstants
from constants.workload_scopes import WorkloadScopes
from models.write_to_lakehouse_file_request import WriteToLakehouseFileRequest
from models.lakehouse_table import LakehouseTable
from services.authentication import AuthenticationService, get_authentication_service, AuthenticationUIRequiredException
from services.onelake_client_service import OneLakeClientService, get_onelake_client_service
from services.lakehouse_client_service import LakehouseClientService, get_lakehouse_client_service

router = APIRouter(tags=["Lakehouse"])
logger = logging.getLogger(__name__)

@router.get("/getLakehouseFile")
async def get_lakehouse_file(
    source: str,
    authorization: Optional[str] = Header(None),
    auth_service: AuthenticationService = Depends(get_authentication_service),
    onelake_service: OneLakeClientService = Depends(get_onelake_client_service)
):
    """
    Retrieves a file from the OneLake storage.
    """
    auth_context = await auth_service.authenticate_data_plane_call(
        authorization, 
        allowed_scopes=[WorkloadScopes.FABRIC_LAKEHOUSE_READ_ALL, WorkloadScopes.FABRIC_LAKEHOUSE_READ_WRITE_ALL]
    )
    
    # Attempt to get token with OneLake scopes
    lakehouse_access_token = await auth_service.get_access_token_on_behalf_of(
        auth_context, OneLakeConstants.ONELAKE_SCOPES
    )
    
    data = await onelake_service.get_onelake_file(lakehouse_access_token, source)
    
    if not data:
        logger.warning(f"GetOneLakeFile returned empty data for source: {source}")
        # Return a 204 No Content status code for empty data
        return None
    
    logger.info(f"GetOneLakeFile succeeded for source: {source}")
    return data

@router.put("/writeToLakehouseFile")
async def write_to_lakehouse_file(
    request: WriteToLakehouseFileRequest,
    authorization: Optional[str] = Header(None),
    auth_service: AuthenticationService = Depends(get_authentication_service),
    onelake_service: OneLakeClientService = Depends(get_onelake_client_service)
):
    """
    Writes content to a file in the OneLake storage.
    """
    auth_context = await auth_service.authenticate_data_plane_call(
        authorization, 
        allowed_scopes=[WorkloadScopes.FABRIC_LAKEHOUSE_READ_WRITE_ALL]
    )
    
    lakehouse_access_token = await auth_service.get_access_token_on_behalf_of(
        auth_context, OneLakeConstants.ONELAKE_SCOPES
    )
    
    file_path = onelake_service.get_onelake_file_path(
        request.workspace_id,
        request.lakehouse_id,
        request.file_name
    )
    
    file_exists = await onelake_service.check_if_file_exists(
        lakehouse_access_token, file_path
    )
    
    if file_exists and not request.overwrite_if_exists:
        # File exists, and overwrite is not allowed, return conflict
        logger.error(f"WriteToOneLakeFile failed. The file already exists at filePath: {file_path}.")
        raise HTTPException(status_code=409, detail="File already exists. Overwrite is not allowed.")
    
    # Write content to file
    await onelake_service.write_to_onelake_file(
        lakehouse_access_token, file_path, request.content
    )
    
    logger.info(f"WriteToOneLakeFile succeeded for filePath: {file_path}")
    return {"success": True}

@router.get("/onelake/{workspace_id}/{lakehouse_id}/tables")
async def get_tables(
    workspace_id: UUID,
    lakehouse_id: UUID,
    authorization: Optional[str] = Header(None),
    auth_service: AuthenticationService = Depends(get_authentication_service),
    lakehouse_service: LakehouseClientService = Depends(get_lakehouse_client_service)
):
    """
    Retrieves tables from a Lakehouse.
    """
    auth_context = await auth_service.authenticate_data_plane_call(
        authorization, 
        allowed_scopes=[WorkloadScopes.FABRIC_LAKEHOUSE_READ_ALL, WorkloadScopes.FABRIC_LAKEHOUSE_READ_WRITE_ALL]
    )
    
    token = await auth_service.get_access_token_on_behalf_of(
        auth_context, OneLakeConstants.ONELAKE_SCOPES
    )
    
    tables = await lakehouse_service.get_lakehouse_tables(token, workspace_id, lakehouse_id)
    
    # Convert LakehouseTable objects to dictionaries for JSON serialization
    result = []
    for table in tables:
        result.append({
            "name": table.name,
            "path": table.path,
            "schema": table.schema_name
        })
    
    return result

@router.get("/onelake/{workspace_id}/{lakehouse_id}/files")
async def get_files(
    workspace_id: UUID,
    lakehouse_id: UUID,
    authorization: Optional[str] = Header(None),
    auth_service: AuthenticationService = Depends(get_authentication_service),
    lakehouse_service: LakehouseClientService = Depends(get_lakehouse_client_service)
):
    """
    Retrieves files from a Lakehouse.
    """
    auth_context = await auth_service.authenticate_data_plane_call(
        authorization, 
        allowed_scopes=[WorkloadScopes.FABRIC_LAKEHOUSE_READ_ALL, WorkloadScopes.FABRIC_LAKEHOUSE_READ_WRITE_ALL]
    )
    
    token = await auth_service.get_access_token_on_behalf_of(
        auth_context, OneLakeConstants.ONELAKE_SCOPES
    )
    
    files = await lakehouse_service.get_lakehouse_files(token, workspace_id, lakehouse_id)
    return files
