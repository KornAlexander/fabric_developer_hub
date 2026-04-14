"""Item1 ResultRetrieval Tests

Tests for Item1 resultretrieval functionality.
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
class TestItem1ResultRetrieval:
    """Result retrieval tests - comprehensive result access coverage."""
    
    @pytest.mark.asyncio
    async def test_get_last_result_successful_retrieval(self, mock_auth_context, mock_all_services):
        """Test successful result retrieval from OneLake."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata.last_calculation_result_location = "/test/path/result.txt"
        
        # Mock services
        mock_auth = mock_all_services['AuthenticationService']
        mock_onelake = mock_all_services['OneLakeClientService']
        
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_onelake.get_onelake_file.return_value = "op1 = 10, op2 = 5, operator = Add, result = 15"
        
        # Act
        result = await item.get_last_result()
        
        # Assert
        assert result == "op1 = 10, op2 = 5, operator = Add, result = 15"
        mock_auth.get_access_token_on_behalf_of.assert_called_once()
        mock_onelake.get_onelake_file.assert_called_once_with("mock_token", "/test/path/result.txt")
    
    @pytest.mark.asyncio
    async def test_get_last_result_authentication_ui_required(self, mock_auth_context, mock_all_services):
        """Test get_last_result re-raises AuthenticationUIRequiredException."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata.last_calculation_result_location = "/test/path/result.txt"
        
        # Mock services
        mock_auth = mock_all_services['AuthenticationService']
        mock_onelake = mock_all_services['OneLakeClientService']
        
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_onelake.get_onelake_file.side_effect = AuthenticationUIRequiredException("Consent required")
        
        # Act & Assert
        with pytest.raises(AuthenticationUIRequiredException):
            await item.get_last_result()
    
    @pytest.mark.asyncio
    async def test_get_last_result_file_not_found(self, mock_auth_context, mock_all_services):
        """Test get_last_result returns empty string when file not found."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata.last_calculation_result_location = "/test/path/nonexistent.txt"
        
        # Mock services
        mock_auth = mock_all_services['AuthenticationService']
        mock_onelake = mock_all_services['OneLakeClientService']
        
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_onelake.get_onelake_file.side_effect = FileNotFoundError("File not found")
        
        # Act
        result = await item.get_last_result()
        
        # Assert
        assert result == ""
    
    @pytest.mark.parametrize("result_location", [
        "",
        None,
        "   "
    ])
    @pytest.mark.asyncio
    async def test_get_last_result_empty_location(self, mock_auth_context, mock_all_services, result_location):
        """Test get_last_result returns empty string for invalid locations."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata.last_calculation_result_location = result_location
        
        # Act
        result = await item.get_last_result()
        
        # Assert
        assert result == ""
        
        # Verify no service calls were made
        mock_auth = mock_all_services['AuthenticationService']
        mock_onelake = mock_all_services['OneLakeClientService']
        mock_auth.get_access_token_on_behalf_of.assert_not_called()
        mock_onelake.get_onelake_file.assert_not_called()
