"""Regression tests for ``services.auth.authorization``.

Locks the Phase-3 fix where ``_resolve_item_permissions`` was catching
``TooManyRequestsException`` and ``UnauthorizedException`` inside the
catch-all ``except Exception`` and re-raising them as 500 ``InternalErrorException``,
masking the original 429/401 semantics. The fix added an explicit
``except (TooManyRequestsException, UnauthorizedException): raise`` ahead of
the catch-all.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from domain.exceptions.exceptions import (
    InternalErrorException,
    TooManyRequestsException,
    UnauthorizedException,
)
from services.auth.authorization import AuthorizationHandler


WS_ID = UUID("11111111-1111-1111-1111-111111111111")
ITEM_ID = UUID("22222222-2222-2222-2222-222222222222")


def _make_response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_body or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.fixture
def handler(monkeypatch: pytest.MonkeyPatch) -> AuthorizationHandler:
    h = AuthorizationHandler()
    # Bypass the lazy-loaded auth_service entirely (we only test _resolve_item_permissions)
    h._auth_service = MagicMock()
    return h


@pytest.mark.asyncio
async def test_resolve_429_raises_too_many_requests_not_500(
    handler: AuthorizationHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION: a 429 from the resolve-permissions API must surface as
    ``TooManyRequestsException`` — NOT as ``InternalErrorException``."""
    fake_http = AsyncMock()
    fake_http.get.return_value = _make_response(429, text="throttled")
    monkeypatch.setattr(
        "services.auth.authorization.get_http_client_service", lambda: fake_http,
    )

    with pytest.raises(TooManyRequestsException):
        await handler._resolve_item_permissions("tok", WS_ID, ITEM_ID)


@pytest.mark.asyncio
async def test_resolve_401_raises_unauthorized_not_500(
    handler: AuthorizationHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION: a 401 must surface as ``UnauthorizedException`` — NOT 500."""
    fake_http = AsyncMock()
    fake_http.get.return_value = _make_response(401, text="denied")
    monkeypatch.setattr(
        "services.auth.authorization.get_http_client_service", lambda: fake_http,
    )

    with pytest.raises(UnauthorizedException):
        await handler._resolve_item_permissions("tok", WS_ID, ITEM_ID)


@pytest.mark.asyncio
async def test_resolve_403_raises_unauthorized_not_500(
    handler: AuthorizationHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_http = AsyncMock()
    fake_http.get.return_value = _make_response(403, text="forbidden")
    monkeypatch.setattr(
        "services.auth.authorization.get_http_client_service", lambda: fake_http,
    )

    with pytest.raises(UnauthorizedException):
        await handler._resolve_item_permissions("tok", WS_ID, ITEM_ID)


@pytest.mark.asyncio
async def test_resolve_500_from_upstream_becomes_internal_error(
    handler: AuthorizationHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5xx upstream → ``InternalErrorException`` (only path where the catch-all
    actually runs)."""
    fake_http = AsyncMock()
    fake_http.get.return_value = _make_response(500, text="upstream boom")
    monkeypatch.setattr(
        "services.auth.authorization.get_http_client_service", lambda: fake_http,
    )

    with pytest.raises(InternalErrorException):
        await handler._resolve_item_permissions("tok", WS_ID, ITEM_ID)


@pytest.mark.asyncio
async def test_resolve_unexpected_error_becomes_internal_error(
    handler: AuthorizationHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-HTTP error (e.g. JSON decoding) → ``InternalErrorException``."""
    fake_http = AsyncMock()
    fake_http.get.side_effect = RuntimeError("parsing blew up")
    monkeypatch.setattr(
        "services.auth.authorization.get_http_client_service", lambda: fake_http,
    )

    with pytest.raises(InternalErrorException):
        await handler._resolve_item_permissions("tok", WS_ID, ITEM_ID)


@pytest.mark.asyncio
async def test_resolve_200_returns_permissions(
    handler: AuthorizationHandler, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_http = AsyncMock()
    fake_http.get.return_value = _make_response(
        200, json_body={"permissions": ["Read", "Write"]},
    )
    monkeypatch.setattr(
        "services.auth.authorization.get_http_client_service", lambda: fake_http,
    )

    result = await handler._resolve_item_permissions("tok", WS_ID, ITEM_ID)
    assert result.permissions == ["Read", "Write"]


@pytest.mark.asyncio
async def test_validate_permissions_missing_perms_raises(
    handler: AuthorizationHandler, monkeypatch: pytest.MonkeyPatch, mock_auth_context,
) -> None:
    handler._auth_service = AsyncMock()
    handler._auth_service.build_composite_token.return_value = "tok"

    fake_http = AsyncMock()
    fake_http.get.return_value = _make_response(
        200, json_body={"permissions": ["Read"]},
    )
    monkeypatch.setattr(
        "services.auth.authorization.get_http_client_service", lambda: fake_http,
    )

    with pytest.raises(UnauthorizedException, match="required permissions"):
        await handler.validate_permissions(
            mock_auth_context, WS_ID, ITEM_ID, required_permissions=["Read", "Write"],
        )


@pytest.mark.asyncio
async def test_validate_permissions_case_insensitive_match(
    handler: AuthorizationHandler, monkeypatch: pytest.MonkeyPatch, mock_auth_context,
) -> None:
    handler._auth_service = AsyncMock()
    handler._auth_service.build_composite_token.return_value = "tok"

    fake_http = AsyncMock()
    fake_http.get.return_value = _make_response(
        200, json_body={"permissions": ["read", "write"]},
    )
    monkeypatch.setattr(
        "services.auth.authorization.get_http_client_service", lambda: fake_http,
    )

    # Should NOT raise — case-insensitive match should accept "Read"/"Write"
    await handler.validate_permissions(
        mock_auth_context, WS_ID, ITEM_ID, required_permissions=["Read", "Write"],
    )
