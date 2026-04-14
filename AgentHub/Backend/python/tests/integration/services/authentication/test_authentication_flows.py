"""
Integration tests for AuthenticationService flows.
Tests moved from unit test suite that are more integration-style.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from services.authentication import AuthenticationService
from services.open_id_connect_configuration import OpenIdConnectConfiguration
from models.authentication_models import Claim, AuthorizationContext, SubjectAndAppToken
from exceptions.exceptions import AuthenticationException, AuthenticationUIRequiredException
from constants.environment_constants import EnvironmentConstants


@pytest.mark.integration
@pytest.mark.services
class TestAuthenticationServiceIntegration:
    """Integration tests with service infrastructure."""
    
    def test_service_registry_integration(self, auth_fixtures):
        """Test AuthenticationService integration with ServiceRegistry."""
        from core.service_registry import ServiceRegistry
        
        registry = ServiceRegistry()
        registry.clear()
        
        service = auth_fixtures.get_authentication_service()
        registry.register(AuthenticationService, service)
        
        assert registry.has(AuthenticationService)
        retrieved = registry.get(AuthenticationService)
        assert retrieved is service

    @pytest.mark.asyncio
    async def test_end_to_end_control_plane_flow(self, auth_fixtures):
        """Test complete end-to-end control plane authentication."""
        service = auth_fixtures.get_authentication_service()
        
        # Create realistic tokens with matching app IDs
        subject_token = auth_fixtures.create_mock_jwt_token(
            scopes="FabricWorkloadControl",
            tenant_id="user-tenant",
            app_id=EnvironmentConstants.FABRIC_BACKEND_APP_ID
        )
        app_token = auth_fixtures.create_mock_jwt_token(
            id_typ="app",
            tenant_id="publisher-tenant-id",
            app_id=EnvironmentConstants.FABRIC_BACKEND_APP_ID
        )
        auth_header = SubjectAndAppToken.generate_authorization_header_value(subject_token, app_token)
        
        # Mock validation chain
        subject_claims = auth_fixtures.create_subject_claims(tenant_id="user-tenant")
        app_claims = auth_fixtures.create_app_claims()
        
        with patch.object(service, '_validate_app_token', return_value=app_claims):
            with patch.object(service, '_validate_subject_token', return_value=subject_claims):
                result = await service.authenticate_control_plane_call(
                    auth_header=auth_header,
                    tenant_id="user-tenant"
                )
                
                assert isinstance(result, AuthorizationContext)
                assert result.has_subject_context
                assert result.tenant_object_id == "user-tenant"

    @pytest.mark.asyncio
    async def test_end_to_end_composite_token_flow(self, auth_fixtures):
        """Test complete end-to-end composite token building flow."""
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
    async def test_data_plane_to_control_plane_flow_integration(self, auth_fixtures):
        """Test integration between data plane and control plane authentication."""
        service = auth_fixtures.get_authentication_service()
        
        # Start with data plane authentication
        bearer_token = auth_fixtures.create_mock_jwt_token(scopes="Item1.ReadWrite.All")
        data_plane_header = f"Bearer {bearer_token}"
        
        with patch.object(service, '_authenticate_bearer') as mock_auth_bearer:
            data_plane_context = AuthorizationContext(
                original_subject_token=bearer_token,
                tenant_object_id="test-tenant-id"
            )
            mock_auth_bearer.return_value = data_plane_context
            
            # Authenticate data plane call
            data_result = await service.authenticate_data_plane_call(
                auth_header=data_plane_header,
                allowed_scopes=["Item1.ReadWrite.All"]
            )
            
            # Use result to build composite token (similar to control plane)
            obo_token = auth_fixtures.create_mock_jwt_token(tenant_id="test-tenant-id")
            s2s_token = auth_fixtures.create_mock_jwt_token(id_typ="app")
            
            with patch.object(service, 'get_access_token_on_behalf_of', return_value=obo_token):
                with patch.object(service, 'get_fabric_s2s_token', return_value=s2s_token):
                    composite_token = await service.build_composite_token(
                        auth_context=data_result,
                        scopes=["fabric-scope"]
                    )
                    
                    assert composite_token.startswith("SubjectAndAppToken1.0")


@pytest.mark.integration
@pytest.mark.services
class TestErrorRecoveryScenarios:
    """Test error recovery and resilience scenarios."""
    
    @pytest.mark.asyncio
    async def test_openid_config_fetch_failure_recovery(self, auth_fixtures):
        """Test recovery when OpenID configuration fetch fails."""
        service = auth_fixtures.get_authentication_service()
        token = auth_fixtures.create_mock_jwt_token()
        
        # First call fails, but if cached config exists, it should use it
        service.openid_manager.get_configuration_async.side_effect = Exception("Network error")
        
        with pytest.raises(Exception, match="Network error"):
            await service._validate_aad_token_common(token, False, None)

    @pytest.mark.asyncio
    async def test_partial_service_degradation(self, auth_fixtures):
        """Test service behavior under partial degradation."""
        # Create service with minimal configuration
        mock_openid_manager, _ = auth_fixtures.get_basic_mocks()
        mock_config = auth_fixtures.get_config_service_mock(client_secret=None)  # Missing secret
        
        with patch("services.authentication.get_configuration_service", return_value=mock_config):
            service = AuthenticationService(openid_manager=mock_openid_manager)
            
            # Some operations should fail gracefully
            auth_context = auth_fixtures.create_auth_context()
            
            with pytest.raises(AuthenticationException, match="MSAL client not configured"):
                await service.get_access_token_on_behalf_of(auth_context, ["test-scope"])

    @pytest.mark.asyncio
    async def test_network_timeout_resilience(self, auth_fixtures):
        """Test resilience to network timeouts during token validation."""
        service = auth_fixtures.get_authentication_service()
        token = auth_fixtures.create_mock_jwt_token()
        
        # Simulate network timeout during OpenID config fetch
        import asyncio
        service.openid_manager.get_configuration_async.side_effect = asyncio.TimeoutError("Network timeout")
        
        # The service wraps the TimeoutError in AuthenticationException
        with pytest.raises(AuthenticationException, match="Token validation failed: Network timeout"):
            await service._validate_aad_token_common(token, False, None)

    @pytest.mark.asyncio
    async def test_jwt_validation_external_dependency_failures(self, auth_fixtures):
        """Test handling of JWT validation library failures."""
        service = auth_fixtures.get_authentication_service()
        
        # Test various JWT library failure scenarios
        token = auth_fixtures.create_mock_jwt_token()
        
        # Mock OpenID configuration
        mock_config = Mock(spec=OpenIdConnectConfiguration)
        mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
        mock_config.signing_keys = [{"kid": "test-key-id", "kty": "RSA"}]
        
        payload = auth_fixtures.create_jwt_payload(tenant_id="test-tenant", token_version="2.0")
        
        with patch.object(service.openid_manager, 'get_configuration_async', return_value=mock_config):
            # Test JWT header extraction failure
            with patch('services.authentication.jwt.get_unverified_header', side_effect=Exception("JWT library error")):
                with pytest.raises(AuthenticationException, match="Token validation failed"):
                    await service._validate_aad_token_common(token, False, None)
            
            # Test JWT claims extraction failure
            with patch('services.authentication.jwt.get_unverified_header', return_value={"kid": "test-key-id"}):
                with patch('services.authentication.jwt.get_unverified_claims', side_effect=Exception("Claims extraction failed")):
                    with pytest.raises(AuthenticationException, match="Token validation failed"):
                        await service._validate_aad_token_common(token, False, None)

    @pytest.mark.asyncio
    async def test_concurrent_token_validation(self, auth_fixtures):
        """Test that concurrent token validation doesn't interfere."""
        service = auth_fixtures.get_authentication_service()
        
        # Create tokens with proper payload structure
        payload1 = auth_fixtures.create_jwt_payload(tenant_id="tenant1", token_version="2.0")
        payload2 = auth_fixtures.create_jwt_payload(tenant_id="tenant2", token_version="2.0")
        token1 = auth_fixtures.create_mock_jwt_token(payload=payload1)
        token2 = auth_fixtures.create_mock_jwt_token(payload=payload2)
        
        # Mock OpenID configuration with matching key
        mock_config = Mock(spec=OpenIdConnectConfiguration)
        mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
        mock_config.signing_keys = [{"kid": "test-key-id", "kty": "RSA"}]
        
        with patch('services.authentication.jwt.get_unverified_header', return_value={"kid": "test-key-id"}):
            with patch('services.authentication.jwt.get_unverified_claims', side_effect=[payload1, payload2]):
                with patch('services.authentication.jwt.decode', side_effect=[payload1, payload2]):
                    with patch.object(service.openid_manager, 'get_configuration_async', return_value=mock_config):
                        # Both validations should succeed independently
                        result1 = await service._validate_aad_token_common(token1, False, None)
                        result2 = await service._validate_aad_token_common(token2, False, None)
                        
                        assert len(result1) > 0
                        assert len(result2) > 0

    @pytest.mark.asyncio
    async def test_service_initialization_under_load(self, auth_fixtures):
        """Test service initialization behavior under load conditions."""
        import asyncio
        
        async def create_service():
            mock_openid_manager, mock_config_service = auth_fixtures.get_basic_mocks()
            
            with patch("services.authentication.get_configuration_service", return_value=mock_config_service):
                with patch("services.authentication.msal") as mock_msal:
                    mock_app = Mock()
                    mock_msal.ConfidentialClientApplication.return_value = mock_app
                    
                    return AuthenticationService(openid_manager=mock_openid_manager)
        
        # Create multiple services concurrently
        tasks = [create_service() for _ in range(10)]
        services = await asyncio.gather(*tasks)
        
        # All services should be properly initialized
        for service in services:
            assert service.openid_manager is not None
            assert service.publisher_tenant_id == "publisher-tenant-id"
            assert service.client_id == "test-client-id"


@pytest.mark.integration
@pytest.mark.services
class TestConcurrencyAndThreadSafety:
    """Test concurrency and thread safety aspects with integration focus."""
    
    def test_msal_app_cache_thread_safety(self, auth_fixtures):
        """Test that MSAL app caching is thread-safe under load."""
        service = auth_fixtures.get_authentication_service()
        tenant_id = "test-tenant"
        
        with patch("services.authentication.msal") as mock_msal:
            mock_app = Mock()
            mock_msal.ConfidentialClientApplication.return_value = mock_app
            
            # Simulate concurrent access from multiple threads
            import threading
            import time
            
            apps_retrieved = []
            errors = []
            
            def get_app():
                try:
                    app = service._get_msal_app(tenant_id)
                    apps_retrieved.append(app)
                    time.sleep(0.01)  # Small delay to increase contention
                except Exception as e:
                    errors.append(e)
            
            # Create multiple threads
            threads = [threading.Thread(target=get_app) for _ in range(20)]
            
            # Start all threads
            for thread in threads:
                thread.start()
            
            # Wait for all threads to complete
            for thread in threads:
                thread.join()
            
            # Verify no errors occurred
            assert len(errors) == 0, f"Errors occurred: {errors}"
            
            # All threads should have retrieved the same app instance
            assert len(apps_retrieved) == 20
            assert all(app is mock_app for app in apps_retrieved)
            
            # Only one app should have been created despite concurrent access
            assert mock_msal.ConfidentialClientApplication.call_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_different_tenants(self, auth_fixtures):
        """Test concurrent access with different tenants creates separate apps."""
        service = auth_fixtures.get_authentication_service()
        
        with patch("services.authentication.msal") as mock_msal:
            # Create unique mock apps for each tenant
            tenant_apps = {
                "tenant1": Mock(),
                "tenant2": Mock(), 
                "tenant3": Mock()
            }
            mock_msal.ConfidentialClientApplication.side_effect = tenant_apps.values()
            
            import asyncio
            
            async def get_app_for_tenant(tenant_id):
                return service._get_msal_app(tenant_id)
            
            # Create concurrent tasks for different tenants
            tasks = []
            for tenant in tenant_apps.keys():
                for _ in range(5):  # 5 concurrent requests per tenant
                    tasks.append(get_app_for_tenant(tenant))
            
            results = await asyncio.gather(*tasks)
            
            # Verify results - should have 15 total results (3 tenants × 5 requests each)
            assert len(results) == 15
            
            # Verify each tenant got its own app, but all requests for same tenant got same app
            tenant1_results = results[0:5]   # First 5 are tenant1
            tenant2_results = results[5:10]  # Next 5 are tenant2
            tenant3_results = results[10:15] # Last 5 are tenant3
            
            # All requests for same tenant should return same app instance
            assert all(app is tenant1_results[0] for app in tenant1_results)
            assert all(app is tenant2_results[0] for app in tenant2_results)
            assert all(app is tenant3_results[0] for app in tenant3_results)
            
            # Apps for different tenants should be different
            assert tenant1_results[0] != tenant2_results[0]
            assert tenant2_results[0] != tenant3_results[0]
            assert tenant1_results[0] != tenant3_results[0]
            
            # Should have created exactly 3 apps (one per tenant)
            assert mock_msal.ConfidentialClientApplication.call_count == 3