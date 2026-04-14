import logging
import aiohttp
from typing import List, Optional, Dict, Any
from uuid import UUID
import httpx
from pydantic import BaseModel

from constants.environment_constants import EnvironmentConstants
from models.authentication_models import AuthorizationContext
from exceptions.exceptions import UnauthorizedException, TooManyRequestsException, InternalErrorException
from constants.api_constants import ApiConstants
from services.http_client import get_http_client_service

logger = logging.getLogger(__name__)

class ResolvePermissionsResponse(BaseModel):
    """Response model for the resolve permissions API."""
    permissions: List[str]

class AuthorizationHandler:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._auth_service = None
        self.fabric_scopes = [f"{EnvironmentConstants.FABRIC_BACKEND_RESOURCE_ID}/.default"]

    @property
    def auth_service(self):
        """Lazy load authentication service to avoid circular dependencies."""
        if self._auth_service is None:
            from services.authentication import get_authentication_service
            self._auth_service = get_authentication_service()
        return self._auth_service
    
    async def dispose_async(self):
        """Cleanup method for service registry."""
        # No resources to cleanup, but method needed for consistency
        self.logger.debug("AuthorizationHandler disposed")
        
    async def validate_permissions(
        self, 
        auth_context: AuthorizationContext, 
        workspace_object_id: UUID, 
        item_object_id: UUID,
        required_permissions: List[str]
    ) -> None:
        """
        Validate that the user has the required permissions for the item.
        
        Args:
            auth_context: The authorization context from authentication
            workspace_object_id: The workspace ID
            item_object_id: The item ID
            required_permissions: List of permissions required (e.g., ["Read", "Write"])
            
        Raises:
            UnauthorizedException: If the user doesn't have the required permissions
            TooManyRequestsException: If API throttling occurs
        """
        self.logger.debug(f"Validating permissions for item {item_object_id} in workspace {workspace_object_id}")
        
        # Get a composite token for calling Fabric APIs
        subject_and_app_token = await self.auth_service.build_composite_token(
            auth_context, 
            self.fabric_scopes
        )
        
        # Resolve item permissions using the provided token
        response = await self._resolve_item_permissions(
            subject_and_app_token, 
            workspace_object_id, 
            item_object_id
        )

        if response is None or not response.permissions:
            self.logger.error("Fabric response should contain permissions")
            raise UnauthorizedException("Failed to resolve permissions")

        # Check if any of the required permissions is missing (case-insensitive comparison)
        missing_permissions = []
        for required_perm in required_permissions:
            if not any(perm.lower() == required_perm.lower() for perm in response.permissions):
                missing_permissions.append(required_perm)
        
        if missing_permissions:
            self.logger.error(
                f"Insufficient permissions: subjectTenantObjectId={auth_context.tenant_object_id}, "
                f"subjectObjectId={auth_context.object_id}, "
                f"workspaceObjectId={workspace_object_id}, "
                f"itemObjectId={item_object_id}, "
                f"requiredPermissions={required_permissions}, "
                f"actualPermissions={response.permissions}"
            )
            raise UnauthorizedException("User does not have required permissions")
            
            
    
    async def _resolve_item_permissions(
        self, 
        token: str, 
        workspace_id: UUID, 
        item_id: UUID
    ) -> ResolvePermissionsResponse:
        """
        Resolve item permissions by calling the Fabric workload-control API.
        
        Args:
            token: The authentication token
            workspace_id: The workspace ID
            item_id: The item ID
            
        Returns:
            ResolvePermissionsResponse: The response containing permissions
            
        Raises:
            TooManyRequestsException: If the API is throttling requests
            UnauthorizedException: If there are permission issues
            Exception: For other errors
        """
        url = f"{ApiConstants.WORKLOAD_CONTROL_API_BASE_URL}/workspaces/{workspace_id}/items/{item_id}/resolvepermissions"
        self.logger.debug(f"Calling resolve permissions API: {url}")

        auth_header_value = token
        if not token.startswith("SubjectAndAppToken"):
            auth_header_value = f"Bearer {token}"
        headers = {
            "Authorization": auth_header_value,
            "Content-Type": "application/json"
        }

        try:
            http_client = get_http_client_service()
            response = await http_client.get(url,token)
            if response.status_code == 429:
                self.logger.warning(f"Throttling from resolvepermissions API (429) for item {item_id}")
                raise TooManyRequestsException("Blocked due to resolved-permissions API throttling.")
        
            if response.status_code in (401, 403):
                error_text = response.text
                self.logger.error(f"Access denied by resolvepermissions API ({response.status_code}): {error_text}")
                raise UnauthorizedException(f"Access denied by resolvepermissions API ({response.status_code}): {error_text}")
            
            response.raise_for_status()
            response_data = response.json()
            return ResolvePermissionsResponse(**response_data)
        
        except httpx.HTTPStatusError as e:
            self.logger.error(f"Error resolving permissions: {str(e)}")
            raise InternalErrorException(f"Error communicating with Fabric API: {str(e)}")
        except Exception as e:
            self.logger.error(f"Unexpected error in _resolve_item_permissions: {str(e)}", exc_info=True)
            raise InternalErrorException(f"Unexpected error: {str(e)}")

def get_authorization_service() -> AuthorizationHandler:
    """Get the singleton AuthorizationHandler instance."""
    from core.service_registry import get_service_registry
    registry = get_service_registry()
    
    if not registry.has(AuthorizationHandler):
        # Fallback for backward compatibility
        if not hasattr(get_authorization_service, "instance"):
            get_authorization_service.instance = AuthorizationHandler()
        return get_authorization_service.instance
    
    return registry.get(AuthorizationHandler)