"""
Endpoint Resolution Controller Implementation

This controller handles endpoint resolution requests from Microsoft Fabric,
determining the appropriate service endpoint URL based on the provided context.
"""

import logging
import json
from typing import Optional
from urllib.parse import urlparse

from starlette.requests import Request
from fastapi import HTTPException

from fabric_api.apis.endpoint_resolution_api_base import BaseEndpointResolutionApi
from fabric_api.models.endpoint_resolution_request import EndpointResolutionRequest
from fabric_api.models.endpoint_resolution_response import EndpointResolutionResponse
from fabric_api.models.endpoint_resolution_context_property import EndpointResolutionContextProperty

from services.authentication import get_authentication_service
from exceptions.exceptions import AuthenticationException

logger = logging.getLogger(__name__)

class EndpointResolutionController(BaseEndpointResolutionApi):
    """
    Implementation of the Endpoint Resolution API.
    
    This controller resolves service endpoints for requests originating from Microsoft Fabric
    based on resolution context properties such as tenant region and workspace region.
    """
    
    def __init__(self, request: Request):
        """
        Initialize the controller with the current request context.
        
        Args:
            request: The FastAPI/Starlette request object
        """
        self.request = request
        self.logger = logging.getLogger(__name__)
    
    async def endpoint_resolution_resolve(
        self,
        activity_id: str,
        request_id: str,
        authorization: str,
        body: EndpointResolutionRequest
    ) -> EndpointResolutionResponse:
        """
        Resolve an endpoint for a given service called by Microsoft Fabric.
        
        This method determines the appropriate service endpoint URL based on the
        provided context properties (e.g., tenant region, workspace region).
        
        Args:
            activity_id: A unique ID for correlating the request
            request_id: A globally unique ID for request tracking
            authorization: The authorization header containing SubjectAndApp tokens
            body: The endpoint resolution request containing context properties
            
        Returns:
            EndpointResolutionResponse with the resolved URL and TTL
            
        Raises:
            HTTPException: If the request is invalid or authentication fails
        """
        self.logger.info(f"ResolveAsync: Attempting to resolve endpoint. Activity ID: {activity_id}, Request ID: {request_id}")
        
        # Validate request body
        if not body:
            self.logger.error("ResolveAsync: The request cannot be null.")
            raise HTTPException(status_code=400, detail="The request cannot be null.")
        
        if not body.context or len(body.context) == 0:
            self.logger.error("ResolveAsync: The resolution context is missing or empty.")
            raise HTTPException(status_code=400, detail="The resolution context is missing or empty.")
        
        try:
            # Authenticate the call (without requiring subject token or tenant ID header)
            auth_service = get_authentication_service()
            auth_context = await auth_service.authenticate_control_plane_call(
                authorization,
                tenant_id=None,
                require_subject_token=False,
                require_tenant_id_header=False
            )
            
            # Log context properties for debugging
            context_dict = {prop.name: prop.value for prop in body.context}
            context_json = json.dumps(context_dict)
            self.logger.info(f"Resolving endpoint with Context Properties: {context_json}")
            
            # Resolve the endpoint URL based on the request
            resolved_url = self._resolve_endpoint_url(body)
            
            # Set TTL (time-to-live) in minutes - default to 60 minutes
            ttl_in_minutes = 60
            
            # Create and return the response
            response = EndpointResolutionResponse(
                url=resolved_url,
                ttl_in_minutes=ttl_in_minutes
            )
            
            self.logger.info(f"Resolved endpoint URL: {response.url}")
            
            return response
            
        except AuthenticationException as e:
            self.logger.error(f"Authentication failed: {str(e)}")
            raise HTTPException(status_code=401, detail=str(e))
        except Exception as e:
            self.logger.error(f"Error resolving endpoint: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error resolving endpoint: {str(e)}")
    
    def _resolve_endpoint_url(self, request: EndpointResolutionRequest) -> str:
        """
        Resolve the endpoint URL based on the request context.
        
        This is a placeholder implementation that returns the workload's base URL.
        In a production environment, this should implement actual endpoint resolution
        logic based on the context properties (e.g., routing to different regions).
        
        Args:
            request: The endpoint resolution request containing context properties
            
        Returns:
            The resolved endpoint URL
        """
        # Extract context properties for potential routing logic
        context_dict = {prop.name: prop.value for prop in request.context}
        
        # Log the context for debugging
        self.logger.debug(f"Endpoint resolution context: {context_dict}")
        
        # Get endpoint name if provided
        endpoint_name = context_dict.get("EndpointName")
        tenant_region = context_dict.get("TenantRegion")
        workspace_region = context_dict.get("WorkspaceRegion")
        tenant_id = context_dict.get("TenantId")
        
        self.logger.info(f"Resolving endpoint: name={endpoint_name}, tenant_region={tenant_region}, "
                         f"workspace_region={workspace_region}, tenant_id={tenant_id}")
        
        # Build the base URL from the current request
        # This ensures we return the correct scheme, host, and port
        if hasattr(self.request, 'url'):
            # Starlette Request object
            base_url = f"{self.request.url.scheme}://{self.request.url.netloc}"
        else:
            # Fallback for testing or other contexts
            base_url = "http://localhost:5000"
            self.logger.warning("Request context not available, using fallback URL")
        
        # Add the workload API base path
        api_base_route = "/workload"  # This should match your actual API base route
        resolved_url = f"{base_url}{api_base_route}"
        
        return resolved_url


# Dependency injection for FastAPI
async def get_endpoint_resolution_controller(request: Request) -> EndpointResolutionController:
    """
    FastAPI dependency to create an EndpointResolutionController instance.
    
    This allows the controller to access the current request context.
    """
    return EndpointResolutionController(request)
