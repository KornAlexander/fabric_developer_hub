from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, TypeVar, Generic, Tuple
from uuid import UUID
import logging
import datetime
from exceptions.exceptions import ItemMetadataNotFoundException, InvariantViolationException, UnexpectedItemTypeException, InvalidItemPayloadException
from models.authentication_models import AuthorizationContext
from fabric_api.models.job_invoke_type import JobInvokeType
from fabric_api.models.create_item_request import CreateItemRequest
from fabric_api.models.update_item_request import UpdateItemRequest
from fabric_api.models.item_job_instance_state import ItemJobInstanceState
from fabric_api.models.job_instance_status import JobInstanceStatus
from models.common_item_metadata import CommonItemMetadata
from typing import Type

# Define type variables for metadata
TItemMetadata = TypeVar('TItemMetadata')
TItemClientMetadata = TypeVar('TItemClientMetadata')

class ItemBase(ABC, Generic[TItemMetadata, TItemClientMetadata]):
    """
    Base class for all items. This is a Python equivalent of ItemBase<TItem, TItemMetadata, TItemClientMetadata>.
    """
    
    def __init__(self, auth_context: AuthorizationContext):
        """Initialize a base item."""
        from services.item_metadata_store import get_item_metadata_store
        from services.onelake_client_service import get_onelake_client_service
        from services.authentication import get_authentication_service
        
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        
        self.auth_context = auth_context
        
        self.item_metadata_store = get_item_metadata_store()
        self.authentication_service = get_authentication_service()
        self.onelake_client_service = get_onelake_client_service()
        
        self.tenant_object_id = None
        self.workspace_object_id = None
        self.item_object_id = None
        self.display_name = None
        self.description = None

    def _ensure_not_null(self, obj: Any, name: str) -> Any:
        if obj is None:
            raise InvariantViolationException(f"Object reference must not be null: {name}")
        return obj

    def _ensure_condition(self, condition: bool, description: str) -> None:
        if not condition:
            raise InvariantViolationException(f"Condition violation detected: {description}")
        
    @property
    @abstractmethod
    def item_type(self) -> str:
        """Get the item type."""
        pass

    @abstractmethod
    def get_metadata_class(self) -> Type[TItemMetadata]:
        """Return the class type of the type-specific metadata."""
        pass
        
    async def load(self, item_id: UUID) -> None:
        """Load an existing item or create a default one if not found."""
        self.logger.info(f"Loading item {item_id}")
        self.item_object_id = str(item_id)
        tenant_object_id = self.auth_context.tenant_object_id

        # Check if the item exists in storage
        if not await self.item_metadata_store.exists(tenant_object_id, str(item_id)):
            self.logger.error(f"Item {item_id} not found")
            raise ItemMetadataNotFoundException(f"Item not found: {item_id}")
        
        metadata_class = self.get_metadata_class()
            
        item_metadata = await self.item_metadata_store.load(tenant_object_id, 
                                                            str(item_id),
                                                            metadata_class)
        
        self._ensure_not_null(item_metadata, "itemMetadata")
        self._ensure_not_null(item_metadata.common_metadata, "itemMetadata.CommonMetadata")
        self._ensure_not_null(item_metadata.type_specific_metadata, "itemMetadata.TypeSpecificMetadata")

        common_metadata = item_metadata.common_metadata

        if common_metadata.type != self.item_type:
            self.logger.error(f"Unexpected item type '{common_metadata.type}'. Expected '{self.item_type}'")
            raise UnexpectedItemTypeException(f"Unexpected item type '{common_metadata.type}'. Expected '{self.item_type}'")
        
        self._ensure_condition(
            str(common_metadata.tenant_object_id).lower() == str(tenant_object_id).lower(),
            "TenantObjectId must match"
        )
        self._ensure_condition(
            str(common_metadata.item_object_id) == str(item_id),
            "ItemObjectId must match"
        )

        self.tenant_object_id = str(common_metadata.tenant_object_id)
        self.workspace_object_id = str(common_metadata.workspace_object_id)
        self.item_object_id = str(common_metadata.item_object_id)
        self.display_name = common_metadata.display_name
        self.description = common_metadata.description
        self.set_type_specific_metadata(item_metadata.type_specific_metadata)
        self.logger.info(f"Successfully loaded item {item_id}")


    @abstractmethod
    async def get_item_payload(self) -> Dict[str, Any]:
        """Get the item payload."""
        pass
        
    async def create(self, workspace_id: UUID, item_id: UUID, create_request: CreateItemRequest) -> None:
        """Create a new item."""
        self.tenant_object_id = str(self.auth_context.tenant_object_id)
        self.workspace_object_id = str(workspace_id)
        self.item_object_id = str(item_id)
        self.display_name = create_request.display_name
        self.description = create_request.description
        
        self.logger.info(f"Creating item {self.item_type} with ID {item_id} in workspace {workspace_id}")
        self.logger.debug(f"Creation payload: {create_request.creation_payload}")
        
        self.set_definition(create_request.creation_payload)
        self.logger.debug(f"Creating item with tenant ID: {self.tenant_object_id}")
        await self.save_changes()
        self.logger.info(f"Successfully created item {item_id}")
        
    async def update(self, update_request: UpdateItemRequest) -> None:
        """Update an existing item."""
        if not update_request:
            self.logger.error(f"Invalid item payload for type {self.item_type}, item ID {self.item_object_id}")
            raise InvalidItemPayloadException(self.item_type, self.item_object_id)

        self.display_name = update_request.display_name
        self.description = update_request.description
        
        self.update_definition(update_request.update_payload)       
        await self.save_changes()
        self.logger.info(f"Successfully updated item {self.item_object_id}")
        
    async def delete(self) -> None:
        """Delete an existing item."""        
        await self.item_metadata_store.delete(self.tenant_object_id, self.item_object_id)
        self.logger.info(f"Successfully deleted item {self.item_object_id}")

    @abstractmethod
    def set_definition(self, payload: Dict[str, Any]) -> None:
        """Set the item definition from a creation payload."""
        pass
        
    @abstractmethod
    def update_definition(self, payload: Dict[str, Any]) -> None:
        """Update the item definition from an update payload."""
        pass
        
    @abstractmethod
    def get_type_specific_metadata(self) -> TItemMetadata:
        """Get the type-specific metadata for this item."""
        pass
        
    @abstractmethod
    def set_type_specific_metadata(self, metadata: TItemMetadata) -> None:
        """Set the type-specific metadata for this item."""
        pass
        
    @abstractmethod
    async def execute_job(self, 
                    job_type: str, 
                    job_instance_id: UUID, 
                    invoke_type: JobInvokeType, 
                    creation_payload: Dict[str, Any]) -> None:
        """Execute a job for this item."""
        pass
        
    @abstractmethod
    async def get_job_state(self, job_type: str, job_instance_id: UUID) -> ItemJobInstanceState:
        """Get the state of a job instance."""
        pass
        
    async def cancel_job(self, job_type: str, job_instance_id: UUID) -> None:
        """Cancel a job instance."""
        # Import JobMetadata here to avoid circular imports
        from models.job_metadata import JobMetadata
        
        # Check if job metadata exists
        job_metadata = None
        
        if not await self.item_metadata_store.exists_job(self.tenant_object_id, self.item_object_id, str(job_instance_id)):
            # Recreate missing job metadata
            self.logger.warning(f"Recreating missing job {job_instance_id} metadata in tenant {self.tenant_object_id} item {self.item_object_id}")
            # Create new JobMetadata instance
            job_metadata = JobMetadata(
                job_type=job_type,
                job_instance_id=job_instance_id
            )
        else:
            # Load existing job metadata
            job_metadata = await self.item_metadata_store.load_job(self.tenant_object_id, self.item_object_id, str(job_instance_id))
            
        # If already canceled, nothing to do
        if job_metadata.is_canceled:
            return
            
        # Mark as canceled and set canceled time
        job_metadata.canceled_time = datetime.datetime.now(datetime.timezone.utc)
        
        # Update job metadata 
        await self.item_metadata_store.upsert_job(
            self.tenant_object_id, 
            self.item_object_id, 
            str(job_instance_id), 
            job_metadata
        )
        self.logger.info(f"Canceled job {job_instance_id} for item {self.item_object_id}")

    async def save_changes(self) -> None:
        """Save changes to this item."""
        self.logger.info(f"Saving item with tenant ID: {self.tenant_object_id}")
        await self.store()
        await self.allocate_and_free_resources()
        await self.update_fabric()
        
    async def store(self) -> None:
        """Store the item metadata."""
        self.logger.info(f"Storing item {self.item_object_id}")
        common_metadata = CommonItemMetadata(
            type=self.item_type,
            tenant_object_id=self.tenant_object_id,
            workspace_object_id=self.workspace_object_id,
            item_object_id=self.item_object_id,
            display_name=self.display_name,
            description=self.description
        )
        
        type_specific_metadata = self.get_type_specific_metadata()
        
        await self.item_metadata_store.upsert(
            self.tenant_object_id,
            self.item_object_id,
            common_metadata,
            type_specific_metadata
        )
        
    async def allocate_and_free_resources(self) -> None:
        """Allocate and free resources as needed."""
        pass
        
    async def update_fabric(self) -> None:
        """Notify Fabric of changes to this item."""
        pass
        
    def get_current_utc_time(self) -> str:
        """Get the current UTC time as an ISO 8601 string."""
        return datetime.datetime.now(datetime.timezone.utc).isoformat()