"""Security-regression tests for ``api.agenthub_controller``.

Locks the Phase-4 fix in ``list_workspaces`` where the 500 path used to leak
``str(e)`` to the client.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import agenthub_controller
from services.agenthub import session_store


@pytest.fixture(autouse=True)
def _isolated_agenthub_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the agenthub store at a per-test SQLite file so the workspace
    cache table exists and is empty for each case."""
    db = tmp_path / "agenthub.db"
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(db))
    monkeypatch.setattr(session_store, "_DB_PATH", None)
    session_store.init_db()
    yield
    monkeypatch.setattr(session_store, "_DB_PATH", None)


@pytest.fixture
def isolated_app() -> FastAPI:
    """Fresh FastAPI app mounting only the agenthub router — bypasses the
    full main.py lifespan to avoid pulling in unrelated dependencies."""
    app = FastAPI()
    app.include_router(agenthub_controller.router)
    return app


@pytest.fixture
def isolated_client(isolated_app: FastAPI) -> TestClient:
    return TestClient(isolated_app)


@pytest.fixture(autouse=True)
def _reset_mcp_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts with a fresh _mcp_manager state."""
    from api import github_chat_controller
    monkeypatch.setattr(github_chat_controller, "_mcp_manager", None, raising=False)


def test_list_workspaces_no_fabric_token_returns_400(isolated_client: TestClient) -> None:
    resp = isolated_client.get("/api/workspaces")
    assert resp.status_code == 400
    assert "Fabric token" in resp.json()["detail"]


def test_list_workspaces_no_mcp_manager_returns_503(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stub OBO exchange so we get past the token guard
    async def _fake_acquire(_token: str) -> dict:
        return {"fabric": "fab-tok", "onelake": "ol-tok"}

    monkeypatch.setattr(
        "api.github_chat_controller._acquire_mcp_tokens", _fake_acquire,
    )

    resp = isolated_client.get(
        "/api/workspaces", headers={"X-Fabric-Token": "Bearer test"},
    )
    assert resp.status_code == 503


def test_list_workspaces_500_does_not_leak_exception_message(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION: previously the 500 response detail echoed ``str(e)`` —
    e.g. ``HTTPException(500, f"... {e!s}")`` — leaking SDK paths and stack
    state. The fix returns a fixed string and logs the exception server-side.
    """
    async def _fake_acquire(_token: str) -> dict:
        return {"fabric": "fab-tok", "onelake": "ol-tok"}

    monkeypatch.setattr(
        "api.github_chat_controller._acquire_mcp_tokens", _fake_acquire,
    )

    secret_marker = "INTERNAL_SECRET_PATH_/etc/sdk/credentials.json"
    fake_manager = AsyncMock()
    fake_manager.call_tool.side_effect = RuntimeError(secret_marker)
    monkeypatch.setattr(
        "api.github_chat_controller._mcp_manager", fake_manager, raising=False,
    )

    resp = isolated_client.get(
        "/api/workspaces", headers={"X-Fabric-Token": "Bearer test"},
    )
    assert resp.status_code == 500
    # The fixed user-facing message is returned
    assert resp.json()["detail"] == "Failed to list workspaces"
    # The raw exception string MUST NOT appear in the response body
    assert secret_marker not in resp.text


def test_list_workspaces_propagates_inner_httpexception(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ``HTTPException`` raised by ``_acquire_mcp_tokens`` must propagate
    with its original status — not be swallowed into a 500 by the catch-all."""
    from fastapi import HTTPException

    async def _fake_acquire(_token: str) -> dict:
        raise HTTPException(status_code=401, detail="bad fabric token")

    monkeypatch.setattr(
        "api.github_chat_controller._acquire_mcp_tokens", _fake_acquire,
    )

    resp = isolated_client.get(
        "/api/workspaces", headers={"X-Fabric-Token": "Bearer test"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "bad fabric token"


def test_list_workspaces_ok_returns_simplified_shape(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: ensure the controller transforms the raw MCP tool output
    into the ``[{id, name}]`` shape expected by the workspace selector UI."""
    async def _fake_acquire(_token: str) -> dict:
        return {"fabric": "fab-tok", "onelake": "ol-tok"}

    monkeypatch.setattr(
        "api.github_chat_controller._acquire_mcp_tokens", _fake_acquire,
    )

    fake_manager = AsyncMock()
    fake_manager.call_tool.return_value = (
        '[{"id": "ws-1", "displayName": "WS One"},'
        ' {"id": "ws-2", "displayName": "WS Two"}]'
    )
    monkeypatch.setattr(
        "api.github_chat_controller._mcp_manager", fake_manager, raising=False,
    )

    resp = isolated_client.get(
        "/api/workspaces", headers={"X-Fabric-Token": "Bearer test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["workspaces"] == [
        {"id": "ws-1", "name": "WS One"},
        {"id": "ws-2", "name": "WS Two"},
    ]
    assert body["source"] == "refreshed"
    assert body["cached_at"] is not None
