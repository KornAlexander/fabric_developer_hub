"""Security-regression tests for ``EndpointResolutionController``.

Locks the Phase-2 fix where the catch-all 500 path used to leak ``str(e)``
to the client.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from domain.exceptions.exceptions import AuthenticationException
from fabric_api.impl.endpoint_resolution_controller import (
    EndpointResolutionController,
)
from fabric_api.models.endpoint_resolution_context_property import (
    EndpointResolutionContextProperty,
)
from fabric_api.models.endpoint_resolution_request import (
    EndpointResolutionRequest,
)


def _make_request_obj() -> MagicMock:
    """Build a minimal Starlette-Request-like object."""
    req = MagicMock()
    req.url.scheme = "https"
    req.url.netloc = "fabric.example.com"
    return req


def _make_body() -> EndpointResolutionRequest:
    return EndpointResolutionRequest(
        context=[
            EndpointResolutionContextProperty(name="EndpointName", value="ep-1"),
        ],
    )


@pytest.mark.asyncio
async def test_resolve_400_when_body_is_none() -> None:
    ctrl = EndpointResolutionController(_make_request_obj())
    with pytest.raises(HTTPException) as excinfo:
        await ctrl.endpoint_resolution_resolve(
            "act-1", "req-1", "Bearer x", body=None,  # type: ignore[arg-type]
        )
    assert excinfo.value.status_code == 400
    assert "cannot be null" in excinfo.value.detail


@pytest.mark.asyncio
async def test_resolve_400_when_context_empty() -> None:
    ctrl = EndpointResolutionController(_make_request_obj())
    body = EndpointResolutionRequest(context=[])
    with pytest.raises(HTTPException) as excinfo:
        await ctrl.endpoint_resolution_resolve("act-1", "req-1", "Bearer x", body=body)
    assert excinfo.value.status_code == 400
    assert "missing or empty" in excinfo.value.detail


@pytest.mark.asyncio
async def test_resolve_401_on_authentication_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_auth = AsyncMock()
    fake_auth.authenticate_control_plane_call.side_effect = AuthenticationException(
        "token expired"
    )
    monkeypatch.setattr(
        "fabric_api.impl.endpoint_resolution_controller.get_authentication_service",
        lambda: fake_auth,
    )

    ctrl = EndpointResolutionController(_make_request_obj())
    with pytest.raises(HTTPException) as excinfo:
        await ctrl.endpoint_resolution_resolve(
            "act-1", "req-1", "Bearer x", body=_make_body(),
        )
    assert excinfo.value.status_code == 401
    # The AuthenticationException message IS exposed as the auth failure
    # detail (intentional — Fabric treats this as the user-visible reason).
    assert "token expired" in excinfo.value.detail


@pytest.mark.asyncio
async def test_resolve_500_does_not_leak_exception_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION: previously ``except Exception as e: raise HTTPException(500, str(e))``
    leaked SDK internals (file paths, stack frames) into the client response.
    The fix raises a fixed-string detail and logs the exception server-side.
    """
    secret_marker = "INTERNAL_LEAK_/var/lib/fabric/secret.json"
    fake_auth = AsyncMock()
    fake_auth.authenticate_control_plane_call.side_effect = RuntimeError(secret_marker)
    monkeypatch.setattr(
        "fabric_api.impl.endpoint_resolution_controller.get_authentication_service",
        lambda: fake_auth,
    )

    ctrl = EndpointResolutionController(_make_request_obj())
    with pytest.raises(HTTPException) as excinfo:
        await ctrl.endpoint_resolution_resolve(
            "act-1", "req-1", "Bearer x", body=_make_body(),
        )
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "Error resolving endpoint"
    assert secret_marker not in excinfo.value.detail


@pytest.mark.asyncio
async def test_resolve_ok_returns_workload_url(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_auth = AsyncMock()
    monkeypatch.setattr(
        "fabric_api.impl.endpoint_resolution_controller.get_authentication_service",
        lambda: fake_auth,
    )

    ctrl = EndpointResolutionController(_make_request_obj())
    response = await ctrl.endpoint_resolution_resolve(
        "act-1", "req-1", "Bearer x", body=_make_body(),
    )
    assert response.url == "https://fabric.example.com/workload"
    assert response.ttl_in_minutes == 60
