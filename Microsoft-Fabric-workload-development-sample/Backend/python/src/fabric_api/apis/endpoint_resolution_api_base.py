# coding: utf-8

from typing import ClassVar, Dict, List, Tuple  # noqa: F401

from pydantic import Field, StrictStr
from typing_extensions import Annotated
from fabric_api.models.endpoint_resolution_request import EndpointResolutionRequest
from fabric_api.models.endpoint_resolution_response import EndpointResolutionResponse
from fabric_api.models.error_response import ErrorResponse


class BaseEndpointResolutionApi:
    subclasses: ClassVar[Tuple] = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        BaseEndpointResolutionApi.subclasses = BaseEndpointResolutionApi.subclasses + (cls,)
    async def endpoint_resolution_resolve(
        self,
        activity_id: Annotated[StrictStr, Field(description="A unique ID for correlating the request with your system when a user interacts with your workload.")],
        request_id: Annotated[StrictStr, Field(description="A globally unique ID that helps Fabric correlate your request with our logs. Provide this ID when reporting an issue.")],
        authorization: Annotated[StrictStr, Field(description="A dual token authorization header that allows the workload to validate the request origin, provide user context, and call other services. This header has the following format: `SubjectAndAppToken1.0 subjectToken=\"delegated token\", appToken=\"S2S token\"`.")],
        body: Annotated[EndpointResolutionRequest, Field(description="Endpoint resolution request payload")],
    ) -> EndpointResolutionResponse:
        """Resolves the endpoint for a given service called by Microsoft Fabric based on the tenant&#39;s region and workspace region. Fabric provides a set of context properties and returns the appropriate service endpoint URL and its time-to-live (TTL).  The Endpoint Resolution API is crucial for services that require dynamic endpoint determination based on operational context. This allows for optimized routing and regional compliance.  To resolve an endpoint, Fabric will send a POST request with the required context properties in the request body. The response will contain the resolved URL and its TTL, which indicates how long the URL is considered valid.  For a sample implementation and usage examples, please refer to the [Endpoint Resolution Sample Code](https://github.com/microsoft/Microsoft-Fabric-workload-development-sample/blob/main/Backend/src/Controllers/EndpointResolutionControllerImpl.cs)."""
        ...
