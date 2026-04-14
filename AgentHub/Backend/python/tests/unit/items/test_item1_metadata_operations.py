"""Item1 MetadataOperations Tests

Tests for Item1 metadataoperations functionality.
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
class TestItem1MetadataOperations:
    """Metadata operations tests - comprehensive set/update/get operations."""
    
    @pytest.mark.parametrize("test_case", [
        {
            "name": "valid_creation_payload",
            "payload": {
                Fields.PAYLOAD_METADATA: {
                    Fields.OPERAND1_FIELD: 15,
                    Fields.OPERAND2_FIELD: 25,
                    Fields.OPERATOR_FIELD: "Add",
                    Fields.LAKEHOUSE_FIELD: {
                        Fields.LAKEHOUSE_ID_FIELD: str(TestFixtures.ITEM_ID),
                        Fields.LAKEHOUSE_WORKSPACE_ID_FIELD: str(TestFixtures.WORKSPACE_ID)
                    },
                    Fields.USE_ONELAKE_FIELD: False
                }
            },
            "expected_operand1": 15,
            "expected_operand2": 25,
            "expected_operator": Item1Operator.ADD,
            "should_raise": False
        },
        {
            "name": "onelake_only_payload",
            "payload": {
                Fields.PAYLOAD_METADATA: {
                    Fields.OPERAND1_FIELD: 50,
                    Fields.OPERAND2_FIELD: 60,
                    Fields.OPERATOR_FIELD: "Divide",
                    Fields.USE_ONELAKE_FIELD: True
                }
            },
            "expected_operand1": 50,
            "expected_operand2": 60,
            "expected_operator": Item1Operator.DIVIDE,
            "should_raise": False
        },
        {
            "name": "missing_metadata_field",
            "payload": {"some_other_field": "value"},
            "should_raise": True,
            "error_message": "Invalid item payload"
        },
        {
            "name": "missing_lakehouse_and_onelake_false",
            "payload": {
                Fields.PAYLOAD_METADATA: {
                    Fields.OPERAND1_FIELD: 10,
                    Fields.OPERAND2_FIELD: 20,
                    Fields.OPERATOR_FIELD: "Add",
                    Fields.USE_ONELAKE_FIELD: False
                }
            },
            "should_raise": True,
            "error_message": "Missing Lakehouse reference"
        }
    ])
    def test_set_definition_scenarios(self, mock_auth_context, mock_all_services, test_case):
        """Test set_definition with various scenarios."""
        # Arrange
        item = Item1(mock_auth_context)
        
        if test_case.get("should_raise", False):
            # Act & Assert
            with pytest.raises(ValueError, match=test_case["error_message"]):
                item.set_definition(test_case["payload"])
        else:
            # Act
            item.set_definition(test_case["payload"])
            
            # Assert
            assert item._metadata.operand1 == test_case["expected_operand1"]
            assert item._metadata.operand2 == test_case["expected_operand2"]
            assert item._metadata.operator == test_case["expected_operator"]
    
    def test_update_definition_preserves_last_result(self, mock_auth_context, mock_all_services):
        """Test that update_definition preserves last_calculation_result_location."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata = Item1Metadata(
            operand1=10,
            operand2=20,
            operator=Item1Operator.ADD,
            last_calculation_result_location="/previous/result.txt"
        )
        
        payload = {
            Fields.PAYLOAD_METADATA: {
                Fields.OPERAND1_FIELD: 30,
                Fields.OPERAND2_FIELD: 40,
                Fields.OPERATOR_FIELD: "Multiply",
                Fields.USE_ONELAKE_FIELD: True
            }
        }
        
        # Act
        item.update_definition(payload)
        
        # Assert - New values set
        assert item._metadata.operand1 == 30
        assert item._metadata.operand2 == 40
        assert item._metadata.operator == Item1Operator.MULTIPLY
        
        # Assert - Last result location preserved
        assert item._metadata.last_calculation_result_location == "/previous/result.txt"
    
    def test_metadata_cloning_operations(self, mock_auth_context, mock_all_services):
        """Test metadata cloning get/set operations."""
        # Arrange
        item = Item1(mock_auth_context)
        original_metadata = Item1Metadata(
            operand1=100,
            operand2=200,
            operator=Item1Operator.DIVIDE,
            use_onelake=True,
            last_calculation_result_location="/path/to/result"
        )
        item._metadata = original_metadata
        
        # Act - Get cloned metadata
        cloned_metadata = item.get_type_specific_metadata()
        
        # Assert - Clone independence
        assert cloned_metadata is not original_metadata
        assert cloned_metadata.operand1 == 100
        assert cloned_metadata.operand2 == 200
        
        # Act - Modify clone
        cloned_metadata.operand1 = 999
        
        # Assert - Original unchanged
        assert item._metadata.operand1 == 100
        
        # Act - Set new metadata
        new_metadata = Item1Metadata(operand1=777, operand2=888, operator=Item1Operator.SUBTRACT)
        item.set_type_specific_metadata(new_metadata)
        
        # Assert - Metadata was set as clone
        assert item._metadata is not new_metadata
        assert item._metadata.operand1 == 777
        assert item._metadata.operand2 == 888