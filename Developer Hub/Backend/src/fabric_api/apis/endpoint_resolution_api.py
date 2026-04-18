# coding: utf-8

from typing import Dict, List  # noqa: F401
import importlib
import pkgutil

from fabric_api.apis.endpoint_resolution_api_base import BaseEndpointResolutionApi
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
from typing_extensions import Annotated
from fabric_api.models.endpoint_resolution_request import EndpointResolutionRequest
from fabric_api.models.endpoint_resolution_response import EndpointResolutionResponse
from fabric_api.models.error_response import ErrorResponse


router = APIRouter()

ns_pkg = fabric_api.impl
for _, name, _ in pkgutil.iter_modules(ns_pkg.__path__, ns_pkg.__name__ + "."):
    importlib.import_module(name)


@router.post(
    "/resolve-api-path-placeholder",
    responses={
        200: {"model": EndpointResolutionResponse, "description": "Endpoint resolution response"},
        200: {"model": ErrorResponse, "description": "For error conditions the workload should return an appropriate HTTP error status code (4xx, 5xx) with detailed error information in the response body."},
    },
    tags=["EndpointResolution"],
    summary="Resolve an endpoint for a given service called by Microsoft Fabric",
    response_model_by_alias=True,
)
async def endpoint_resolution_resolve(
    activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")] = Header(None, description="A unique ID for correlating the request with your system when a user interacts with your workload."),
    request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")] = Header(None, description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue."),
    authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")] = Header(None, description=r"A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: &#x60;SubjectAndAppToken1.0 subjectToken&#x3D;\&quot;delegated token\&quot;, appToken&#x3D;\&quot;S2S token\&quot;&#x60;."),
    body: Annotated[EndpointResolutionRequest, Field(description="Endpoint resolution request payload")] = Body(None, description="Endpoint resolution request payload"),
) -> EndpointResolutionResponse:
    """Resolves the endpoint for a given service called by Microsoft Fabric based on the tenant&#39;s region and workspace region. Fabric provides a set of context properties and returns the appropriate service endpoint URL and its time-to-live (TTL).  The Endpoint Resolution API is crucial for services that require dynamic endpoint determination based on operational context. This allows for optimized routing and regional compliance.  To resolve an endpoint, Fabric will send a POST request with the required context properties in the request body. The response will contain the resolved URL and its TTL, which indicates how long the URL is considered valid.  For a sample implementation and usage examples, please refer to the [Endpoint Resolution Sample Code](https://github.com/microsoft/Microsoft-Fabric-workload-development-sample/blob/main/Backend/src/Controllers/EndpointResolutionControllerImpl.cs)."""
    if not BaseEndpointResolutionApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseEndpointResolutionApi.subclasses[0]().endpoint_resolution_resolve(activity_id, request_id, authorization, body)
