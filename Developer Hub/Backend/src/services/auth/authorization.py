import logging
from uuid import UUID

import httpx
from pydantic import BaseModel

from app.core.service_registry import get_service_registry
from domain.constants.api_constants import ApiConstants
from domain.constants.environment_constants import EnvironmentConstants
from domain.exceptions.exceptions import (
    InternalErrorException,
    TooManyRequestsException,
    UnauthorizedException,
)
from domain.models.authentication_models import AuthorizationContext
from services.auth.authentication import get_authentication_service
from services.http_client import get_http_client_service

logger = logging.getLogger(__name__)

class ResolvePermissionsResponse(BaseModel):
    """Response model for the resolve permissions API."""
    permissions: list[str]

class AuthorizationHandler:
    def __init__(self) -> None:
        self.logger = logging.getLogger(__name__)
        self._auth_service = None
        self.fabric_scopes = [f"{EnvironmentConstants.FABRIC_BACKEND_RESOURCE_ID}/.default"]

    @property
    def auth_service(self):
        """Lazy load authentication service to avoid circular dependencies."""
        if self._auth_service is None:
            self._auth_service = get_authentication_service()
        return self._auth_service

    async def dispose_async(self) -> None:
        """Cleanup method for service registry."""
        # No resources to cleanup, but method needed for consistency
        self.logger.debug("AuthorizationHandler disposed")

    async def validate_permissions(
        self,
        auth_context: AuthorizationContext,
        workspace_object_id: UUID,
        item_object_id: UUID,
        required_permissions: list[str]
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
        self.logger.debug(
            "Validating permissions for item %s in workspace %s",
            item_object_id, workspace_object_id,
        )

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
                "Insufficient permissions: subjectTenantObjectId=%s, "
                "subjectObjectId=%s, workspaceObjectId=%s, itemObjectId=%s, "
                "requiredPermissions=%s, actualPermissions=%s",
                auth_context.tenant_object_id,
                auth_context.object_id,
                workspace_object_id,
                item_object_id,
                required_permissions,
                response.permissions,
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
        self.logger.debug("Calling resolve permissions API: %s", url)

        try:
            http_client = get_http_client_service()
            response = await http_client.get(url, token)
            if response.status_code == 429:
                self.logger.warning("Throttling from resolvepermissions API (429) for item %s", item_id)
                raise TooManyRequestsException("Blocked due to resolved-permissions API throttling.")

            if response.status_code in (401, 403):
                error_text = response.text
                self.logger.error(
                    "Access denied by resolvepermissions API (%s): %s",
                    response.status_code, error_text,
                )
                raise UnauthorizedException(
                    f"Access denied by resolvepermissions API ({response.status_code}): {error_text}"
                )

            response.raise_for_status()
            response_data = response.json()
            return ResolvePermissionsResponse(**response_data)

        except (TooManyRequestsException, UnauthorizedException):
            raise
        except httpx.HTTPStatusError as e:
            self.logger.error("Error resolving permissions: %s", e)
            raise InternalErrorException(f"Error communicating with Fabric API: {e!s}") from e
        except Exception as e:
            self.logger.exception("Unexpected error in _resolve_item_permissions")
            raise InternalErrorException(f"Unexpected error: {e!s}") from e

def get_authorization_service() -> AuthorizationHandler:
    """Get the singleton AuthorizationHandler from ServiceRegistry."""
    try:
        return get_service_registry().get(AuthorizationHandler)
    except KeyError:
        raise RuntimeError(
            "AuthorizationHandler not initialized. Ensure ServiceInitializer.initialize_all_services() ran at startup."
        ) from None
