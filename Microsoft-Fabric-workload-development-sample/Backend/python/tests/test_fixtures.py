"""Common test fixtures and data."""

import json
import base64
import time
from unittest.mock import Mock, AsyncMock, patch
from uuid import UUID
from typing import Dict, Any, List, Optional

from constants.environment_constants import EnvironmentConstants
from models.authentication_models import AuthorizationContext, Claim, SubjectAndAppToken
from services.configuration_service import ConfigurationService
from services.open_id_connect_configuration import OpenIdConnectConfigurationManager, OpenIdConnectConfiguration
from services.authentication import AuthenticationService


class TestFixtures:
    """Common test data fixtures."""
    
    # Test UUIDs
    WORKSPACE_ID = UUID("11111111-1111-1111-1111-111111111111")
    ITEM_ID = UUID("22222222-2222-2222-2222-222222222222")
    JOB_INSTANCE_ID = UUID("33333333-3333-3333-3333-333333333333")
    TENANT_ID = UUID("44444444-4444-4444-4444-444444444444")
    
    # Authentication test data
    AUTH_TENANT_ID = "test-tenant-id"
    AUTH_OBJECT_ID = "test-object-id"
    AUTH_PUBLISHER_TENANT_ID = "publisher-tenant-id"
    AUTH_AUDIENCE = "test-audience"
    AUTH_CLIENT_ID = "test-client-id"
    AUTH_CLIENT_SECRET = "test-client-secret"
    AUTH_ISSUER = "https://login.microsoftonline.com/test-tenant/v2.0"
    AUTH_APP_ID = EnvironmentConstants.FABRIC_BACKEND_APP_ID
    AUTH_SCOPES = "FabricWorkloadControl"
    
    # JWT test data
    JWT_HEADER = {
        "typ": "JWT",
        "alg": "RS256",
        "kid": "test-key-id"
    }
    
    JWT_PAYLOAD_DELEGATED = {
        "iss": "https://login.microsoftonline.com/test-tenant/v2.0",
        "aud": "test-audience",
        "sub": "test-subject",
        "azp": EnvironmentConstants.FABRIC_BACKEND_APP_ID,
        "tid": "test-tenant-id",
        "oid": "test-object-id",
        "ver": "2.0",
        "scp": "FabricWorkloadControl"
    }
    
    JWT_PAYLOAD_APP_ONLY = {
        "iss": "https://login.microsoftonline.com/publisher-tenant/v2.0",
        "aud": "test-audience",
        "sub": "service-principal-id",
        "azp": EnvironmentConstants.FABRIC_BACKEND_APP_ID,
        "tid": "publisher-tenant-id",
        "oid": "service-principal-id",
        "ver": "2.0",
        "idtyp": "app"
    }
    
    # OpenID Connect configuration test data
    OPENID_CONFIG = {
        "issuer_configuration": "https://login.microsoftonline.com/{tenantid}/v2.0",
        "signing_keys": [{"kid": "test-key-id", "kty": "RSA"}]
    }
    
    # Authentication headers
    AUTH_HEADERS = {
        "activity_id": "test-activity-123",
        "request_id": "test-request-456",
        "authorization": "SubjectAndAppToken1.0 subjectToken=\"mock_subject_token\", appToken=\"mock_app_token\"",
        "x_ms_client_tenant_id": "44444444-4444-4444-4444-444444444444",
    }
    
    # Claims test data
    SUBJECT_CLAIMS = [
        {"type": "tid", "value": "test-tenant-id"},
        {"type": "oid", "value": "test-object-id"},
        {"type": "scp", "value": "FabricWorkloadControl"},
        {"type": "ver", "value": "2.0"},
        {"type": "azp", "value": EnvironmentConstants.FABRIC_BACKEND_APP_ID}
    ]
    
    APP_CLAIMS = [
        {"type": "tid", "value": "publisher-tenant-id"},
        {"type": "oid", "value": "service-principal-id"},
        {"type": "idtyp", "value": "app"},
        {"type": "ver", "value": "2.0"},
        {"type": "azp", "value": EnvironmentConstants.FABRIC_BACKEND_APP_ID}
    ]
    
    # Test item types
    ITEM_TYPE = "Item1"
    UNKNOWN_ITEM_TYPE = "UnknownItem"
    
    # Test payloads
    CREATE_PAYLOAD = {
        "display_name": "Test Item",
        "description": "Test Description",
        "creation_payload": {
            "metadata": {
                "operand1": 10,
                "operand2": 20,
                "operator": "Add",
                "lakehouse": {
                    "id": "44444444-4444-4444-4444-444444444444",
                    "workspace_id": "55555555-5555-5555-5555-555555555555"
                }
            }
        }
    }
    
    UPDATE_PAYLOAD = {
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
    
    INVALID_PAYLOAD = {
        "display_name": "Invalid Item",
        # Missing required fields
    }

     # Job test data
    JOB_CREATE_PAYLOAD = {
        "invoke_type": "Manual",
        "creation_payload": {
            "key": "value",
            "nested": {
                "data": "test"
            }
        }
    }
    
    SCHEDULED_JOB_PAYLOAD = {
        "invoke_type": "Scheduled",
        "creation_payload": {
            "schedule": "0 * * * *",
            "timezone": "UTC"
        }
    }
    
    # Authentication error scenarios
    EXPIRED_JWT_PAYLOAD = {
        "iss": "https://login.microsoftonline.com/test-tenant/v2.0",
        "aud": "test-audience",
        "sub": "test-subject",
        "azp": EnvironmentConstants.FABRIC_BACKEND_APP_ID,
        "tid": "test-tenant-id",
        "oid": "test-object-id",
        "ver": "2.0",
        "scp": "FabricWorkloadControl",
        "exp": 1234567890  # Expired timestamp
    }

    INVALID_AUDIENCE_JWT_PAYLOAD = {
        "iss": "https://login.microsoftonline.com/test-tenant/v2.0",
        "aud": "wrong-audience",
        "sub": "test-subject",
        "azp": EnvironmentConstants.FABRIC_BACKEND_APP_ID,
        "tid": "test-tenant-id",
        "oid": "test-object-id",
        "ver": "2.0",
        "scp": "FabricWorkloadControl"
    }

    MISSING_CLAIMS_JWT_PAYLOAD = {
        "iss": "https://login.microsoftonline.com/test-tenant/v2.0",
        "aud": "test-audience",
        "sub": "test-subject"
        # Missing required claims like tid, oid, etc.
    }


# ===== AUTHENTICATION TEST FIXTURES =====
class AuthenticationTestFixtures:
    """Consolidated test fixtures and helpers for authentication testing."""
    
    @staticmethod
    def create_jwt_header(kid: str = "test-key-id", alg: str = "RS256") -> Dict[str, Any]:
        """Create a JWT header for testing."""
        return {"typ": "JWT", "alg": alg, "kid": kid}
    
    @staticmethod
    def create_jwt_payload(
        iss: str = "https://login.microsoftonline.com/test-tenant/v2.0",
        aud: str = "test-audience",
        tenant_id: str = "test-tenant-id",
        object_id: str = "test-object-id",
        app_id: str = EnvironmentConstants.FABRIC_BACKEND_APP_ID,
        scopes: Optional[str] = "FabricWorkloadControl",  # Default scope for delegated tokens
        id_typ: Optional[str] = None,
        token_version: str = "2.0",
        exp_offset_minutes: int = 60
    ) -> Dict[str, Any]:
        """Create a JWT payload for testing."""
        now = int(time.time())
        exp = now + (exp_offset_minutes * 60)
        
        payload = {
            "iss": iss,
            "aud": aud,
            "sub": "test-subject",
            "azp": app_id if token_version == "2.0" else None,
            "appid": app_id if token_version == "1.0" else None,
            "tid": tenant_id,
            "oid": object_id,
            "ver": token_version,
            "iat": now,
            "nbf": now,
            "exp": exp
        }
        
        # Remove None values
        payload = {k: v for k, v in payload.items() if v is not None}
        
        # Add scopes for delegated tokens (unless it's an app-only token)
        if scopes and not id_typ:
            payload["scp"] = scopes
        if id_typ:
            payload["idtyp"] = id_typ
            
        return payload
    
    @staticmethod
    def encode_jwt_part(data: Dict[str, Any]) -> str:
        """Encode a JWT part for testing."""
        json_str = json.dumps(data, separators=(',', ':'))
        encoded = base64.urlsafe_b64encode(json_str.encode()).decode()
        return encoded.rstrip('=')
    
    @staticmethod
    def create_mock_jwt_token(
        header: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        signature: str = "mock-signature",
        **payload_kwargs
    ) -> str:
        """Create a mock JWT token for testing."""
        if header is None:
            header = AuthenticationTestFixtures.create_jwt_header()
        if payload is None:
            payload = AuthenticationTestFixtures.create_jwt_payload(**payload_kwargs)
            
        header_encoded = AuthenticationTestFixtures.encode_jwt_part(header)
        payload_encoded = AuthenticationTestFixtures.encode_jwt_part(payload)
        
        return f"{header_encoded}.{payload_encoded}.{signature}"
    
    @staticmethod
    def get_basic_mocks():
        """Get basic mock objects for testing."""
        # OpenID manager mock
        mock_openid_manager = AsyncMock(spec=OpenIdConnectConfigurationManager)
        mock_config = Mock(spec=OpenIdConnectConfiguration)
        mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
        mock_config.signing_keys = [{"kid": "test-key-id", "kty": "RSA"}]
        mock_openid_manager.get_configuration_async.return_value = mock_config
        
        # Configuration service mock
        mock_config_service = Mock(spec=ConfigurationService)
        mock_config_service.get_publisher_tenant_id.return_value = "publisher-tenant-id"
        mock_config_service.get_audience.return_value = "test-audience"
        mock_config_service.get_client_id.return_value = "test-client-id"
        mock_config_service.get_client_secret.return_value = "test-client-secret"
        
        return mock_openid_manager, mock_config_service
    
    @staticmethod
    def get_config_service_mock(
        publisher_tenant_id: str = "publisher-tenant-id",
        audience: str = "test-audience",
        client_id: str = "test-client-id",
        client_secret: str = "test-client-secret"
    ):
        """Get a configuration service mock with specific values."""
        mock_config = Mock(spec=ConfigurationService)
        mock_config.get_publisher_tenant_id.return_value = publisher_tenant_id
        mock_config.get_audience.return_value = audience
        mock_config.get_client_id.return_value = client_id
        mock_config.get_client_secret.return_value = client_secret
        return mock_config
    
    @staticmethod
    def get_authentication_service():
        """Get a configured AuthenticationService for testing."""
        mock_openid_manager, mock_config_service = AuthenticationTestFixtures.get_basic_mocks()
        
        with patch("services.authentication.get_configuration_service", return_value=mock_config_service):
            with patch("services.authentication.msal"):
                return AuthenticationService(openid_manager=mock_openid_manager)
    
    @staticmethod
    def create_auth_context(
        tenant_id: str = "test-tenant-id",
        has_subject_token: bool = True
    ) -> AuthorizationContext:
        """Create an AuthorizationContext for testing."""
        return AuthorizationContext(
            original_subject_token="mock-subject-token" if has_subject_token else None,
            tenant_object_id=tenant_id,
            claims=[
                Claim(type="oid", value="test-object-id"),
                Claim(type="tid", value=tenant_id),
                Claim(type="scp", value="FabricWorkloadControl")
            ]
        )
    
    @staticmethod
    def create_subject_claims(
        tenant_id: str = "test-tenant-id",
        scopes: str = "FabricWorkloadControl",
        app_id: str = EnvironmentConstants.FABRIC_BACKEND_APP_ID
    ) -> List[Claim]:
        """Create subject token claims."""
        return [
            Claim(type="tid", value=tenant_id),
            Claim(type="oid", value="test-object-id"),
            Claim(type="scp", value=scopes),
            Claim(type="ver", value="2.0"),
            Claim(type="azp", value=app_id)
        ]
    
    @staticmethod
    def create_app_claims(
        tenant_id: str = "publisher-tenant-id",
        app_id: str = EnvironmentConstants.FABRIC_BACKEND_APP_ID
    ) -> List[Claim]:
        """Create app token claims."""
        return [
            Claim(type="tid", value=tenant_id),
            Claim(type="oid", value="service-principal-id"),
            Claim(type="idtyp", value="app"),
            Claim(type="ver", value="2.0"),
            Claim(type="azp", value=app_id)
        ]
    
    @staticmethod
    def create_claims_from_payload(payload: Dict[str, Any]) -> List[Claim]:
        """Convert JWT payload to claims list."""
        claims = []
        for key, value in payload.items():
            claims.append(Claim(type=key, value=value))
        return claims