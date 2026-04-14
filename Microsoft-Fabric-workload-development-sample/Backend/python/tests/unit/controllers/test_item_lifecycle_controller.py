"""Comprehensive tests for ItemLifecycleController."""

import pytest
from unittest.mock import Mock, AsyncMock, patch, ANY
from uuid import UUID
import json

from tests.test_helpers import TestHelpers
from tests.test_fixtures import TestFixtures
from exceptions.exceptions import (
    AuthenticationException,
    ItemMetadataNotFoundException,
    UnauthorizedException,
    UnexpectedItemTypeException,
    InternalErrorException
)
from fabric_api.models.get_item_payload_response import GetItemPayloadResponse
from tests.constants import ExpectedResponses


@pytest.mark.unit
@pytest.mark.controllers
class TestItemLifecycleController:
    """Test cases for ItemLifecycleController."""
    
    @pytest.mark.asyncio
    async def test_create_item_success(
        self, 
        client, 
        mock_authentication_service, 
        mock_item_factory,
        valid_headers
    ):
        """Test successful item creation."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        # Act
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers,
            json=TestFixtures.CREATE_PAYLOAD
        )
        
        # Assert
        assert response.status_code == 200
        
        # Verify service calls - note that the controller might pass tenant_id differently
        mock_authentication_service.authenticate_control_plane_call.assert_called_once()
        
        # Check the call arguments more flexibly
        call_args = mock_authentication_service.authenticate_control_plane_call.call_args
        assert call_args[0][0] == valid_headers["authorization"]
        
        mock_item_factory.create_item.assert_called_once()
        mock_item.create.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_item_missing_auth_header(self, client, valid_headers, mock_authentication_service):
        """Test item creation with missing authorization header."""
        # Arrange
        headers = valid_headers.copy()
        del headers["authorization"]

        mock_authentication_service.authenticate_control_plane_call.side_effect = AuthenticationException(
        "Missing authorization header")
        
        # Act
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=headers,
            json=TestFixtures.CREATE_PAYLOAD
        )
        
        expected = ExpectedResponses.MISSING_AUTH_HEADER
        assert response.status_code == expected["status_code"]

    
    @pytest.mark.asyncio
    async def test_create_item_authentication_failure(
        self, 
        client, 
        mock_authentication_service,
        valid_headers
    ):
        """Test item creation with authentication failure."""
        # Arrange
        mock_authentication_service.authenticate_control_plane_call.side_effect = AuthenticationException(
            "Invalid token"
        )
        
        # Act
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers,
            json=TestFixtures.CREATE_PAYLOAD
        )
        
        # Assert
        expected = ExpectedResponses.INVALID_AUTH_TOKEN
        assert response.status_code == expected["status_code"]
        response_data = response.json()
        assert response_data.get("error_code") == expected["error_code"]
        assert response_data.get("source") == expected["source"]
        assert "Invalid token" in response_data.get("message", "")
    
    @pytest.mark.asyncio
    async def test_update_item_success(
        self,
        client,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test successful item update."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        # Act
        response = client.patch(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers,
            json=TestFixtures.UPDATE_PAYLOAD
        )
        
        # Assert
        assert response.status_code == 200
        
        # Verify service calls - the item ID might be passed as string
        mock_item.load.assert_called_once()
        # Check if called with either UUID or string
        load_call_args = mock_item.load.call_args[0][0]
        assert str(load_call_args) == str(TestFixtures.ITEM_ID)
        
        mock_item.update.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_update_item_not_found(
        self,
        client,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test updating a non-existent item."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item.load.side_effect = ItemMetadataNotFoundException(TestFixtures.ITEM_ID)
        mock_item_factory.create_item.return_value = mock_item
        
        # Act
        response = client.patch(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers,
            json=TestFixtures.UPDATE_PAYLOAD
        )
        
        # Assert
        expected = ExpectedResponses.ITEM_NOT_FOUND
        assert response.status_code == expected["status_code"]
        response_data = response.json()
        assert response_data.get("error_code") == expected["error_code"]
        assert response_data.get("source") == expected["source"]
        assert "Item metadata file cannot be found" in response_data.get("message", "")
    
    @pytest.mark.asyncio
    async def test_delete_item_success(
        self,
        client,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test successful item deletion."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        # Act
        response = client.delete(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers
        )
        
        # Assert
        assert response.status_code == 200
        
        # Verify service calls
        mock_authentication_service.authenticate_control_plane_call.assert_called_once()
        
        # The load and delete should be called
        mock_item.load.assert_called_once()
        mock_item.delete.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_delete_item_without_subject_token(
        self,
        client,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test item deletion without subject token (system deletion)."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        # Create auth context without subject context
        auth_context = TestHelpers.create_auth_context(has_subject_context=False)
        mock_authentication_service.authenticate_control_plane_call.return_value = auth_context
        
        # Act
        response = client.delete(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers
        )
        
        # Assert
        assert response.status_code == 200
        mock_item.delete.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_item_payload_success(
        self,
        client,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test successful retrieval of item payload."""
        # Arrange
        test_payload = {"test": "payload", "data": {"value": 123}}
        mock_item = TestHelpers.create_mock_item()
        mock_item.get_item_payload.return_value = test_payload
        mock_item_factory.create_item.return_value = mock_item
        
        # Act
        response = client.get(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/payload",
            headers=valid_headers
        )
        
        # Assert
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["itemPayload"] == test_payload
        
        # Verify service calls - be flexible about UUID vs string
        mock_item.load.assert_called_once()
        mock_item.get_item_payload.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_item_payload_unauthorized(
        self,
        client,
        mock_authentication_service,
        valid_headers
    ):
        """Test getting item payload with unauthorized access."""
        # Arrange
        mock_authentication_service.authenticate_control_plane_call.side_effect = UnauthorizedException(
            "Access denied"
        )
        
        # Act
        response = client.get(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/payload",
            headers=valid_headers
        )
        
        # Assert
        expected = ExpectedResponses.ACCESS_DENIED
        assert response.status_code == expected["status_code"]
        response_data = response.json()
        assert response_data.get("error_code") == expected["error_code"]
        assert response_data.get("source") == expected["source"]
    
    @pytest.mark.asyncio
    async def test_invalid_item_type(
        self,
        client,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test operations with invalid item type."""
        # Arrange
        exception = UnexpectedItemTypeException(f"Unknown item type: {TestFixtures.UNKNOWN_ITEM_TYPE}")
        mock_item_factory.create_item.side_effect = exception
        
        # Act
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.UNKNOWN_ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers,
            json=TestFixtures.CREATE_PAYLOAD
        )
        
        # Assert
        expected = ExpectedResponses.UNEXPECTED_ITEM_TYPE
        assert response.status_code == expected["status_code"]
        response_data = response.json()
        assert response_data.get("error_code") == expected["error_code"]
        assert response_data.get("source") == expected["source"]
    
    @pytest.mark.asyncio
    async def test_missing_tenant_id_header(self, client, valid_headers, mock_authentication_service):
        """Test API call with missing tenant ID header."""
        # Arrange
        headers = valid_headers.copy()
        del headers["x_ms_client_tenant_id"]
        
        # Configure mock to raise exception
        exception = AuthenticationException("tenant_id header is missing")
        mock_authentication_service.authenticate_control_plane_call.side_effect = exception
        
        # Act
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=headers,
            json=TestFixtures.CREATE_PAYLOAD
        )
        
        # Assert - Use the expected response constants
        expected = ExpectedResponses.MISSING_TENANT_ID
        assert response.status_code == expected["status_code"]
        response_data = response.json()
        assert response_data.get("error_code") == expected["error_code"]
        assert "tenant_id header is missing" in response_data.get("message", "")
    
    @pytest.mark.asyncio
    async def test_malformed_request_body(self, client, valid_headers):
        """Test API call with malformed request body."""
        # Act
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers,
            json="invalid json string"
        )
        
        # Assert
        expected = ExpectedResponses.VALIDATION_ERROR
        assert response.status_code == expected["status_code"]
    
    @pytest.mark.asyncio
    async def test_concurrent_operations(
        self,
        client,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test handling of concurrent operations on the same item."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        # Act - Make 3 update requests (not truly concurrent in test client)
        responses = []
        for _ in range(3):
            response = client.patch(
                f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
                headers=valid_headers,
                json=TestFixtures.UPDATE_PAYLOAD
            )
            responses.append(response)
        
        # Assert - All should succeed
        for response in responses:
            assert response.status_code == 200
        
        # Verify the item was loaded and updated 3 times
        assert mock_item.load.call_count == 3
        assert mock_item.update.call_count == 3