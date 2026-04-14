"""Tests for Jobs API endpoints."""

import pytest
from fastapi import status
from uuid import UUID
import asyncio

from tests.test_fixtures import TestFixtures
from tests.test_helpers import TestHelpers
from tests.constants import ExpectedResponses
from fabric_api.models.job_instance_status import JobInstanceStatus
from fabric_api.models.item_job_instance_state import ItemJobInstanceState
from fabric_api.models.error_details import ErrorDetails
from fabric_api.models.error_source import ErrorSource


@pytest.mark.unit
@pytest.mark.api
class TestJobsAPI:
    """Test cases for Jobs API endpoints."""
    
    def test_create_job_instance_endpoint_valid(self, client, valid_headers, mock_item_factory):
        """Test valid job instance creation request."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        # Act
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/RunCalculation/instances/{TestFixtures.JOB_INSTANCE_ID}",
            headers=valid_headers,
            json=TestFixtures.JOB_CREATE_PAYLOAD
        )
        
        # Assert - Jobs API returns 202 Accepted with no content
        assert response.status_code == status.HTTP_202_ACCEPTED
        
    def test_create_job_instance_missing_headers(self, client, mock_authentication_service):
        """Test job creation with missing authorization header."""
        headers = {"x_ms_client_tenant_id": str(TestFixtures.TENANT_ID)}
        
        # Configure mock to raise exception for missing auth
        from exceptions.exceptions import AuthenticationException
        mock_authentication_service.authenticate_control_plane_call.side_effect = AuthenticationException("Missing authorization header")
        
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/RunCalculation/instances/{TestFixtures.JOB_INSTANCE_ID}",
            headers=headers,
            json=TestFixtures.JOB_CREATE_PAYLOAD
        )
        
        expected = ExpectedResponses.MISSING_AUTH_HEADER
        assert response.status_code == expected["status_code"]
    
    def test_create_job_instance_invalid_json(self, client, valid_headers):
        """Test job creation with invalid JSON payload."""
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/RunCalculation/instances/{TestFixtures.JOB_INSTANCE_ID}",
            headers=valid_headers,
            content="invalid json"  # Not JSON
        )
        
        # FastAPI returns 422 for validation errors
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_create_job_instance_empty_body(self, client, valid_headers, mock_item_factory):
        """Test job creation with empty request body."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/RunCalculation/instances/{TestFixtures.JOB_INSTANCE_ID}",
            headers=valid_headers,
            json=None
        )
        
        # Should still accept empty body for jobs
        assert response.status_code == status.HTTP_202_ACCEPTED
    
    def test_get_job_instance_state_valid(self, client, valid_headers, mock_item_factory):
        """Test getting job instance state."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item.get_job_state.return_value = ItemJobInstanceState(
            status=JobInstanceStatus.COMPLETED,
            message="Job completed successfully"
        )
        mock_item_factory.create_item.return_value = mock_item
        
        response = client.get(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/RunCalculation/instances/{TestFixtures.JOB_INSTANCE_ID}",
            headers=valid_headers
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "Completed"
    
    def test_get_job_instance_state_missing_auth(self, client, mock_authentication_service):
        """Test getting job state without authorization."""
        headers = {"x_ms_client_tenant_id": str(TestFixtures.TENANT_ID)}
        
        from exceptions.exceptions import AuthenticationException
        mock_authentication_service.authenticate_control_plane_call.side_effect = AuthenticationException("Missing authorization")
        
        response = client.get(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/RunCalculation/instances/{TestFixtures.JOB_INSTANCE_ID}",
            headers=headers
        )
        
        expected = ExpectedResponses.MISSING_AUTH_HEADER
        assert response.status_code == expected["status_code"]
    
    def test_cancel_job_instance_valid(self, client, valid_headers, mock_item_factory):
        """Test cancelling a job instance."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/RunCalculation/instances/{TestFixtures.JOB_INSTANCE_ID}/cancel",
            headers=valid_headers
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "Cancelled"
    
    def test_cancel_job_instance_missing_tenant_id(self, client, mock_authentication_service):
        """Test cancelling job without tenant ID."""
        headers = {"authorization": "Bearer token"}
        
        from exceptions.exceptions import AuthenticationException
        mock_authentication_service.authenticate_control_plane_call.side_effect = AuthenticationException("tenant_id header is missing")
        
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/RunCalculation/instances/{TestFixtures.JOB_INSTANCE_ID}/cancel",
            headers=headers
        )
        
        expected = ExpectedResponses.MISSING_TENANT_ID
        assert response.status_code == expected["status_code"]
    
    @pytest.mark.parametrize("job_type", ["RunCalculation", "ScheduledJob", "CustomJob"])
    def test_different_job_types(self, client, valid_headers, job_type, mock_item_factory):
        """Test creating jobs with different job types."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/{job_type}/instances/{TestFixtures.JOB_INSTANCE_ID}",
            headers=valid_headers,
            json=TestFixtures.JOB_CREATE_PAYLOAD
        )
        
        assert response.status_code == status.HTTP_202_ACCEPTED
    
    @pytest.mark.parametrize("invoke_type,payload", [
        ("Manual", TestFixtures.JOB_CREATE_PAYLOAD),
        ("Scheduled", TestFixtures.SCHEDULED_JOB_PAYLOAD)
                ])
    def test_different_invoke_types(self, client, valid_headers, invoke_type, payload, mock_item_factory):
        """Test creating jobs with different invoke types."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        response = client.post(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/RunCalculation/instances/{TestFixtures.JOB_INSTANCE_ID}",
            headers=valid_headers,
            json=payload
        )
        
        assert response.status_code == status.HTTP_202_ACCEPTED
    
    def test_invalid_uuid_format(self, client, valid_headers):
        """Test API with invalid UUID format."""
        response = client.get(
            f"/workspaces/invalid-uuid/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/RunCalculation/instances/{TestFixtures.JOB_INSTANCE_ID}",
            headers=valid_headers
        )
        
        # Should return 400 for invalid UUID
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    def test_special_characters_in_job_type(self, client, valid_headers, mock_item_factory):
        """Test API with special characters in job type."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item.get_job_state.return_value = ItemJobInstanceState(
            status=JobInstanceStatus.NOTSTARTED
        )
        mock_item_factory.create_item.return_value = mock_item
        
        job_type_encoded = "Run%20Calculation%20With%20Spaces"
        
        response = client.get(
            f"/workspaces/{TestFixtures.WORKSPACE_ID}/items/{TestFixtures.ITEM_TYPE}/{TestFixtures.ITEM_ID}/jobTypes/{job_type_encoded}/instances/{TestFixtures.JOB_INSTANCE_ID}",
            headers=valid_headers
        )
        
        # Should handle URL encoded job types
        assert response.status_code == status.HTTP_200_OK