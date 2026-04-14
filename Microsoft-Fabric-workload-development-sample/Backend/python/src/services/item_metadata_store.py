import asyncio
import logging
import json
import os
import shutil
from typing import Any, TypeVar, Type
from pathlib import Path
import aiofiles
from models.job_metadata import JobMetadata
from models.common_item_metadata import CommonItemMetadata
from models.item_metadata import ItemMetadata
from constants.workload_constants import WorkloadConstants
from services.configuration_service import get_configuration_service

T = TypeVar('T')


class ItemMetadataStore:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.config_service = get_configuration_service()
        self.data_dir = self.get_base_directory_path(WorkloadConstants.WORKLOAD_NAME)
        self.logger.debug(f"created Data directory: {self.data_dir}")
        os.makedirs(self.data_dir, exist_ok=True)

    async def _ensure_dir_exists(self, path: Path) -> None:
        """Ensure a directory exists, in a non-blocking way."""
        # Run directory creation in a thread to avoid blocking
        await asyncio.to_thread(os.makedirs, path, exist_ok=True)
    
    def get_base_directory_path(self, workload_name: str) -> Path:
        """Get the application data directory for the workload."""
        if os.name == 'nt':
            # On Windows, use APPDATA environment variable (Roaming)
            appdata = os.environ.get('APPDATA')
            if not appdata:
                # Fallback if APPDATA is not set
                appdata = os.path.expanduser('~\\AppData\\Roaming')
            base_path = Path(appdata)
        else:
            base_path = Path.home() / '.local' / 'share'
        return base_path / workload_name
        
        
    def _get_item_dir_path(self, tenant_id: str, item_id: str) -> Path:
        tenant_id_str = str(tenant_id)
        item_id_str = str(item_id)
        """Get directory path for an item."""
        tenant_dir = self.data_dir / tenant_id_str
        return tenant_dir / item_id_str
        
    def _get_common_metadata_path(self, tenant_id: str, item_id: str) -> Path:
        """Get path for common metadata file."""
        item_dir = self._get_item_dir_path(tenant_id, item_id)
        return item_dir / self.config_service.get_common_metadata_file_name()
        
    def _get_type_specific_metadata_path(self, tenant_id: str, item_id: str) -> Path:
        """Get path for type-specific metadata file."""
        item_dir = self._get_item_dir_path(tenant_id, item_id)
        return item_dir / self.config_service.get_type_specific_metadata_file_name()
        
    def _get_job_metadata_path(self, tenant_id: str, item_id: str, job_id: str) -> Path:
        """Get path for job metadata file."""
        item_dir = self._get_item_dir_path(tenant_id, item_id)
        jobs_dir = item_dir / self.config_service.get_jobs_directory_name()
        return jobs_dir / f"{job_id}.json"
    
    #todo: change the type_specific_metadata type!
    async def upsert(
        self,
        tenant_id: str,
        item_id: str,
        common_metadata: CommonItemMetadata,
        type_specific_metadata: Any
    ) -> None:
        """Create or update an item's metadata.
        
        Args:
            tenant_id: The tenant ID
            item_id: The item ID
            common_metadata: The common metadata model
            type_specific_metadata: The type-specific metadata model
        """
        self.logger.info(f"Upserting metadata for item {item_id} in tenant {tenant_id}")

        # Ensure directories exist 
        item_dir = self._get_item_dir_path(tenant_id, item_id)
        await self._ensure_dir_exists(item_dir)
        
        # Save common metadata
        common_path = self._get_common_metadata_path(tenant_id, item_id)
        async with aiofiles.open(common_path, 'w') as f:
            # Convert model to dictionary for JSON serialization
            common_data = common_metadata.model_dump(mode='json')
            await f.write(json.dumps(common_data, indent=2))
            
        # Save type-specific metadata
        specific_path = self._get_type_specific_metadata_path(tenant_id, item_id)
        async with aiofiles.open(specific_path, 'w') as f:
            # Handle different types of metadata objects
            if hasattr(type_specific_metadata, 'model_dump'):
                # If it's a Pydantic model, use model_dump()
                data = type_specific_metadata.model_dump(mode='json', by_alias=True)
            else:
                data = type_specific_metadata
                # Otherwise, try direct serialization
            await f.write(json.dumps(data, indent=2))
    
    async def load(self, tenant_id: str, item_id: str, metadata_class: Type[T] = None) -> ItemMetadata[T]:
        """Load an item's metadata.
        
        Args:
            tenant_id: The tenant ID
            item_id: The item ID
            metadata_class: Optional type-specific metadata class to instantiate
            
        Returns:
            An ItemMetadata instance with both common and type-specific metadata
            
        Raises:
            FileNotFoundError: If the item metadata doesn't exist
        """
        self.logger.info(f"Loading metadata for item {item_id} in tenant {tenant_id}")
        
        # Load common metadata
        common_path = self._get_common_metadata_path(tenant_id, item_id)
        type_specific_path = self._get_type_specific_metadata_path(tenant_id, item_id)

        common_exists = await asyncio.to_thread(common_path.exists)
        type_specific_exists = await asyncio.to_thread(type_specific_path.exists)
        
        if not common_exists or not type_specific_exists:
            self.logger.error(f"Metadata not found for item {item_id} in tenant {tenant_id}")
            raise FileNotFoundError(f"Item metadata not found for {item_id}")
            
        async with aiofiles.open(common_path, 'r') as f:
            common_data = json.loads(await f.read())
            common_metadata = CommonItemMetadata(**common_data)
            
        async with aiofiles.open(type_specific_path, 'r') as f:
            type_specific_data = json.loads(await f.read())
            
            # If a specific metadata class was provided, instantiate it
            if metadata_class:
                type_specific_metadata = metadata_class(**type_specific_data)
            else:
                # Otherwise just use the raw data
                type_specific_metadata = type_specific_data
        
        self.logger.info(f"Metadata loaded for item {item_id} in tenant {tenant_id}:")
        self.logger.info(f"Common metadata: {common_metadata}")
        self.logger.info(f"Type-specific metadata: {type_specific_metadata}")
            
        return ItemMetadata(
            common_metadata=common_metadata,
            type_specific_metadata=type_specific_metadata
        )
    
    async def exists(self, tenant_id: str, item_id: str) -> bool:
        """Check if an item's metadata exists."""
        common_path = self._get_common_metadata_path(tenant_id, item_id)
        type_specific_path = self._get_type_specific_metadata_path(tenant_id, item_id)

        common_exists = await asyncio.to_thread(common_path.exists)
        type_specific_exists = await asyncio.to_thread(type_specific_path.exists)
        return common_exists and type_specific_exists
    
    async def delete(self, tenant_id: str, item_id: str) -> None:
        """Delete an item's metadata."""
        self.logger.info(f"Deleting metadata for item {item_id} in tenant {tenant_id}")
        item_dir = self._get_item_dir_path(tenant_id, item_id)

        dir_exists = await asyncio.to_thread(item_dir.exists)
        if dir_exists:
            await asyncio.to_thread(shutil.rmtree, item_dir)
        else:
            self.logger.warning(f"Item directory {item_dir} does not exist, nothing to delete.")
        self.logger.info(f"Metadata for item {item_id} in tenant {tenant_id} deleted successfully.")
    
    async def upsert_job(
        self,
        tenant_id: str,
        item_id: str,
        job_id: str,
        job_metadata: JobMetadata
    ) -> None:
        """Create or update job metadata.
        
        Args:
            tenant_id: The tenant ID
            item_id: The item ID
            job_id: The job ID
            job_metadata: The job metadata model
        """
        self.logger.info(f"Upserting job metadata for job {job_id} in item {item_id}")

        jobs_dir = self._get_item_dir_path(tenant_id, item_id) / self.config_service.get_jobs_directory_name()
        await self._ensure_dir_exists(jobs_dir)

        job_path = self._get_job_metadata_path(tenant_id, item_id, job_id)
        async with aiofiles.open(job_path, 'w') as f:
            job_data = job_metadata.model_dump(mode='json')
            await f.write(json.dumps(job_data, indent=2))
    
    async def load_job(
        self, 
        tenant_id: str, 
        item_id: str, 
        job_id: str
    ) -> JobMetadata:
        """Load job metadata.
        
        Args:
            tenant_id: The tenant ID
            item_id: The item ID
            job_id: The job ID
            
        Returns:
            JobMetadata: The job metadata model
            
        Raises:
            FileNotFoundError: If the job metadata doesn't exist
        """
        job_path = self._get_job_metadata_path(tenant_id, item_id, job_id)
        job_exists = await asyncio.to_thread(job_path.exists)
        
        if not job_exists:
            self.logger.error(f"Metadata not found for job {job_id} in item {item_id}")
            raise FileNotFoundError(f"Job metadata not found for job {job_id}")
            
        async with aiofiles.open(job_path, 'r') as f:
            job_data = json.loads(await f.read())
            return JobMetadata(**job_data)
    
    async def exists_job(self, tenant_id: str, item_id: str, job_id: str) -> bool:
        """Check if job metadata exists."""
        job_path = self._get_job_metadata_path(tenant_id, item_id, job_id)
        return await asyncio.to_thread(job_path.exists)
    
    async def delete_job(self, tenant_id: str, item_id: str, job_id: str) -> None:
        """Delete job metadata."""
        job_path = self._get_job_metadata_path(tenant_id, item_id, job_id)
        job_exists = await asyncio.to_thread(job_path.exists)
        if job_exists:
            await asyncio.to_thread(os.remove, job_path)
            
    
def get_item_metadata_store() -> ItemMetadataStore:
    from core.service_registry import get_service_registry
    registry = get_service_registry()
    
    if not registry.has(ItemMetadataStore):
        if not hasattr(get_item_metadata_store, "instance"):
            get_item_metadata_store.instance = ItemMetadataStore()
        return get_item_metadata_store.instance
    
    return registry.get(ItemMetadataStore)