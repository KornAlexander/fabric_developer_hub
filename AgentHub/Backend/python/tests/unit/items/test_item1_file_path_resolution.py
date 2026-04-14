"""Item1 FilePathResolution Tests

Tests for Item1 filepathresolution functionality.
"""

import pytest
import asyncio
import os
import random
from unittest.mock import Mock, AsyncMock, patch, MagicMock, mock_open
from uuid import UUID, uuid4
from typing import Dict, Any

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
class TestItem1FilePathResolution:
    """File path resolution tests - comprehensive path generation coverage."""
    
    @pytest.mark.parametrize("job_type,expected_extension", [
        (Item1JobType.CALCULATE_AS_TEXT, ".txt"),
        (Item1JobType.SCHEDULED_JOB, ".txt"),
        (Item1JobType.LONG_RUNNING_CALCULATE_AS_TEXT, ".txt"),
        (Item1JobType.CALCULATE_AS_PARQUET, ".parquet"),
        ("UnknownJobType", ".txt")  # Default fallback
    ])
    def test_get_calculation_result_file_path_job_types(self, mock_auth_context, mock_all_services, job_type, expected_extension):
        """Test file path generation for different job types."""
        # Arrange
        item = Item1(mock_auth_context)
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        item._metadata.use_onelake = True
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        job_metadata = JobMetadata(
            job_type=job_type,
            job_instance_id=job_instance_id,
            use_onelake=True
        )
        
        # Mock OneLake service
        mock_onelake = mock_all_services['OneLakeClientService']
        expected_path = f"/workspace/{TestFixtures.WORKSPACE_ID}/item/{TestFixtures.ITEM_ID}/CalculationResult_{job_instance_id}{expected_extension}"
        mock_onelake.get_onelake_file_path.return_value = expected_path
        
        # Act
        file_path = item._get_calculation_result_file_path(job_metadata)
        
        # Assert
        assert file_path == expected_path
        mock_onelake.get_onelake_file_path.assert_called_once()
        args = mock_onelake.get_onelake_file_path.call_args[0]
        assert args[0] == TestFixtures.WORKSPACE_ID
        assert args[1] == TestFixtures.ITEM_ID
        assert expected_extension in args[2]
    
    def test_get_calculation_result_file_path_use_lakehouse(self, mock_auth_context, mock_all_services):
        """Test file path generation using lakehouse storage."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata = Item1Metadata(
            lakehouse=ItemReference(
                workspace_id=str(TestFixtures.WORKSPACE_ID),
                id=str(TestFixtures.ITEM_ID)
            ),
            use_onelake=False
        )
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        job_metadata = JobMetadata(
            job_type=Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id=job_instance_id,
            use_onelake=False
        )
        
        # Mock OneLake service
        mock_onelake = mock_all_services['OneLakeClientService']
        expected_path = f"/lakehouse/{TestFixtures.WORKSPACE_ID}/{TestFixtures.ITEM_ID}/CalculationResult_{job_instance_id}.txt"
        mock_onelake.get_onelake_file_path.return_value = expected_path
        
        # Act
        file_path = item._get_calculation_result_file_path(job_metadata)
        
        # Assert
        assert file_path == expected_path
    
    def test_get_calculation_result_file_path_no_storage_error(self, mock_auth_context, mock_all_services):
        """Test file path generation raises error when no valid storage."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata = Item1Metadata(use_onelake=False)  # No lakehouse, no OneLake
        
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        job_metadata = JobMetadata(
            job_type=Item1JobType.CALCULATE_AS_TEXT,
            job_instance_id=job_instance_id,
            use_onelake=False
        )
        
        # Act & Assert
        with pytest.raises(ValueError, match="Cannot write to lakehouse or OneLake"):
            item._get_calculation_result_file_path(job_metadata)
    
    def test_get_calculation_result_file_path_missing_job_id_error(self, mock_auth_context, mock_all_services):
        """Test file path generation raises error when job instance ID is missing."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata.use_onelake = True
        
        # Job metadata without job_instance_id
        job_metadata = {"job_type": Item1JobType.CALCULATE_AS_TEXT}
        
        # Act & Assert
        with pytest.raises(ValueError, match="job_instance_id is missing"):
            item._get_calculation_result_file_path(job_metadata)