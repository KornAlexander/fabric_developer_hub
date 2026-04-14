# coding: utf-8

"""
Enhanced tests for item_lifecycle_api endpoints.
These tests focus on the API layer validation and routing.
"""

from exceptions.exceptions import AuthenticationException
import pytest
from fastapi.testclient import TestClient
from uuid import UUID
import json
from unittest.mock import ANY

from tests.test_fixtures import TestFixtures
from tests.test_helpers import TestHelpers
from tests.constants import ExpectedResponses


@pytest.mark.unit
@pytest.mark.api
class TestItemLifecycleAPI:
    """Test cases for Item Lifecycle API endpoints."""
    
    def test_create_item_valid_request(self, client: TestClient, valid_headers, mock_item_factory):
        """Test creating an item with valid request."""
        # Ensure mock returns async mock item
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers,
            json=TestFixtures.CREATE_PAYLOAD
        )     
        assert response.status_code == 200
        mock_item_factory.create_item.assert_called_once_with(
        TestFixtures.ITEM_TYPE, 
        ANY  # auth context
        )
        mock_item.create.assert_called_once()
        
        # Assert - Call arguments
        create_call_args = mock_item.create.call_args
        assert create_call_args[0][0] == TestFixtures.WORKSPACE_ID
        assert create_call_args[0][1] == TestFixtures.ITEM_ID
        assert create_call_args[0][2] is not None

    @pytest.mark.parametrize("invalid_uuid", [
        "not-a-uuid",
        "12345",
        "123e4567-e89b-12d3-a456-426614174000-extra"
    ])
    def test_invalid_uuid_parameters(self, client, valid_headers, invalid_uuid):
        """Test API with various invalid UUID formats."""
        response = client.post(
            f"/workspaces/{invalid_uuid}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers,
            json=TestFixtures.CREATE_PAYLOAD
        )
        
        assert response.status_code == 400 
        error_data = response.json()
        assert error_data["error_code"] == "InvalidParameter"
        assert error_data["source"] == "User"
        assert error_data["is_permanent"] == True
        assert "Invalid parameter 'workspaceId'" in error_data["message"]
        assert "badly formed hexadecimal UUID string" in error_data["message"]
    
    def test_update_item_valid_request(self, client: TestClient, valid_headers, mock_item_factory):
        """Test updating an item with valid request."""
        # Ensure mock returns async mock item
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        response = client.patch(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers,
            json=TestFixtures.UPDATE_PAYLOAD
        )
        
        assert response.status_code == 200
    
    def test_delete_item_valid_request(self, client: TestClient, valid_headers, mock_item_factory):
        """Test deleting an item with valid request."""
        # Ensure mock returns async mock item
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        response = client.delete(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers
        )
        
        assert response.status_code == 200
    
    def test_get_item_payload_valid_request(self, client: TestClient, valid_headers, mock_item_factory):
        """Test getting item payload with valid request."""
        # Ensure mock returns async mock item with payload
        test_payload = {"test": "payload"}
        mock_item = TestHelpers.create_mock_item()
        mock_item.get_item_payload.return_value = test_payload
        mock_item_factory.create_item.return_value = mock_item
        
        response = client.get(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/payload",
            headers=valid_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "itemPayload" in data
    
    def test_missing_required_headers(self, client: TestClient, mock_authentication_service):
        """Test API call with missing required headers."""
        # Ensure mock returns async mock item
        mock_authentication_service.authenticate_control_plane_call.side_effect = AuthenticationException(
        "Missing authorization header"
    )
        
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            json=TestFixtures.CREATE_PAYLOAD
        )
        
        expected = ExpectedResponses.MISSING_AUTH_HEADER
        assert response.status_code == expected["status_code"]
    
    def test_invalid_json_payload(self, client: TestClient, valid_headers):
        """Test API call with invalid JSON payload."""
        # Merge headers and add content-type
        headers = {**valid_headers, "content-type": "application/json"}
        
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=headers,
            content="invalid json" 
        )
        
        expected = ExpectedResponses.VALIDATION_ERROR
        assert response.status_code == expected["status_code"]
    
    def test_empty_request_body(self, client: TestClient, valid_headers):
        """Test create/update with empty request body."""
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}",
            headers=valid_headers,
            json={}
        )
        
        expected = ExpectedResponses.VALIDATION_ERROR
        assert response.status_code == expected["status_code"]