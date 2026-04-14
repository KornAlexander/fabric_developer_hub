"""Item1 Job Execution Tests

Tests for Item1 job execution pipeline including workflow coverage and edge cases.
"""

import pytest
from unittest.mock import Mock, AsyncMock
from datetime import datetime, timezone

from items.item1 import Item1
from models.authentication_models import AuthorizationContext
from models.item1_metadata import Item1Metadata, Item1Operator
from models.job_metadata import JobMetadata
from fabric_api.models.job_invoke_type import JobInvokeType
from constants.job_types import Item1JobType
from tests.test_helpers import TestHelpers
from tests.test_fixtures import TestFixtures


@pytest.mark.unit
@pytest.mark.models
class TestItem1JobExecution:
    """Job execution pipeline tests - comprehensive workflow coverage."""
    
    @pytest.mark.asyncio
    async def test_execute_job_instant_job_immediate_return(self, mock_auth_context, mock_all_services):
        """Test that InstantJob returns immediately without processing."""
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Act
        await item.execute_job(
            Item1JobType.INSTANT_JOB,
            job_instance_id,
            JobInvokeType.MANUAL,
            {}
        )
        
        # Assert - No metadata store operations should happen for instant jobs
        mock_store = mock_all_services['ItemMetadataStore']
        mock_store.upsert_job.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_execute_job_full_workflow_success(self, mock_auth_context, mock_all_services):
        """Test complete job execution workflow for regular jobs."""
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        item._metadata.operand1 = 10
        item._metadata.operand2 = 5
        item._metadata.operator = Item1Operator.ADD
        item._metadata.use_onelake = True
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock services
        mock_store = mock_all_services['ItemMetadataStore']
        mock_onelake = mock_all_services['OneLakeClientService']
        mock_auth = mock_all_services['AuthenticationService']
        
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_onelake.get_onelake_file_path.return_value = "/test/path/result.txt"
        mock_store.load_job.return_value = JobMetadata(
            job_type=Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id=job_instance_id,
            use_onelake=True,
            is_canceled=False
        )
        
        # Act
        await item.execute_job(
            Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id,
            JobInvokeType.MANUAL,
            {}
        )
        
        # Assert - Job metadata was created
        mock_store.upsert_job.assert_called()
        args = mock_store.upsert_job.call_args[0]
        assert args[0] == TestFixtures.TENANT_ID
        assert args[1] == TestFixtures.ITEM_ID
        assert args[2] == str(job_instance_id)
        
        # Assert - OneLake file was written
        mock_onelake.write_to_onelake_file.assert_called_once()
        write_args = mock_onelake.write_to_onelake_file.call_args[0]
        assert write_args[0] == "mock_token"
        assert write_args[1] == "/test/path/result.txt"
        assert "op1 = 10, op2 = 5, operator = Add, result = 15" in write_args[2]
    
    @pytest.mark.asyncio
    async def test_execute_job_cancellation_handling(self, mock_auth_context, mock_all_services):
        """Test job execution with cancellation behavior.
        """
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        item._metadata.operand1 = 10
        item._metadata.operand2 = 5
        item._metadata.operator = Item1Operator.ADD
        item._metadata.use_onelake = True
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock services
        mock_store = mock_all_services['ItemMetadataStore']
        mock_onelake = mock_all_services['OneLakeClientService']
        mock_auth = mock_all_services['AuthenticationService']
        
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_onelake.get_onelake_file_path.return_value = "/test/path/result.txt"
        
        # Set up the load_job mock to return canceled job on the reload
        canceled_job = JobMetadata(
            job_type=Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id=job_instance_id,
            use_onelake=True,
            canceled_time=datetime.now(timezone.utc) 
        )
        mock_store.load_job.return_value = canceled_job
        
        # Act
        await item.execute_job(
            Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id,
            JobInvokeType.MANUAL,
            {}
        )
        
        mock_store.upsert_job.assert_called()  # Initial job metadata creation
        mock_store.load_job.assert_called()    # Job metadata reload
        mock_onelake.write_to_onelake_file.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_execute_job_missing_metadata_recreation(self, mock_auth_context, mock_all_services):
        """Test job metadata recreation when missing."""
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        item._metadata.operand1 = 10
        item._metadata.operand2 = 5
        item._metadata.operator = Item1Operator.ADD
        item._metadata.use_onelake = True  # Ensure valid storage
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock services
        mock_store = mock_all_services['ItemMetadataStore']
        mock_onelake = mock_all_services['OneLakeClientService']
        mock_auth = mock_all_services['AuthenticationService']
        
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_onelake.get_onelake_file_path.return_value = "/test/path/result.txt"
        
        # First load_job call raises FileNotFoundError, second succeeds
        original_metadata = JobMetadata(
            job_type=Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id=job_instance_id,
            use_onelake=True,  # Match item metadata
            is_canceled=False
        )
        mock_store.load_job.side_effect = [FileNotFoundError(), original_metadata]
        
        # Act
        await item.execute_job(
            Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id,
            JobInvokeType.MANUAL,
            {}
        )
        
        # Assert - upsert_job was called twice (initial creation + recreation)
        assert mock_store.upsert_job.call_count == 2