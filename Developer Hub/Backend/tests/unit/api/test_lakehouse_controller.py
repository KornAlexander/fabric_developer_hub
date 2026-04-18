"""Targeted tests for ``api.lakehouse_controller``.

Exercises all 4 endpoints (``/getLakehouseFile``, ``/writeToLakehouseFile``,
``/onelake/.../tables``, ``/onelake/.../files``) against a minimal FastAPI
app with the auth + onelake/lakehouse services mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.lakehouse_controller import router
from domain.models.authentication_models import AuthorizationContext
from domain.models.lakehouse_table import LakehouseTable
from services.auth.authentication import (
    AuthenticationService,
    get_authentication_service,
)
from services.fabric.lakehouse_client_service import (
    LakehouseClientService,
    get_lakehouse_client_service,
)
from services.fabric.onelake_client_service import (
    OneLakeClientService,
    get_onelake_client_service,
)


@pytest.fixture
def auth_ctx() -> AuthorizationContext:
    return AuthorizationContext(
        original_subject_token="t",
        tenant_object_id="44444444-4444-4444-4444-444444444444",
        claims=[],
    )


@pytest.fixture
def mock_auth(auth_ctx: AuthorizationContext) -> AsyncMock:
    auth = AsyncMock(spec=AuthenticationService)
    auth.authenticate_data_plane_call.return_value = auth_ctx
    auth.get_access_token_on_behalf_of.return_value = "ol-token"
    return auth


@pytest.fixture
def mock_onelake() -> AsyncMock:
    return AsyncMock(spec=OneLakeClientService)


@pytest.fixture
def mock_lakehouse() -> AsyncMock:
    return AsyncMock(spec=LakehouseClientService)


@pytest.fixture
def client(
    mock_auth: AsyncMock, mock_onelake: AsyncMock, mock_lakehouse: AsyncMock,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_authentication_service] = lambda: mock_auth
    app.dependency_overrides[get_onelake_client_service] = lambda: mock_onelake
    app.dependency_overrides[get_lakehouse_client_service] = lambda: mock_lakehouse
    return TestClient(app)


# ── /getLakehouseFile ───────────────────────────────────────────────

def test_get_lakehouse_file_returns_data(
    client: TestClient, mock_onelake: AsyncMock,
) -> None:
    mock_onelake.get_onelake_file.return_value = "hello"
    resp = client.get("/getLakehouseFile?source=Files/x.csv")
    assert resp.status_code == 200
    assert resp.text == '"hello"'


def test_get_lakehouse_file_empty_returns_204_like(
    client: TestClient, mock_onelake: AsyncMock,
) -> None:
    """Endpoint returns ``None`` for empty data — FastAPI serializes as ``null``."""
    mock_onelake.get_onelake_file.return_value = None
    resp = client.get("/getLakehouseFile?source=Files/none")
    assert resp.status_code == 200
    assert resp.text == "null"


def test_get_lakehouse_file_calls_auth_with_correct_scopes(
    client: TestClient, mock_auth: AsyncMock, mock_onelake: AsyncMock,
) -> None:
    mock_onelake.get_onelake_file.return_value = "ok"
    client.get("/getLakehouseFile?source=Files/x", headers={"Authorization": "Bearer t"})
    mock_auth.authenticate_data_plane_call.assert_awaited_once()
    _, kwargs = mock_auth.authenticate_data_plane_call.call_args
    # Both READ and READ_WRITE scopes must be allowed
    assert len(kwargs["allowed_scopes"]) == 2


# ── /writeToLakehouseFile ───────────────────────────────────────────

def test_write_lakehouse_file_creates_when_not_exists(
    client: TestClient, mock_onelake: AsyncMock,
) -> None:
    mock_onelake.get_onelake_file_path.return_value = "https://onelake/.../x"
    mock_onelake.check_if_file_exists.return_value = False

    resp = client.put("/writeToLakehouseFile", json={
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "lakehouse_id": "22222222-2222-2222-2222-222222222222",
        "file_name": "x.csv",
        "content": "hello",
        "overwrite_if_exists": False,
    })
    assert resp.status_code == 200
    assert resp.json() == {"success": True}
    mock_onelake.write_to_onelake_file.assert_awaited_once()


def test_write_lakehouse_file_409_when_exists_no_overwrite(
    client: TestClient, mock_onelake: AsyncMock,
) -> None:
    mock_onelake.get_onelake_file_path.return_value = "https://onelake/.../x"
    mock_onelake.check_if_file_exists.return_value = True

    resp = client.put("/writeToLakehouseFile", json={
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "lakehouse_id": "22222222-2222-2222-2222-222222222222",
        "file_name": "x.csv",
        "content": "hello",
        "overwrite_if_exists": False,
    })
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]
    mock_onelake.write_to_onelake_file.assert_not_called()


def test_write_lakehouse_file_overwrites_when_allowed(
    client: TestClient, mock_onelake: AsyncMock,
) -> None:
    mock_onelake.get_onelake_file_path.return_value = "https://onelake/.../x"
    mock_onelake.check_if_file_exists.return_value = True

    resp = client.put("/writeToLakehouseFile", json={
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "lakehouse_id": "22222222-2222-2222-2222-222222222222",
        "file_name": "x.csv",
        "content": "hello",
        "overwrite_if_exists": True,
    })
    assert resp.status_code == 200
    mock_onelake.write_to_onelake_file.assert_awaited_once()


def test_write_lakehouse_file_uses_only_read_write_scope(
    client: TestClient, mock_auth: AsyncMock, mock_onelake: AsyncMock,
) -> None:
    mock_onelake.get_onelake_file_path.return_value = "p"
    mock_onelake.check_if_file_exists.return_value = False
    client.put("/writeToLakehouseFile", json={
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "lakehouse_id": "22222222-2222-2222-2222-222222222222",
        "file_name": "x", "content": "y", "overwrite_if_exists": True,
    })
    _, kwargs = mock_auth.authenticate_data_plane_call.call_args
    # WRITE endpoint must NOT accept read-only scope
    assert len(kwargs["allowed_scopes"]) == 1


# ── /onelake/{ws}/{lh}/tables ───────────────────────────────────────

def test_get_tables_returns_serialized_list(
    client: TestClient, mock_lakehouse: AsyncMock,
) -> None:
    mock_lakehouse.get_lakehouse_tables.return_value = [
        LakehouseTable(name="orders", path="Tables/orders", schema_name="dbo"),
        LakehouseTable(name="customers", path="Tables/customers", schema_name="dbo"),
    ]
    ws = "11111111-1111-1111-1111-111111111111"
    lh = "22222222-2222-2222-2222-222222222222"
    resp = client.get(f"/onelake/{ws}/{lh}/tables")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0] == {"name": "orders", "path": "Tables/orders", "schema": "dbo"}


def test_get_tables_empty_list(
    client: TestClient, mock_lakehouse: AsyncMock,
) -> None:
    mock_lakehouse.get_lakehouse_tables.return_value = []
    ws = "11111111-1111-1111-1111-111111111111"
    lh = "22222222-2222-2222-2222-222222222222"
    resp = client.get(f"/onelake/{ws}/{lh}/tables")
    assert resp.status_code == 200
    assert resp.json() == []


# ── /onelake/{ws}/{lh}/files ────────────────────────────────────────

def test_get_files_returns_lakehouse_service_output(
    client: TestClient, mock_lakehouse: AsyncMock,
) -> None:
    mock_lakehouse.get_lakehouse_files.return_value = [
        {"name": "a.csv", "path": "Files/a.csv"},
    ]
    ws = "11111111-1111-1111-1111-111111111111"
    lh = "22222222-2222-2222-2222-222222222222"
    resp = client.get(f"/onelake/{ws}/{lh}/files")
    assert resp.status_code == 200
    assert resp.json() == [{"name": "a.csv", "path": "Files/a.csv"}]
