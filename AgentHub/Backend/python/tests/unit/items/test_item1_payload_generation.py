"""Item1 PayloadGeneration Tests

Tests for Item1 payloadgeneration functionality.
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
class TestItem1PayloadGeneration:
    """Item payload generation tests - comprehensive payload handling coverage."""
    
    @pytest.mark.asyncio
    async def test_get_item_payload_with_valid_lakehouse(self, mock_auth_context, mock_all_services):
        """Test get_item_payload with successful lakehouse retrieval."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata = Item1Metadata(
            operand1=10,
            operand2=20,
            operator=Item1Operator.ADD,
            lakehouse=ItemReference(
                workspace_id=str(TestFixtures.WORKSPACE_ID),
                id=str(TestFixtures.ITEM_ID)
            ),
            use_onelake=False
        )
        
        # Mock services
        mock_auth = mock_all_services['AuthenticationService']
        mock_lakehouse = mock_all_services['LakehouseClientService']
        
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        
        # Create proper FabricItem mock
        from models.fabric_item import FabricItem
        mock_lakehouse_item = FabricItem(
            id=str(TestFixtures.ITEM_ID),
            workspace_id=str(TestFixtures.WORKSPACE_ID),
            type="Lakehouse",
            display_name="Test Lakehouse"
        )
        mock_lakehouse.get_fabric_lakehouse.return_value = mock_lakehouse_item
        
        # Act
        payload = await item.get_item_payload()
        
        # Assert
        assert Fields.PAYLOAD_METADATA in payload
        metadata = payload[Fields.PAYLOAD_METADATA]
        assert metadata.operand1 == 10
        assert metadata.operand2 == 20
        assert str(metadata.operator) == "Add"
        
        # Verify authentication and lakehouse calls
        mock_auth.get_access_token_on_behalf_of.assert_called_once()
        mock_lakehouse.get_fabric_lakehouse.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_item_payload_without_lakehouse(self, mock_auth_context, mock_all_services):
        """Test get_item_payload when no valid lakehouse reference."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata = Item1Metadata(
            operand1=10,
            operand2=20,
            operator=Item1Operator.ADD,
            use_onelake=True  # No lakehouse needed
        )
        
        # Act
        payload = await item.get_item_payload()
        
        # Assert
        assert Fields.PAYLOAD_METADATA in payload
        metadata = payload[Fields.PAYLOAD_METADATA]
        assert metadata.operand1 == 10
        assert metadata.operand2 == 20
        
        # Verify no lakehouse calls were made
        mock_lakehouse = mock_all_services['LakehouseClientService']
        mock_lakehouse.get_fabric_lakehouse.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_get_item_payload_lakehouse_access_failure(self, mock_auth_context, mock_all_services):
        """Test get_item_payload when lakehouse access fails."""
        # Arrange
        item = Item1(mock_auth_context)
        item._metadata = Item1Metadata(
            operand1=10,
            operand2=20,
            operator=Item1Operator.ADD,
            lakehouse=ItemReference(
                workspace_id=str(TestFixtures.WORKSPACE_ID),
                id=str(TestFixtures.ITEM_ID)
            ),
            use_onelake=False
        )
        
        # Mock services
        mock_auth = mock_all_services['AuthenticationService']
        mock_lakehouse = mock_all_services['LakehouseClientService']
        
        mock_auth.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_lakehouse.get_fabric_lakehouse.side_effect = Exception("Lakehouse access failed")
        
        # Act
        payload = await item.get_item_payload()
        
        # Assert - Should still return payload with None lakehouse
        assert Fields.PAYLOAD_METADATA in payload
        metadata = payload[Fields.PAYLOAD_METADATA]
        assert metadata.operand1 == 10
        assert metadata.operand2 == 20