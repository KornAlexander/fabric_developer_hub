"""
Core unit tests for AuthenticationService - consolidated essential tests.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from jose import jwt, JWTError
from jose.exceptions import JWTClaimsError, ExpiredSignatureError

from services.authentication import AuthenticationService, get_authentication_service
from services.open_id_connect_configuration import  OpenIdConnectConfiguration
from services.configuration_service import ConfigurationService
from models.authentication_models import Claim, AuthorizationContext, SubjectAndAppToken, TokenVersion
from exceptions.exceptions import AuthenticationException, AuthenticationUIRequiredException
from constants.environment_constants import EnvironmentConstants
from constants.workload_scopes import WorkloadScopes


@pytest.mark.unit
@pytest.mark.services
class TestAuthenticationServiceInitialization:
    """Test AuthenticationService initialization and configuration."""
    
    def test_init_with_valid_configuration(self, auth_fixtures):
        """Test successful initialization with valid configuration."""
        mock_openid_manager, mock_config_service = auth_fixtures.get_basic_mocks()
        
        with patch("services.authentication.get_configuration_service", return_value=mock_config_service):
            with patch("services.authentication.msal") as mock_msal:
                mock_app = Mock()
                mock_msal.ConfidentialClientApplication.return_value = mock_app
                
                service = AuthenticationService(openid_manager=mock_openid_manager)
                
                # Verify initialization
                assert service.openid_manager == mock_openid_manager
                assert service.publisher_tenant_id == "publisher-tenant-id"
                assert service.client_id == "test-client-id"
                
                # Verify MSAL app creation
                mock_msal.ConfidentialClientApplication.assert_called_once()


@pytest.mark.unit
@pytest.mark.services
class TestControlPlaneAuthentication:
    """Test control plane authentication flow."""
    
    @pytest.mark.asyncio
    async def test_authenticate_control_plane_success(self, auth_fixtures):
        """Test successful control plane authentication."""
        service = auth_fixtures.get_authentication_service()
        
        # Create valid tokens with matching app IDs
        subject_token = auth_fixtures.create_mock_jwt_token(
            scopes="FabricWorkloadControl",
            app_id=EnvironmentConstants.FABRIC_BACKEND_APP_ID
        )
        app_token = auth_fixtures.create_mock_jwt_token(
            id_typ="app",
            app_id=EnvironmentConstants.FABRIC_BACKEND_APP_ID,
            tenant_id="publisher-tenant-id"
        )
        auth_header = SubjectAndAppToken.generate_authorization_header_value(subject_token, app_token)
        
        # Mock validation methods
        subject_claims = auth_fixtures.create_subject_claims()
        app_claims = auth_fixtures.create_app_claims()
        
        with patch.object(service, '_validate_app_token', return_value=app_claims):
            with patch.object(service, '_validate_subject_token', return_value=subject_claims):
                result = await service.authenticate_control_plane_call(
                    auth_header=auth_header,
                    tenant_id="test-tenant-id"
                )
                
                assert isinstance(result, AuthorizationContext)
                assert result.original_subject_token == subject_token
                assert result.tenant_object_id == "test-tenant-id"
    
    @pytest.mark.asyncio
    async def test_authenticate_control_plane_missing_auth_header(self, auth_fixtures):
        """Test control plane authentication with missing authorization header."""
        service = auth_fixtures.get_authentication_service()
        
        with pytest.raises(AuthenticationException, match="Missing or invalid Authorization header"):
            await service.authenticate_control_plane_call(
                auth_header=None,
                tenant_id="test-tenant-id"
            )
    
    @pytest.mark.asyncio
    async def test_authenticate_control_plane_app_only_mode(self, auth_fixtures):
        """Test control plane authentication in app-only mode."""
        service = auth_fixtures.get_authentication_service()
        app_token = auth_fixtures.create_mock_jwt_token(
            id_typ="app",
            app_id=EnvironmentConstants.FABRIC_BACKEND_APP_ID,
            tenant_id="publisher-tenant-id"
        )
        auth_header = SubjectAndAppToken.generate_authorization_header_value(None, app_token)
        
        app_claims = auth_fixtures.create_app_claims()
        
        with patch.object(service, '_validate_app_token', return_value=app_claims):
            result = await service.authenticate_control_plane_call(
                auth_header=auth_header,
                tenant_id="test-tenant-id",
                require_subject_token=False
            )
            
            assert result.original_subject_token is None
            assert not result.has_subject_context


@pytest.mark.unit
@pytest.mark.services
class TestDataPlaneAuthentication:
    """Test data plane authentication flow."""
    
    @pytest.mark.asyncio
    async def test_authenticate_data_plane_success(self, auth_fixtures):
        """Test successful data plane authentication with Bearer token."""
        service = auth_fixtures.get_authentication_service()
        bearer_token = auth_fixtures.create_mock_jwt_token(scopes="Item1.ReadWrite.All")
        auth_header = f"Bearer {bearer_token}"
        
        with patch.object(service, '_authenticate_bearer') as mock_auth_bearer:
            mock_auth_bearer.return_value = AuthorizationContext(
                original_subject_token=bearer_token,
                tenant_object_id="test-tenant-id"
            )
            
            result = await service.authenticate_data_plane_call(
                auth_header=auth_header,
                allowed_scopes=["Item1.ReadWrite.All"]
            )
            
            assert isinstance(result, AuthorizationContext)
            mock_auth_bearer.assert_called_once_with(bearer_token, ["Item1.ReadWrite.All"])
    
    @pytest.mark.asyncio
    async def test_authenticate_data_plane_invalid_bearer(self, auth_fixtures):
        """Test data plane authentication with invalid Bearer token format."""
        service = auth_fixtures.get_authentication_service()
        
        with pytest.raises(AuthenticationException, match="Missing or invalid Authorization header"):
            await service.authenticate_data_plane_call(
                auth_header="Basic invalid-auth",  # Not Bearer
                allowed_scopes=["Item1.ReadWrite.All"]
            )


@pytest.mark.unit
@pytest.mark.services
class TestTokenValidation:
    """Test core token validation methods."""
    
    @pytest.mark.asyncio
    async def test_validate_aad_token_common_success(self, auth_fixtures):
        """Test successful AAD token validation."""
        service = auth_fixtures.get_authentication_service()
        
        # Create a realistic payload with all required claims
        payload = auth_fixtures.create_jwt_payload(
            tenant_id="test-tenant",
            token_version="2.0"
        )
        token = auth_fixtures.create_mock_jwt_token(payload=payload)
        
        # Mock OpenID configuration with matching key
        mock_config = Mock(spec=OpenIdConnectConfiguration)
        mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
        mock_config.signing_keys = [{"kid": "test-key-id", "kty": "RSA"}]
        
        # Mock JWT library calls with proper claims
        with patch('services.authentication.jwt.get_unverified_header', return_value={"kid": "test-key-id"}):
            with patch('services.authentication.jwt.get_unverified_claims', return_value=payload):
                with patch('services.authentication.jwt.decode', return_value=payload):
                    with patch.object(service.openid_manager, 'get_configuration_async', return_value=mock_config):
                        result = await service._validate_aad_token_common(token, False, None)
                        
                        assert isinstance(result, list)
                        assert all(isinstance(claim, Claim) for claim in result)
                        # Verify essential claims are present
                        claim_types = [claim.type for claim in result]
                        assert "tid" in claim_types
                        assert "ver" in claim_types
    
    @pytest.mark.asyncio
    async def test_validate_aad_token_expired(self, auth_fixtures):
        """Test token validation with expired token."""
        service = auth_fixtures.get_authentication_service()
        
        # Create expired token with proper claims structure
        payload = auth_fixtures.create_jwt_payload(
            tenant_id="test-tenant",
            token_version="2.0",
            exp_offset_minutes=-60  # Expired
        )
        expired_token = auth_fixtures.create_mock_jwt_token(payload=payload)
        
        # Mock OpenID configuration with matching key
        mock_config = Mock(spec=OpenIdConnectConfiguration)
        mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
        mock_config.signing_keys = [{"kid": "test-key-id", "kty": "RSA"}]
        
        with patch('services.authentication.jwt.get_unverified_header', return_value={"kid": "test-key-id"}):
            with patch('services.authentication.jwt.get_unverified_claims', return_value=payload):
                with patch('services.authentication.jwt.decode', side_effect=ExpiredSignatureError("Token expired")):
                    with patch.object(service.openid_manager, 'get_configuration_async', return_value=mock_config):
                        with pytest.raises(AuthenticationException, match="Token has expired"):
                            await service._validate_aad_token_common(expired_token, False, None)
    
    def test_validate_claim_exists_success(self, auth_fixtures):
        """Test successful claim existence validation."""
        service = auth_fixtures.get_authentication_service()
        claims = [Claim(type="tid", value="test-tenant-id")]
        
        result = service._validate_claim_exists(claims, "tid", "Tenant ID required")
        assert result == "test-tenant-id"
    
    def test_validate_claim_exists_missing(self, auth_fixtures):
        """Test claim validation with missing claim."""
        service = auth_fixtures.get_authentication_service()
        claims = []
        
        with pytest.raises(AuthenticationException, match="Missing claim tid"):
            service._validate_claim_exists(claims, "tid", "Tenant ID required")
    
    def test_validate_any_scope_success(self, auth_fixtures):
        """Test successful scope validation."""
        service = auth_fixtures.get_authentication_service()
        claims = [Claim(type="scp", value="FabricWorkloadControl other-scope")]
        
        # Should not raise exception
        service._validate_any_scope(claims, ["FabricWorkloadControl"])
    
    def test_validate_any_scope_failure(self, auth_fixtures):
        """Test scope validation failure."""
        service = auth_fixtures.get_authentication_service()
        claims = [Claim(type="scp", value="wrong-scope")]
        
        with pytest.raises(AuthenticationException, match="missing required scopes"):
            service._validate_any_scope(claims, ["FabricWorkloadControl"])


@pytest.mark.unit
@pytest.mark.services
class TestOBOFlow:
    """Test On-Behalf-Of flow."""
    
    @pytest.mark.asyncio
    async def test_get_access_token_on_behalf_of_success(self, auth_fixtures):
        """Test successful OBO token acquisition."""
        service = auth_fixtures.get_authentication_service()
        auth_context = auth_fixtures.create_auth_context()
        
        mock_app = Mock()
        mock_app.acquire_token_on_behalf_of.return_value = {"access_token": "obo-token"}
        
        with patch.object(service, '_get_msal_app', return_value=mock_app):
            result = await service.get_access_token_on_behalf_of(
                auth_context=auth_context,
                scopes=["https://graph.microsoft.com/.default"]
            )
            
            assert result == "obo-token"
    
    @pytest.mark.asyncio
    async def test_obo_flow_missing_subject_token(self, auth_fixtures):
        """Test OBO flow with missing original subject token."""
        service = auth_fixtures.get_authentication_service()
        auth_context = AuthorizationContext(original_subject_token=None)
        
        with pytest.raises(AuthenticationException, match="OBO flow requires an original subject token"):
            await service.get_access_token_on_behalf_of(
                auth_context=auth_context,
                scopes=["test-scope"]
            )
    
    @pytest.mark.asyncio
    async def test_obo_flow_ui_required(self, auth_fixtures):
        """Test OBO flow with UI required error."""
        service = auth_fixtures.get_authentication_service()
        auth_context = auth_fixtures.create_auth_context()
        
        mock_app = Mock()
        mock_app.acquire_token_on_behalf_of.return_value = {
            "error": "interaction_required",
            "error_description": "User interaction required",
            "claims": '{"access_token":{"polids":{"essential":true}}}'
        }
        
        with patch.object(service, '_get_msal_app', return_value=mock_app):
            with patch('services.authentication.AuthenticationUIRequiredException') as mock_ui_ex:
                mock_exception = Mock(spec=AuthenticationUIRequiredException)
                mock_ui_ex.return_value = mock_exception
                
                with pytest.raises(Exception):  # Will raise the mock exception
                    await service.get_access_token_on_behalf_of(
                        auth_context=auth_context,
                        scopes=["test-scope"]
                    )


@pytest.mark.unit
@pytest.mark.services
class TestS2SFlow:
    """Test Service-to-Service flow."""
    
    @pytest.mark.asyncio
    async def test_get_fabric_s2s_token_success(self, auth_fixtures):
        """Test successful S2S token acquisition."""
        service = auth_fixtures.get_authentication_service()
        
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "s2s-token"}
        
        with patch.object(service, '_get_msal_app', return_value=mock_app):
            result = await service.get_fabric_s2s_token()
            
            assert result == "s2s-token"
            
            # Verify correct scope was used
            expected_scopes = [f"{EnvironmentConstants.FABRIC_BACKEND_RESOURCE_ID}/.default"]
            mock_app.acquire_token_for_client.assert_called_once_with(scopes=expected_scopes)



@pytest.mark.unit
@pytest.mark.services
class TestCompositeTokenFlow:
    """Test composite token building."""
    
    @pytest.mark.asyncio
    async def test_build_composite_token_success(self, auth_fixtures):
        """Test successful composite token building."""
        service = auth_fixtures.get_authentication_service()
        auth_context = auth_fixtures.create_auth_context()
        
        # Use realistic JWT-like tokens for OBO and S2S
        obo_token = auth_fixtures.create_mock_jwt_token(
            scopes="https://graph.microsoft.com/.default",
            tenant_id="user-tenant"
        )
        s2s_token = auth_fixtures.create_mock_jwt_token(
            id_typ="app", 
            tenant_id="publisher-tenant-id"
        )
        
        with patch.object(service, 'get_access_token_on_behalf_of', return_value=obo_token):
            with patch.object(service, 'get_fabric_s2s_token', return_value=s2s_token):
                result = await service.build_composite_token(
                    auth_context=auth_context,
                    scopes=["test-scope"]
                )
                
                # Verify result format - should be a proper SubjectAndAppToken header
                assert result.startswith("SubjectAndAppToken1.0")
                assert obo_token in result
                assert s2s_token in result
                
                # Verify it can be parsed
                parsed = SubjectAndAppToken.parse(result)
                assert parsed.subject_token == obo_token
                assert parsed.app_token == s2s_token
    
    @pytest.mark.asyncio
    async def test_build_composite_token_obo_failure(self, auth_fixtures):
        """Test composite token building when OBO fails."""
        service = auth_fixtures.get_authentication_service()
        auth_context = auth_fixtures.create_auth_context()
        
        with patch.object(service, 'get_access_token_on_behalf_of', 
                         side_effect=AuthenticationException("OBO failed")):
            with pytest.raises(AuthenticationException, match="OBO failed"):
                await service.build_composite_token(
                    auth_context=auth_context,
                    scopes=["test-scope"]
                )
