"""Item1 Core Functionality Tests

Tests for core Item1 functionality including initialization, properties, and basic operations.
"""

import pytest
from unittest.mock import Mock, AsyncMock

from items.item1 import Item1
from models.authentication_models import AuthorizationContext
from models.item1_metadata import Item1Metadata, Item1Operator
from models.item_reference import ItemReference
from constants.workload_constants import WorkloadConstants
from constants.environment_constants import EnvironmentConstants
from tests.test_helpers import TestHelpers
from tests.test_fixtures import TestFixtures


@pytest.mark.unit
@pytest.mark.models
class TestItem1Core:
    """Core Item1 functionality tests - initialization, properties, and basic operations."""
    
    def test_item1_initialization_success(self, mock_auth_context, mock_all_services):
        """Test successful Item1 initialization with all required services."""
        # Act
        item = Item1(mock_auth_context)
        
        # Assert - Verify core initialization
        assert item.item_type == WorkloadConstants.ItemTypes.ITEM1
        assert item.auth_context is mock_auth_context
        assert isinstance(item._metadata, Item1Metadata)
        assert item._lakehouse_client_service is not None
        
        # Assert - Verify default metadata state
        assert item._metadata.operand1 == 0
        assert item._metadata.operand2 == 0
        assert item._metadata.operator == Item1Operator.UNDEFINED
        assert item._metadata.use_onelake is False
    
    def test_static_class_variables_configuration(self, mock_auth_context, mock_all_services):
        """Test that static class variables are properly configured."""
        # Act
        item = Item1(mock_auth_context)
        
        # Assert - Test supported_operators
        assert Item1Operator.UNDEFINED.value not in Item1.supported_operators
        expected_operators = [op.value for op in Item1Operator if op != Item1Operator.UNDEFINED]
        assert set(Item1.supported_operators) == set(expected_operators)
        
        # Assert - Test fabric_scopes
        assert len(Item1.fabric_scopes) == 1
        assert "Lakehouse.Read.All" in Item1.fabric_scopes[0]
        assert EnvironmentConstants.FABRIC_BACKEND_RESOURCE_ID in Item1.fabric_scopes[0]
    
    @pytest.mark.parametrize("property_name,metadata_attribute,test_value", [
        ("operand1", "operand1", 42),
        ("operand2", "operand2", 84),
        ("operator", "operator", Item1Operator.MULTIPLY),
        ("lakehouse", "lakehouse", ItemReference(workspace_id="test-ws", id="test-id")),
    ])
    def test_properties_delegate_to_metadata(self, mock_auth_context, mock_all_services,
                                           property_name, metadata_attribute, test_value):
        """Test that all properties correctly delegate to metadata."""
        # Arrange
        item = Item1(mock_auth_context)
        setattr(item._metadata, metadata_attribute, test_value)
        
        # Act
        property_value = getattr(item, property_name)
        
        # Assert
        assert property_value == test_value
        assert property_value == getattr(item._metadata, metadata_attribute)
    
    @pytest.mark.parametrize("lakehouse_id,workspace_id,expected", [
        (str(TestFixtures.ITEM_ID), str(TestFixtures.WORKSPACE_ID), True),
        ("00000000-0000-0000-0000-000000000000", str(TestFixtures.WORKSPACE_ID), False),
        ("", str(TestFixtures.WORKSPACE_ID), False),
        (str(TestFixtures.ITEM_ID), "", False),
        (None, str(TestFixtures.WORKSPACE_ID), False),
    ])
    def test_is_valid_lakehouse_scenarios(self, mock_auth_context, mock_all_services,
                                        lakehouse_id, workspace_id, expected):
        """Test is_valid_lakehouse validation scenarios."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata.lakehouse = ItemReference(id=lakehouse_id, workspace_id=workspace_id)
        
        # Act & Assert
        assert item.is_valid_lakehouse() == expected