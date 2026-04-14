"""Comprehensive tests for JobsController."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from uuid import UUID
import asyncio

from tests.test_helpers import TestHelpers
from tests.test_fixtures import TestFixtures
from exceptions.exceptions import (
    AuthenticationException,
    ItemMetadataNotFoundException,
    UnauthorizedException,
    AuthenticationUIRequiredException
)
from fabric_api.models.create_item_job_instance_request import CreateItemJobInstanceRequest
from fabric_api.models.item_job_instance_state import ItemJobInstanceState
from fabric_api.models.job_instance_status import JobInstanceStatus
from fabric_api.models.job_invoke_type import JobInvokeType
from fabric_api.models.error_details import ErrorDetails
from fabric_api.models.error_source import ErrorSource
from fabric_api.impl.jobs_controller import JobsController, _background_tasks


@pytest.mark.unit
@pytest.mark.controllers
class TestJobsController:
    """Test cases for JobsController."""
    
    @pytest.fixture
    def controller(self):
        """Create a JobsController instance."""
        return JobsController()
    
    @pytest.mark.asyncio
    async def test_create_job_instance_success(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test successful job instance creation."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        job_request = CreateItemJobInstanceRequest(
            invoke_type=JobInvokeType.MANUAL,
            creation_payload={"test": "data"}
        )
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            # Act
            result = await controller.jobs_create_item_job_instance(
                workspaceId=TestFixtures.WORKSPACE_ID,
                itemType=TestFixtures.ITEM_TYPE,
                itemId=TestFixtures.ITEM_ID,
                jobType="RunCalculation",
                jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                authorization=valid_headers["authorization"],
                x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"],
                create_item_job_instance_request=job_request
            )
            
            # Assert
            assert result is None  # 202 Accepted returns None
            
            # Wait for background task to complete
            await asyncio.sleep(0.1)
            
            # Verify service calls
            mock_authentication_service.authenticate_control_plane_call.assert_called_once()
            mock_item.load.assert_called_once()
            mock_item.execute_job.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_job_instance_authentication_failure(
        self,
        controller,
        mock_authentication_service,
        valid_headers
    ):
        """Test job creation with authentication failure."""
        # Arrange
        mock_authentication_service.authenticate_control_plane_call.side_effect = AuthenticationException(
            "Invalid token"
        )
        
        job_request = CreateItemJobInstanceRequest(
            invoke_type=JobInvokeType.MANUAL,
            creation_payload={}
        )
        
        # Patch the service getter
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service):
            
            # Act & Assert
            with pytest.raises(AuthenticationException) as exc_info:
                await controller.jobs_create_item_job_instance(
                    workspaceId=TestFixtures.WORKSPACE_ID,
                    itemType=TestFixtures.ITEM_TYPE,
                    itemId=TestFixtures.ITEM_ID,
                    jobType="RunCalculation",
                    jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                    authorization=valid_headers["authorization"],
                    x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"],
                    create_item_job_instance_request=job_request
                )
            
            assert "Invalid token" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_create_job_instance_item_not_found(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test job creation when item doesn't exist."""
        # Arrange
        # Create a real item instance (not a mock) or a partial mock
        from items.item1 import Item1  # Import your actual item class
        mock_item = Item1(mock_authentication_service.authenticate_control_plane_call.return_value)
        
        # Mock the item_metadata_store to simulate item not found
        mock_metadata_store = AsyncMock()
        mock_metadata_store.exists.return_value = False  # This will trigger ItemMetadataNotFoundException
        
        # Inject the mock store into the item
        mock_item.item_metadata_store = mock_metadata_store
        
        mock_item_factory.create_item.return_value = mock_item
        
        job_request = CreateItemJobInstanceRequest(
            invoke_type=JobInvokeType.MANUAL,
            creation_payload={}
        )
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            # Act & Assert - Should raise the exception immediately
            with pytest.raises(ItemMetadataNotFoundException) as exc_info:
                await controller.jobs_create_item_job_instance(
                    workspaceId=TestFixtures.WORKSPACE_ID,
                    itemType=TestFixtures.ITEM_TYPE,
                    itemId=TestFixtures.ITEM_ID,
                    jobType="RunCalculation",
                    jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                    authorization=valid_headers["authorization"],
                    x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"],
                    create_item_job_instance_request=job_request
                )
            
            # Verify the exception message
            assert str(TestFixtures.ITEM_ID) in str(exc_info.value)
            
            # Verify that exists was called
            mock_metadata_store.exists.assert_called_once_with(
                mock_authentication_service.authenticate_control_plane_call.return_value.tenant_object_id,
                str(TestFixtures.ITEM_ID)
            )
    
    @pytest.mark.asyncio
    async def test_create_job_instance_without_request_body(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test job creation without request body."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            # Act
            result = await controller.jobs_create_item_job_instance(
                workspaceId=TestFixtures.WORKSPACE_ID,
                itemType=TestFixtures.ITEM_TYPE,
                itemId=TestFixtures.ITEM_ID,
                jobType="RunCalculation",
                jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                authorization=valid_headers["authorization"],
                x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"],
                create_item_job_instance_request=None
            )
            
            # Assert
            assert result is None
            
            # Wait for background task
            await asyncio.sleep(0.1)
            
            # Verify execute_job was called with None invoke_type and empty payload
            mock_item.execute_job.assert_called_once()
            call_args = mock_item.execute_job.call_args[0]
            assert call_args[2] is None  # invoke_type
            assert call_args[3] == {}  # creation_payload
    
    @pytest.mark.asyncio
    async def test_get_job_instance_state_success(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test successful retrieval of job instance state."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item.item_object_id = TestFixtures.ITEM_ID
        
        expected_state = ItemJobInstanceState(
            status=JobInstanceStatus.COMPLETED,
            message="Job completed successfully"
        )
        mock_item.get_job_state.return_value = expected_state
        mock_item_factory.create_item.return_value = mock_item
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            # Act
            result = await controller.jobs_get_item_job_instance_state(
                workspaceId=TestFixtures.WORKSPACE_ID,
                itemType=TestFixtures.ITEM_TYPE,
                itemId=TestFixtures.ITEM_ID,
                jobType="RunCalculation",
                jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                authorization=valid_headers["authorization"],
                x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"]
            )
            
            # Assert
            assert result.status == JobInstanceStatus.COMPLETED
            
            # Verify service calls
            mock_item.load.assert_called_once_with(TestFixtures.ITEM_ID)
            mock_item.get_job_state.assert_called_once_with("RunCalculation", TestFixtures.JOB_INSTANCE_ID)
    
    @pytest.mark.asyncio
    async def test_get_job_instance_state_item_not_found(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test getting job state when item doesn't exist."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item.item_object_id = None  # Item not found after load
        mock_item_factory.create_item.return_value = mock_item
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            # Act
            result = await controller.jobs_get_item_job_instance_state(
                workspaceId=TestFixtures.WORKSPACE_ID,
                itemType=TestFixtures.ITEM_TYPE,
                itemId=TestFixtures.ITEM_ID,
                jobType="RunCalculation",
                jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                authorization=valid_headers["authorization"],
                x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"]
            )
            
            # Assert
            assert result.status == JobInstanceStatus.FAILED
            assert result.error_details.error_code == "ItemNotFound"
            assert result.error_details.source == ErrorSource.SYSTEM
    
    @pytest.mark.asyncio
    async def test_get_job_state_various_statuses(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test getting job state with various status values."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item.item_object_id = TestFixtures.ITEM_ID
        mock_item_factory.create_item.return_value = mock_item
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            statuses = [
                (JobInstanceStatus.NOTSTARTED, None),
                (JobInstanceStatus.INPROGRESS, "Processing..."),
                (JobInstanceStatus.COMPLETED, "Success"),
                (JobInstanceStatus.FAILED, ErrorDetails(
                    error_code="ProcessingError",
                    message="Job failed",
                    source=ErrorSource.SYSTEM
                )),
                (JobInstanceStatus.CANCELLED, None)
            ]
            
            for status, detail in statuses:
                # Configure mock
                if isinstance(detail, str):
                    state = ItemJobInstanceState(status=status, message=detail)
                elif isinstance(detail, ErrorDetails):
                    state = ItemJobInstanceState(status=status, error_details=detail)
                else:
                    state = ItemJobInstanceState(status=status)
                
                mock_item.get_job_state.return_value = state
                
                # Act
                result = await controller.jobs_get_item_job_instance_state(
                    workspaceId=TestFixtures.WORKSPACE_ID,
                    itemType=TestFixtures.ITEM_TYPE,
                    itemId=TestFixtures.ITEM_ID,
                    jobType="RunCalculation",
                    jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                    authorization=valid_headers["authorization"],
                    x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"]
                )
                
                # Assert
                assert result.status == status
    
    @pytest.mark.asyncio
    async def test_cancel_job_instance_success(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test successful job cancellation."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item.item_object_id = TestFixtures.ITEM_ID
        mock_item_factory.create_item.return_value = mock_item
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            # Act
            result = await controller.jobs_cancel_item_job_instance(
                workspaceId=TestFixtures.WORKSPACE_ID,
                itemType=TestFixtures.ITEM_TYPE,
                itemId=TestFixtures.ITEM_ID,
                jobType="RunCalculation",
                jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                authorization=valid_headers["authorization"],
                x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"]
            )
            
            # Assert
            assert result.status == JobInstanceStatus.CANCELLED
            
            # Verify service calls
            mock_item.cancel_job.assert_called_once_with("RunCalculation", TestFixtures.JOB_INSTANCE_ID)
    
    @pytest.mark.asyncio
    async def test_cancel_job_instance_unauthorized(
        self,
        controller,
        mock_authentication_service,
        valid_headers
    ):
        """Test job cancellation with unauthorized access."""
        # Arrange
        mock_authentication_service.authenticate_control_plane_call.side_effect = UnauthorizedException(
            "Access denied"
        )
        
        # Patch the service getter
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service):
            
            # Act & Assert
            with pytest.raises(UnauthorizedException):
                await controller.jobs_cancel_item_job_instance(
                    workspaceId=TestFixtures.WORKSPACE_ID,
                    itemType=TestFixtures.ITEM_TYPE,
                    itemId=TestFixtures.ITEM_ID,
                    jobType="RunCalculation",
                    jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                    authorization=valid_headers["authorization"],
                    x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"]
                )
    
    @pytest.mark.asyncio
    async def test_cancel_job_instance_item_not_found(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test cancelling job when item doesn't exist."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item.item_object_id = None  # Item not found
        mock_item_factory.create_item.return_value = mock_item
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            # Act
            result = await controller.jobs_cancel_item_job_instance(
                workspaceId=TestFixtures.WORKSPACE_ID,
                itemType=TestFixtures.ITEM_TYPE,
                itemId=TestFixtures.ITEM_ID,
                jobType="RunCalculation",
                jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                authorization=valid_headers["authorization"],
                x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"]
            )
            
            # Assert
            assert result.status == JobInstanceStatus.FAILED
            assert result.error_details.error_code == "ItemNotFound"
    
    @pytest.mark.asyncio
    async def test_job_execution_error_handling(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test error handling during job execution."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item.execute_job.side_effect = Exception("Job execution failed")
        mock_item_factory.create_item.return_value = mock_item
        
        job_request = CreateItemJobInstanceRequest(
            invoke_type=JobInvokeType.MANUAL,
            creation_payload={}
        )
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            # Act - Should not raise exception (background task handles it)
            result = await controller.jobs_create_item_job_instance(
                workspaceId=TestFixtures.WORKSPACE_ID,
                itemType=TestFixtures.ITEM_TYPE,
                itemId=TestFixtures.ITEM_ID,
                jobType="RunCalculation",
                jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                authorization=valid_headers["authorization"],
                x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"],
                create_item_job_instance_request=job_request
            )
            
            # Assert
            assert result is None  # 202 Accepted
            
            # Wait for background task to process
            await asyncio.sleep(0.1)
            
            # Verify execute_job was called and failed
            mock_item.execute_job.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_concurrent_job_requests(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test handling of concurrent job requests."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        job_request = CreateItemJobInstanceRequest(
            invoke_type=JobInvokeType.MANUAL,
            creation_payload={}
        )
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            # Act - Create multiple jobs concurrently
            job_ids = [
                UUID("55555555-5555-5555-5555-555555555550"),
                UUID("55555555-5555-5555-5555-555555555551"),
                UUID("55555555-5555-5555-5555-555555555552")
            ]
            
            tasks = []
            for job_id in job_ids:
                task = controller.jobs_create_item_job_instance(
                    workspaceId=TestFixtures.WORKSPACE_ID,
                    itemType=TestFixtures.ITEM_TYPE,
                    itemId=TestFixtures.ITEM_ID,
                    jobType="RunCalculation",
                    jobInstanceId=job_id,
                    authorization=valid_headers["authorization"],
                    x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"],
                    create_item_job_instance_request=job_request
                )
                tasks.append(task)
            
            # Execute all tasks concurrently
            results = await asyncio.gather(*tasks)
            
            # Assert
            for result in results:
                assert result is None  # All should return 202 Accepted
            
            # Wait for all background tasks
            await asyncio.sleep(0.2)
            
            # Verify all jobs were executed
            assert mock_item.execute_job.call_count == 3
    
    @pytest.mark.asyncio
    async def test_background_task_tracking(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test that background tasks are properly tracked."""
        # Clear any existing tasks
        _background_tasks.clear()
        
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        # Make execute_job take some time
        async def slow_execute(*args, **kwargs):
            await asyncio.sleep(0.5)
        mock_item.execute_job = slow_execute
        mock_item_factory.create_item.return_value = mock_item
        
        job_request = CreateItemJobInstanceRequest(
            invoke_type=JobInvokeType.MANUAL,
            creation_payload={}
        )
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            # Act
            await controller.jobs_create_item_job_instance(
                workspaceId=TestFixtures.WORKSPACE_ID,
                itemType=TestFixtures.ITEM_TYPE,
                itemId=TestFixtures.ITEM_ID,
                jobType="RunCalculation",
                jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                authorization=valid_headers["authorization"],
                x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"],
                create_item_job_instance_request=job_request
            )
            
            # Assert - Task should be tracked
            await asyncio.sleep(0.1)  # Let task start
            assert len(_background_tasks) == 1
            
            # Wait for task to complete
            await asyncio.sleep(0.6)
            assert len(_background_tasks) == 0
    
    @pytest.mark.asyncio
    async def test_cleanup_background_tasks(self):
        """Test cleanup of background tasks during shutdown."""
        # Clear any existing tasks
        _background_tasks.clear()
        
        # Add some mock tasks
        async def dummy_task():
            await asyncio.sleep(1)
        
        task1 = asyncio.create_task(dummy_task())
        task2 = asyncio.create_task(dummy_task())
        _background_tasks.add(task1)
        _background_tasks.add(task2)
        
        # Act
        from fabric_api.impl.jobs_controller import cleanup_background_tasks
        await cleanup_background_tasks(timeout=0.1)
        
        # Assert
        assert len(_background_tasks) == 0
        assert task1.cancelled()
        assert task2.cancelled()
    
    @pytest.mark.asyncio
    async def test_cleanup_background_tasks_with_completed_tasks(self):
        """Test cleanup handles already completed tasks."""
        # Clear any existing tasks
        _background_tasks.clear()
        
        # Add a completed task
        async def quick_task():
            return "done"
        
        task = asyncio.create_task(quick_task())
        await task  # Let it complete
        _background_tasks.add(task)
        
        # Act
        from fabric_api.impl.jobs_controller import cleanup_background_tasks
        await cleanup_background_tasks()
        
        # Assert
        assert len(_background_tasks) == 0
    
    @pytest.mark.asyncio
    async def test_auth_ui_required_exception(
        self,
        controller,
        mock_authentication_service,
        valid_headers
    ):
        """Test handling of authentication UI required exception."""
        # Arrange
        mock_authentication_service.authenticate_control_plane_call.side_effect = AuthenticationUIRequiredException(
            "User interaction required"
        )
        
        # Patch the service getter
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service):
            
            # Act & Assert
            with pytest.raises(AuthenticationUIRequiredException):
                await controller.jobs_get_item_job_instance_state(
                    workspaceId=TestFixtures.WORKSPACE_ID,
                    itemType=TestFixtures.ITEM_TYPE,
                    itemId=TestFixtures.ITEM_ID,
                    jobType="RunCalculation",
                    jobInstanceId=TestFixtures.JOB_INSTANCE_ID,
                    authorization=valid_headers["authorization"],
                    x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"]
                )
    
    @pytest.mark.asyncio
    async def test_create_job_with_all_invoke_types(
        self,
        controller,
        mock_authentication_service,
        mock_item_factory,
        valid_headers
    ):
        """Test creating jobs with all invoke types."""
        # Arrange
        mock_item = TestHelpers.create_mock_item()
        mock_item_factory.create_item.return_value = mock_item
        
        # Patch the service getters
        with patch('fabric_api.impl.jobs_controller.get_authentication_service', return_value=mock_authentication_service), \
             patch('fabric_api.impl.jobs_controller.get_item_factory', return_value=mock_item_factory):
            
            invoke_types = [
                (JobInvokeType.MANUAL, {"manual": "data"}),
                (JobInvokeType.SCHEDULED, {"schedule": "0 * * * *"})            ]
            
            for i, (invoke_type, payload) in enumerate(invoke_types):
                job_request = CreateItemJobInstanceRequest(
                    invoke_type=invoke_type,
                    creation_payload=payload
                )
                
                # Use unique job instance ID for each
                job_id = UUID(f"66666666-6666-6666-6666-66666666666{i}")
                
                # Act
                result = await controller.jobs_create_item_job_instance(
                    workspaceId=TestFixtures.WORKSPACE_ID,
                    itemType=TestFixtures.ITEM_TYPE,
                    itemId=TestFixtures.ITEM_ID,
                    jobType="RunCalculation",
                    jobInstanceId=job_id,
                    authorization=valid_headers["authorization"],
                    x_ms_client_tenant_id=valid_headers["x_ms_client_tenant_id"],
                    create_item_job_instance_request=job_request
                )
                
                # Assert
                assert result is None  # 202 Accepted
            
            # Wait for all background tasks
            await asyncio.sleep(0.2)
            
            # Verify all jobs were executed
            assert mock_item.execute_job.call_count == 2