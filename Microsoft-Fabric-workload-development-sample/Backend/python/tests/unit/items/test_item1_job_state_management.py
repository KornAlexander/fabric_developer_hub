"""Item1 JobStateManagement Tests

Tests for Item1 jobstatemanagement functionality.
"""

import pytest
import asyncio
import os
import random
from unittest.mock import Mock, AsyncMock, patch, MagicMock, mock_open
from uuid import UUID, uuid4
from typing import Dict, Any
from datetime import datetime, timezone

from items.item1 import Item1
from models.authentication_models import AuthorizationContext
from models.item1_metadata import Item1Metadata, Item1Operator
from models.item_reference import ItemReference
from models.fabric_item import FabricItem
from models.job_metadata import JobMetadata
from fabric_api.models.job_invoke_type import JobInvokeType
from fabric_api.models.item_job_instance_state import ItemJobInstanceState
from fabric_api.models.job_instance_status import JobInstanceStatus
from constants.workload_constants import WorkloadConstants
from constants.environment_constants import EnvironmentConstants
from constants.job_types import Item1JobType
from constants.item1_field_names import Item1FieldNames as Fields
from exceptions.exceptions import (
    DoubledOperandsOverflowException, 
    AuthenticationUIRequiredException
)
from tests.test_helpers import TestHelpers
from tests.test_fixtures import TestFixtures


@pytest.mark.unit
@pytest.mark.models
class TestItem1JobStateManagement:
    """Job state management tests - comprehensive state tracking coverage."""
    
    @pytest.mark.asyncio
    async def test_get_job_state_instant_job_completed(self, mock_auth_context, mock_all_services):
        """Test that instant jobs immediately return COMPLETED status."""
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Act
        state = await item.get_job_state(Item1JobType.INSTANT_JOB, job_instance_id)
        
        # Assert
        assert state.status == JobInstanceStatus.COMPLETED
    
    @pytest.mark.asyncio
    async def test_get_job_state_missing_metadata_failed(self, mock_auth_context, mock_all_services):
        """Test get_job_state returns FAILED when job metadata doesn't exist."""
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock services
        mock_store = mock_all_services['ItemMetadataStore']
        mock_store.exists_job.return_value = False
        
        # Act
        state = await item.get_job_state(Item1JobType.CALCULATE_AS_TEXT, job_instance_id)
        
        # Assert
        assert state.status == JobInstanceStatus.FAILED
    
    @pytest.mark.asyncio
    async def test_get_job_state_canceled_job(self, mock_auth_context, mock_all_services):
        """Test get_job_state behavior for canceled jobs.
        """
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock services
        mock_store = mock_all_services['ItemMetadataStore']
        mock_onelake = mock_all_services['OneLakeClientService']
        mock_auth = mock_all_services['AuthenticationService']
        
        mock_store.exists_job.return_value = True
        
        # Create a canceled job metadata
        canceled_job = JobMetadata(
            job_type=Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id=job_instance_id,
            use_onelake=True,
            canceled_time=datetime.now(timezone.utc)
        )
        mock_store.load_job.return_value = canceled_job
        
        # Mock the file existence check to return True (file exists)
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_onelake.get_onelake_file_path.return_value = "/test/path/result.txt"
        mock_onelake.check_if_file_exists.return_value = True
        
        # Act
        state = await item.get_job_state(Item1JobType.CALCULATE_AS_TEXT, job_instance_id)
        
        # Assert 
        assert state.status == JobInstanceStatus.CANCELLED
        
    
    @pytest.mark.asyncio
    async def test_get_job_state_file_exists_completed(self, mock_auth_context, mock_all_services):
        """Test get_job_state returns COMPLETED when result file exists."""
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        item._metadata.use_onelake = True
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock services
        mock_store = mock_all_services['ItemMetadataStore']
        mock_onelake = mock_all_services['OneLakeClientService']
        mock_auth = mock_all_services['AuthenticationService']
        
        mock_store.exists_job.return_value = True
        mock_store.load_job.return_value = JobMetadata(
            job_type=Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id=job_instance_id,
            use_onelake=True,
            is_canceled=False
        )
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_onelake.get_onelake_file_path.return_value = "/test/path/result.txt"
        mock_onelake.check_if_file_exists.return_value = True  # File exists
        
        # Act
        state = await item.get_job_state(Item1JobType.CALCULATE_AS_TEXT, job_instance_id)
        
        # Assert
        assert state.status == JobInstanceStatus.COMPLETED
    
    @pytest.mark.asyncio
    async def test_get_job_state_file_missing_in_progress(self, mock_auth_context, mock_all_services):
        """Test get_job_state returns IN_PROGRESS when result file doesn't exist."""
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        item._metadata.use_onelake = True
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock services
        mock_store = mock_all_services['ItemMetadataStore']
        mock_onelake = mock_all_services['OneLakeClientService']
        mock_auth = mock_all_services['AuthenticationService']
        
        mock_store.exists_job.return_value = True
        mock_store.load_job.return_value = JobMetadata(
            job_type=Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id=job_instance_id,
            use_onelake=True,
            is_canceled=False
        )
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_onelake.get_onelake_file_path.return_value = "/test/path/result.txt"
        mock_onelake.check_if_file_exists.return_value = False  # File doesn't exist
        
        # Act
        state = await item.get_job_state(Item1JobType.CALCULATE_AS_TEXT, job_instance_id)
        
        # Assert
        assert state.status == JobInstanceStatus.INPROGRESS