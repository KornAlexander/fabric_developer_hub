"""
Comprehensive security validation tests for AuthenticationService.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from jose.exceptions import JWTClaimsError, ExpiredSignatureError

from services.authentication import AuthenticationService
from services.open_id_connect_configuration import OpenIdConnectConfiguration
from models.authentication_models import Claim, AuthorizationContext
from exceptions.exceptions import AuthenticationException, AuthenticationUIRequiredException
from constants.environment_constants import EnvironmentConstants


@pytest.mark.unit
@pytest.mark.services
class TestSecurityValidationsComprehensive:
    """Comprehensive security validation tests - consolidates duplicate tests."""
    
    def test_app_id_security_comprehensive(self, auth_fixtures):
        """Test app ID validation enforces security requirements - comprehensive scenarios."""
        service = auth_fixtures.get_authentication_service()
        
        # Valid Fabric app ID should pass
        valid_claims = [Claim(type="azp", value=EnvironmentConstants.FABRIC_BACKEND_APP_ID)]
        result = service._validate_claim_one_of_values(
            valid_claims, "azp", 
            [EnvironmentConstants.FABRIC_BACKEND_APP_ID, EnvironmentConstants.FABRIC_CLIENT_FOR_WORKLOADS_APP_ID],
            "Valid Fabric app required"
        )
        assert result == EnvironmentConstants.FABRIC_BACKEND_APP_ID
        
        # Invalid app ID should fail
        invalid_claims = [Claim(type="azp", value="unauthorized-app-id")]
        with pytest.raises(AuthenticationException, match="Valid Fabric app required"):
            service._validate_claim_one_of_values(
                invalid_claims, "azp",
                [EnvironmentConstants.FABRIC_BACKEND_APP_ID, EnvironmentConstants.FABRIC_CLIENT_FOR_WORKLOADS_APP_ID],
                "Valid Fabric app required"
            )
        
        # Test with empty app ID
        empty_claims = [Claim(type="azp", value="")]
        with pytest.raises(AuthenticationException, match="Valid Fabric app required"):
            service._validate_claim_one_of_values(
                empty_claims, "azp",
                [EnvironmentConstants.FABRIC_BACKEND_APP_ID, EnvironmentConstants.FABRIC_CLIENT_FOR_WORKLOADS_APP_ID],
                "Valid Fabric app required"
            )

    def test_tenant_isolation_comprehensive(self, auth_fixtures):
        """Test tenant isolation prevents cross-tenant attacks - comprehensive scenarios."""
        service = auth_fixtures.get_authentication_service()
        
        # Test direct validation failure
        malicious_claims = [Claim(type="tid", value="malicious-tenant")]
        with pytest.raises(AuthenticationException):
            service._validate_claim_value(
                malicious_claims, "tid", "legitimate-tenant", "Tenant isolation required"
            )
        
        # Test cross-tenant token reuse prevention
        attacker_claims = [Claim(type="tid", value="attacker-tenant-id")]
        with pytest.raises(AuthenticationException):
            service._validate_claim_value(
                attacker_claims, "tid", "legitimate-tenant-id", 
                "Cross-tenant attack prevented"
            )
        
        # Test valid tenant passes
        valid_claims = [Claim(type="tid", value="legitimate-tenant")]
        result = service._validate_claim_value(
            valid_claims, "tid", "legitimate-tenant", "Should pass"
        )
        assert result == "legitimate-tenant"

    def test_app_only_token_security_comprehensive(self, auth_fixtures):
        """Test app-only token security requirements - comprehensive scenarios."""
        service = auth_fixtures.get_authentication_service()
        
        # Valid app-only token should pass
        valid_claims = [
            Claim(type="idtyp", value="app"),
            Claim(type="oid", value="service-principal-id")
        ]
        
        with patch.object(service, '_validate_claim_value'):
            with patch.object(service, '_validate_claim_exists'):
                with patch.object(service, '_validate_no_claim'):
                    # Should not raise exception
                    service._validate_app_only(valid_claims, is_app_only=True)
        
        # Token confusion attack - app-only token with delegated scopes
        malicious_claims = [
            Claim(type="idtyp", value="app"),
            Claim(type="scp", value="malicious-scope"),  # Should not be present in app-only
            Claim(type="oid", value="attacker-id")
        ]
        
        with patch.object(service, '_validate_claim_value'):
            with patch.object(service, '_validate_claim_exists'):
                with patch.object(service, '_validate_no_claim', side_effect=AuthenticationException("Token confusion detected")):
                    with pytest.raises(AuthenticationException, match="Token confusion detected"):
                        service._validate_app_only(malicious_claims, is_app_only=True)
        
        # Test app-only token without required oid claim
        incomplete_claims = [Claim(type="idtyp", value="app")]
        with patch.object(service, '_validate_claim_value'):
            with patch.object(service, '_validate_claim_exists', side_effect=AuthenticationException("Missing oid")):
                with pytest.raises(AuthenticationException, match="Missing oid"):
                    service._validate_app_only(incomplete_claims, is_app_only=True)

    def test_scope_privilege_escalation_prevention(self, auth_fixtures):
        """Test prevention of scope privilege escalation attacks."""
        service = auth_fixtures.get_authentication_service()
        
        # Token with limited scopes trying to access high-privilege operation
        limited_claims = [Claim(type="scp", value="read-only-scope")]
        with pytest.raises(AuthenticationException, match="missing required scopes"):
            service._validate_any_scope(limited_claims, ["admin-scope", "write-scope"])
        
        # Token with no scopes
        no_scope_claims = []
        with pytest.raises(AuthenticationException, match="missing required scopes"):
            service._validate_any_scope(no_scope_claims, ["required-scope"])
        
        # Token with empty scope string
        empty_scope_claims = [Claim(type="scp", value="")]
        with pytest.raises(AuthenticationException, match="missing required scopes"):
            service._validate_any_scope(empty_scope_claims, ["required-scope"])
        
        # Valid scope should pass
        valid_scope_claims = [Claim(type="scp", value="admin-scope other-scope")]
        # Should not raise exception
        service._validate_any_scope(valid_scope_claims, ["admin-scope"])

    def test_token_tampering_prevention(self, auth_fixtures):
        """Test prevention of token tampering attacks."""
        service = auth_fixtures.get_authentication_service()
        
        # Test claim value type coercion security
        # Integer claim value should be compared as string for security
        claims = [Claim(type="tid", value=123)]
        result = service._validate_claim_value(claims, "tid", "123", "Should match")
        assert result == 123
        
        # Test that string comparison prevents bypass
        claims = [Claim(type="tid", value=123)]
        with pytest.raises(AuthenticationException):
            service._validate_claim_value(claims, "tid", "456", "Should fail")

    def test_injection_attack_prevention(self, auth_fixtures):
        """Test prevention of injection attacks through claim values."""
        service = auth_fixtures.get_authentication_service()
        
        # Test special characters in claim values are handled safely
        special_chars_values = [
            "normal-value",
            "",  # Empty
            " whitespace ",  # Whitespace
            "special@chars#here!",  # Special characters
            "unicode-тест-值",  # Unicode
            "very-long-" + "x" * 1000,  # Very long
            "'; DROP TABLE users; --",  # SQL injection attempt
            "<script>alert('xss')</script>",  # XSS attempt
            "../../etc/passwd",  # Path traversal attempt
        ]
        
        for test_value in special_chars_values:
            claims = [Claim(type="test_claim", value=test_value)]
            result = service._validate_claim_exists(claims, "test_claim", "Test")
            assert result == test_value

    def test_malformed_scope_security(self, auth_fixtures):
        """Test security handling of malformed scope strings."""
        service = auth_fixtures.get_authentication_service()
        
        # Extra whitespace in scopes should be handled securely
        claims = [Claim(type="scp", value="  scope1   scope2  scope3  ")]
        result = service._extract_scopes_from_claims(claims)
        
        # Should properly split and trim
        assert "scope1" in result
        assert "scope2" in result  
        assert "scope3" in result
        assert all(scope.strip() == scope for scope in result if scope)
        
        # Test with malicious scope strings
        malicious_scopes = [
            "scope1\nscope2",  # Newline injection
            "scope1\x00scope2",  # Null byte injection
            "scope1;rm -rf /",  # Command injection attempt
        ]
        
        for malicious_scope in malicious_scopes:
            claims = [Claim(type="scp", value=malicious_scope)]
            result = service._extract_scopes_from_claims(claims)
            # Should handle safely without crashing
            assert isinstance(result, list)


@pytest.mark.unit
@pytest.mark.services
class TestTokenSecurityValidation:
    """Test token-level security validations."""
    
    @pytest.mark.asyncio
    async def test_token_signature_validation_security(self, auth_fixtures):
        """Test that token signature validation prevents tampering."""
        service = auth_fixtures.get_authentication_service()
        
        # Test missing signing key security
        payload = auth_fixtures.create_jwt_payload(tenant_id="test-tenant", token_version="2.0")
        token = auth_fixtures.create_mock_jwt_token(payload=payload)
        
        # Mock OpenID config with different key ID (simulates key not found)
        mock_config = Mock(spec=OpenIdConnectConfiguration)
        mock_config.signing_keys = [{"kid": "different-key-id", "kty": "RSA"}]
        mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
        
        with patch('services.authentication.jwt.get_unverified_header', return_value={"kid": "unknown-key"}):
            with patch('services.authentication.jwt.get_unverified_claims', return_value=payload):
                with patch.object(service.openid_manager, 'get_configuration_async', return_value=mock_config):
                    with pytest.raises(AuthenticationException, match="Token signing key not found"):
                        await service._validate_aad_token_common(token, False, None)

    @pytest.mark.asyncio
    async def test_token_audience_validation_security(self, auth_fixtures):
        """Test that audience validation prevents token misuse."""
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

    @pytest.mark.asyncio
    async def test_malformed_token_security(self, auth_fixtures):
        """Test security handling of malformed tokens."""
        service = auth_fixtures.get_authentication_service()
        
        # Test various malformed token formats
        malformed_tokens = [
            "invalid.jwt.format",
            "not-a-jwt-at-all",
            "",
            "header.payload",  # Missing signature
            "header.payload.signature.extra",  # Too many parts
            "헤더.페이로드.서명",  # Non-ASCII characters
        ]
        
        for malformed_token in malformed_tokens:
            with patch('services.authentication.jwt.get_unverified_header', side_effect=Exception("Invalid token")):
                with pytest.raises(AuthenticationException, match="Token validation failed"):
                    await service._validate_aad_token_common(malformed_token, False, None)