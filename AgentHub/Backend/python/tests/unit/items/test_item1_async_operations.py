"""Item1 Async Operations Tests

Tests for Item1 async operations including the double operation and boundary validation.
"""

import pytest
from unittest.mock import Mock, AsyncMock

from items.item1 import Item1
from models.authentication_models import AuthorizationContext
from models.item1_metadata import Item1Metadata, Item1Operator
from exceptions.exceptions import DoubledOperandsOverflowException
from tests.test_helpers import TestHelpers
from tests.test_fixtures import TestFixtures


@pytest.mark.unit
@pytest.mark.models
class TestItem1AsyncOperations:
    """Async operations tests - double operation and boundary validation."""
    
    @pytest.mark.asyncio
    async def test_double_operation_comprehensive(self, mock_auth_context, mock_all_services):
        """Test double operation with comprehensive validation."""
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        item._metadata.operand1 = 10
        item._metadata.operand2 = 20
        
        # Mock save_changes call
        mock_store = mock_all_services['ItemMetadataStore']
        mock_store.upsert = AsyncMock()
        
        # Act
        result = await item.double()
        
        # Assert
        assert result == (20, 40)
        assert item._metadata.operand1 == 20
        assert item._metadata.operand2 == 40
        mock_store.upsert.assert_called_once()
    
    @pytest.mark.parametrize("operand1,operand2,should_raise,expected_operands", [
        (2147483647, 2147483647, False, []),            # Max safe values
        (2147483648, 1, True, ["Operand1"]),            # operand1 overflow
        (1, 2147483648, True, ["Operand2"]),            # operand2 overflow
        (2147483648, 2147483648, True, ["Operand1"]),   # Both overflow (first invalid operand reported)
        (-2147483649, 1, True, ["Operand1"]),           # operand1 underflow
        (1, -2147483649, True, ["Operand2"]),           # operand2 underflow
        (0, 0, False, []),                              # Zero operands
        (-1000000, -1000000, False, []),                # Large negative safe
    ])
    @pytest.mark.asyncio
    async def test_double_operation_boundary_validation(self, mock_auth_context, mock_all_services,
                                                      operand1, operand2, should_raise, expected_operands):
        """Test double operation boundary validation.
        
        Tests that DoubledOperandsOverflowException is raised with proper operand names
        when operands would cause overflow after doubling.
        """
        # Arrange
        item = Item1(mock_auth_context)
        item.tenant_object_id = TestFixtures.TENANT_ID
        item.workspace_object_id = TestFixtures.WORKSPACE_ID
        item.item_object_id = TestFixtures.ITEM_ID
        item._metadata.operand1 = operand1
        item._metadata.operand2 = operand2
        
        mock_store = mock_all_services['ItemMetadataStore']
        mock_store.upsert = AsyncMock()
        
        if should_raise:
            # Act & Assert
            with pytest.raises(DoubledOperandsOverflowException) as exc_info:
                await item.double()
            
            # Verify that the exception message contains the expected operand name
            exception_message = str(exc_info.value)
            assert "may lead to overflow" in exception_message
            
            # Verify that the specific invalid operand is mentioned in the message
            if expected_operands:
                expected_operand = expected_operands[0]  # Take first expected operand
                assert expected_operand in exception_message
            
            mock_store.upsert.assert_not_called()
        else:
            # Act
            result = await item.double()
            
            # Assert
            assert result == (operand1 * 2, operand2 * 2)
            mock_store.upsert.assert_called_once()