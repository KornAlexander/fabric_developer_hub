"""Additional route tests for ``api.agenthub_controller`` — agent templates,
user-key derivation, and simple session routes that don't need a live
orchestrator/copilot.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import agenthub_controller, github_chat_controller
from domain.models.authentication_models import AuthorizationContext
from domain.models.composition import AgentSlot, Budget, Composition, Handoff
from services.agenthub import _db, orchestrator_engine as oe, session_event_store, session_store, tool_policies


@pytest.fixture(autouse=True)
def _isolated_agenthub_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "agenthub.db"
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(db))
    monkeypatch.setattr(_db, "_DB_PATH", None)
    session_event_store.reset_init_for_tests()
    agenthub_controller._mission_status_log_cache.clear()
    session_store.init_db()
    session_event_store.init_schema()
    yield
    monkeypatch.setattr(_db, "_DB_PATH", None)
    session_event_store.reset_init_for_tests()


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
    assert "orchestrator" not in {item["id"] for item in body}


def test_catalog_agents_excludes_internal_orchestrator(client: TestClient) -> None:
    r = client.get("/api/catalogs/agents")
    assert r.status_code == 200
    assert "orchestrator" not in {item["id"] for item in r.json()}


def test_get_agent_template_found(client: TestClient) -> None:
    list_r = client.get("/api/agents").json()
    first_id = list_r[0]["id"]
    r = client.get(f"/api/agents/{first_id}")
    assert r.status_code == 200
    assert r.json()["id"] == first_id


def test_get_agent_template_not_found(client: TestClient) -> None:
    r = client.get("/api/agents/nonexistent-template-xyz")
    assert r.status_code == 404


def test_get_internal_orchestrator_template_not_found(client: TestClient) -> None:
    r = client.get("/api/agents/orchestrator")
    assert r.status_code == 404


def test_workspace_items_preview_degrades_to_empty_on_transient_item_failure(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_user(app, _make_ctx())
    agenthub_controller._workspace_items_cache.clear()

    async def _fake_acquire(_token: str) -> dict:
        return {"FABRIC_API_TOKEN": "fab-tok"}

    async def _call_tool(name: str, *_args, **_kwargs) -> str:
        if name == "fabric_list_items":
            return "HTTP 503: transient Fabric item listing failure"
        if name == "fabric_list_folders":
            return "[]"
        raise AssertionError(name)

    fake_manager = AsyncMock()
    fake_manager.call_tool.side_effect = _call_tool
    monkeypatch.setattr(github_chat_controller, "_acquire_mcp_tokens", _fake_acquire)
    monkeypatch.setattr(github_chat_controller, "_mcp_manager", fake_manager, raising=False)

    r = client.get("/api/workspaces/ws-1/items", headers={"X-Fabric-Token": "Bearer test"})

    assert r.status_code == 200
    assert r.json()["items"] == []


def test_workspace_items_manual_refresh_keeps_item_failure_strict(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _override_user(app, _make_ctx())
    agenthub_controller._workspace_items_cache.clear()

    async def _fake_acquire(_token: str) -> dict:
        return {"FABRIC_API_TOKEN": "fab-tok"}

    async def _call_tool(name: str, *_args, **_kwargs) -> str:
        if name == "fabric_list_items":
            return "HTTP 503: transient Fabric item listing failure"
        if name == "fabric_list_folders":
            return "[]"
        raise AssertionError(name)

    fake_manager = AsyncMock()
    fake_manager.call_tool.side_effect = _call_tool
    monkeypatch.setattr(github_chat_controller, "_acquire_mcp_tokens", _fake_acquire)
    monkeypatch.setattr(github_chat_controller, "_mcp_manager", fake_manager, raising=False)

    r = client.get("/api/workspaces/ws-1/items?refresh=1", headers={"X-Fabric-Token": "Bearer test"})

    assert r.status_code == 502
    assert "transient Fabric item listing failure" in r.json()["detail"]


def test_plan_view_filters_legacy_internal_orchestrator_slot() -> None:
    comp = Composition(
        session_id="s1",
        task="Do work",
        architecture="supervisor",
        rationale="Legacy saved composition.",
        headline="Work.",
        subtitle="Specialists only.",
        slots=[
            AgentSlot(id="orchestrator", agent_id="orchestrator", role="Coordinate", skills=[]),
            AgentSlot(id="worker", agent_id="architect", role="Plan", skills=[]),
        ],
        handoffs=[Handoff.model_validate({"from": "orchestrator", "to": "worker", "kind": "delegate"})],
        entrypoint_slot_id="orchestrator",
        budget=Budget(),
    )

    plan = agenthub_controller._composition_to_plan_view(comp)
    assert [node["id"] for node in plan["team"]["nodes"]] == ["worker"]
    assert plan["team"]["edges"] == []
    assert plan["footer"]["agentCount"] == 1


# ─────────────────────────────────────────────────────────────
# Session listing / get / delete — run under an injected dev auth
# context so we exercise the ownership logic without real tokens.
# ─────────────────────────────────────────────────────────────
def test_list_sessions_empty(app: FastAPI, client: TestClient) -> None:
    _override_user(app, _make_ctx())
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_sessions_summary_returns_aggregate_counts(app: FastAPI, client: TestClient) -> None:
    _override_user(app, _make_ctx("oid-123"))
    with patch.object(agenthub_controller.session_store, "summarize_sessions") as mock_summary:
        mock_summary.return_value = {
            "total": 12,
            "active_total": 7,
            "history_total": 5,
            "running": 2,
            "waiting": 3,
            "failed": 2,
            "completed": 4,
            "cancelled": 1,
            "other_active": 0,
            "by_status": {
                "running": 2,
                "planned": 3,
                "failed": 2,
                "completed": 4,
                "cancelled": 1,
            },
        }
        r = client.get("/api/sessions/summary")

    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 12
    assert body["active_total"] == 7
    assert body["history_total"] == 5
    assert body["running"] == 2
    assert body["waiting"] == 3
    assert body["failed"] == 2


def test_get_session_not_found(app: FastAPI, client: TestClient) -> None:
    _override_user(app, _make_ctx())
    r = client.get("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_get_session_poll_does_not_emit_info_load_log(
    app: FastAPI,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _override_user(app, _make_ctx())

    class _ExplodingEngine:
        async def compose(self, *_args, **_kwargs):
            raise AssertionError("create_session must not call ComposeService")

    with patch.object(agenthub_controller, "get_orchestrator_engine", return_value=_ExplodingEngine()):
        created = client.post(
            "/api/sessions",
            json={
                "task_description": "Build the workspace inventory report",
                "workspace_id": "11111111-1111-4111-8111-111111111111",
            },
        )
    assert created.status_code == 200
    session_id = created.json()["id"]

    caplog.clear()
    caplog.set_level(logging.INFO, logger="api.agenthub_controller")
    loaded = client.get(f"/api/sessions/{session_id}")

    assert loaded.status_code == 200
    assert "[SESSION] load" not in caplog.text


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


def test_sse_snapshot_seq_does_not_skip_initial_replay() -> None:
    assert agenthub_controller._sse_snapshot_seq(None) == 0
    assert agenthub_controller._sse_snapshot_seq(7) == 7


def test_empty_replay_sse_logs_actionable_mission_status(
    app: FastAPI,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _override_user(app, _make_ctx())
    created = client.post(
        "/api/sessions",
        json={
            "task_description": "Build the workspace inventory report",
            "workspace_id": "11111111-1111-4111-8111-111111111111",
        },
    )
    assert created.status_code == 200
    session_id = created.json()["id"]

    caplog.clear()
    caplog.set_level(logging.INFO, logger="api.agenthub_controller")
    for _ in range(2):
        with client.stream("GET", f"/api/sessions/{session_id}/events") as response:
            assert response.status_code == 200
            assert "".join(response.iter_text()) == ""

    mission_status = [
        record.getMessage()
        for record in caplog.records
        if "[MISSION_STATUS:" in record.getMessage()
    ]
    assert len(mission_status) == 1
    assert "process=no" in mission_status[0]
    assert "persisted_events=0" in mission_status[0]
    assert "waiting_for=session is planned/approved and waiting for the run request to attach execution" in mission_status[0]
    assert "next_action=classify as no-active-execution; do not reconnect every 2s" in mission_status[0]


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
    assert "Invalid Fabric token:" in r.json()["detail"]


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


def test_create_session_seeds_dynamic_composition_without_composer(
    app: FastAPI,
    client: TestClient,
) -> None:
    _override_user(app, _make_ctx())

    class _ExplodingEngine:
        async def compose(self, *_args, **_kwargs):
            raise AssertionError("create_session must not call ComposeService")

    with patch.object(agenthub_controller, "get_orchestrator_engine", return_value=_ExplodingEngine()):
        r = client.post(
            "/api/sessions",
            json={
                "task_description": "Build the workspace inventory report",
                "workspace_id": "11111111-1111-4111-8111-111111111111",
                "context": {"require_approvals": False},
            },
        )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "planned"
    assert body["composition"]["architecture"] == "dynamic"
    assert body["composition"]["entrypointSlotId"] == "generalist"
    assert body["composition"]["budget"]["requireApprovals"] is False
    assert body["composition"]["slots"] == [
        {
            "id": "generalist",
            "agentId": "generalist",
            "role": "Generalist mission controller",
            "skills": [],
            "parentId": None,
            "subteam": None,
            "status": "planned",
        }
    ]


def test_e2e_create_run_events_prove_backend_pi_harness_has_tools(
    app: FastAPI,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _override_user(app, _make_ctx())
    tool_policies.register_all()

    class _PiHarnessEngine:
        def __init__(self) -> None:
            self._real = oe.OrchestratorEngine()
            self._execution: oe._JobExecution | None = None

        async def start_job(self, job, copilot_token, mcp_tokens):
            execution = oe._JobExecution(job, copilot_token=copilot_token, mcp_tokens=mcp_tokens)
            self._execution = execution
            job.status = agenthub_controller.JobStatus.RUNNING
            session_store.update_session(job)
            self._real._emit_startup_snapshot(execution)

        def get_job_execution(self, session_id: str):
            if self._execution and self._execution.job.id == session_id:
                return self._execution
            return None

    engine = _PiHarnessEngine()

    async def _fake_copilot_token(_request) -> str:
        return "copilot-token"

    monkeypatch.setattr(agenthub_controller, "_copilot_token", _fake_copilot_token)
    monkeypatch.setattr(agenthub_controller, "get_orchestrator_engine", lambda: engine)

    created = client.post(
        "/api/sessions",
        json={
            "task_description": "Verify backend Pi orchestration harness",
            "workspace_id": "11111111-1111-4111-8111-111111111111",
            "context": {"require_approvals": False},
        },
    )
    assert created.status_code == 200
    session_id = created.json()["id"]
    created_context = created.json()["context"]
    assert created_context["runtime"] == "pi"
    assert created_context["orchestration_runtime"] == "pi"
    assert created_context["pi_orchestration"]["orchestrationHarness"] == "pi-agent-core"
    assert created_context["pi_orchestration"]["toolCount"] > 0
    assert any(tool["name"] == "fabric_list_workspaces" for tool in created_context["pi_orchestration"]["tools"])

    run = client.post(f"/api/sessions/{session_id}/run")
    assert run.status_code == 200

    events = client.get(f"/api/sessions/{session_id}/events.json?types=pi.orchestration.start")
    assert events.status_code == 200
    body = events.json()
    assert body["liveExecution"] is True
    assert body["count"] == 1
    start_event = body["events"][0]
    assert start_event["runtime"] == "pi"
    assert start_event["runtimePackage"] == "@mariozechner/pi-agent-core"
    assert start_event["orchestrationHarness"] == "pi-agent-core"
    assert start_event["harnessPackage"] == "npm:@mariozechner/pi-agent-core@0.71.1"
    assert start_event["toolRegistry"] == "agenthub-tool-runtime"
    assert start_event["toolExecutionBridge"] == "agenthub-tool-runtime-proxy"
    assert start_event["toolCount"] > 0
    assert start_event["emittedToolCount"] > 0
    assert start_event["toolPolicySummary"]["readSafe"] > 0
    assert start_event["toolPolicySummary"]["autoAllowed"] > 0
    assert any(tool["name"] == "fabric_list_workspaces" for tool in start_event["tools"])
