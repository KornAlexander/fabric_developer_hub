"""Item1 Mathematical Calculations Tests

Tests for Item1 mathematical operations including all operators, edge cases, and error handling.
"""

import pytest
from unittest.mock import Mock, AsyncMock

from items.item1 import Item1
from models.authentication_models import AuthorizationContext
from models.item1_metadata import Item1Metadata, Item1Operator
from tests.test_helpers import TestHelpers
from tests.test_fixtures import TestFixtures


@pytest.mark.unit
@pytest.mark.models  
class TestItem1Calculations:
    """Mathematical operations tests - comprehensive calculation coverage."""
    
    @pytest.mark.parametrize("op1,op2,operator,expected_result", [
        (10, 5, Item1Operator.ADD, "op1 = 10, op2 = 5, operator = Add, result = 15"),
        (10, 5, Item1Operator.SUBTRACT, "op1 = 10, op2 = 5, operator = Subtract, result = 5"),
        (10, 5, Item1Operator.MULTIPLY, "op1 = 10, op2 = 5, operator = Multiply, result = 50"),
        (10, 5, Item1Operator.DIVIDE, "op1 = 10, op2 = 5, operator = Divide, result = 2"),
        (100, 3, Item1Operator.DIVIDE, "op1 = 100, op2 = 3, operator = Divide, result = 33"),
        (-10, 5, Item1Operator.ADD, "op1 = -10, op2 = 5, operator = Add, result = -5"),
        (-10, -5, Item1Operator.MULTIPLY, "op1 = -10, op2 = -5, operator = Multiply, result = 50"),
    ])
    def test_calculate_result_operations(self, mock_auth_context, mock_all_services,
                                       op1, op2, operator, expected_result):
        """Test all arithmetic operations with comprehensive coverage."""
        # Arrange
        item = Item1(mock_auth_context)
        
        # Act
        result = item._calculate_result(op1, op2, operator)
        
        # Assert
        assert result == expected_result
    
    @pytest.mark.parametrize("op1,op2", [
        (5, 10), (1, 100), (0, 5), (-5, 5), (10, 10)
    ])
    def test_calculate_result_random_valid_ranges(self, mock_auth_context, mock_all_services, op1, op2):
        """Test RANDOM operator with valid ranges."""
        # Arrange
        item = Item1(mock_auth_context)
        
        # Act
        result = item._calculate_result(op1, op2, Item1Operator.RANDOM)
        
        # Assert
        assert f"op1 = {op1}, op2 = {op2}, operator = Random" in result
        result_value = int(result.split("result = ")[1])
        assert op1 <= result_value <= op2
    
    @pytest.mark.parametrize("op1,op2,operator,error_message", [
        (10, 0, Item1Operator.DIVIDE, "Cannot divide by zero"),
        (10, 5, Item1Operator.UNDEFINED, "Undefined operator"),
        (15, 5, Item1Operator.RANDOM, "operand1 must not be greater than operand2"),
        (10, 5, "InvalidOperator", "Unknown operator"),
        (10, 5, None, "Unknown operator"),
    ])
    def test_calculate_result_error_scenarios(self, mock_auth_context, mock_all_services,
                                            op1, op2, operator, error_message):
        """Test calculation error handling scenarios."""
        # Arrange
        item = Item1(mock_auth_context)
        
        # Act & Assert
        with pytest.raises(ValueError, match=error_message):
            item._calculate_result(op1, op2, operator)
    
    @pytest.mark.parametrize("string_operator,expected_enum", [
        ("Add", Item1Operator.ADD),
        ("SUBTRACT", Item1Operator.SUBTRACT),
        ("multiply", Item1Operator.MULTIPLY),
        ("Divide", Item1Operator.DIVIDE),
        ("Random", Item1Operator.RANDOM),
    ])
    def test_string_operator_conversion(self, mock_auth_context, mock_all_services,
                                      string_operator, expected_enum):
        """Test string to enum operator conversion."""
        # Arrange
        item = Item1(mock_auth_context)
        op1, op2 = 5, 10  # Valid for all operators including RANDOM
        
        # Act
        result = item._calculate_result(op1, op2, string_operator)
        
        # Assert
        assert f"operator = {expected_enum.name.title()}" in result