
from typing import Annotated  # noqa: F401

from fastapi.testclient import TestClient
from pydantic import Field, StrictStr  # noqa: F401

from fabric_api.models.endpoint_resolution_request import (
    EndpointResolutionRequest,  # noqa: F401
)
from fabric_api.models.endpoint_resolution_response import (
    EndpointResolutionResponse,  # noqa: F401
)
from fabric_api.models.error_response import ErrorResponse  # noqa: F401


def test_endpoint_resolution_resolve(client: TestClient):
    """Test case for endpoint_resolution_resolve

    Resolve an endpoint for a given service called by Microsoft Fabric
    """

    # uncomment below to make a request
    #response = client.request(
    #    "POST",
    #    "/resolve-api-path-placeholder",
    #    headers=headers,
    #    json=body,
    #)

    # uncomment below to assert the status code of the HTTP response
    #assert response.status_code == 200

