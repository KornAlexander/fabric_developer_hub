import json
import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

class Environment(Enum):
    """Application environments."""
    DEVELOPMENT = "Development"
    STAGING = "Staging" 
    PRODUCTION = "Production"

@dataclass
class ServerConfig:
    """Server configuration."""
    host: str = "0.0.0.0"
    port: int = 5000
    workers: int = 1
    shutdown_timeout: int = 10
    force_shutdown_timeout: int = 15

@dataclass
class SecurityConfig:
    """Security configuration."""
    allowed_hosts: List[str] = field(default_factory=lambda: ["*"])
    cors_origins: List[str] = field(default_factory=lambda: ["*"])

class ConfigurationService:
    """
    Service for accessing application configuration.
    Provides a clean interface for configuration management across environments.
    """
    
    _instance: Optional['ConfigurationService'] = None
    _initialized: bool = False
    
    def __new__(cls, *args, **kwargs):
        """Ensure singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, base_path: Optional[Path] = None, environment: Optional[str] = None):
        """
        Initialize the configuration service.
        
        Args:
            base_path: Base path for configuration files (defaults to src directory)
            environment: Environment name (Development, Staging, Production)
        """
        # Prevent re-initialization
        if ConfigurationService._initialized:
            return
            
        self.logger = logging.getLogger(__name__)
        
        # Determine base path
        if base_path is None:
            base_path = Path(__file__).parent.parent  # Go up to src directory
        else:
            base_path = Path(base_path)
            
        self.base_path = base_path
        
        self.environment = (
            environment or 
            os.environ.get('PYTHON_ENVIRONMENT') or 
            os.environ.get('ASPNETCORE_ENVIRONMENT') or  
            Environment.DEVELOPMENT.value
        )
        
        # Initialize configuration storage
        self.config: Dict[str, Any] = {}
        self._server_config: Optional[ServerConfig] = None
        self._security_config: Optional[SecurityConfig] = None
        
        # Load all configurations
        self._load_configurations()
        
        ConfigurationService._initialized = True
        
    def _load_configurations(self) -> None:
        """Load all configuration files in order of precedence."""
        try:
            # 1. Load base appsettings.json
            base_config_path = self.base_path / "appsettings.json"
            self._load_config_file(base_config_path, required=True)
            
            # 2. Load environment-specific settings
            env_config_path = self.base_path / f"appsettings.{self.environment}.json"
            self._load_config_file(env_config_path, required=False)
            
            # 3. Override with environment variables
            self._load_environment_variables()
            
            # 4. Validate required settings
            self._validate_configuration()
            
            # 5. Parse structured configs
            self._parse_structured_configs()
            
            self.logger.info(f"Configuration loaded successfully for environment: {self.environment}")
            
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
            raise
        
    def _load_config_file(self, config_path: Path, required: bool = True) -> None:
        """Load a configuration file and merge with existing config."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # Remove comments (// style) for JSON compatibility
                lines = content.split('\n')
                cleaned_lines = []
                for line in lines:
                    comment_idx = line.find('//')
                    if comment_idx != -1 and not self._is_in_string(line, comment_idx):
                        line = line[:comment_idx]
                    cleaned_lines.append(line)
                content = '\n'.join(cleaned_lines)
                
                file_config = json.loads(content)
                self._deep_merge(self.config, file_config)
                self.logger.debug(f"Loaded configuration from {config_path}")
                
        except FileNotFoundError:
            if required:
                self.logger.error(f"Required configuration file not found: {config_path}")
                raise
            else:
                self.logger.debug(f"Optional configuration file not found: {config_path}")
                
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in configuration file {config_path}: {e}")
            raise
            
    def _is_in_string(self, line: str, position: int) -> bool:
        """Check if a position in a line is within a string literal."""
        in_string = False
        escape_next = False
        
        for i, char in enumerate(line):
            if i >= position:
                break
                
            if escape_next:
                escape_next = False
                continue
                
            if char == '\\':
                escape_next = True
            elif char == '"':
                in_string = not in_string
                
        return in_string
    
    def _load_environment_variables(self) -> None:
        """Override configuration with environment variables."""
        # Standard mappings
        env_mappings = {
            'PUBLISHER_TENANT_ID': 'PublisherTenantId',
            'CLIENT_ID': 'ClientId',
            'CLIENT_SECRET': 'ClientSecret',
            'AUDIENCE': 'Audience',
        }
        
        # Support ASP.NET Core style environment variables (with __ as separator)
        for env_key, env_value in os.environ.items():
            if '__' in env_key:
                # Convert __ to : for nested keys
                config_key = env_key.replace('__', ':')
                self._set_nested_value(config_key, env_value)
                self.logger.debug(f"Set {config_key} from environment variable {env_key}")
        
        # Apply standard mappings
        for env_var, config_key in env_mappings.items():
            if value := os.environ.get(env_var):
                self._set_nested_value(config_key, value)
                self.logger.debug(f"Overrode {config_key} from environment variable {env_var}")
    
    def _set_nested_value(self, key_path: str, value: Any) -> None:
        """Set a nested configuration value using : separator."""
        keys = key_path.split(':')
        current = self.config
        
        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        # Convert string values to appropriate types
        if isinstance(value, str):
            # Try to parse as JSON first (for arrays/objects)
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                # Not JSON, try other conversions
                if value.lower() in ('true', 'false'):
                    value = value.lower() == 'true'
                elif value.isdigit():
                    value = int(value)
                elif '.' in value and all(part.isdigit() for part in value.split('.', 1)):
                    value = float(value)
        
        current[keys[-1]] = value
                
    def _deep_merge(self, base: Dict[str, Any], update: Dict[str, Any]) -> None:
        """Deep merge update dictionary into base dictionary."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
                
    def _validate_configuration(self) -> None:
        """Validate that required configuration values are present."""
        if self.environment == Environment.PRODUCTION.value:
            required_keys = [
                'PublisherTenantId',
                'ClientId',
                'ClientSecret',
                'Audience'
            ]
            
            missing_keys = []
            for key in required_keys:
                if not self.get_value(key):
                    missing_keys.append(key)
                    
            if missing_keys:
                raise ValueError(f"Missing required configuration keys for production: {missing_keys}")
    
    def _parse_structured_configs(self) -> None:
        """Parse structured configuration sections into typed objects."""
        # Parse server config
        server_section = self.get_section("Server")
        if server_section:
            self._server_config = ServerConfig(
                host=server_section.get("Host", "0.0.0.0"),
                port=int(server_section.get("Port", 5000)),
                workers=int(server_section.get("Workers", 1)),
                shutdown_timeout=int(server_section.get("ShutdownTimeout", 10)),
                force_shutdown_timeout=int(server_section.get("ForceShutdownTimeout", 15))
            )
        else:
            self._server_config = ServerConfig()
            
        # Parse security config
        security_section = self.get_section("Security")
        if security_section:
            self._security_config = SecurityConfig(
                allowed_hosts=security_section.get("AllowedHosts", ["*"]),
                cors_origins=security_section.get("CorsOrigins", ["*"])
            )
        else:
            self._security_config = SecurityConfig()
        
    def get_value(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by key.
        Supports nested keys with : separator (e.g., "Logging:LogLevel:Default")
        """
        keys = key.split(':')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
                
        return value
        
    def get_section(self, key: str) -> Dict[str, Any]:
        """Get a configuration section as a dictionary."""
        value = self.get_value(key, {})
        return value if isinstance(value, dict) else {}
    
    def get_connection_string(self, name: str) -> Optional[str]:
        """Get a connection string by name (C# compatibility)."""
        return self.get_value(f"ConnectionStrings:{name}")
    
    def __getitem__(self, key: str) -> Any:
        """Support dictionary-style access: config['key']."""
        value = self.get_value(key)
        if value is None:
            raise KeyError(f"Configuration key '{key}' not found")
        return value
    
    def get(self, key: str, default: Any = None) -> Any:
        """Support dict.get() style access."""
        return self.get_value(key, default)
    
    # Specific configuration accessors
    
    def get_publisher_tenant_id(self) -> str:
        """Get the PublisherTenantId configuration value."""
        return self.get_value("PublisherTenantId", "")
        
    def get_audience(self) -> str:
        """Get the Audience configuration value."""
        return self.get_value("Audience", "")
        
    def get_client_id(self) -> str:
        """Get the ClientId configuration value."""
        return self.get_value("ClientId", "")
        
    def get_client_secret(self) -> str:
        """Get the ClientSecret configuration value."""
        return self.get_value("ClientSecret", "")
        
    # Storage configuration
    
    def get_jobs_directory_name(self) -> str:
        """Get the JobsDirectory configuration value."""
        return self.get_value("Storage:Metadata:JobsDirectory", "jobs")
    
    def get_common_metadata_file_name(self) -> str:
        """Get the CommonMetadataFile configuration value."""
        return self.get_value("Storage:Metadata:CommonMetadataFile", "common_metadata.json")
    
    def get_type_specific_metadata_file_name(self) -> str:
        """Get the TypeSpecificMetadataFile configuration value."""
        return self.get_value("Storage:Metadata:TypeSpecificMetadataFile", "type_specific_metadata.json")
    
    # Application configuration
    
    def get_app_name(self) -> str:
        """Get application name."""
        return self.get_value("Application:Name", "Microsoft Fabric Python Backend")
        
    def get_environment(self) -> str:
        """Get current environment."""
        return self.environment
        
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.environment == Environment.DEVELOPMENT.value
        
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment == Environment.PRODUCTION.value
        
    def is_debug(self) -> bool:
        """Check if debug mode is enabled."""
        # In development, debug is true by default
        if self.is_development():
            return bool(self.get_value("Application:Debug", True))
        # In production, debug is false by default
        return bool(self.get_value("Application:Debug", False))
    
    # Server configuration
    
    def get_host(self) -> str:
        """Get server host."""
        if self._server_config:
            return self._server_config.host
        return "0.0.0.0"
        
    def get_port(self) -> int:
        """Get server port."""
        if self._server_config:
            return self._server_config.port
        return 5000
        
    def get_workers(self) -> int:
        """Get number of workers."""
        if self._server_config:
            return self._server_config.workers
        return 1
    
    # Security configuration
    
    def get_allowed_hosts(self) -> List[str]:
        """Get allowed hosts."""
        if self._security_config:
            return self._security_config.allowed_hosts
        return ["*"]
        
    def get_cors_origins(self) -> List[str]:
        """Get CORS origins."""
        if self._security_config:
            return self._security_config.cors_origins
        return ["*"]
    
    # Kestrel/Server configuration
    
    def get_http_endpoint(self) -> str:
        """Get HTTP endpoint URL."""
        host = self.get_host()
        port = self.get_port()
        return f"http://{host}:{port}"
        
    def get_https_endpoint(self) -> str:
        """Get HTTPS endpoint URL."""
        host = self.get_host()
        port = self.get_port()
        return f"https://{host}:{port + 1}"
    
    # Logging configuration
    
    def get_log_level(self) -> str:
        """Get log level for a specific category."""
        return self.get_value(f"Logging:LogLevel", "Information")
    
    def get_shutdown_timeout(self) -> int:
        """Get server shutdown timeout in seconds."""
        if self._server_config:
            return self._server_config.shutdown_timeout
        return 10
    
    def get_force_shutdown_timeout(self) -> int:
        """Get force shutdown timeout in seconds."""
        if self._server_config:
            return self._server_config.force_shutdown_timeout
        return 15
    
    # ServiceRegistry integration
    
    async def dispose_async(self) -> None:
        """Dispose method for ServiceRegistry cleanup."""
        self.logger.debug("ConfigurationService disposed")


def get_configuration_service() -> ConfigurationService:
    """
    Get the ConfigurationService instance from ServiceRegistry.
    This ensures proper lifecycle management and dependency injection.
    """
    from core.service_registry import get_service_registry
    
    registry = get_service_registry()
    
    # Check if already registered
    if registry.has(ConfigurationService):
        return registry.get(ConfigurationService)
    
    # Create and register if not exists (bootstrap case)
    config_service = ConfigurationService()
    registry.register(ConfigurationService, config_service)
    
    return config_service