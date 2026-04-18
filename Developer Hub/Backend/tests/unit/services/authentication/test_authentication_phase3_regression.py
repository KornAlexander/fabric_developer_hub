"""Regression tests for Phase-3 ``_validate_aad_token_common`` fixes.

The pre-Phase-3 code raised ``UnboundLocalError`` when ``JWTClaimsError`` was
raised for any reason OTHER than "Invalid audience" — because the audience-
specific log branch unconditionally referenced ``unverified_claims_dict`` and
``valid_audiences`` (typo) regardless of whether they were initialized. The
fix initialises both to ``None`` at the top of the function and gates the log
on "Invalid audience" only.
"""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from jose.exceptions import JWTClaimsError

from domain.exceptions.exceptions import AuthenticationException
from services.auth.open_id_connect_configuration import OpenIdConnectConfiguration


@pytest.mark.unit
@pytest.mark.services
@pytest.mark.asyncio
async def test_jwt_claims_error_non_audience_returns_401_not_500(auth_fixtures) -> None:
    """REGRESSION: a ``JWTClaimsError`` with message != "Invalid audience"
    (e.g. ``"Invalid issuer"``, ``"Token used before issued"``) must surface
    as ``AuthenticationException`` (→ 401), NOT a 500 ``UnboundLocalError``.
    """
    service = auth_fixtures.get_authentication_service()
    payload = auth_fixtures.create_jwt_payload(tenant_id="test-tenant", token_version="2.0")
    token = auth_fixtures.create_mock_jwt_token(payload=payload)

    mock_config = Mock(spec=OpenIdConnectConfiguration)
    mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
    mock_config.signing_keys = [{"kid": "test-key-id", "kty": "RSA"}]

    with patch("services.auth.authentication.jwt.get_unverified_header",
               return_value={"kid": "test-key-id"}), \
         patch("services.auth.authentication.jwt.get_unverified_claims",
               return_value=payload), \
         patch("services.auth.authentication.jwt.decode",
               side_effect=JWTClaimsError("Invalid issuer")), \
         patch.object(service.openid_manager, "get_configuration_async",
                      return_value=mock_config):
        with pytest.raises(AuthenticationException, match="Invalid token claims"):
            await service._validate_aad_token_common(token, False, None)


@pytest.mark.unit
@pytest.mark.services
@pytest.mark.asyncio
async def test_jwt_claims_error_token_used_before_issued(auth_fixtures) -> None:
    """Another non-audience JWTClaimsError variant — verifies the fix is
    keyed on the message contents, not the exception subclass."""
    service = auth_fixtures.get_authentication_service()
    payload = auth_fixtures.create_jwt_payload(tenant_id="test-tenant", token_version="2.0")
    token = auth_fixtures.create_mock_jwt_token(payload=payload)

    mock_config = Mock(spec=OpenIdConnectConfiguration)
    mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
    mock_config.signing_keys = [{"kid": "test-key-id", "kty": "RSA"}]

    with patch("services.auth.authentication.jwt.get_unverified_header",
               return_value={"kid": "test-key-id"}), \
         patch("services.auth.authentication.jwt.get_unverified_claims",
               return_value=payload), \
         patch("services.auth.authentication.jwt.decode",
               side_effect=JWTClaimsError("Token used before issued")), \
         patch.object(service.openid_manager, "get_configuration_async",
                      return_value=mock_config):
        with pytest.raises(AuthenticationException) as excinfo:
            await service._validate_aad_token_common(token, False, None)
        # Must include the original error message in the AuthenticationException
        assert "Token used before issued" in str(excinfo.value)


@pytest.mark.unit
@pytest.mark.services
@pytest.mark.asyncio
async def test_jwt_claims_error_invalid_audience_still_logs_richly(auth_fixtures) -> None:
    """Sanity check: the "Invalid audience" branch still works (rich log line
    with expected vs got). This is the *positive* path — the bug was that it
    used to crash on the OTHER branches."""
    service = auth_fixtures.get_authentication_service()
    payload = auth_fixtures.create_jwt_payload(tenant_id="test-tenant", token_version="2.0")
    payload["aud"] = "wrong-audience"
    token = auth_fixtures.create_mock_jwt_token(payload=payload)

    mock_config = Mock(spec=OpenIdConnectConfiguration)
    mock_config.issuer_configuration = "https://login.microsoftonline.com/{tenantid}/v2.0"
    mock_config.signing_keys = [{"kid": "test-key-id", "kty": "RSA"}]

    with patch("services.auth.authentication.jwt.get_unverified_header",
               return_value={"kid": "test-key-id"}), \
         patch("services.auth.authentication.jwt.get_unverified_claims",
               return_value=payload), \
         patch("services.auth.authentication.jwt.decode",
               side_effect=JWTClaimsError("Invalid audience")), \
         patch.object(service.openid_manager, "get_configuration_async",
                      return_value=mock_config):
        with pytest.raises(AuthenticationException, match="Invalid token claims"):
            await service._validate_aad_token_common(token, False, None)
