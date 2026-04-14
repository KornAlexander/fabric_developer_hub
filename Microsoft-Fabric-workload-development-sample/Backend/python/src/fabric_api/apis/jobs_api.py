# coding: utf-8

from typing import Dict, List  # noqa: F401
import importlib
import pkgutil
import logging
from uuid import UUID

from fabric_api.apis.jobs_api_base import BaseJobsApi
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
from fabric_api.models.job_invoke_type import JobInvokeType
from fabric_api.models.create_item_job_instance_request import CreateItemJobInstanceRequest
from fabric_api.models.error_response import ErrorResponse
from fabric_api.models.item_job_instance_state import ItemJobInstanceState

from fastapi import Request

logger = logging.getLogger(__name__)
router = APIRouter()

ns_pkg = fabric_api.impl
for _, name, _ in pkgutil.iter_modules(ns_pkg.__path__, ns_pkg.__name__ + "."):
    importlib.import_module(name)


@router.post(
    "/workspaces/{workspaceId}/items/{itemType}/{itemId}/jobTypes/{jobType}/instances/{jobInstanceId}/cancel",
    responses={
        200: {"model": ItemJobInstanceState, "description": "Completed successfully."},
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        500: {"model": ErrorResponse, "description": "Server error"},
    },
    tags=["Jobs"],
    summary="Called by Microsoft Fabric for cancelling a job instance.",
    response_model_by_alias=True,
)
async def jobs_cancel_item_job_instance(
    workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")] = Path(..., description="The workspace ID."),
    itemType: Annotated[StrictStr, Field(description="The item type.")] = Path(..., description="The item type."),
    itemId: Annotated[StrictStr, Field(description="The item ID.")] = Path(..., description="The item ID."),
    jobType: Annotated[StrictStr, Field(description="The job type.")] = Path(..., description="The job type."),
    jobInstanceId: Annotated[StrictStr, Field(description="The job instance ID.")] = Path(..., description="The job instance ID."),
    activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")] = Header(None, description="A unique ID for correlating the request with your system when a user interacts with your workload."),
    request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")] = Header(None, description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue."),
    authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")] = Header(None, description=r"A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: &#x60;SubjectAndAppToken1.0 subjectToken&#x3D;\&quot;delegated token\&quot;, appToken&#x3D;\&quot;S2S token\&quot;&#x60;."),
    x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")] = Header(None, description="The tenant ID of the client making the request."),
) -> ItemJobInstanceState:
    """Fabric performs basic validations and calls this API to cancel an item job instance in the workload.  This API should accept SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
    logger.info(f"Cancelling job instance: {workspaceId}/{itemType}/{itemId}/{jobType}/{jobInstanceId}")

    if not BaseJobsApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")

    workspace_id = UUID(workspaceId)
    item_id = UUID(itemId)
    job_instance_id = UUID(jobInstanceId)

    return await BaseJobsApi.subclasses[0]().jobs_cancel_item_job_instance(
        workspace_id, itemType, item_id, jobType, job_instance_id, 
        activity_id, request_id, authorization, x_ms_client_tenant_id
    )
    
@router.post(
    "/workspaces/{workspaceId}/items/{itemType}/{itemId}/jobTypes/{jobType}/instances/{jobInstanceId}",
    responses={
        202: {"description": "Successfully scheduled."},
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        500: {"model": ErrorResponse, "description": "Server error"},
    },
    tags=["Jobs"],
    summary="Called by Microsoft Fabric for starting a new job instance.",
    response_model_by_alias=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def jobs_create_item_job_instance(
    workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")] = Path(..., description="The workspace ID."),
    itemType: Annotated[StrictStr, Field(description="The item type.")] = Path(..., description="The item type."),
    itemId: Annotated[StrictStr, Field(description="The item ID.")] = Path(..., description="The item ID."),
    jobType: Annotated[StrictStr, Field(description="The job type.")] = Path(..., description="The job type."),
    jobInstanceId: Annotated[StrictStr, Field(description="The job instance ID.")] = Path(..., description="The job instance ID."),
    activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")] = Header(None, description="A unique ID for correlating the request with your system when a user interacts with your workload."),
    request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")] = Header(None, description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue."),
    authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")] = Header(None, description=r"A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: &#x60;SubjectAndAppToken1.0 subjectToken&#x3D;\&quot;delegated token\&quot;, appToken&#x3D;\&quot;S2S token\&quot;&#x60;."),
    x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")] = Header(None, description="The tenant ID of the client making the request."),
    create_item_job_instance_request: Annotated[CreateItemJobInstanceRequest, Field(description="The job instance properties.")] = Body(None, description="The job instance properties."),
) -> None:
    """Fabric performs basic validations and calls this API to start a new instance of the job in the workload.  This API should accept SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
    logger.info(f"Creating job instance: {workspaceId}/{itemType}/{itemId}/{jobType}/{jobInstanceId}")
    logger.info(f"Request body: {create_item_job_instance_request}")
    if not BaseJobsApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    
    workspace_id = UUID(workspaceId)
    item_id = UUID(itemId)
    job_instance_id = UUID(jobInstanceId)

    await BaseJobsApi.subclasses[0]().jobs_create_item_job_instance(
        workspace_id, itemType, item_id, jobType, job_instance_id, 
        activity_id, request_id, authorization, x_ms_client_tenant_id,
        create_item_job_instance_request
    )
    return None
    

@router.get(
    "/workspaces/{workspaceId}/items/{itemType}/{itemId}/jobTypes/{jobType}/instances/{jobInstanceId}",
    responses={
        200: {"model": ItemJobInstanceState, "description": "Completed successfully."},
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        500: {"model": ErrorResponse, "description": "Server error"},
    },
    tags=["Jobs"],
    summary="Called by Microsoft Fabric for retrieving a job instance state.",
    response_model_by_alias=True,
)
async def jobs_get_item_job_instance_state(
    workspaceId: Annotated[StrictStr, Field(description="The workspace ID.")] = Path(..., description="The workspace ID."),
    itemType: Annotated[StrictStr, Field(description="The item type.")] = Path(..., description="The item type."),
    itemId: Annotated[StrictStr, Field(description="The item ID.")] = Path(..., description="The item ID."),
    jobType: Annotated[StrictStr, Field(description="The job type.")] = Path(..., description="The job type."),
    jobInstanceId: Annotated[StrictStr, Field(description="The job instance ID.")] = Path(..., description="The job instance ID."),
    activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")] = Header(None, description="A unique ID for correlating the request with your system when a user interacts with your workload."),
    request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")] = Header(None, description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue."),
    authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")] = Header(None, description=r"A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: &#x60;SubjectAndAppToken1.0 subjectToken&#x3D;\&quot;delegated token\&quot;, appToken&#x3D;\&quot;S2S token\&quot;&#x60;."),
    x_ms_client_tenant_id: Annotated[StrictStr, Field(description="The tenant ID of the client making the request.")] = Header(None, description="The tenant ID of the client making the request."),
) -> ItemJobInstanceState:
    """Fabric performs basic validations and calls this API to retrieve the item job instance state in the workload.  This API should accept SubjectAndApp authentication.  ## Permissions  Permissions are checked by Microsoft Fabric."""
    if not BaseJobsApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    
    workspace_id = UUID(workspaceId)
    item_id = UUID(itemId)
    job_instance_id = UUID(jobInstanceId)

    return await BaseJobsApi.subclasses[0]().jobs_get_item_job_instance_state(
            workspace_id, itemType, item_id, jobType, job_instance_id,
            activity_id, request_id, authorization, x_ms_client_tenant_id
    )
