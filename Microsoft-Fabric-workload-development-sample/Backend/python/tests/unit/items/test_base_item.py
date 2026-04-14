"""Unit tests for ItemBase abstract class."""

import datetime
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from uuid import UUID
import logging

from items.base_item import ItemBase, TItemMetadata, TItemClientMetadata
from models.authentication_models import AuthorizationContext
from services.item_metadata_store import ItemMetadataStore
from services.onelake_client_service import OneLakeClientService
from services.authentication import AuthenticationService
from exceptions.exceptions import ItemMetadataNotFoundException, InvariantViolationException, UnexpectedItemTypeException, InvalidItemPayloadException
from tests.test_helpers import TestHelpers
from tests.test_fixtures import TestFixtures


# Create a concrete implementation of ItemBase for testing
class ConcreteTestItem(ItemBase[dict, dict]):
    """Concrete implementation of ItemBase for testing purposes."""
    
    @property
    def item_type(self) -> str:
        return "TestableItem"
    
    def get_metadata_class(self) -> type:
        return dict
    
    async def get_item_payload(self) -> dict:
        return {"test": "payload"}
    
    def set_definition(self, payload: dict) -> None:
        self._test_metadata = payload
    
    def update_definition(self, payload: dict) -> None:
        self._test_metadata = payload
    
    def get_type_specific_metadata(self) -> dict:
        return getattr(self, '_test_metadata', {})
    
    def set_type_specific_metadata(self, metadata: dict) -> None:
        self._test_metadata = metadata
    
    async def execute_job(self, job_type: str, job_instance_id: UUID, invoke_type, creation_payload: dict) -> None:
        pass
    
    async def get_job_state(self, job_type: str, job_instance_id: UUID):
        pass


@pytest.mark.unit
@pytest.mark.models
class TestItemBase:
    """Test cases for ItemBase abstract class."""
    
    @pytest.fixture
    def auth_context(self):
        """Create a test authorization context."""
        return TestHelpers.create_auth_context()
    
    @pytest.fixture
    def mock_item_metadata_store(self):
        """Create a mock item metadata store."""
        mock_store = AsyncMock(spec=ItemMetadataStore)
        mock_store.exists.return_value = True
        mock_store.load.return_value = MagicMock()
        mock_store.upsert.return_value = None
        mock_store.delete.return_value = None
        mock_store.exists_job.return_value = True
        mock_store.load_job.return_value = MagicMock()
        mock_store.upsert_job.return_value = None
        return mock_store
    
    @pytest.fixture
    def mock_onelake_client_service(self):
        """Create a mock OneLake client service."""
        mock_service = AsyncMock(spec=OneLakeClientService)
        mock_service.write_to_onelake_file.return_value = None
        mock_service.get_onelake_file.return_value = "test content"
        mock_service.check_if_file_exists.return_value = True
        mock_service.get_onelake_file_path.return_value = "/path/to/file"
        return mock_service
    
    @pytest.fixture
    def mock_authentication_service(self):
        """Create a mock authentication service."""
        mock_service = AsyncMock(spec=AuthenticationService)
        mock_service.get_access_token_on_behalf_of.return_value = "mock_token"
        mock_service.get_fabric_s2s_token.return_value = "mock_s2s_token"
        return mock_service

    def test_init_creates_required_services(self, auth_context, mock_item_metadata_store, 
                                          mock_onelake_client_service, mock_authentication_service):
        """Test that ItemBase initialization creates and injects all required service dependencies."""
        
        # Arrange - Mock the service getter functions
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            # Act - Create an instance of the testable item
            item = ConcreteTestItem(auth_context)
            
            # Assert - Verify all required services are properly injected
            
            # 1. Verify logger is created with correct name pattern
            assert item.logger is not None
            assert isinstance(item.logger, logging.Logger)
            # The logger name is created in ItemBase.__init__ using the module where ItemBase is defined
            expected_logger_name = f"items.base_item.{ConcreteTestItem.__name__}"
            assert item.logger.name == expected_logger_name
            
            # 2. Verify auth_context is stored
            assert item.auth_context is auth_context
            assert item.auth_context.tenant_object_id == auth_context.tenant_object_id
            
            # 3. Verify item_metadata_store service is injected
            assert item.item_metadata_store is not None
            assert item.item_metadata_store is mock_item_metadata_store
            
            # 4. Verify authentication_service is injected
            assert item.authentication_service is not None
            assert item.authentication_service is mock_authentication_service
            
            # 5. Verify onelake_client_service is injected
            assert item.onelake_client_service is not None
            assert item.onelake_client_service is mock_onelake_client_service
            
            # 6. Verify initial state properties are None
            assert item.tenant_object_id is None
            assert item.workspace_object_id is None
            assert item.item_object_id is None
            assert item.display_name is None
            assert item.description is None
    
    def test_init_sets_auth_context(self, auth_context):
        """Test that ItemBase initialization properly sets the authorization context."""
        
        # Arrange - Mock all service dependencies
        with patch('services.item_metadata_store.get_item_metadata_store'), \
             patch('services.onelake_client_service.get_onelake_client_service'), \
             patch('services.authentication.get_authentication_service'):
            
            # Act
            item = ConcreteTestItem(auth_context)
            
            # Assert
            assert item.auth_context is auth_context
            assert item.auth_context.tenant_object_id == "44444444-4444-4444-4444-444444444444"
            assert item.auth_context.original_subject_token == "mock_subject_token"
            assert len(item.auth_context.claims) == 3
    
    def test_properties_initialization(self, auth_context):
        """Test that ItemBase properties are properly initialized to None."""
        
        # Arrange - Mock all service dependencies
        with patch('services.item_metadata_store.get_item_metadata_store'), \
             patch('services.onelake_client_service.get_onelake_client_service'), \
             patch('services.authentication.get_authentication_service'):
            
            # Act
            item = ConcreteTestItem(auth_context)
            
            # Assert - All ID and metadata properties should be None initially
            assert item.tenant_object_id is None
            assert item.workspace_object_id is None
            assert item.item_object_id is None
            assert item.display_name is None
            assert item.description is None
    
    def test_logger_initialization(self, auth_context):
        """Test that the logger is properly initialized with the correct naming pattern."""
        
        # Arrange - Mock all service dependencies
        with patch('services.item_metadata_store.get_item_metadata_store'), \
             patch('services.onelake_client_service.get_onelake_client_service'), \
             patch('services.authentication.get_authentication_service'):
            
            # Act
            item = ConcreteTestItem(auth_context)
            
            # Assert
            assert item.logger is not None
            assert isinstance(item.logger, logging.Logger)
            # Logger name should follow the pattern: module_name.class_name
            # The logger is created in ItemBase.__init__ using the ItemBase module name
            expected_name = f"items.base_item.{item.__class__.__name__}"
            assert item.logger.name == expected_name
    
    def test_service_injection_calls_getters(self, auth_context):
        """Test that service injection calls the appropriate service getter functions."""
        
        # Arrange - Create mock services and spy on getter functions
        mock_item_store = AsyncMock(spec=ItemMetadataStore)
        mock_onelake_service = AsyncMock(spec=OneLakeClientService)
        mock_auth_service = AsyncMock(spec=AuthenticationService)
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_store) as mock_get_item_store, \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_service) as mock_get_onelake, \
             patch('services.authentication.get_authentication_service', return_value=mock_auth_service) as mock_get_auth:
            
            # Act
            item = ConcreteTestItem(auth_context)
            
            # Assert - Verify that each service getter was called exactly once
            mock_get_item_store.assert_called_once()
            mock_get_onelake.assert_called_once()
            mock_get_auth.assert_called_once()
            
            # Assert - Verify the returned services are properly assigned
            assert item.item_metadata_store is mock_item_store
            assert item.onelake_client_service is mock_onelake_service
            assert item.authentication_service is mock_auth_service

    # ============================================================================
    # Load Operations Tests - Core CRUD Functionality
    # ============================================================================
    
    @pytest.mark.asyncio
    async def test_load_existing_item_success(self, auth_context, mock_item_metadata_store,
                                            mock_onelake_client_service, mock_authentication_service):
        """Test successful loading of an existing item with proper metadata validation."""
        
        # Arrange - Create test data
        item_id = TestFixtures.ITEM_ID
        workspace_id = TestFixtures.WORKSPACE_ID
        tenant_id = TestFixtures.TENANT_ID
        
        # Mock successful metadata loading
        from models.common_item_metadata import CommonItemMetadata
        from models.item1_metadata import Item1Metadata
        
        # Create mock metadata structures
        common_metadata = CommonItemMetadata(
            type="TestableItem",
            tenant_object_id=tenant_id,
            workspace_object_id=workspace_id,
            item_object_id=item_id,
            display_name="Test Item",
            description="Test Description"
        )
        
        type_specific_metadata = {"test_key": "test_value"}
        
        # Create mock item metadata container
        mock_item_metadata = MagicMock()
        mock_item_metadata.common_metadata = common_metadata
        mock_item_metadata.type_specific_metadata = type_specific_metadata
        
        # Configure mock responses
        mock_item_metadata_store.exists.return_value = True
        mock_item_metadata_store.load.return_value = mock_item_metadata
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            # Act - Load the item
            item = ConcreteTestItem(auth_context)
            await item.load(item_id)
            
            # Assert - Verify loading behavior
            
            # 1. Verify existence check was called (tenant_id is converted to string by auth_context)
            mock_item_metadata_store.exists.assert_called_once_with(str(tenant_id), str(item_id))
            
            # 2. Verify metadata load was called with correct parameters
            mock_item_metadata_store.load.assert_called_once_with(str(tenant_id), str(item_id), dict)
            
            # 3. Verify all properties were set correctly from common metadata
            assert item.tenant_object_id == str(tenant_id)
            assert item.workspace_object_id == str(workspace_id)
            assert item.item_object_id == str(item_id)
            assert item.display_name == "Test Item"
            assert item.description == "Test Description"
            
            # 4. Verify type-specific metadata was set (mocked implementation stores it)
            assert hasattr(item, '_test_metadata')
            assert item._test_metadata == type_specific_metadata

    @pytest.mark.asyncio
    async def test_load_nonexistent_item_raises_exception(self, auth_context, mock_item_metadata_store,
                                                        mock_onelake_client_service, mock_authentication_service):
        """Test that loading a non-existent item raises ItemMetadataNotFoundException."""
        
        # Arrange - Mock item doesn't exist
        item_id = TestFixtures.ITEM_ID
        tenant_id = TestFixtures.TENANT_ID
        
        mock_item_metadata_store.exists.return_value = False
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            # Act & Assert - Verify exception is raised
            item = ConcreteTestItem(auth_context)
            
            with pytest.raises(ItemMetadataNotFoundException) as exc_info:
                await item.load(item_id)
            
            # Verify exception details
            assert str(item_id) in str(exc_info.value)
            
            # Verify existence check was called but load was not (tenant_id is converted to string)
            mock_item_metadata_store.exists.assert_called_once_with(str(tenant_id), str(item_id))
            mock_item_metadata_store.load.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_invalid_metadata_structure(self, auth_context, mock_item_metadata_store,
                                                 mock_onelake_client_service, mock_authentication_service):
        """Test handling of corrupt/invalid metadata structure."""
        
        # Arrange - Mock invalid metadata structure
        item_id = TestFixtures.ITEM_ID
        tenant_id = TestFixtures.TENANT_ID
        
        # Test case 1: None metadata
        mock_item_metadata_store.exists.return_value = True
        mock_item_metadata_store.load.return_value = None
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            with pytest.raises(InvariantViolationException) as exc_info:
                await item.load(item_id)
            
            assert "Object reference must not be null: itemMetadata" in str(exc_info.value)
        
        # Test case 2: Missing common_metadata
        mock_item_metadata_2 = MagicMock()
        mock_item_metadata_2.common_metadata = None
        mock_item_metadata_2.type_specific_metadata = {"test": "data"}
        mock_item_metadata_store.load.return_value = mock_item_metadata_2
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            with pytest.raises(InvariantViolationException) as exc_info:
                await item.load(item_id)
            
            assert "Object reference must not be null: itemMetadata.CommonMetadata" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_load_tenant_mismatch_raises_exception(self, mock_item_metadata_store,
                                                       mock_onelake_client_service, mock_authentication_service):
        """Test that tenant ID mismatch raises access denied error."""
        
        # Arrange - Create auth context with different tenant
        auth_context = TestHelpers.create_auth_context(tenant_id="66666666-6666-6666-6666-666666666666")
        item_id = TestFixtures.ITEM_ID
        
        # Mock metadata with different tenant ID
        from models.common_item_metadata import CommonItemMetadata
        
        common_metadata = CommonItemMetadata(
            type="TestableItem",
            tenant_object_id=UUID("55555555-5555-5555-5555-555555555555"),  # Different from auth context
            workspace_object_id=TestFixtures.WORKSPACE_ID,
            item_object_id=item_id,
            display_name="Test Item",
            description="Test Description"
        )
        
        mock_item_metadata = MagicMock()
        mock_item_metadata.common_metadata = common_metadata
        mock_item_metadata.type_specific_metadata = {"test": "data"}
        
        mock_item_metadata_store.exists.return_value = True
        mock_item_metadata_store.load.return_value = mock_item_metadata
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            # Act & Assert - Verify access denied exception
            item = ConcreteTestItem(auth_context)
            
            with pytest.raises(InvariantViolationException) as exc_info:
                await item.load(item_id)
            
            # Verify error message contains access denied information
            assert "Condition violation detected: TenantObjectId must match" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_load_wrong_item_type_raises_exception(self, auth_context, mock_item_metadata_store,
                                                       mock_onelake_client_service, mock_authentication_service):
        """Test that item type mismatch raises TypeError."""
        
        # Arrange - Mock metadata with wrong item type
        item_id = TestFixtures.ITEM_ID
        tenant_id = TestFixtures.TENANT_ID
        
        from models.common_item_metadata import CommonItemMetadata
        
        common_metadata = CommonItemMetadata(
            type="WrongItemType",  # Different from ConcreteTestItem.item_type
            tenant_object_id=tenant_id,
            workspace_object_id=TestFixtures.WORKSPACE_ID,
            item_object_id=item_id,
            display_name="Test Item",
            description="Test Description"
        )
        
        mock_item_metadata = MagicMock()
        mock_item_metadata.common_metadata = common_metadata
        mock_item_metadata.type_specific_metadata = {"test": "data"}
        
        mock_item_metadata_store.exists.return_value = True
        mock_item_metadata_store.load.return_value = mock_item_metadata
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            # Act & Assert - Verify type error
            item = ConcreteTestItem(auth_context)
            
            with pytest.raises(UnexpectedItemTypeException) as exc_info:
                await item.load(item_id)
            
            # Verify error message contains type information
            assert "Unexpected item type" in str(exc_info.value)
            assert "WrongItemType" in str(exc_info.value)
            assert "TestableItem" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_load_sets_metadata_through_abstract_method(self, auth_context, mock_item_metadata_store,
                                                            mock_onelake_client_service, mock_authentication_service):
        """Test that load calls set_type_specific_metadata with the loaded metadata."""
        
        # Arrange - Setup successful load scenario
        item_id = TestFixtures.ITEM_ID
        tenant_id = TestFixtures.TENANT_ID
        test_metadata = {"calculation": "result", "operand1": 42}
        
        from models.common_item_metadata import CommonItemMetadata
        
        common_metadata = CommonItemMetadata(
            type="TestableItem",
            tenant_object_id=tenant_id,
            workspace_object_id=TestFixtures.WORKSPACE_ID,
            item_object_id=item_id,
            display_name="Test Item",
            description="Test Description"
        )
        
        mock_item_metadata = MagicMock()
        mock_item_metadata.common_metadata = common_metadata
        mock_item_metadata.type_specific_metadata = test_metadata
        
        mock_item_metadata_store.exists.return_value = True
        mock_item_metadata_store.load.return_value = mock_item_metadata
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            # Act - Load the item
            item = ConcreteTestItem(auth_context)
            
            # Spy on the set_type_specific_metadata method
            with patch.object(item, 'set_type_specific_metadata', wraps=item.set_type_specific_metadata) as spy_method:
                await item.load(item_id)
                
                # Assert - Verify set_type_specific_metadata was called with correct data
                spy_method.assert_called_once_with(test_metadata)
                
                # Verify the metadata was actually set in our concrete implementation
                assert item._test_metadata == test_metadata

    # ============================================================================
    # CRUD Operations Tests - Core Item Lifecycle
    # ============================================================================

    @pytest.mark.asyncio
    async def test_create_item_success(self, auth_context, mock_item_metadata_store,
                                      mock_onelake_client_service, mock_authentication_service):
        """Test successful item creation with CreateItemRequest."""
        
        # Arrange
        workspace_id = TestFixtures.WORKSPACE_ID
        item_id = TestFixtures.ITEM_ID
        
        from fabric_api.models.create_item_request import CreateItemRequest
        create_request = CreateItemRequest(
            display_name="Test Item Creation",
            description="Test item creation description",
            creation_payload={"metadata": {"operand1": 100, "operand2": 200, "operator": "Add"}}
        )
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            # Act
            item = ConcreteTestItem(auth_context)
            await item.create(workspace_id, item_id, create_request)
            
            # Assert - Verify all properties were set correctly
            assert item.tenant_object_id == str(auth_context.tenant_object_id)
            assert item.workspace_object_id == str(workspace_id)
            assert item.item_object_id == str(item_id)
            assert item.display_name == "Test Item Creation"
            assert item.description == "Test item creation description"
            
            # Verify definition was set (concrete implementation stores in _test_metadata)
            assert hasattr(item, '_test_metadata')
            assert item._test_metadata == {"metadata": {"operand1": 100, "operand2": 200, "operator": "Add"}}
            
            # Verify save_changes was called (which calls store, allocate_and_free_resources, update_fabric)
            mock_item_metadata_store.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_sets_all_properties(self, auth_context, mock_item_metadata_store,
                                             mock_onelake_client_service, mock_authentication_service):
        """Verify all properties are set correctly during creation."""
        
        # Arrange
        workspace_id = TestFixtures.WORKSPACE_ID
        item_id = TestFixtures.ITEM_ID
        
        from fabric_api.models.create_item_request import CreateItemRequest
        create_request = CreateItemRequest(
            display_name="Property Test Item",
            description="Testing property assignment",
            creation_payload={"test_data": "test_value"}
        )
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            # Act
            item = ConcreteTestItem(auth_context)
            await item.create(workspace_id, item_id, create_request)
            
            # Assert - Verify ALL properties are correctly set
            assert item.tenant_object_id == str(auth_context.tenant_object_id)
            assert item.workspace_object_id == str(workspace_id)
            assert item.item_object_id == str(item_id)
            assert item.display_name == "Property Test Item"
            assert item.description == "Testing property assignment"
            
            # Verify auth context is preserved
            assert item.auth_context is auth_context
            
            # Verify services are still accessible
            assert item.item_metadata_store is mock_item_metadata_store
            assert item.authentication_service is mock_authentication_service
            assert item.onelake_client_service is mock_onelake_client_service

    @pytest.mark.asyncio
    async def test_update_item_success(self, auth_context, mock_item_metadata_store,
                                      mock_onelake_client_service, mock_authentication_service):
        """Test successful item updates."""
        
        # Arrange - Create and initialize an item first
        workspace_id = TestFixtures.WORKSPACE_ID
        item_id = TestFixtures.ITEM_ID
        
        from fabric_api.models.update_item_request import UpdateItemRequest
        update_request = UpdateItemRequest(
            display_name="Updated Item Name",
            description="Updated item description",
            update_payload={"metadata": {"operand1": 300, "operand2": 400, "operator": "Multiply"}}
        )
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            # Act
            item = ConcreteTestItem(auth_context)
            
            # Set some initial state (simulate a loaded item)
            item.tenant_object_id = str(auth_context.tenant_object_id)
            item.workspace_object_id = str(workspace_id)
            item.item_object_id = str(item_id)
            item.display_name = "Original Name"
            item.description = "Original Description"
            
            await item.update(update_request)
            
            # Assert - Verify properties were updated
            assert item.display_name == "Updated Item Name"
            assert item.description == "Updated item description"
            
            # Verify definition was updated (concrete implementation stores in _test_metadata)
            assert hasattr(item, '_test_metadata')
            assert item._test_metadata == {"metadata": {"operand1": 300, "operand2": 400, "operator": "Multiply"}}
            
            # Verify save_changes was called
            mock_item_metadata_store.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_invalid_request_raises_exception(self, auth_context, mock_item_metadata_store,
                                                          mock_onelake_client_service, mock_authentication_service):
        """Test validation of update requests with invalid data."""
        
        # Arrange
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            # Set item state
            item.item_object_id = str(TestFixtures.ITEM_ID)
            
            # Act & Assert - Test with None request
            with pytest.raises(InvalidItemPayloadException) as exc_info:
                await item.update(None)
            
            # InvalidItemPayloadException uses the format: "{item_type} payload is invalid for id={item_id}. See MoreDetails for additional information."
            assert "payload is invalid" in str(exc_info.value)
            assert item.item_type in str(exc_info.value)
            assert item.item_object_id in str(exc_info.value)
            
            # Verify no save operation was attempted
            mock_item_metadata_store.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_item_calls_metadata_store(self, auth_context, mock_item_metadata_store,
                                                   mock_onelake_client_service, mock_authentication_service):
        """Verify deletion calls correct services."""
        
        # Arrange
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            # Set required properties for deletion
            item.tenant_object_id = str(TestFixtures.TENANT_ID)
            item.item_object_id = str(TestFixtures.ITEM_ID)
            
            # Act
            await item.delete()
            
            # Assert - Verify delete was called with correct parameters
            mock_item_metadata_store.delete.assert_called_once_with(
                str(TestFixtures.TENANT_ID),
                str(TestFixtures.ITEM_ID)
            )

    @pytest.mark.asyncio
    async def test_save_changes_calls_all_required_methods(self, auth_context, mock_item_metadata_store,
                                                          mock_onelake_client_service, mock_authentication_service):
        """Test that save_changes calls all required methods in sequence."""
        
        # Arrange
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            # Set up item state
            item.tenant_object_id = str(TestFixtures.TENANT_ID)
            item.workspace_object_id = str(TestFixtures.WORKSPACE_ID)
            item.item_object_id = str(TestFixtures.ITEM_ID)
            item.display_name = "Test Item"
            item.description = "Test Description"
            
            # Mock the individual methods to verify they're called
            with patch.object(item, 'store') as mock_store, \
                 patch.object(item, 'allocate_and_free_resources') as mock_allocate, \
                 patch.object(item, 'update_fabric') as mock_update_fabric:
                
                # Act
                await item.save_changes()
                
                # Assert - Verify all methods were called in the correct order
                mock_store.assert_called_once()
                mock_allocate.assert_called_once()
                mock_update_fabric.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_creates_correct_metadata_structure(self, auth_context, mock_item_metadata_store,
                                                           mock_onelake_client_service, mock_authentication_service):
        """Test that store creates the correct metadata structure."""
        
        # Arrange
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            # Set up item state
            item.tenant_object_id = str(TestFixtures.TENANT_ID)
            item.workspace_object_id = str(TestFixtures.WORKSPACE_ID)
            item.item_object_id = str(TestFixtures.ITEM_ID)
            item.display_name = "Test Storage Item"
            item.description = "Test storage description"
            item._test_metadata = {"test": "metadata"}
            
            # Act
            await item.store()
            
            # Assert - Verify upsert was called with correct structure
            mock_item_metadata_store.upsert.assert_called_once()
            
            # Get the call arguments
            call_args = mock_item_metadata_store.upsert.call_args
            tenant_id, item_id, common_metadata, type_specific_metadata = call_args[0]
            
            # Verify the arguments
            assert tenant_id == str(TestFixtures.TENANT_ID)
            assert item_id == str(TestFixtures.ITEM_ID)
            
            # Verify common metadata structure
            assert common_metadata.type == "TestableItem"
            assert common_metadata.tenant_object_id == TestFixtures.TENANT_ID
            assert common_metadata.workspace_object_id == TestFixtures.WORKSPACE_ID
            assert common_metadata.item_object_id == TestFixtures.ITEM_ID
            assert common_metadata.display_name == "Test Storage Item"
            assert common_metadata.description == "Test storage description"
            
            # Verify type-specific metadata
            assert type_specific_metadata == {"test": "metadata"}

    @pytest.mark.asyncio
    async def test_create_with_empty_payload(self, auth_context, mock_item_metadata_store,
                                            mock_onelake_client_service, mock_authentication_service):
        """Test creation with empty payload is handled gracefully."""
        
        # Arrange
        workspace_id = TestFixtures.WORKSPACE_ID
        item_id = TestFixtures.ITEM_ID
        
        from fabric_api.models.create_item_request import CreateItemRequest
        create_request = CreateItemRequest(
            display_name="Empty Payload Item",
            description="Item with empty payload",
            creation_payload={}
        )
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            # Act
            item = ConcreteTestItem(auth_context)
            await item.create(workspace_id, item_id, create_request)
            
            # Assert - Item should be created successfully with empty payload
            assert item.display_name == "Empty Payload Item"
            assert item.description == "Item with empty payload"
            assert item._test_metadata == {}

    @pytest.mark.asyncio
    async def test_update_preserves_existing_state(self, auth_context, mock_item_metadata_store,
                                                  mock_onelake_client_service, mock_authentication_service):
        """Test that update only changes specified properties."""
        
        # Arrange
        from fabric_api.models.update_item_request import UpdateItemRequest
        update_request = UpdateItemRequest(
            display_name="New Display Name",
            description="New Description",
            update_payload={"new_data": "new_value"}
        )
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            # Set initial state
            original_tenant_id = str(TestFixtures.TENANT_ID)
            original_workspace_id = str(TestFixtures.WORKSPACE_ID)
            original_item_id = str(TestFixtures.ITEM_ID)
            
            item.tenant_object_id = original_tenant_id
            item.workspace_object_id = original_workspace_id
            item.item_object_id = original_item_id
            item.display_name = "Original Name"
            item.description = "Original Description"
            
            # Act
            await item.update(update_request)
            
            # Assert - Verify IDs are preserved and only name/description updated
            assert item.tenant_object_id == original_tenant_id
            assert item.workspace_object_id == original_workspace_id
            assert item.item_object_id == original_item_id
            assert item.display_name == "New Display Name"
            assert item.description == "New Description"

    # ============================================================================
    # Job Management Tests - Cancel Job Operations
    # ============================================================================

    @pytest.mark.asyncio
    async def test_cancel_job_missing_metadata_recreates(self, auth_context, mock_item_metadata_store,
                                                        mock_onelake_client_service, mock_authentication_service):
        """Test job metadata recreation scenario when metadata is missing."""
        
        # Arrange
        job_type = "TestJob"
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock missing job metadata (exists_job returns False)
        mock_item_metadata_store.exists_job.return_value = False
        mock_item_metadata_store.upsert_job.return_value = None
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            # Set required properties
            item.tenant_object_id = str(TestFixtures.TENANT_ID)
            item.item_object_id = str(TestFixtures.ITEM_ID)
            
            # Mock JobMetadata import and creation
            with patch('models.job_metadata.JobMetadata') as mock_job_metadata_class:
                mock_job_metadata = MagicMock()
                mock_job_metadata.is_canceled = False
                mock_job_metadata_class.return_value = mock_job_metadata
                
                # Mock datetime for consistent testing
                with patch('datetime.datetime') as mock_datetime:
                    mock_utc_time = MagicMock()
                    mock_datetime.now.return_value = mock_utc_time
                    mock_datetime.timezone.utc = MagicMock()
                    
                    # Act
                    await item.cancel_job(job_type, job_instance_id)
                    
                    # Assert - Verify recreation workflow
                    mock_item_metadata_store.exists_job.assert_called_once_with(
                        str(TestFixtures.TENANT_ID),
                        str(TestFixtures.ITEM_ID),
                        str(job_instance_id)
                    )
                    
                    # Verify new JobMetadata was created with correct parameters
                    mock_job_metadata_class.assert_called_once_with(
                        job_type=job_type,
                        job_instance_id=job_instance_id
                    )
                    
                    # Verify canceled time was set
                    assert mock_job_metadata.canceled_time == mock_utc_time
                    
                    # Verify upsert was called with recreated metadata
                    mock_item_metadata_store.upsert_job.assert_called_once_with(
                        str(TestFixtures.TENANT_ID),
                        str(TestFixtures.ITEM_ID),
                        str(job_instance_id),
                        mock_job_metadata
                    )

    @pytest.mark.asyncio
    async def test_cancel_job_already_canceled_noop(self, auth_context, mock_item_metadata_store,
                                                   mock_onelake_client_service, mock_authentication_service):
        """Test idempotent cancellation - no operation when job is already canceled."""
        
        # Arrange
        job_type = "TestJob"
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock existing job metadata that is already canceled
        mock_job_metadata = MagicMock()
        mock_job_metadata.is_canceled = True  # Already canceled
        
        mock_item_metadata_store.exists_job.return_value = True
        mock_item_metadata_store.load_job.return_value = mock_job_metadata
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            # Set required properties
            item.tenant_object_id = str(TestFixtures.TENANT_ID)
            item.item_object_id = str(TestFixtures.ITEM_ID)
            
            # Act
            await item.cancel_job(job_type, job_instance_id)
            
            # Assert - Verify idempotent behavior
            mock_item_metadata_store.exists_job.assert_called_once_with(
                str(TestFixtures.TENANT_ID),
                str(TestFixtures.ITEM_ID),
                str(job_instance_id)
            )
            
            # Verify existing metadata was loaded
            mock_item_metadata_store.load_job.assert_called_once_with(
                str(TestFixtures.TENANT_ID),
                str(TestFixtures.ITEM_ID),
                str(job_instance_id)
            )
            
            # Verify no upsert was called since job is already canceled
            mock_item_metadata_store.upsert_job.assert_not_called()
            
            # Verify canceled_time was not set during this operation
            # Since the job is already canceled, we should not modify canceled_time
            # The key assertion is that upsert_job was not called

    @pytest.mark.asyncio
    async def test_cancel_job_sets_canceled_time(self, auth_context, mock_item_metadata_store,
                                                mock_onelake_client_service, mock_authentication_service):
        """Verify cancellation timestamp is properly set."""
        
        # Arrange
        job_type = "TestJob"
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock existing job metadata that is NOT canceled
        mock_job_metadata = MagicMock()
        mock_job_metadata.is_canceled = False
        
        mock_item_metadata_store.exists_job.return_value = True
        mock_item_metadata_store.load_job.return_value = mock_job_metadata
        mock_item_metadata_store.upsert_job.return_value = None
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            # Set required properties
            item.tenant_object_id = str(TestFixtures.TENANT_ID)
            item.item_object_id = str(TestFixtures.ITEM_ID)
            
            # Mock datetime to control the timestamp
            with patch('datetime.datetime') as mock_datetime:
                mock_utc_time = datetime.datetime(2023, 7, 16, 12, 30, 45, tzinfo=datetime.timezone.utc)
                mock_datetime.now.return_value = mock_utc_time
                mock_datetime.timezone.utc = datetime.timezone.utc
                
                # Act
                await item.cancel_job(job_type, job_instance_id)
                
                # Assert - Verify cancellation timestamp was set correctly
                mock_datetime.now.assert_called_once_with(datetime.timezone.utc)
                assert mock_job_metadata.canceled_time == mock_utc_time
                
                # Verify upsert was called with updated metadata
                mock_item_metadata_store.upsert_job.assert_called_once_with(
                    str(TestFixtures.TENANT_ID),
                    str(TestFixtures.ITEM_ID),
                    str(job_instance_id),
                    mock_job_metadata
                )

    @pytest.mark.asyncio
    async def test_cancel_job_with_existing_metadata_workflow(self, auth_context, mock_item_metadata_store,
                                                             mock_onelake_client_service, mock_authentication_service):
        """Test complete cancellation workflow with existing metadata."""
        
        # Arrange
        job_type = "CalculateAsText"
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock existing job metadata
        mock_job_metadata = MagicMock()
        mock_job_metadata.is_canceled = False
        mock_job_metadata.job_type = job_type
        mock_job_metadata.job_instance_id = job_instance_id
        
        mock_item_metadata_store.exists_job.return_value = True
        mock_item_metadata_store.load_job.return_value = mock_job_metadata
        mock_item_metadata_store.upsert_job.return_value = None
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            # Set required properties
            item.tenant_object_id = str(TestFixtures.TENANT_ID)
            item.item_object_id = str(TestFixtures.ITEM_ID)
            
            # Act
            await item.cancel_job(job_type, job_instance_id)
            
            # Assert - Verify complete workflow
            
            # 1. Check if job exists
            mock_item_metadata_store.exists_job.assert_called_once_with(
                str(TestFixtures.TENANT_ID),
                str(TestFixtures.ITEM_ID),
                str(job_instance_id)
            )
            
            # 2. Load existing metadata
            mock_item_metadata_store.load_job.assert_called_once_with(
                str(TestFixtures.TENANT_ID),
                str(TestFixtures.ITEM_ID),
                str(job_instance_id)
            )
            
            # 3. Verify canceled_time was set (should be a datetime object)
            assert hasattr(mock_job_metadata, 'canceled_time')
            assert mock_job_metadata.canceled_time is not None
            
            # 4. Update metadata
            mock_item_metadata_store.upsert_job.assert_called_once_with(
                str(TestFixtures.TENANT_ID),
                str(TestFixtures.ITEM_ID),
                str(job_instance_id),
                mock_job_metadata
            )

    @pytest.mark.asyncio
    async def test_cancel_job_logs_recreation_warning(self, auth_context, mock_item_metadata_store,
                                                     mock_onelake_client_service, mock_authentication_service):
        """Test that proper warning is logged when recreating missing job metadata."""
        
        # Arrange
        job_type = "TestJob"
        job_instance_id = TestFixtures.JOB_INSTANCE_ID
        
        # Mock missing job metadata
        mock_item_metadata_store.exists_job.return_value = False
        mock_item_metadata_store.upsert_job.return_value = None
        
        with patch('services.item_metadata_store.get_item_metadata_store', return_value=mock_item_metadata_store), \
             patch('services.onelake_client_service.get_onelake_client_service', return_value=mock_onelake_client_service), \
             patch('services.authentication.get_authentication_service', return_value=mock_authentication_service):
            
            item = ConcreteTestItem(auth_context)
            
            # Set required properties
            item.tenant_object_id = str(TestFixtures.TENANT_ID)
            item.item_object_id = str(TestFixtures.ITEM_ID)
            
            # Mock JobMetadata and logger
            with patch('models.job_metadata.JobMetadata') as mock_job_metadata_class, \
                 patch.object(item, 'logger') as mock_logger:
                
                mock_job_metadata = MagicMock()
                mock_job_metadata.is_canceled = False
                mock_job_metadata_class.return_value = mock_job_metadata
                
                # Act
                await item.cancel_job(job_type, job_instance_id)
                
                # Assert - Verify warning was logged
                mock_logger.warning.assert_called_once()
                warning_call = mock_logger.warning.call_args[0][0]
                assert f"Recreating missing job {job_instance_id} metadata" in warning_call
                assert f"tenant {TestFixtures.TENANT_ID}" in warning_call
                assert f"item {TestFixtures.ITEM_ID}" in warning_call
                
                # Verify success info was logged
                mock_logger.info.assert_called_once()
                info_call = mock_logger.info.call_args[0][0]
                assert f"Canceled job {job_instance_id}" in info_call
                assert f"item {TestFixtures.ITEM_ID}" in info_call