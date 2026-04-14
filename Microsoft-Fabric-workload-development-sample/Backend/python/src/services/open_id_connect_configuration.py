import httpx
import asyncio
import time
import logging
from typing import Dict, Any, Optional, List
from constants.environment_constants import EnvironmentConstants
from constants.api_constants import ApiConstants

logger = logging.getLogger(__name__)

class OpenIdConnectConfiguration:
    """Configuration container for OpenID Connect metadata."""
    
    def __init__(self, issuer: str, jwks_data: Dict[str, Any]):
        self.issuer_configuration = issuer
        self._signing_keys = jwks_data.get("keys", [])
        
    @property
    def signing_keys(self) -> List[Dict[str, Any]]:
        """Gets the signing keys for JWT validation."""
        return self._signing_keys

class OpenIdConnectConfigurationManager:
    """
    Manager for fetching and caching OpenID Connect configuration.
    """
    _instance = None
    _instance_lock = asyncio.Lock()
    
    def __init__(self, metadata_endpoint: str, cache_duration_seconds: int = 3600):
        self.metadata_endpoint = metadata_endpoint
        self.cache_duration_seconds = cache_duration_seconds
        self.configuration: Optional[OpenIdConnectConfiguration] = None
        self.last_updated: float = 0
        self._lock = asyncio.Lock()  # For thread-safe updates
    
    async def get_configuration_async(self, timeout_seconds: int = 5) -> OpenIdConnectConfiguration:
        """
        Gets or refreshes the OpenID Connect configuration.
        """
        current_time = time.time()
        
        # Return cached configuration if still valid
        if self.configuration and current_time - self.last_updated < self.cache_duration_seconds:
            return self.configuration
        
        # Use lock to prevent multiple concurrent refreshes
        async with self._lock:
            # Check again in case another request refreshed while waiting for lock
            if self.configuration and current_time - self.last_updated < self.cache_duration_seconds:
                return self.configuration
                
            # Fetch new configuration with timeout
            try:
                async with httpx.AsyncClient() as client:
                    # Get OpenID configuration
                    response = await client.get(
                        self.metadata_endpoint, 
                        timeout=timeout_seconds
                    )
                    response.raise_for_status()
                    config_data = response.json()
                    
                    # Fetch signing keys (JWKS)
                    jwks_uri = config_data.get("jwks_uri")
                    if not jwks_uri:
                        raise ValueError("JWKS URI not found in OpenID configuration")
                        
                    jwks_response = await client.get(jwks_uri, timeout=timeout_seconds)
                    jwks_response.raise_for_status()
                    jwks_data = jwks_response.json()
                    
                    # Create and cache the configuration
                    self.configuration = OpenIdConnectConfiguration(
                        issuer=config_data.get("issuer"),
                        jwks_data=jwks_data
                    )
                    
                    self.last_updated = current_time
                    logger.info(f"OpenID Connect configuration refreshed from {self.metadata_endpoint}")
                    
                    return self.configuration
                    
            except Exception as e:
                logger.error(f"Failed to fetch OpenID Connect configuration: {str(e)}")
                if not self.configuration:
                    raise  # Only raise if we don't have a cached configuration
                logger.warning("Returning expired cached configuration")
                return self.configuration
            
async def get_openid_manager_service() -> OpenIdConnectConfigurationManager:
    async with OpenIdConnectConfigurationManager._instance_lock:
        if OpenIdConnectConfigurationManager._instance is None:
            metadata_endpoint = ApiConstants.DEFAULT_OPENID_CONFIG_ENDPOINT
            OpenIdConnectConfigurationManager._instance = OpenIdConnectConfigurationManager(metadata_endpoint)
            logger.info(f"Created OpenID Connect configuration manager with endpoint: {metadata_endpoint}")
    return OpenIdConnectConfigurationManager._instance