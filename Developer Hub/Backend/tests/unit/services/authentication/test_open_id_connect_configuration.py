"""Tests for OpenIdConnectConfiguration manager (cache + fetch flow)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.auth.open_id_connect_configuration import (
    OpenIdConnectConfiguration,
    OpenIdConnectConfigurationManager,
    get_openid_manager_service,
)

pytestmark = [pytest.mark.unit, pytest.mark.services]


def _mk_response(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=status)
        )
    return resp


class _FakeAsyncClient:
    """Minimal async-context-manager stub for httpx.AsyncClient."""

    def __init__(self, responses: list[MagicMock]):
        self._responses = list(responses)
        self.get = AsyncMock(side_effect=self._responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_configuration_exposes_signing_keys():
    cfg = OpenIdConnectConfiguration(
        issuer="https://issuer.example",
        jwks_data={"keys": [{"kid": "abc"}]},
    )
    assert cfg.issuer_configuration == "https://issuer.example"
    assert cfg.signing_keys == [{"kid": "abc"}]


def test_configuration_handles_missing_keys():
    cfg = OpenIdConnectConfiguration(issuer="i", jwks_data={})
    assert cfg.signing_keys == []


@pytest.mark.asyncio
async def test_get_configuration_fetches_and_caches():
    mgr = OpenIdConnectConfigurationManager("https://meta.example/.well-known", cache_duration_seconds=3600)
    config_resp = _mk_response({"issuer": "https://i", "jwks_uri": "https://j"})
    jwks_resp = _mk_response({"keys": [{"kid": "k1"}]})

    fake = _FakeAsyncClient([config_resp, jwks_resp])
    with patch("services.auth.open_id_connect_configuration.httpx.AsyncClient", return_value=fake):
        cfg = await mgr.get_configuration_async()

    assert cfg.issuer_configuration == "https://i"
    assert cfg.signing_keys == [{"kid": "k1"}]
    assert mgr.configuration is cfg
    assert mgr.last_updated > 0

    # Second call within TTL must be a cache hit (no new HTTP calls)
    fake.get.reset_mock()
    cfg2 = await mgr.get_configuration_async()
    assert cfg2 is cfg
    fake.get.assert_not_called()


@pytest.mark.asyncio
async def test_get_configuration_raises_when_jwks_uri_missing_and_no_cache():
    mgr = OpenIdConnectConfigurationManager("https://meta.example/.well-known")
    config_resp = _mk_response({"issuer": "https://i"})  # no jwks_uri

    fake = _FakeAsyncClient([config_resp])
    with patch("services.auth.open_id_connect_configuration.httpx.AsyncClient", return_value=fake):
        with pytest.raises(ValueError, match="JWKS URI not found"):
            await mgr.get_configuration_async()


@pytest.mark.asyncio
async def test_get_configuration_returns_stale_cache_on_fetch_error():
    mgr = OpenIdConnectConfigurationManager("https://meta.example/.well-known", cache_duration_seconds=0)
    # Pre-seed a cached configuration.
    cached = OpenIdConnectConfiguration(issuer="https://cached", jwks_data={"keys": []})
    mgr.configuration = cached
    mgr.last_updated = 0  # force expiry

    fake = _FakeAsyncClient([])  # no responses
    fake.get = AsyncMock(side_effect=httpx.ConnectError("dns"))
    with patch("services.auth.open_id_connect_configuration.httpx.AsyncClient", return_value=fake):
        result = await mgr.get_configuration_async()

    assert result is cached  # falls back to stale cache


@pytest.mark.asyncio
async def test_get_openid_manager_service_is_singleton():
    OpenIdConnectConfigurationManager._instance = None
    try:
        a = await get_openid_manager_service()
        b = await get_openid_manager_service()
        assert a is b
        assert isinstance(a, OpenIdConnectConfigurationManager)
    finally:
        OpenIdConnectConfigurationManager._instance = None
