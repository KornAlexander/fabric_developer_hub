"""Additional route tests for ``api.agenthub_controller`` — agent templates,
user-id extraction, and simple session routes that don't need a live
orchestrator/copilot.
"""
from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import agenthub_controller, github_chat_controller
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
# Session listing / get / delete without orchestrator
# ─────────────────────────────────────────────────────────────
def test_list_sessions_empty(client: TestClient) -> None:
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_get_session_not_found(client: TestClient) -> None:
    r = client.get("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_delete_session_not_found(client: TestClient) -> None:
    r = client.delete("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_delete_my_agent_not_found(client: TestClient) -> None:
    r = client.delete("/api/agents/my/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_my_agents_route_registered(client: TestClient) -> None:
    """Route ordering quirk: ``/agents/{agent_id}`` is declared before
    ``/agents/my`` in the router, so ``/agents/my`` currently matches the
    template-by-id route and returns 404. This test pins that behaviour so
    any future fix (or accidental further regression) is surfaced."""
    r = client.get("/api/agents/my")
    assert r.status_code == 404


def test_audit_log_empty(client: TestClient) -> None:
    r = client.get("/api/sessions/00000000-0000-0000-0000-000000000000/audit")
    assert r.status_code == 200
    assert r.json() == []


def test_send_message_to_missing_session(client: TestClient) -> None:
    r = client.post(
        "/api/sessions/00000000-0000-0000-0000-000000000000/message",
        json={"message": "hi"},
    )
    # Either 404 (session not found) or 422 (validation on target_agent_id) are
    # acceptable for coverage — we just want the route exercised.
    assert r.status_code in (404, 422)


# ─────────────────────────────────────────────────────────────
# _user_id_from_request branches
# ─────────────────────────────────────────────────────────────
def _make_fabric_token(claims: dict) -> str:
    """Build an unsigned JWT — ``get_unverified_claims`` only parses the payload."""
    def _b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    return f"{header}.{payload}."


def test_user_id_from_upn_claim(client: TestClient) -> None:
    token = _make_fabric_token({"upn": "Alice@Example.COM"})
    with patch.object(session_store, "list_sessions") as mock_list:
        mock_list.return_value = []
        r = client.get(
            "/api/sessions", headers={"X-Fabric-Token": f"Bearer {token}"},
        )
    assert r.status_code == 200
    assert mock_list.call_args[0][0] == "alice@example.com"


def test_user_id_from_oid_when_no_upn(client: TestClient) -> None:
    token = _make_fabric_token({"oid": "objid-123"})
    with patch.object(session_store, "list_sessions") as mock_list:
        mock_list.return_value = []
        client.get("/api/sessions", headers={"X-Fabric-Token": f"Bearer {token}"})
    assert mock_list.call_args[0][0] == "oid:objid-123"


def test_user_id_from_sub_claim(client: TestClient) -> None:
    token = _make_fabric_token({"sub": "subject-xyz"})
    with patch.object(session_store, "list_sessions") as mock_list:
        mock_list.return_value = []
        client.get("/api/sessions", headers={"X-Fabric-Token": f"Bearer {token}"})
    assert mock_list.call_args[0][0] == "oid:subject-xyz"


def test_user_id_falls_back_to_auth_hash(client: TestClient) -> None:
    with patch.object(session_store, "list_sessions") as mock_list:
        mock_list.return_value = []
        client.get("/api/sessions", headers={"Authorization": "Bearer gh-pat"})
    user_id = mock_list.call_args[0][0]
    assert user_id.startswith("user-")
    assert len(user_id) == len("user-") + 5


def test_user_id_anonymous_without_any_auth(client: TestClient) -> None:
    with patch.object(session_store, "list_sessions") as mock_list:
        mock_list.return_value = []
        client.get("/api/sessions")
    assert mock_list.call_args[0][0] == "anonymous"


def test_user_id_malformed_token_falls_through_to_auth(client: TestClient) -> None:
    """A garbage Fabric token shouldn't crash — we fall through to Authorization."""
    with patch.object(session_store, "list_sessions") as mock_list:
        mock_list.return_value = []
        client.get(
            "/api/sessions",
            headers={
                "X-Fabric-Token": "Bearer not-a-real-jwt",
                "Authorization": "Bearer gh",
            },
        )
    user_id = mock_list.call_args[0][0]
    assert user_id.startswith("user-")
