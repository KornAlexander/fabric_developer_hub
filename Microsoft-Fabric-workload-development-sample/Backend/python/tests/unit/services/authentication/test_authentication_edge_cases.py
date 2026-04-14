"""
Edge cases and error scenarios for AuthenticationService.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from jose.exceptions import JWTClaimsError, ExpiredSignatureError

from services.authentication import AuthenticationService
from services.open_id_connect_configuration import OpenIdConnectConfiguration
from models.authentication_models import Claim, AuthorizationContext
from exceptions.exceptions import AuthenticationException, AuthenticationUIRequiredException


@pytest.mark.unit
@pytest.mark.services
class TestTokenValidationEdgeCases:
    """Test token validation edge cases and error scenarios."""
    
    @pytest.mark.asyncio
    async def test_malformed_jwt_token(self, auth_fixtures):
        """Test handling of malformed JWT tokens."""
        service = auth_fixtures.get_authentication_service()
        malformed_token = "invalid.jwt.format"
        
        with patch('services.authentication.jwt.get_unverified_header', side_effect=Exception("Invalid token")):
            with pytest.raises(AuthenticationException, match="Token validation failed"):
                await service._validate_aad_token_common(malformed_token, False, None)
    
    @pytest.mark.asyncio
    async def test_token_missing_signing_key(self, auth_fixtures):
        """Test token validation when signing key is not found."""
        service = auth_fixtures.get_authentication_service()
        
        # Create token with proper payload structure
        payload = auth_fixtures.create_jwt_payload(tenant_id="test-tenant", token_version="2.0")
        token = auth_fixtures.create_mock_jwt_token(payload=payload)
        
        # Mock OpenID config with different key ID
        mock_config = Mock(spec=OpenIdConnectConfiguration)
        mock_config.signing_keys = [{"kid": "different-key-id", "kty": "RSA"}]
        mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
        
        with patch('services.authentication.jwt.get_unverified_header', return_value={"kid": "unknown-key"}):
            with patch('services.authentication.jwt.get_unverified_claims', return_value=payload):
                with patch.object(service.openid_manager, 'get_configuration_async', return_value=mock_config):
                    with pytest.raises(AuthenticationException, match="Token signing key not found"):
                        await service._validate_aad_token_common(token, False, None)
    
    @pytest.mark.asyncio
    async def test_token_invalid_audience(self, auth_fixtures):
        """Test token validation with invalid audience."""
        service = auth_fixtures.get_authentication_service()
        
        # Create token with proper payload structure
        payload = auth_fixtures.create_jwt_payload(tenant_id="test-tenant", token_version="2.0")
        token = auth_fixtures.create_mock_jwt_token(payload=payload)
        
        # Mock OpenID configuration with matching key
        mock_config = Mock(spec=OpenIdConnectConfiguration)
        mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
        mock_config.signing_keys = [{"kid": "test-key-id", "kty": "RSA"}]
        
        with patch('services.authentication.jwt.get_unverified_header', return_value={"kid": "test-key-id"}):
            with patch('services.authentication.jwt.get_unverified_claims', return_value=payload):
                with patch('services.authentication.jwt.decode', side_effect=JWTClaimsError("Invalid audience")):
                    with patch.object(service.openid_manager, 'get_configuration_async', return_value=mock_config):
                        with pytest.raises(AuthenticationException, match="Invalid token claims"):
                            await service._validate_aad_token_common(token, False, None)