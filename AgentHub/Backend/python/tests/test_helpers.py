"""Helper utilities for testing."""

from typing import Dict, Any, Optional, List
from uuid import UUID, uuid4
from unittest.mock import Mock, AsyncMock

from models.authentication_models import AuthorizationContext, Claim
from fabric_api.models.create_item_request import CreateItemRequest
from fabric_api.models.update_item_request import UpdateItemRequest
from fabric_api.models.get_item_payload_response import GetItemPayloadResponse
from items.base_item import ItemBase
from tests.test_fixtures import TestFixtures


class TestHelpers:
    """Helper methods for creating test objects."""
    
    @staticmethod
    def create_mock_item(item_type: str = "Item1", item_id: UUID = None) -> Mock:
        """Create a mock item with all required attributes."""
        mock_item = AsyncMock(spec=ItemBase)
        mock_item.item_type = item_type
        mock_item.item_object_id = item_id or TestFixtures.ITEM_ID  # Add this attribute
        
        # Mock async methods
        mock_item.create = AsyncMock()
        mock_item.update = AsyncMock()
        mock_item.delete = AsyncMock()
        mock_item.load = AsyncMock()
        mock_item.get_item_payload = AsyncMock(return_value={"test": "payload"})
        mock_item.execute_job = AsyncMock()
        mock_item.get_job_state = AsyncMock()
        mock_item.cancel_job = AsyncMock()
        
        return mock_item
    
    @staticmethod
    def create_auth_context(
        tenant_id: str = "44444444-4444-4444-4444-444444444444",
        user_id: str = "test-user-id",
        user_name: str = "Test User",
        has_subject_context: bool = True
    ) -> AuthorizationContext:
        """Create an authorization context for testing."""
        # Control has_subject_context by setting original_subject_token
        context = AuthorizationContext(
            original_subject_token="mock_subject_token" if has_subject_context else None,
            tenant_object_id=tenant_id,
            claims=[
                {"type": "oid", "value": user_id},
                {"type": "name", "value": user_name},
                {"type": "tid", "value": tenant_id}
            ]
        )
        return context
    
    @staticmethod
    def create_headers(
        activity_id: Optional[str] = None,
        request_id: Optional[str] = None,
        authorization: Optional[str] = None,
        tenant_id: Optional[str] = None
    ) -> Dict[str, str]:
        """Create request headers for testing."""
        headers = {}
        
        if activity_id:
            headers["activity_id"] = activity_id
        
        if request_id:
            headers["request_id"] = request_id
        
        if authorization:
            headers["authorization"] = authorization
        
        if tenant_id:
            headers["x_ms_client_tenant_id"] = tenant_id
        
        return headers
    
    @staticmethod
    def assert_error_response(response, expected_status: int, expected_error_code: Optional[str] = None):
        """Assert that a response is an error with expected properties."""
        assert response.status_code == expected_status, \
            f"Expected status code {expected_status}, got {response.status_code}. Response: {response.text}"
        
        if expected_error_code:
            error_data = response.json()
            assert "errorCode" in error_data, f"Missing errorCode in response: {error_data}"
            assert error_data["errorCode"] == expected_error_code, \
                f"Expected error code '{expected_error_code}', got '{error_data.get('errorCode')}'"
    
    @staticmethod
    def create_error_response(error_code: str, message: str, status_code: int = 400) -> Dict[str, Any]:
        """Create a standard error response."""
        return {
            "errorCode": error_code,
            "message": message,
            "details": {
                "statusCode": status_code
            }
        }