"""Security-regression tests for ``api.agenthub_controller``.

Locks the Phase-4 fix in ``list_workspaces`` where the 500 path used to leak
``str(e)`` to the client.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api import agenthub_controller, github_chat_controller
from services.agenthub import _db, session_store


@pytest.fixture(autouse=True)
def _isolated_agenthub_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the agenthub store at a per-test SQLite file so the workspace
    cache table exists and is empty for each case."""
    db = tmp_path / "agenthub.db"
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(db))
    monkeypatch.setattr(_db, "_DB_PATH", None)
    session_store.init_db()
    yield
    monkeypatch.setattr(_db, "_DB_PATH", None)


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
    monkeypatch.setattr(github_chat_controller, "_mcp_manager", None, raising=False)


@pytest.fixture(autouse=True)
def _clear_auth_service_from_registry() -> None:
    """Force the dev-mode "no auth service available" path for these tests.

    These tests use the bogus token string ``"Bearer test"`` to exercise the
    logic AFTER the token guard -- they are NOT auth-contract tests. The
    controller's dev-mode fallback treats "auth service unavailable" as
    ``ctx = None`` (anonymous dev user), which is what these tests expect.

    Previously this path worked by accident: if no earlier test had
    registered an ``AuthenticationService`` in the global ``ServiceRegistry``,
    ``get_authentication_service()`` raised and the controller fell through.
    Once a neighbouring test suite registered a real auth service, these
    tests started failing with 401 because the bogus token now reached a
    live validator. Removing the service from the registry for the duration
    of each test restores the intended isolation.
    """
    from app.core.service_registry import get_service_registry
    from services.auth.authentication import AuthenticationService

    registry = get_service_registry()
    services = getattr(registry, "_services", None)
    existing = services.pop(AuthenticationService, None) if services is not None else None
    try:
        yield
    finally:
        if existing is not None and services is not None:
            services[AuthenticationService] = existing


def test_list_workspaces_no_fabric_token_returns_400(isolated_client: TestClient) -> None:
    resp = isolated_client.get("/api/workspaces")
    assert resp.status_code == 400
    assert "Fabric token" in resp.json()["detail"]


def test_e2e_fabric_token_bypasses_auth_validation_in_dev(isolated_client: TestClient) -> None:
    from app.core.service_registry import get_service_registry
    from services.auth.authentication import AuthenticationService

    registry = get_service_registry()
    services = getattr(registry, "_services")
    fake_auth = AsyncMock()
    fake_auth.authenticate_data_plane_call.side_effect = AssertionError("should not validate e2e token")
    services[AuthenticationService] = fake_auth
    try:
        resp = isolated_client.get(
            "/api/sessions/summary",
            headers={"X-Fabric-Token": "Bearer e2e-fabric-token"},
        )
    finally:
        services.pop(AuthenticationService, None)

    assert resp.status_code == 200
    fake_auth.authenticate_data_plane_call.assert_not_called()


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
    # Each workspace row exposes the core identity fields plus the git
    # provenance columns the dashboard reads. Compare by (id, name) so we
    # stay insensitive to additional columns added later.
    workspaces = body["workspaces"]
    assert [(w["id"], w["name"]) for w in workspaces] == [
        ("ws-1", "WS One"),
        ("ws-2", "WS Two"),
    ]
    # And: the git_* provenance fields are present (may be None for
    # workspaces not connected to a git provider).
    for w in workspaces:
        for key in ("git_connected", "git_provider", "git_branch", "git_repo_name"):
            assert key in w, f"missing {key} in {w}"
    assert body["source"] == "refreshed"
    assert body["cached_at"] is not None


def test_query_semantic_model_uses_backend_obo_powerbi_token(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_acquire(_token: str) -> dict:
        return {
            "FABRIC_API_TOKEN": "fab-tok",
            "POWERBI_API_TOKEN": "pbi-tok",
            "ONELAKE_TOKEN": "ol-tok",
        }

    monkeypatch.setattr(
        "api.github_chat_controller._acquire_mcp_tokens", _fake_acquire,
    )

    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = "ok"

        def json(self) -> dict:
            return {"results": [{"tables": [{"rows": [{"[ItemCount]": 3}]}]}]}

    class _FakePowerBIClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, *, headers: dict, json: dict):
            captured.update({"url": url, "headers": headers, "json": json})
            return _FakeResponse()

    monkeypatch.setattr(agenthub_controller.httpx, "AsyncClient", _FakePowerBIClient)

    resp = isolated_client.post(
        "/api/workspaces/ws-1/semantic-models/ds-1/query",
        headers={"X-Fabric-Token": "Bearer test"},
        json={"query": 'EVALUATE ROW("ItemCount", [Item Count])'},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "rows": [{"[ItemCount]": 3}],
        "source": "powerbi_executeQueries",
    }
    assert captured["url"] == (
        "https://api.powerbi.com/v1.0/myorg/groups/ws-1"
        "/datasets/ds-1/executeQueries"
    )
    assert captured["headers"] == {
        "Authorization": "Bearer pbi-tok",
        "Content-Type": "application/json",
    }
    assert captured["json"] == {
        "queries": [{"query": 'EVALUATE ROW("ItemCount", [Item Count])'}],
        "serializerSettings": {"includeNulls": True},
    }
