import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typing import Dict, Any, Generator, Optional, List
from uuid import UUID
from datetime import datetime, timedelta
import sys
from pathlib import Path

# Add src to Python path for imports
src_path = Path(__file__).parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from main import app as application
from core.service_registry import ServiceRegistry
from services.authentication import AuthenticationService
from services.item_factory import ItemFactory
from models.authentication_models import AuthorizationContext, Claim, SubjectAndAppToken

# Import the services that need to be mocked
from services.configuration_service import ConfigurationService
from services.open_id_connect_configuration import OpenIdConnectConfigurationManager, OpenIdConnectConfiguration
from services.http_client import HttpClientService
from services.item_metadata_store import ItemMetadataStore
from services.lakehouse_client_service import LakehouseClientService
from services.onelake_client_service import OneLakeClientService
from services.authorization import AuthorizationHandler
from constants.environment_constants import EnvironmentConstants


@pytest.fixture(scope="session")
def event_loop_policy():
    """Set the event loop policy for the test session."""
    return asyncio.get_event_loop_policy()


@pytest.fixture(scope="function")
def event_loop(event_loop_policy):
    """Create an instance of the default event loop for each test function."""
    loop = event_loop_policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_service_registry():
    """Create a mock service registry for testing."""
    # Clear the singleton instance to ensure clean state
    ServiceRegistry._instance = None
    registry = ServiceRegistry()
    
    # Mark as initialized to bypass initialization checks
    registry._initialized = True
    
    return registry


@pytest.fixture
def mock_auth_context():
    """Create a mock authorization context for testing."""
    context = AuthorizationContext(
        original_subject_token="mock_subject_token",
        tenant_object_id="44444444-4444-4444-4444-444444444444",
        claims=[
            {"type": "oid", "value": "test-user-id"},
            {"type": "name", "value": "Test User"},
            {"type": "tid", "value": "44444444-4444-4444-4444-444444444444"}
        ]
    )
    return context


@pytest.fixture
def mock_authentication_service(mock_auth_context):
    """Create a mock authentication service."""
    mock_service = AsyncMock(spec=AuthenticationService)
    
    # Configure the mock's authenticate_control_plane_call to return a mock auth context
    mock_service.authenticate_control_plane_call.return_value = mock_auth_context
    mock_service.authenticate_data_plane_call.return_value = mock_auth_context
    mock_service.get_access_token_on_behalf_of.return_value = "mock_access_token"
    mock_service.get_fabric_s2s_token.return_value = "mock_s2s_token"
    mock_service.build_composite_token.return_value = "SubjectAndAppToken1.0 subjectToken=\"mock_subject\", appToken=\"mock_app\""
    
    return mock_service


@pytest.fixture
def mock_item_factory():
    """Create a mock item factory."""
    mock_factory = Mock(spec=ItemFactory)
    
    # Import test helpers here to avoid circular imports
    from tests.test_helpers import TestHelpers
    
    # Create a default mock item that will be returned
    mock_item = TestHelpers.create_mock_item()
    mock_factory.create_item.return_value = mock_item
    
    return mock_factory


@pytest.fixture
def mock_configuration_service():
    """Create a mock configuration service."""
    mock_config = Mock(spec=ConfigurationService)
    mock_config.get_environment.return_value = "test"
    mock_config.get_publisher_tenant_id.return_value = "test-publisher-tenant"
    mock_config.get_audience.return_value = "test-audience"
    mock_config.get_client_id.return_value = "test-client-id"
    mock_config.get_client_secret.return_value = "test-client-secret"
    mock_config.get_common_metadata_file_name.return_value = "common_metadata.json"
    mock_config.get_type_specific_metadata_file_name.return_value = "type_specific_metadata.json"
    mock_config.get_jobs_directory_name.return_value = "jobs"
    return mock_config


@pytest.fixture
def mock_all_services(mock_service_registry, mock_authentication_service, mock_item_factory, mock_configuration_service):
    """Mock all services required for testing."""
    # Register the already created mocks
    mock_service_registry.register(AuthenticationService, mock_authentication_service)
    mock_service_registry.register(ItemFactory, mock_item_factory)
    mock_service_registry.register(ConfigurationService, mock_configuration_service)
    
    # Mock remaining services
    services_to_mock = [
        (OpenIdConnectConfigurationManager, AsyncMock),
        (HttpClientService, Mock),
        (ItemMetadataStore, Mock),
        (LakehouseClientService, Mock),
        (OneLakeClientService, Mock),
        (AuthorizationHandler, Mock)
    ]
    
    mocked_services = {
        'AuthenticationService': mock_authentication_service,
        'ItemFactory': mock_item_factory,
        'ConfigurationService': mock_configuration_service
    }
    
    for service_class, mock_type in services_to_mock:
        mock_service = mock_type(spec=service_class)
        mock_service_registry.register(service_class, mock_service)
        mocked_services[service_class.__name__] = mock_service
    
    return mocked_services


@pytest.fixture
def app(mock_all_services, mock_service_registry) -> Generator[FastAPI, None, None]:
    """Create FastAPI app with mocked services."""
    # Clear any existing dependency overrides
    application.dependency_overrides = {}
    
    # Create a context manager to patch all the service getter functions
    patches = []
    
    # Patch service registry getter
    registry_patch = patch('core.service_registry.get_service_registry', return_value=mock_service_registry)
    patches.append(registry_patch)
    
    # Patch authentication service getter
    auth_patch = patch('fabric_api.impl.item_lifecycle_controller.get_authentication_service', 
                      return_value=mock_all_services['AuthenticationService'])
    patches.append(auth_patch)
    
    # Patch item factory getter
    factory_patch = patch('fabric_api.impl.item_lifecycle_controller.get_item_factory', 
                         return_value=mock_all_services['ItemFactory'])
    patches.append(factory_patch)
    
    # Patch configuration service getter
    config_patch = patch('services.configuration_service.get_configuration_service',
                        return_value=mock_all_services['ConfigurationService'])
    patches.append(config_patch)

    # Add patches for jobs controller
    jobs_auth_patch = patch('fabric_api.impl.jobs_controller.get_authentication_service',
                           return_value=mock_all_services['AuthenticationService'])
    patches.append(jobs_auth_patch)
    
    jobs_factory_patch = patch('fabric_api.impl.jobs_controller.get_item_factory',
                              return_value=mock_all_services['ItemFactory'])
    patches.append(jobs_factory_patch)
    
    # Apply all patches
    for p in patches:
        p.start()
    
    try:
        yield application
    finally:
        # Stop all patches
        for p in patches:
            p.stop()


@pytest.fixture
def client(app) -> TestClient:
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def valid_headers():
    """Provide valid headers for API requests."""
    return {
        "activity_id": "test-activity-123",
        "request_id": "test-request-456",
        "authorization": "SubjectAndAppToken1.0 subjectToken=\"mock_subject_token\", appToken=\"mock_app_token\"",
        "x_ms_client_tenant_id": "44444444-4444-4444-4444-444444444444",
    }


@pytest.fixture
def sample_create_request():
    """Provide a sample create item request."""
    return {
        "display_name": "Test Item",
        "description": "Test Description",
        "creation_payload": {
            "metadata": {
                "operand1": 10,
                "operand2": 20,
                "operator": "Add"
            }
        }
    }


@pytest.fixture
def sample_update_request():
    """Provide a sample update item request."""
    return {
        "display_name": "Updated Test Item",
        "description": "Updated Test Description",
        "update_payload": {
            "metadata": {
                "operand1": 30,
                "operand2": 40,
                "operator": "Multiply"
            }
        }
    }


# Import AuthenticationTestFixtures from test_fixtures.py
from tests.test_fixtures import AuthenticationTestFixtures


@pytest.fixture
def auth_fixtures():
    """Provide AuthenticationTestFixtures as a pytest fixture."""
    return AuthenticationTestFixtures


@pytest.fixture
def enhanced_mock_authentication_service():
    """Create an enhanced mock authentication service with more comprehensive capabilities."""
    mock_service = AsyncMock(spec=AuthenticationService)
    
    # Use AuthenticationTestFixtures for enhanced mock context
    auth_context = AuthenticationTestFixtures.create_auth_context()
    
    # Configure the mock's methods
    mock_service.authenticate_control_plane_call.return_value = auth_context
    mock_service.authenticate_data_plane_call.return_value = auth_context
    mock_service.get_access_token_on_behalf_of.return_value = "mock_access_token"
    mock_service.get_fabric_s2s_token.return_value = "mock_s2s_token"
    mock_service.build_composite_token.return_value = "SubjectAndAppToken1.0 subjectToken=\"mock_subject\", appToken=\"mock_app\""
    
    return mock_service