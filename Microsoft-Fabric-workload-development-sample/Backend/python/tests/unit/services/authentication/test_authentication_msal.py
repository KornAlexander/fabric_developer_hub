"""
Parameterized MSAL error handling tests for AuthenticationService.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from services.authentication import AuthenticationService
from models.authentication_models import AuthorizationContext
from exceptions.exceptions import AuthenticationException, AuthenticationUIRequiredException
from constants.environment_constants import EnvironmentConstants


@pytest.mark.unit
@pytest.mark.services
class TestMSALErrorHandlingParameterized:
    """Parameterized MSAL error handling tests to eliminate duplication."""
    
    @pytest.mark.asyncio
    @pytest.mark.parametrize("error_scenario", [
        {
            "error_code": "invalid_client",
            "error_description": "Client authentication failed",
            "expected_message": "MSAL exception: invalid_client"
        },
        {
            "error_code": "invalid_scope",
            "error_description": "The provided scope is invalid",
            "expected_message": "MSAL exception: invalid_scope"
        },
        {
            "error_code": "unauthorized_client",
            "error_description": "The client is not authorized",
            "expected_message": "MSAL exception: unauthorized_client"
        },
        {
            "error_code": "access_denied",
            "error_description": "Access denied by authorization server",
            "expected_message": "MSAL exception: access_denied"
        },
        {
            "error_code": "server_error",
            "error_description": "Internal server error",
            "expected_message": "MSAL exception: server_error"
        }
    ])
    async def test_s2s_msal_errors_comprehensive(self, auth_fixtures, error_scenario):
        """Test S2S token acquisition with various MSAL error scenarios."""
        service = auth_fixtures.get_authentication_service()
        
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {
            "error": error_scenario["error_code"],
            "error_description": error_scenario["error_description"]
        }
        
        with patch.object(service, '_get_msal_app', return_value=mock_app):
            with pytest.raises(AuthenticationException, match=error_scenario["expected_message"]):
                await service.get_fabric_s2s_token()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exception_scenario", [
        {
            "exception": Exception("Service unavailable"),
            "expected_message": "OBO token acquisition failed: Service unavailable"
        },
        {
            "exception": ConnectionError("Network timeout"),
            "expected_message": "OBO token acquisition failed: Network timeout"
        },
        {
            "exception": ValueError("Invalid parameter"),
            "expected_message": "OBO token acquisition failed: Invalid parameter"
        }
    ])
    async def test_obo_msal_exceptions_comprehensive(self, auth_fixtures, exception_scenario):
        """Test OBO flow with various MSAL exception scenarios."""
        service = auth_fixtures.get_authentication_service()
        auth_context = auth_fixtures.create_auth_context()
        
        mock_app = Mock()
        mock_app.acquire_token_on_behalf_of.side_effect = exception_scenario["exception"]
        
        with patch.object(service, '_get_msal_app', return_value=mock_app):
            with pytest.raises(AuthenticationException, match=exception_scenario["expected_message"]):
                await service.get_access_token_on_behalf_of(auth_context, ["test-scope"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ui_required_scenario", [
        {
            "error_code": "interaction_required",
            "error_description": "User interaction required",
            "suberror": None,
            "claims": '{"access_token":{"polids":{"essential":true}}}',
            "test_description": "Basic interaction required"
        },
        {
            "error_code": "consent_required", 
            "error_description": "Admin consent required for application",
            "suberror": None,
            "claims": None,
            "test_description": "Admin consent required"
        },
        {
            "error_code": "interaction_required",
            "error_description": "Conditional access policy requires device compliance",
            "suberror": "conditional_access",
            "claims": '{"access_token":{"capolids":{"essential":true,"values":["device-compliance"]}}}',
            "test_description": "Conditional access policy"
        },
        {
            "error_code": "invalid_grant",
            "error_description": "Token has been revoked",
            "suberror": None,
            "claims": None,
            "test_description": "Token revoked scenario"
        }
    ])
    async def test_obo_ui_required_scenarios(self, auth_fixtures, ui_required_scenario):
        """Test OBO flow UI required scenarios with comprehensive error codes."""
        service = auth_fixtures.get_authentication_service()
        auth_context = auth_fixtures.create_auth_context()
        
        mock_app = Mock()
        msal_response = {
            "error": ui_required_scenario["error_code"],
            "error_description": ui_required_scenario["error_description"]
        }
        
        if ui_required_scenario["suberror"]:
            msal_response["suberror"] = ui_required_scenario["suberror"]
        
        if ui_required_scenario["claims"]:
            msal_response["claims"] = ui_required_scenario["claims"]
            
        mock_app.acquire_token_on_behalf_of.return_value = msal_response
        
        with patch.object(service, '_get_msal_app', return_value=mock_app):
            with patch('services.authentication.AuthenticationUIRequiredException') as mock_ui_ex:
                mock_exception = Mock(spec=AuthenticationUIRequiredException)
                mock_ui_ex.return_value = mock_exception
                
                with pytest.raises(Exception):  # Will raise the mock exception
                    await service.get_access_token_on_behalf_of(auth_context, ["test-scope"])
                
                # Verify the exception was created
                mock_ui_ex.assert_called_once_with(ui_required_scenario["error_description"])
                
                # Verify claims were added if present
                if ui_required_scenario["claims"]:
                    mock_exception.add_claims_for_conditional_access.assert_called_once_with(ui_required_scenario["claims"])
                
                # Verify scopes were added for consent scenarios
                if "consent_required" in ui_required_scenario["error_code"] or "consent_required" in ui_required_scenario["error_description"].lower():
                    mock_exception.add_scopes_to_consent.assert_called_once_with(["test-scope"])

    @pytest.mark.asyncio
    async def test_obo_missing_access_token_in_response(self, auth_fixtures):
        """Test OBO flow when MSAL returns success but no access token."""
        service = auth_fixtures.get_authentication_service()
        auth_context = auth_fixtures.create_auth_context()
        
        mock_app = Mock()
        # Simulate successful response but missing access_token field
        mock_app.acquire_token_on_behalf_of.return_value = {
            "token_type": "Bearer",
            "expires_in": 3600
            # Missing "access_token" field
        }
        
        with patch.object(service, '_get_msal_app', return_value=mock_app):
            with pytest.raises(AuthenticationException, match="Access token not found in OBO result"):
                await service.get_access_token_on_behalf_of(auth_context, ["test-scope"])

    @pytest.mark.asyncio
    async def test_obo_missing_subject_token_scenarios(self, auth_fixtures):
        """Test OBO flow error scenarios with missing subject tokens."""
        service = auth_fixtures.get_authentication_service()
        
        # Test with None original_subject_token
        auth_context_none = AuthorizationContext(original_subject_token=None)
        with pytest.raises(AuthenticationException, match="OBO flow requires an original subject token"):
            await service.get_access_token_on_behalf_of(auth_context_none, ["test-scope"])
        
        # Test with empty string original_subject_token
        auth_context_empty = AuthorizationContext(original_subject_token="")
        with pytest.raises(AuthenticationException, match="OBO flow requires an original subject token"):
            await service.get_access_token_on_behalf_of(auth_context_empty, ["test-scope"])

    @pytest.mark.asyncio
    async def test_obo_missing_tenant_context(self, auth_fixtures):
        """Test OBO flow with missing tenant context."""
        service = auth_fixtures.get_authentication_service()
        
        # Create auth context without tenant_object_id
        auth_context = AuthorizationContext(
            original_subject_token="valid-token",
            tenant_object_id=None
        )
        
        with pytest.raises(AuthenticationException, match="Cannot determine tenant authority for OBO flow"):
            await service.get_access_token_on_behalf_of(auth_context, ["test-scope"])

    @pytest.mark.asyncio
    async def test_msal_client_not_configured_scenarios(self, auth_fixtures):
        """Test scenarios where MSAL client is not properly configured."""
        mock_openid_manager, _ = auth_fixtures.get_basic_mocks()
        
        # Test with missing client_id
        mock_config_no_id = auth_fixtures.get_config_service_mock(
            client_id=None,
            client_secret="valid-secret"
        )
        
        with patch("services.authentication.get_configuration_service", return_value=mock_config_no_id):
            service = AuthenticationService(openid_manager=mock_openid_manager)
            auth_context = auth_fixtures.create_auth_context()
            
            with pytest.raises(AuthenticationException, match="MSAL client not configured"):
                await service.get_access_token_on_behalf_of(auth_context, ["test-scope"])
        
        # Test with missing client_secret
        mock_config_no_secret = auth_fixtures.get_config_service_mock(
            client_id="valid-id",
            client_secret=None
        )
        
        with patch("services.authentication.get_configuration_service", return_value=mock_config_no_secret):
            service = AuthenticationService(openid_manager=mock_openid_manager)
            auth_context = auth_fixtures.create_auth_context()
            
            with pytest.raises(AuthenticationException, match="MSAL client not configured"):
                await service.get_access_token_on_behalf_of(auth_context, ["test-scope"])


@pytest.mark.unit
@pytest.mark.services
class TestMSALAppManagement:
    """Test MSAL application management and caching."""
    
    def test_msal_app_caching_comprehensive(self, auth_fixtures):
        """Test MSAL app caching behavior comprehensively."""
        service = auth_fixtures.get_authentication_service()
        
        with patch("services.authentication.msal") as mock_msal:
            mock_app1 = Mock()
            mock_app2 = Mock()
            mock_msal.ConfidentialClientApplication.side_effect = [mock_app1, mock_app2]
            
            # Test same tenant returns cached app
            tenant1 = "tenant-1"
            result1a = service._get_msal_app(tenant1)
            result1b = service._get_msal_app(tenant1)
            
            assert result1a == mock_app1
            assert result1b == mock_app1  # Same instance
            assert result1a is result1b
            
            # Test different tenant creates new app
            tenant2 = "tenant-2"
            result2 = service._get_msal_app(tenant2)
            
            assert result2 == mock_app2
            assert result2 != result1a  # Different instances
            
            # Verify MSAL was called correctly
            assert mock_msal.ConfidentialClientApplication.call_count == 2
            
            # Verify authorities were constructed correctly
            calls = mock_msal.ConfidentialClientApplication.call_args_list
            assert f"{EnvironmentConstants.AAD_INSTANCE_URL}/{tenant1}" in str(calls[0])
            assert f"{EnvironmentConstants.AAD_INSTANCE_URL}/{tenant2}" in str(calls[1])

    def test_msal_app_cache_isolation(self, auth_fixtures):
        """Test that MSAL app cache properly isolates different tenants."""
        # Create a fresh service instance to avoid cached apps from other tests
        mock_openid_manager, mock_config_service = auth_fixtures.get_basic_mocks()
        
        with patch("services.authentication.get_configuration_service", return_value=mock_config_service):
            fresh_service = AuthenticationService(openid_manager=mock_openid_manager)
        
        test_tenants = ["tenant-a", "tenant-b", "tenant-c", "tenant-d"]
        
        with patch("services.authentication.msal") as mock_msal:
            # Create unique mock app for each tenant
            mock_apps = []
            for i, tenant in enumerate(test_tenants):
                mock_app = Mock()
                mock_app.tenant_id = tenant  # Add identifier for testing
                mock_apps.append(mock_app)
            
            mock_msal.ConfidentialClientApplication.side_effect = mock_apps
            
            # Get app for each tenant
            retrieved_apps = {}
            for tenant in test_tenants:
                retrieved_apps[tenant] = fresh_service._get_msal_app(tenant)
            
            # Each tenant should get a unique app (not cached across tenants)
            # Verify each tenant gets a different app
            app_ids = set(id(app) for app in retrieved_apps.values())
            assert len(app_ids) == len(test_tenants), f"Expected {len(test_tenants)} unique apps, got {len(app_ids)}"
            
            # Test caching: same tenant should get the same app on subsequent calls
            for tenant in test_tenants:
                cached_app = fresh_service._get_msal_app(tenant)
                assert cached_app is retrieved_apps[tenant], f"App for {tenant} should be cached"
                
            # Verify MSAL was called exactly once per unique tenant
            assert mock_msal.ConfidentialClientApplication.call_count == len(test_tenants), \
                f"Expected {len(test_tenants)} MSAL app creations, got {mock_msal.ConfidentialClientApplication.call_count}"
            

    @pytest.mark.asyncio
    async def test_concurrent_msal_app_access(self, auth_fixtures):
        """Test concurrent access to MSAL apps doesn't cause issues."""
        service = auth_fixtures.get_authentication_service()
        tenant_id = "concurrent-test-tenant"
        
        with patch("services.authentication.msal") as mock_msal:
            mock_app = Mock()
            mock_msal.ConfidentialClientApplication.return_value = mock_app
            
            # Simulate concurrent access
            import asyncio
            
            async def get_app():
                return service._get_msal_app(tenant_id)
            
            # Create multiple concurrent tasks
            tasks = [get_app() for _ in range(10)]
            results = await asyncio.gather(*tasks)
            
            # All should return the same app instance
            assert all(app is mock_app for app in results)
            
            # Should only create one app despite concurrent access
            assert mock_msal.ConfidentialClientApplication.call_count == 1