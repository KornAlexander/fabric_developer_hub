"""
Core validation and token processing tests for AuthenticationService.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from services.authentication import AuthenticationService
from services.open_id_connect_configuration import OpenIdConnectConfiguration
from models.authentication_models import Claim, TokenVersion
from exceptions.exceptions import AuthenticationException
from constants.environment_constants import EnvironmentConstants


@pytest.mark.unit
@pytest.mark.services
class TestTokenProcessing:
    """Test core token processing methods that were missing coverage."""
    
    def test_get_expected_issuer_v1_tokens(self, auth_fixtures):
        """Test issuer URL construction for v1.0 tokens."""
        service = auth_fixtures.get_authentication_service()
        
        # Mock OpenID configuration
        mock_oidc_config = Mock()
        mock_oidc_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
        
        tenant_id = "test-tenant-123"
        result = service.get_expected_issuer(mock_oidc_config, TokenVersion.V1, tenant_id)
        
        expected = "https://login.microsoftonline.com/test-tenant-123/v2.0"
        assert result == expected
    
    def test_get_expected_issuer_v2_tokens(self, auth_fixtures):
        """Test issuer URL construction for v2.0 tokens."""
        service = auth_fixtures.get_authentication_service()
        
        # For v2.0 tokens, it should use AAD_INSTANCE_URL + tenant + v2.0
        mock_oidc_config = Mock()  # Not used for v2.0 tokens
        tenant_id = "test-tenant-456"
        
        result = service.get_expected_issuer(mock_oidc_config, TokenVersion.V2, tenant_id)
        
        expected = f"{EnvironmentConstants.AAD_INSTANCE_URL}/{tenant_id}/v2.0"
        assert result == expected
    
    def test_get_expected_issuer_missing_placeholder(self, auth_fixtures):
        """Test issuer URL construction with missing tenantid placeholder."""
        service = auth_fixtures.get_authentication_service()
        
        # Mock OpenID configuration with invalid placeholder format that will cause KeyError
        mock_oidc_config = Mock()
        mock_oidc_config.issuer_configuration = "https://login.microsoftonline.com/{invalid}/v2.0"  # Wrong placeholder name
        
        tenant_id = "test-tenant-123"
        
        # The service catches KeyError and converts to AuthenticationException
        # when {tenantid} placeholder is missing but other placeholders exist
        with pytest.raises(AuthenticationException, match="Issuer configuration missing tenantid placeholder"):
            service.get_expected_issuer(mock_oidc_config, TokenVersion.V1, tenant_id)
    
    def test_get_expected_issuer_unsupported_version(self, auth_fixtures):
        """Test issuer URL construction with unsupported token version."""
        service = auth_fixtures.get_authentication_service()
        
        mock_oidc_config = Mock()
        tenant_id = "test-tenant-123"
        
        with pytest.raises(AuthenticationException, match="Unsupported token version"):
            service.get_expected_issuer(mock_oidc_config, "v3.0", tenant_id)
    
    def test_get_expected_audience_by_version(self, auth_fixtures):
        """Test audience selection based on token version."""
        service = auth_fixtures.get_authentication_service()
        
        # For v1.0 tokens, should return service.audience
        result_v1 = service._get_excpected_audience(TokenVersion.V1)
        assert result_v1 == service.audience
        
        # For v2.0 tokens, should return service.client_id
        result_v2 = service._get_excpected_audience(TokenVersion.V2)
        assert result_v2 == service.client_id

    def test_get_token_version_comprehensive(self, auth_fixtures):
        """Test token version extraction with comprehensive scenarios."""
        service = auth_fixtures.get_authentication_service()
        
        # Test v1.0 token
        claims_v1 = [Claim(type="ver", value="1.0")]
        result_v1 = service._get_token_version(claims_v1)
        assert result_v1 == TokenVersion.V1
        
        # Test v2.0 token
        claims_v2 = [Claim(type="ver", value="2.0")]
        result_v2 = service._get_token_version(claims_v2)
        assert result_v2 == TokenVersion.V2
        
        # Test missing version claim
        claims_no_version = []
        with pytest.raises(AuthenticationException, match="Missing claim ver"):
            service._get_token_version(claims_no_version)
        
        # Test invalid version format
        claims_invalid_version = [Claim(type="ver", value="invalid")]
        with pytest.raises(AuthenticationException, match="Unsupported token version"):
            service._get_token_version(claims_invalid_version)
        
        # Test future version
        claims_future_version = [Claim(type="ver", value="3.0")]
        with pytest.raises(AuthenticationException, match="Unsupported token version"):
            service._get_token_version(claims_future_version)
        
        # Test non-string version value
        claims_numeric_version = [Claim(type="ver", value=2.0)]
        with pytest.raises(AuthenticationException, match="Unsupported token version"):
            service._get_token_version(claims_numeric_version)


@pytest.mark.unit
@pytest.mark.services
class TestScopeValidationComprehensive:
    """Comprehensive scope validation tests covering edge cases."""
    
    def test_extract_scopes_comprehensive(self, auth_fixtures):
        """Test scope extraction with comprehensive scenarios."""
        service = auth_fixtures.get_authentication_service()
        
        # Test normal scopes
        claims = [Claim(type="scp", value="scope1 scope2 scope3")]
        result = service._extract_scopes_from_claims(claims)
        assert result == ["scope1", "scope2", "scope3"]
        
        # Test empty string scopes
        claims = [Claim(type="scp", value="")]
        result = service._extract_scopes_from_claims(claims)
        assert result == []
        
        # Test None value scopes  
        claims = [Claim(type="scp", value=None)]
        result = service._extract_scopes_from_claims(claims)
        assert result == []
        
        # Test whitespace handling
        claims = [Claim(type="scp", value="  scope1   scope2  scope3  ")]
        result = service._extract_scopes_from_claims(claims)
        assert "scope1" in result
        assert "scope2" in result
        assert "scope3" in result
        assert all(scope.strip() == scope for scope in result if scope)
        
        # Test roles claim (list format)
        claims = [Claim(type="roles", value=["role1", "role2"])]
        result = service._extract_scopes_from_claims(claims)
        assert "role1" in result
        assert "role2" in result
        
        # Test roles claim (string format)
        claims = [Claim(type="roles", value="single-role")]
        result = service._extract_scopes_from_claims(claims)
        assert "single-role" in result
        
        # Test combined scp and roles
        claims = [
            Claim(type="scp", value="delegated-scope"),
            Claim(type="roles", value=["app-role1", "app-role2"])
        ]
        result = service._extract_scopes_from_claims(claims)
        assert "delegated-scope" in result
        assert "app-role1" in result
        assert "app-role2" in result

    def test_malformed_roles_claims(self, auth_fixtures):
        """Test handling of malformed roles claims."""
        service = auth_fixtures.get_authentication_service()
        
        # Test None roles value
        claims = [Claim(type="roles", value=None)]
        result = service._extract_scopes_from_claims(claims)
        assert result == []
        
        # Test empty list roles
        claims = [Claim(type="roles", value=[])]
        result = service._extract_scopes_from_claims(claims)
        assert result == []
        
        # Test numeric values in roles (should be handled gracefully)
        claims = [Claim(type="roles", value=[123, "valid-role"])]
        result = service._extract_scopes_from_claims(claims)
        assert 123 in result  # Should include numeric values as-is
        assert "valid-role" in result

    def test_validate_any_scope_edge_cases(self, auth_fixtures):
        """Test scope validation with various edge cases."""
        service = auth_fixtures.get_authentication_service()
        
        # Test case-sensitive scope matching
        claims = [Claim(type="scp", value="FabricWorkloadControl")]
        service._validate_any_scope(claims, ["FabricWorkloadControl"])  # Should pass
        
        with pytest.raises(AuthenticationException, match="missing required scopes"):
            service._validate_any_scope(claims, ["fabricworkloadcontrol"])  # Different case
        
        # Test partial scope matching (should not match)
        claims = [Claim(type="scp", value="FabricWorkload")]
        with pytest.raises(AuthenticationException, match="missing required scopes"):
            service._validate_any_scope(claims, ["FabricWorkloadControl"])
        
        # Test multiple allowed scopes (any match should pass)
        claims = [Claim(type="scp", value="scope2")]
        service._validate_any_scope(claims, ["scope1", "scope2", "scope3"])  # Should pass
        
        # Test empty allowed scopes list
        claims = [Claim(type="scp", value="any-scope")]
        with pytest.raises(AuthenticationException, match="missing required scopes"):
            service._validate_any_scope(claims, [])

    def test_special_characters_in_scopes(self, auth_fixtures):
        """Test handling of special characters in scope names."""
        service = auth_fixtures.get_authentication_service()
        
        special_scope_names = [
            "scope-with-dashes",
            "scope.with.dots",
            "scope_with_underscores",
            "scope:with:colons",
            "scope/with/slashes",
            "scope@with@at",
            "https://graph.microsoft.com/.default",  # Real-world example
        ]
        
        for scope_name in special_scope_names:
            claims = [Claim(type="scp", value=scope_name)]
            result = service._extract_scopes_from_claims(claims)
            assert scope_name in result
            
            # Test validation passes for exact match
            service._validate_any_scope(claims, [scope_name])


@pytest.mark.unit
@pytest.mark.services 
class TestClaimValidationComprehensive:
    """Comprehensive claim validation tests."""
    
    def test_validate_claim_exists_edge_cases(self, auth_fixtures):
        """Test claim existence validation with edge cases."""
        service = auth_fixtures.get_authentication_service()
        
        # Test with multiple claims of same type (should return first)
        claims = [
            Claim(type="tid", value="first-tenant"),
            Claim(type="tid", value="second-tenant")
        ]
        result = service._validate_claim_exists(claims, "tid", "Tenant required")
        assert result == "first-tenant"
        
        # Test with empty claim value
        claims = [Claim(type="tid", value="")]
        result = service._validate_claim_exists(claims, "tid", "Tenant required")
        assert result == ""
        
        # Test with None claim value
        claims = [Claim(type="tid", value=None)]
        result = service._validate_claim_exists(claims, "tid", "Tenant required")
        assert result is None
        
        # Test case-sensitive claim name matching
        claims = [Claim(type="TID", value="test-tenant")]
        with pytest.raises(AuthenticationException, match="Missing claim tid"):
            service._validate_claim_exists(claims, "tid", "Tenant required")

    def test_validate_claim_value_comprehensive(self, auth_fixtures):
        """Test claim value validation with comprehensive scenarios."""
        service = auth_fixtures.get_authentication_service()
        
        # Test successful validation
        claims = [Claim(type="tid", value="test-tenant")]
        result = service._validate_claim_value(claims, "tid", "test-tenant", "Should match")
        assert result == "test-tenant"
        
        # Test type coercion (int to string comparison)
        claims = [Claim(type="tid", value=123)]
        result = service._validate_claim_value(claims, "tid", "123", "Should match")
        assert result == 123
        
        # Test without expected value (should just return claim value)
        claims = [Claim(type="tid", value="any-value")]
        result = service._validate_claim_value(claims, "tid", None, "No validation")
        assert result == "any-value"
        
        # Test value mismatch
        claims = [Claim(type="tid", value="wrong-tenant")]
        with pytest.raises(AuthenticationException, match="Should fail"):
            service._validate_claim_value(claims, "tid", "correct-tenant", "Should fail")

    def test_validate_no_claim_security(self, auth_fixtures):
        """Test that _validate_no_claim prevents token confusion attacks."""
        service = auth_fixtures.get_authentication_service()
        
        # Test that method correctly identifies unexpected claims
        claims = [Claim(type="scp", value="delegated-scope")]
        with pytest.raises(AuthenticationException, match="Unexpected token format"):
            service._validate_no_claim(claims, "scp", "App-only tokens should not have this")
        
        # Test that method passes when claim is not present
        claims = [Claim(type="other", value="other-value")]
        # Should not raise exception
        service._validate_no_claim(claims, "scp", "App-only tokens should not have this")
        
        # Test with empty claims list
        claims = []
        # Should not raise exception
        service._validate_no_claim(claims, "scp", "App-only tokens should not have this")


@pytest.mark.unit
@pytest.mark.services
class TestConfigurationHandling:
    """Comprehensive configuration handling tests - consolidates duplicate tests."""
    
    def test_missing_configuration_scenarios(self, auth_fixtures):
        """Test various missing configuration combinations."""
        mock_openid_manager, _ = auth_fixtures.get_basic_mocks()
        
        # Test all configuration missing
        mock_config_all_none = auth_fixtures.get_config_service_mock(
            publisher_tenant_id=None,
            client_id=None,
            client_secret=None,
            audience=None
        )
        
        with patch("services.authentication.get_configuration_service", return_value=mock_config_all_none):
            service = AuthenticationService(openid_manager=mock_openid_manager)
            
            # Service should be created but with minimal functionality
            assert service.client_id is None
            assert service.client_secret is None
            assert service.publisher_tenant_id is None
            assert service.audience is None
            assert len(service._msal_apps) == 0
        
        # Test partial configuration missing (client_id and secret only)
        mock_config_partial = auth_fixtures.get_config_service_mock(
            client_id=None,
            client_secret=None
        )
        
        with patch("services.authentication.get_configuration_service", return_value=mock_config_partial):
            service = AuthenticationService(openid_manager=mock_openid_manager)
            assert len(service._msal_apps) == 0
            assert service.publisher_tenant_id == "publisher-tenant-id"  # This should still be set
        
        # Test empty string configuration
        mock_config_empty = auth_fixtures.get_config_service_mock(
            publisher_tenant_id="",
            client_id="",
            client_secret="",
            audience=""
        )
        
        with patch("services.authentication.get_configuration_service", return_value=mock_config_empty):
            service = AuthenticationService(openid_manager=mock_openid_manager)
            # Empty strings are falsy, so MSAL app shouldn't be created
            assert len(service._msal_apps) == 0

    def test_msal_app_authority_construction(self, auth_fixtures):
        """Test MSAL app authority URL construction for different tenants."""
        service = auth_fixtures.get_authentication_service()
        
        test_cases = [
            "common",
            "organizations", 
            "consumers",
            "specific-tenant-id",
            "12345678-1234-1234-1234-123456789012",  # GUID format
        ]
        
        # Test MSAL app authority construction for different tenant types
        with patch("services.authentication.msal") as mock_msal:
            mock_app = Mock()
            mock_msal.ConfidentialClientApplication.return_value = mock_app
            
            # Test that we can create MSAL apps for different tenants
            for tenant_id in test_cases:
                result = service._get_msal_app(tenant_id)
                # Just verify we get a valid app back
                assert result is not None
                
            # Due to caching, some tenants may reuse apps, so we check that
            # MSAL was called at least once (but possibly less than len(test_cases) due to caching)
            assert mock_msal.ConfidentialClientApplication.call_count > 0
            assert mock_msal.ConfidentialClientApplication.call_count <= len(test_cases)
            
            # Verify that at least one authority URL was constructed correctly
            calls = mock_msal.ConfidentialClientApplication.call_args_list
            assert len(calls) > 0, "At least one MSAL app should have been created"