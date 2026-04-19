"""Additional route tests for ``api.agenthub_controller`` — agent templates,
user-key derivation, and simple session routes that don't need a live
orchestrator/copilot.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import agenthub_controller, github_chat_controller
from domain.models.authentication_models import AuthorizationContext
from services.agenthub import _db, session_store


@pytest.fixture(autouse=True)
def _isolated_agenthub_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "agenthub.db"
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(db))
    monkeypatch.setattr(_db, "_DB_PATH", None)
    session_store.init_db()
    yield
    monkeypatch.setattr(_db, "_DB_PATH", None)


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(agenthub_controller.router)
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_mcp_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_chat_controller, "_mcp_manager", None, raising=False)


def _make_ctx(oid: str = "test-user-id") -> AuthorizationContext:
    return AuthorizationContext(
        original_subject_token="mock_subject_token",
        tenant_object_id="44444444-4444-4444-4444-444444444444",
        claims=[
            {"type": "oid", "value": oid},
            {"type": "tid", "value": "44444444-4444-4444-4444-444444444444"},
        ],
    )


def _override_user(app: FastAPI, ctx: AuthorizationContext | None) -> None:
    app.dependency_overrides[agenthub_controller.require_user] = lambda: ctx


# ─────────────────────────────────────────────────────────────
# Agent template routes (no external dependencies)
# ─────────────────────────────────────────────────────────────
def test_list_agent_templates_returns_array(client: TestClient) -> None:
    r = client.get("/api/agents")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) > 0
    assert "id" in body[0]


def test_get_agent_template_found(client: TestClient) -> None:
    list_r = client.get("/api/agents").json()
    first_id = list_r[0]["id"]
    r = client.get(f"/api/agents/{first_id}")
    assert r.status_code == 200
    assert r.json()["id"] == first_id


def test_get_agent_template_not_found(client: TestClient) -> None:
    r = client.get("/api/agents/nonexistent-template-xyz")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────
# Session listing / get / delete — run under an injected dev auth
# context so we exercise the ownership logic without real tokens.
# ─────────────────────────────────────────────────────────────
def test_list_sessions_empty(app: FastAPI, client: TestClient) -> None:
    _override_user(app, _make_ctx())
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_get_session_not_found(app: FastAPI, client: TestClient) -> None:
    _override_user(app, _make_ctx())
    r = client.get("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_delete_session_not_found(app: FastAPI, client: TestClient) -> None:
    _override_user(app, _make_ctx())
    r = client.delete("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_delete_my_agent_not_found(app: FastAPI, client: TestClient) -> None:
    _override_user(app, _make_ctx())
    r = client.delete("/api/agents/my/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_my_agents_route_registered(app: FastAPI, client: TestClient) -> None:
    """Route ordering quirk: ``/agents/{agent_id}`` is declared before
    ``/agents/my`` in the router, so ``/agents/my`` currently matches the
    template-by-id route and returns 404. This test pins that behaviour so
    any future fix (or accidental further regression) is surfaced."""
    _override_user(app, _make_ctx())
    r = client.get("/api/agents/my")
    assert r.status_code == 404


def test_audit_log_empty_is_blocked_for_unknown_session(
    app: FastAPI, client: TestClient
) -> None:
    """Audit endpoint now enforces ownership — unknown sessions always 404."""
    _override_user(app, _make_ctx())
    r = client.get("/api/sessions/00000000-0000-0000-0000-000000000000/audit")
    assert r.status_code == 404


def test_send_message_to_missing_session(app: FastAPI, client: TestClient) -> None:
    _override_user(app, _make_ctx())
    r = client.post(
        "/api/sessions/00000000-0000-0000-0000-000000000000/message",
        json={"message": "hi"},
    )
    # Ownership check rejects unknown sessions with 404; validation may also
    # reject the body (422). Either is acceptable — we just want the route
    # exercised without leaking existence information.
    assert r.status_code in (404, 422)


# ─────────────────────────────────────────────────────────────
# _user_key_from_context — unit tests for the helper directly.
# ─────────────────────────────────────────────────────────────
def test_user_key_prefers_oid() -> None:
    ctx = AuthorizationContext(
        original_subject_token="tok",
        tenant_object_id="t",
        claims=[
            {"type": "oid", "value": "objid-123"},
            {"type": "upn", "value": "alice@example.com"},
        ],
    )
    assert agenthub_controller._user_key_from_context(ctx) == "oid:objid-123"


def test_user_key_falls_back_to_upn_without_oid() -> None:
    ctx = AuthorizationContext(
        original_subject_token="tok",
        tenant_object_id="t",
        claims=[
            {"type": "upn", "value": "Alice@Example.COM"},
        ],
    )
    assert agenthub_controller._user_key_from_context(ctx) == "alice@example.com"


def test_user_key_anonymous_when_ctx_is_none() -> None:
    assert agenthub_controller._user_key_from_context(None) == "anonymous"


def test_user_key_anonymous_when_ctx_has_no_identity_claims() -> None:
    ctx = AuthorizationContext(
        original_subject_token="tok",
        tenant_object_id="t",
        claims=[{"type": "tid", "value": "tenant-x"}],
    )
    assert agenthub_controller._user_key_from_context(ctx) == "anonymous"


# ─────────────────────────────────────────────────────────────
# require_user — production rejects missing/invalid tokens with 401.
# ─────────────────────────────────────────────────────────────
def test_unauthenticated_request_rejected_in_production(
    app: FastAPI, client: TestClient
) -> None:
    """Production: no Fabric token → 401, never degrade to anonymous."""
    # No dependency override — we want the real require_user to run.
    with patch.object(
        agenthub_controller, "get_configuration_service"
    ) as mock_cfg:
        mock_cfg.return_value.is_production.return_value = True
        r = client.get("/api/sessions")
    assert r.status_code == 401


def test_authenticated_request_rejects_invalid_fabric_token_in_production(
    app: FastAPI, client: TestClient
) -> None:
    """Production: garbage Fabric token → 401 via the auth service path."""
    from domain.exceptions.exceptions import AuthenticationException

    with patch.object(
        agenthub_controller, "get_configuration_service"
    ) as mock_cfg, patch.object(
        agenthub_controller, "get_authentication_service"
    ) as mock_auth:
        mock_cfg.return_value.is_production.return_value = True

        async def _raise(*_a, **_kw):
            raise AuthenticationException("invalid")

        mock_auth.return_value.authenticate_data_plane_call = _raise
        r = client.get(
            "/api/sessions",
            headers={"X-Fabric-Token": "Bearer not-a-real-jwt"},
        )
    assert r.status_code == 401


def test_unauthenticated_request_allowed_in_dev_mode(
    app: FastAPI, client: TestClient
) -> None:
    """Dev mode: no Fabric token → soft fallback to the anonymous dev user."""
    with patch.object(
        agenthub_controller, "get_configuration_service"
    ) as mock_cfg:
        mock_cfg.return_value.is_production.return_value = False
        r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == []
