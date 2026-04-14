# coding: utf-8

from typing import Dict, List  # noqa: F401
import importlib
import pkgutil
from uuid import UUID

from fabric_api.apis.item_lifecycle_api_base import BaseItemLifecycleApi
import fabric_api.impl

from fastapi import (  # noqa: F401
    APIRouter,
    Body,
    Cookie,
    Depends,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Response,
    Security,
    status,
)

from fabric_api.models.extra_models import TokenModel  # noqa: F401
from pydantic import Field, StrictStr
from typing import Any
from typing_extensions import Annotated
from fabric_api.models.create_item_request import CreateItemRequest
from fabric_api.models.error_response import ErrorResponse
from fabric_api.models.get_item_payload_response import GetItemPayloadResponse
from fabric_api.models.update_item_request import UpdateItemRequest


router = APIRouter()

ns_pkg = fabric_api.impl
for _, name, _ in pkgutil.iter_modules(ns_pkg.__path__, ns_pkg.__name__ + "."):
    importlib.import_module(name)


@router.post(
    "/workspaces/{workspaceId}/items/{itemType}/{itemId}",
    responses={
        200: {"description": "Successfully created."},
        200: {"model": ErrorResponse, "description": "For error conditions the workload should return an appropriate HTTP error status code (4xx, 5xx) with detailed error information in the response body."},
    },
    tags=["ItemLifecycle"],
    summary="Called by Microsoft Fabric for creating a new item.",
    response_model_by_alias=True,
)
async def item_lifecycle_create_item(
    workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")] = Path(..., description="The workspace ID."),
    itemType: Annotated[StrictStr, Field(description="The item type.")] = Path(..., description="The item type."),
    itemId: Annotated[StrictStr, Field(description="The item ID.")] = Path(..., description="The item ID."),
    activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")] = Header(None, description="A unique ID for correlating the request with your system when a user interacts with your workload."),
    request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")] = Header(None, description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue."),
    authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")] = Header(None, description=r"A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: &#x60;SubjectAndAppToken1.0 subjectToken&#x3D;\&quot;delegated token\&quot;, appToken&#x3D;\&quot;S2S token\&quot;&#x60;."),
    x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")] = Header(None, description="The tenant ID of the client making the request."),
    create_item_request: Annotated[CreateItemRequest, Field(description="The item creation request.")] = Body(None, description="The item creation request."),
) -> None:
    """Upon item creation Fabric performs basic validations, creates the item in a provisioning state and calls this API to notify the workload. The workload is expected to perform required validations, store the item metadata, allocate required resources, and update the Fabric item metadata cache with item relations and ETag.  This API should accept SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
    if not BaseItemLifecycleApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    
    workspaceId = UUID(workspaceId)
    itemId = UUID(itemId)
    return await BaseItemLifecycleApi.subclasses[0]().item_lifecycle_create_item(workspaceId, itemType, itemId, activity_id, request_id, authorization, x_ms_client_tenant_id, create_item_request)


@router.delete(
    "/workspaces/{workspaceId}/items/{itemType}/{itemId}",
    responses={
        200: {"description": "Successfully deleted."},
        200: {"model": ErrorResponse, "description": "For error conditions the workload should return an appropriate HTTP error status code (4xx, 5xx) with detailed error information in the response body."},
    },
    tags=["ItemLifecycle"],
    summary="Called by Microsoft Fabric for deleting an existing item.",
    response_model_by_alias=True,
)
async def item_lifecycle_delete_item(
    workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")] = Path(..., description="The workspace ID."),
    itemType: Annotated[StrictStr, Field(description="The item type.")] = Path(..., description="The item type."),
    itemId: Annotated[StrictStr, Field(description="The item ID.")] = Path(..., description="The item ID."),
    activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")] = Header(None, description="A unique ID for correlating the request with your system when a user interacts with your workload."),
    request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")] = Header(None, description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue."),
    authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")] = Header(None, description=r"A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: &#x60;SubjectAndAppToken1.0 subjectToken&#x3D;\&quot;delegated token\&quot;, appToken&#x3D;\&quot;S2S token\&quot;&#x60;."),
    x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")] = Header(None, description="The tenant ID of the client making the request."),
) -> None:
    """Upon item deletion Fabric performs basic validations and calls this API to notify the workload. The workload is expected to delete the item metadata and free resources.   This API should accept SubjectAndApp authentication. However, the subject token may be unavailable in some cases.  ## Permissions  Permissions are checked by Microsoft Fabric."""
    if not BaseItemLifecycleApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    
    workspaceId = UUID(workspaceId)
    itemId = UUID(itemId)
    return await BaseItemLifecycleApi.subclasses[0]().item_lifecycle_delete_item(workspaceId, itemType, itemId, activity_id, request_id, authorization, x_ms_client_tenant_id)


@router.get(
    "/workspaces/{workspaceId}/items/{itemType}/{itemId}/payload",
    responses={
        200: {"model": GetItemPayloadResponse, "description": "Completed successfully."},
        200: {"model": ErrorResponse, "description": "For error conditions the workload should return an appropriate HTTP error status code (4xx, 5xx) with detailed error information in the response body."},
    },
    tags=["ItemLifecycle"],
    summary="Called by Microsoft Fabric for retrieving the workload payload for an item.",
    response_model_by_alias=True,
)
async def item_lifecycle_get_item_payload(
    workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")] = Path(..., description="The workspace ID."),
    itemType: Annotated[StrictStr, Field(description="The item type.")] = Path(..., description="The item type."),
    itemId: Annotated[StrictStr, Field(description="The item ID.")] = Path(..., description="The item ID."),
    activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")] = Header(None, description="A unique ID for correlating the request with your system when a user interacts with your workload."),
    request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")] = Header(None, description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue."),
    authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")] = Header(None, description=r"A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: &#x60;SubjectAndAppToken1.0 subjectToken&#x3D;\&quot;delegated token\&quot;, appToken&#x3D;\&quot;S2S token\&quot;&#x60;."),
    x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")] = Header(None, description="The tenant ID of the client making the request."),
) -> GetItemPayloadResponse:
    """When the item editor &amp;lt;iframe&amp;gt; requests an item, Fabric performs basic validations and calls this API to retrieve the payload from the workload.  This API accepts SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
    if not BaseItemLifecycleApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    
    workspaceId = UUID(workspaceId)
    itemId = UUID(itemId)
    return await BaseItemLifecycleApi.subclasses[0]().item_lifecycle_get_item_payload(workspaceId, itemType, itemId, activity_id, request_id, authorization, x_ms_client_tenant_id)


@router.patch(
    "/workspaces/{workspaceId}/items/{itemType}/{itemId}",
    responses={
        200: {"description": "Successfully updated."},
        200: {"model": ErrorResponse, "description": "For error conditions the workload should return an appropriate HTTP error status code (4xx, 5xx) with detailed error information in the response body."},
    },
    tags=["ItemLifecycle"],
    summary="Called by Microsoft Fabric for updating an existing item.",
    response_model_by_alias=True,
)
async def item_lifecycle_update_item(
    workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")] = Path(..., description="The workspace ID."),
    itemType: Annotated[StrictStr, Field(description="The item type.")] = Path(..., description="The item type."),
    itemId: Annotated[StrictStr, Field(description="The item ID.")] = Path(..., description="The item ID."),
    activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")] = Header(None, description="A unique ID for correlating the request with your system when a user interacts with your workload."),
    request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")] = Header(None, description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue."),
    authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")] = Header(None, description=r"A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: &#x60;SubjectAndAppToken1.0 subjectToken&#x3D;\&quot;delegated token\&quot;, appToken&#x3D;\&quot;S2S token\&quot;&#x60;."),
    x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")] = Header(None, description="The tenant ID of the client making the request."),
    update_item_request: Annotated[UpdateItemRequest, Field(description="The item update request.")] = Body(None, description="The item update request."),
) -> None:
    """Upon item update Fabric performs basic validations and calls this API to notify the workload. The workload is expected to perform required validations, store the item metadata, allocate and/or free resources, and update the Fabric item metadata cache with item relations and ETag.  This API should accept SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
    if not BaseItemLifecycleApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    
    workspaceId = UUID(workspaceId)
    itemId = UUID(itemId)
    return await BaseItemLifecycleApi.subclasses[0]().item_lifecycle_update_item(workspaceId, itemType, itemId, activity_id, request_id, authorization, x_ms_client_tenant_id, update_item_request)
