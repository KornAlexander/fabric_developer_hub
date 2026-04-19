import datetime
import logging
from abc import ABC, abstractmethod
from typing import Any, TypeVar
from uuid import UUID

from domain.exceptions.exceptions import (
    InvalidItemPayloadException,
    InvariantViolationException,
    ItemMetadataNotFoundException,
    UnexpectedItemTypeException,
)
from domain.models.authentication_models import AuthorizationContext
from domain.models.common_item_metadata import CommonItemMetadata
from domain.models.job_metadata import JobMetadata
from fabric_api.models.create_item_request import CreateItemRequest
from fabric_api.models.item_job_instance_state import ItemJobInstanceState
from fabric_api.models.job_invoke_type import JobInvokeType
from fabric_api.models.update_item_request import UpdateItemRequest
from services.auth.authentication import get_authentication_service
from services.fabric.item_metadata_store import get_item_metadata_store
from services.fabric.onelake_client_service import get_onelake_client_service

# Define type variables for metadata
TItemMetadata = TypeVar('TItemMetadata')
TItemClientMetadata = TypeVar('TItemClientMetadata')

class ItemBase[TItemMetadata, TItemClientMetadata](ABC):
    """
    Base class for all items. This is a Python equivalent of ItemBase<TItem, TItemMetadata, TItemClientMetadata>.
    """

    def __init__(self, auth_context: AuthorizationContext):
        """Initialize a base item."""
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        self.auth_context = auth_context

        self.item_metadata_store = get_item_metadata_store()
        self.authentication_service = get_authentication_service()
        self.onelake_client_service = get_onelake_client_service()

        # These are populated during ``load()`` or ``create()`` — before
        # either call, the item is in an "unloaded" state and accessing
        # them is a programming error. Typed as ``str | None`` so mypy
        # catches the occasional reader that forgets to check.
        self.tenant_object_id: str | None = None
        self.workspace_object_id: str | None = None
        self.item_object_id: str | None = None
        self.display_name: str | None = None
        self.description: str | None = None

    def _ensure_not_null(self, obj: Any, name: str) -> Any:
        if obj is None:
            raise InvariantViolationException(f"Object reference must not be null: {name}")
        return obj

    def _ensure_condition(self, condition: bool, description: str) -> None:
        if not condition:
            raise InvariantViolationException(f"Condition violation detected: {description}")

    def _require_tenant_id(self) -> str:
        """Return ``self.tenant_object_id`` narrowed to ``str``.

        Any call path that touches the metadata store or needs to write
        tenant-scoped data goes through here. If the auth context never
        populated a tenant claim (e.g. an app-only token on an endpoint
        that didn't require the tenant header), that's a programming
        error — raise an ``InvariantViolationException`` so the failure
        mode is a clean 500 rather than a downstream ``TypeError``.
        """
        if self.tenant_object_id is None:
            raise InvariantViolationException(
                "tenant_object_id is not populated — item is not loaded or "
                "auth context lacks a tenant claim"
            )
        return self.tenant_object_id

    def _require_item_id(self) -> str:
        """Return ``self.item_object_id`` narrowed to ``str``.

        Counterpart to :meth:`_require_tenant_id` — see its docstring.
        """
        if self.item_object_id is None:
            raise InvariantViolationException(
                "item_object_id is not populated — item is not loaded"
            )
        return self.item_object_id

    @property
    @abstractmethod
    def item_type(self) -> str:
        """Get the item type."""
        pass

    @abstractmethod
    def get_metadata_class(self) -> type[TItemMetadata]:
        """Return the class type of the type-specific metadata."""
        pass

    async def load(self, item_id: UUID) -> None:
        """Load an existing item or create a default one if not found."""
        self.logger.info("Loading item %s", item_id)
        self.item_object_id = str(item_id)
        # ``load()`` always requires a populated auth context — callers that
        # haven't authenticated can't ask for an item by ID. Narrow here so
        # the rest of the method is ``str`` (and the metadata-store's
        # stricter ``str`` signatures line up).
        if self.auth_context.tenant_object_id is None:
            raise InvariantViolationException(
                "auth_context.tenant_object_id must be populated before load()"
            )
        tenant_object_id: str = self.auth_context.tenant_object_id

        # Check if the item exists in storage
        if not await self.item_metadata_store.exists(tenant_object_id, str(item_id)):
            self.logger.error("Item %s not found", item_id)
            raise ItemMetadataNotFoundException(item_id)

        metadata_class = self.get_metadata_class()

        item_metadata = await self.item_metadata_store.load(tenant_object_id,
                                                            str(item_id),
                                                            metadata_class)

        self._ensure_not_null(item_metadata, "itemMetadata")
        self._ensure_not_null(item_metadata.common_metadata, "itemMetadata.CommonMetadata")
        self._ensure_not_null(item_metadata.type_specific_metadata, "itemMetadata.TypeSpecificMetadata")

        common_metadata = item_metadata.common_metadata

        if common_metadata.type != self.item_type:
            self.logger.error(
                "Unexpected item type '%s'. Expected '%s'",
                common_metadata.type,
                self.item_type,
            )
            raise UnexpectedItemTypeException(
                f"Unexpected item type '{common_metadata.type}'. Expected '{self.item_type}'"
            )

        # Tenant isolation: the metadata-store's tenant key MUST match the
        # tenant claim from the validated bearer token. Never trust
        # tenant/workspace IDs supplied via path/body without this cross-check.
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
        self.logger.info("Successfully loaded item %s", item_id)


    @abstractmethod
    async def get_item_payload(self) -> dict[str, Any]:
        """Get the item payload."""
        pass

    async def create(self, workspace_id: UUID, item_id: UUID, create_request: CreateItemRequest) -> None:
        """Create a new item."""
        self.tenant_object_id = str(self.auth_context.tenant_object_id)
        self.workspace_object_id = str(workspace_id)
        self.item_object_id = str(item_id)
        self.display_name = create_request.display_name
        self.description = create_request.description

        self.logger.info(
            "Creating item %s with ID %s in workspace %s",
            self.item_type,
            item_id,
            workspace_id,
        )
        self.logger.debug("Creation payload: %s", create_request.creation_payload)

        if create_request.creation_payload is None:
            # Create with empty default metadata rather than crashing — the
            # OpenAPI layer may optionally send a payload; items define
            # their own defaults in ``set_definition``.
            self.set_definition({})
        else:
            self.set_definition(create_request.creation_payload)
        self.logger.debug("Creating item with tenant ID: %s", self.tenant_object_id)
        await self.save_changes()
        self.logger.info("Successfully created item %s", item_id)

    async def update(self, update_request: UpdateItemRequest) -> None:
        """Update an existing item."""
        if not update_request:
            self.logger.error(
                "Invalid item payload for type %s, item ID %s",
                self.item_type,
                self.item_object_id,
            )
            raise InvalidItemPayloadException(self.item_type, self._require_item_id())

        self.display_name = update_request.display_name
        self.description = update_request.description

        if update_request.update_payload is None:
            raise InvalidItemPayloadException(self.item_type, self._require_item_id())
        self.update_definition(update_request.update_payload)
        await self.save_changes()
        self.logger.info("Successfully updated item %s", self.item_object_id)

    async def delete(self) -> None:
        """Delete an existing item."""
        await self.item_metadata_store.delete(self._require_tenant_id(), self._require_item_id())
        self.logger.info("Successfully deleted item %s", self.item_object_id)

    @abstractmethod
    def set_definition(self, payload: dict[str, Any]) -> None:
        """Set the item definition from a creation payload."""
        pass

    @abstractmethod
    def update_definition(self, payload: dict[str, Any]) -> None:
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
                    creation_payload: dict[str, Any]) -> None:
        """Execute a job for this item."""
        pass

    @abstractmethod
    async def get_job_state(self, job_type: str, job_instance_id: UUID) -> ItemJobInstanceState:
        """Get the state of a job instance."""
        pass

    async def cancel_job(self, job_type: str, job_instance_id: UUID) -> None:
        """Cancel a job instance."""
        # Check if job metadata exists
        job_metadata = None
        tenant_id = self._require_tenant_id()
        item_id = self._require_item_id()

        if not await self.item_metadata_store.exists_job(tenant_id, item_id, str(job_instance_id)):
            # Recreate missing job metadata
            self.logger.warning(
                "Recreating missing job %s metadata in tenant %s item %s",
                job_instance_id,
                tenant_id,
                item_id,
            )
            # Create new JobMetadata instance
            job_metadata = JobMetadata(
                job_type=job_type,
                job_instance_id=job_instance_id
            )
        else:
            # Load existing job metadata
            job_metadata = await self.item_metadata_store.load_job(tenant_id, item_id, str(job_instance_id))

        # If already canceled, nothing to do
        if job_metadata.is_canceled:
            return

        # Mark as canceled and set canceled time
        job_metadata.canceled_time = datetime.datetime.now(datetime.UTC)

        # Update job metadata
        await self.item_metadata_store.upsert_job(
            tenant_id,
            item_id,
            str(job_instance_id),
            job_metadata
        )
        self.logger.info(
            "Canceled job %s for item %s",
            job_instance_id,
            self.item_object_id,
        )

    async def save_changes(self) -> None:
        """Save changes to this item."""
        self.logger.info("Saving item with tenant ID: %s", self.tenant_object_id)
        await self.store()
        await self.allocate_and_free_resources()
        await self.update_fabric()

    async def store(self) -> None:
        """Store the item metadata."""
        self.logger.info("Storing item %s", self.item_object_id)
        tenant_id = self._require_tenant_id()
        item_id = self._require_item_id()
        common_metadata = CommonItemMetadata(
            type=self.item_type,
            tenant_object_id=tenant_id,
            workspace_object_id=self.workspace_object_id,
            item_object_id=item_id,
            display_name=self.display_name,
            description=self.description
        )

        type_specific_metadata = self.get_type_specific_metadata()

        await self.item_metadata_store.upsert(
            tenant_id,
            item_id,
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
        return datetime.datetime.now(datetime.UTC).isoformat()
