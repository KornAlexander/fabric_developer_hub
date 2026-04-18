"""Unit tests for ``app.exception_handlers`` and ``services.fabric.item_factory``."""
from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.exception_handlers import (
    authentication_ui_required_exception_handler,
    global_exception_handler,
    register_exception_handlers,
    too_many_requests_exception_handler,
    value_error_handler,
    workload_exception_handler,
)
from domain.constants.workload_constants import WorkloadConstants
from domain.exceptions.exceptions import (
    AuthenticationUIRequiredException,
    InternalErrorException,
    TooManyRequestsException,
    UnauthorizedException,
    UnexpectedItemTypeException,
)
from domain.items.agenthub_item import AgentHubItem
from services.fabric.item_factory import ItemFactory


# ─────────────────────────────────────────────────────────────
# ItemFactory
# ─────────────────────────────────────────────────────────────
def test_item_factory_creates_agenthub_item(mock_all_services, mock_auth_context) -> None:
    factory = ItemFactory()
    item = factory.create_item(
        WorkloadConstants.ItemTypes.AGENTHUB_ITEM, mock_auth_context
    )
    assert isinstance(item, AgentHubItem)


def test_item_factory_rejects_unknown_type(mock_all_services, mock_auth_context) -> None:
    factory = ItemFactory()
    with pytest.raises(UnexpectedItemTypeException):
        factory.create_item("NotARealType", mock_auth_context)


# ─────────────────────────────────────────────────────────────
# Exception handlers — integration via TestClient
# ─────────────────────────────────────────────────────────────
@pytest.fixture
def app_with_handlers() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom/workload")
    async def _wl() -> None:
        raise UnauthorizedException("no-go")

    @app.get("/boom/rate")
    async def _rate() -> None:
        raise TooManyRequestsException("slow down")

    @app.get("/boom/auth-ui")
    async def _auth() -> None:
        raise AuthenticationUIRequiredException("login")

    @app.get("/boom/value")
    async def _val() -> None:
        raise ValueError("badly formed hexadecimal UUID string")

    @app.get("/boom/value-path/{oid}")
    async def _val_path(oid: str) -> None:
        # Force the UUID validation to fail inside the handler body so the
        # value_error_handler inspects path_params.
        from uuid import UUID
        UUID(oid)

    @app.get("/boom/unexpected")
    async def _oops() -> None:
        raise KeyError("surprise")

    return app


@pytest.fixture
def client(app_with_handlers: FastAPI) -> TestClient:
    return TestClient(app_with_handlers, raise_server_exceptions=False)


def test_workload_exception_returned_as_fabric_response(client: TestClient) -> None:
    r = client.get("/boom/workload")
    assert r.status_code == 403


def test_too_many_requests_handler(client: TestClient) -> None:
    r = client.get("/boom/rate")
    assert r.status_code == 429


def test_auth_ui_required_sets_www_authenticate(client: TestClient) -> None:
    r = client.get("/boom/auth-ui")
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers
    assert r.headers["WWW-Authenticate"].startswith("Bearer")


def test_value_error_handler_plain(client: TestClient) -> None:
    r = client.get("/boom/value")
    assert r.status_code == 400


def test_value_error_handler_identifies_uuid_path_param(client: TestClient) -> None:
    r = client.get("/boom/value-path/not-a-uuid")
    assert r.status_code == 400


def test_global_exception_handler_hides_internals(client: TestClient) -> None:
    r = client.get("/boom/unexpected")
    assert r.status_code == 500
    # The opaque error message must NOT leak the original KeyError("surprise")
    assert "surprise" not in r.text


# ─────────────────────────────────────────────────────────────
# Handler coroutines — direct invocation (covers simple log paths)
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_workload_handler_direct_invocation() -> None:
    req = Mock(spec=Request)
    req.url = Mock(path="/x")
    resp = await workload_exception_handler(req, UnauthorizedException("nope"))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_too_many_requests_handler_direct_invocation() -> None:
    req = Mock(spec=Request)
    resp = await too_many_requests_exception_handler(req, TooManyRequestsException())
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_auth_ui_handler_direct_invocation() -> None:
    req = Mock(spec=Request)
    resp = await authentication_ui_required_exception_handler(
        req, AuthenticationUIRequiredException("login")
    )
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers
