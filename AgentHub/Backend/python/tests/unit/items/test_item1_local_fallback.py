"""Item1 LocalFallback Tests

Tests for Item1 localfallback functionality.
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
class TestItem1LocalFallback:
    """Local fallback tests - comprehensive local storage coverage."""
    
    def test_save_result_locally_successful(self, mock_auth_context, mock_all_services):
        """Test successful local result saving."""
        # Arrange
        item = Item1(mock_auth_context)
        job_instance_id = str(TestFixtures.JOB_INSTANCE_ID)
        result = "op1 = 10, op2 = 5, operator = Add, result = 15"
        
        with patch('os.makedirs') as mock_makedirs, \
             patch('builtins.open', mock_open()) as mock_file, \
             patch('os.getcwd', return_value="/test/cwd"):
            
            # Act
            item._save_result_locally(job_instance_id, result)
            
            # Assert - Directory created (use os.path.join for platform independence)
            import os
            expected_path = os.path.join("/test/cwd", "results")
            mock_makedirs.assert_called_once_with(expected_path, exist_ok=True)
            
            # Assert - File written
            expected_file_path = os.path.join("/test/cwd", "results", f"CalculationResult_{job_instance_id}.txt")
            mock_file.assert_called_once_with(expected_file_path, "w")
            mock_file().write.assert_called_once_with(result)
            
            # Assert - Metadata updated
            assert item._metadata.last_calculation_result_location == expected_file_path
    
    def test_save_result_locally_directory_creation_failure(self, mock_auth_context, mock_all_services):
        """Test local save handles directory creation failure gracefully."""
        # Arrange
        item = Item1(mock_auth_context)
        job_instance_id = str(TestFixtures.JOB_INSTANCE_ID)
        result = "test result"
        
        with patch('os.makedirs', side_effect=OSError("Permission denied")) as mock_makedirs, \
             patch('os.getcwd', return_value="/test/cwd"):
            
            # Act - Should not raise exception
            item._save_result_locally(job_instance_id, result)
            
            # Assert - Attempted directory creation
            mock_makedirs.assert_called_once()