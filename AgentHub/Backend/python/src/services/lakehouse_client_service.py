import json
import logging
import urllib.parse
from typing import List, Optional
from uuid import UUID

import httpx

from constants.environment_constants import EnvironmentConstants
from models.fabric_item import FabricItem
from models.lakehouse_table import LakehouseTable
from models.lakehouse_file import LakehouseFile
from models.onelake_folder import OneLakePathContainer, OneLakePathData


class LakehouseClientService:
    """Service for interacting with Fabric Lakehouse and OneLake storage."""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._http_client_service = None
    
    @property
    def http_client_service(self):
        """Lazy load HTTP client service."""
        if self._http_client_service is None:
            from services.http_client import get_http_client_service
            self._http_client_service = get_http_client_service()
        return self._http_client_service
    
    async def dispose_async(self):
        """Cleanup method for service registry."""
        self.logger.debug("LakehouseClientService disposed")
    
    async def get_lakehouse_tables(self, token: str, workspace_id: UUID, lakehouse_id: UUID) -> List[LakehouseTable]:
        """
        Retrieves a list of tables available in the current lakehouse.
        
        Args:
            token: The access token required to authorize the API requests.
            workspace_id: The id of the workspace that contains the selected lakehouse.
            lakehouse_id: The id of the lakehouse from which we want to retrieve tables.
            
        Returns:
            A list of LakehouseTable objects.
            
        Raises:
            httpx.HTTPStatusError: If the HTTP request fails.
            Exception: For other types of errors.
        """
        directory = f"{lakehouse_id}/Tables/"
        onelake_container = await self._get_path_list(token, workspace_id, directory, recursive=True)
        delta_log_directory = "/_delta_log"
        
        # Filter and map paths to LakehouseTable objects
        tables = []
        
        # A Onelake table is a delta table that consists of Parquet files and a _delta_log/ directory
        # or a shortcut to a Onelake table
        filtered_paths = [
            path for path in onelake_container.paths
            if path.name.endswith(delta_log_directory) or 
               (path.is_shortcut == True and path.account_type == "ADLS")
        ]
        
        for path in filtered_paths:
            path_name = path.name
            parts = path_name.split('/')
            schema_name = None
            
            # Check if the path ends with '_delta_log' and remove it if needed
            if path_name.endswith(delta_log_directory):
                path_name = '/'.join(parts[:-1])
                parts = path_name.split('/')
            
            # path structure without schema: <lakehouseId>/Tables/<tableName> (3 parts long)
            # path structure with schema: <lakehouseId>/Tables/<schemaName>/<tableName> (4 parts long)
            table_name = parts[-1]
            if len(parts) == 4:
                schema_name = parts[2]
            
            tables.append(LakehouseTable(
                name=table_name,
                path=path_name + '/',
                schema=schema_name
            ))
        
        return tables
    
    async def get_fabric_lakehouse(self, token: str, workspace_id: UUID, lakehouse_id: UUID) -> Optional[FabricItem]:
        """
        Get Lakehouse item from Fabric.
        
        Args:
            token: The bearer token for authentication.
            workspace_id: The workspace id of the lakehouse.
            lakehouse_id: The Lakehouse id.
            
        Returns:
            Lakehouse item metadata or None if retrieval fails.
        """
        url = f"{EnvironmentConstants.FABRIC_API_BASE_URL}/v1/workspaces/{workspace_id}/items/{lakehouse_id}"
        
        try:
            response = await self.http_client_service.get(url, token)
            response.raise_for_status()  # This will raise an exception for non-2xx status codes
            
            lakehouse_data = response.json()
            return FabricItem(**lakehouse_data)
            
        except Exception as ex:
            self.logger.error(
                f"Failed to retrieve FabricLakehouse for lakehouse: {lakehouse_id} "
                f"in workspace: {workspace_id}. Error: {str(ex)}"
            )
            return None
    
    async def get_lakehouse_files(self, token: str, workspace_id: UUID, lakehouse_id: UUID) -> List[LakehouseFile]:
        """
        Retrieves a list of files available in the current lakehouse.
        
        Args:
            token: The access token required to authorize the API requests.
            workspace_id: The id of the workspace that contains the selected lakehouse.
            lakehouse_id: The id of the lakehouse from which we want to retrieve files.
            
        Returns:
            A list of LakehouseFile objects.
            
        Raises:
            httpx.HTTPStatusError: If the HTTP request fails.
            Exception: For other types of errors.
        """
        directory = f"{lakehouse_id}/Files/"
        onelake_container = await self._get_path_list(token, workspace_id, directory, recursive=True)
        
        files = []
        for path in onelake_container.paths:
            path_name = path.name
            parts = path_name.split('/')
            
            # Path structure: <lakehouseId>/Files/...<Subdirectories>.../<fileName>
            file_name = parts[-1]
            
            # Remove the prefix (lakehouseId/Files/) from the path
            relative_path = path_name[len(directory):] if len(path_name) > len(directory) else ""
            
            files.append(LakehouseFile(
                name=file_name,
                path=relative_path,
                is_directory=path.is_directory
            ))
        
        return files
    
    async def _get_path_list(
        self, 
        token: str, 
        workspace_id: UUID, 
        directory: str, 
        recursive: bool = False
    ) -> OneLakePathContainer:
        """
        Retrieves a list of paths available in the selected directory.
        
        Args:
            token: The access token required to authorize the API requests.
            workspace_id: The id of the workspace that contains the directory.
            directory: The directory containing the desired paths.
            recursive: Whether to search the entire directory or only immediate descendants.
            
        Returns:
            OneLakePathContainer with the list of paths.
            
        Raises:
            httpx.HTTPStatusError: If the HTTP request fails.
            Exception: For other types of errors.
        """
        # Create the URL using the provided source
        encoded_directory = urllib.parse.quote(directory)
        url = (
            f"{EnvironmentConstants.ONELAKE_DFS_BASE_URL}/{workspace_id}/"
            f"?recursive={str(recursive).lower()}&resource=filesystem"
            f"&directory={encoded_directory}&getShortcutMetadata=true"
        )
        
        try:
            # Set the Authorization header using the bearer token
            response = await self.http_client_service.get(url, token)
            response.raise_for_status()  # This will raise httpx.HTTPStatusError for non-2xx status codes
            
            # Parse the response content as JSON and create typed object
            content = response.json()
            
            # Convert the raw JSON to our Pydantic model
            return OneLakePathContainer(**content)
            
        except httpx.HTTPStatusError as ex:
            # Handle HTTP request failure
            self.logger.error(f"HTTP request failed: {str(ex)}")
            raise
            
        except Exception as ex:
            # Handle other types of exceptions
            self.logger.error(f"Error in _get_path_list: {str(ex)}")
            raise


def get_lakehouse_client_service() -> LakehouseClientService:
    """Get the singleton LakehouseClientService instance."""
    from core.service_registry import get_service_registry
    registry = get_service_registry()
    
    if not registry.has(LakehouseClientService):
        if not hasattr(get_lakehouse_client_service, "instance"):
            get_lakehouse_client_service.instance = LakehouseClientService()
        return get_lakehouse_client_service.instance
    
    return registry.get(LakehouseClientService)