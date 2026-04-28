"""Per-tool tests for ``mcp_servers.semantic_link``.

Coverage focuses on:
  * The bugs surfaced during Phase-5 refactor (sl_get_git_status LRO path,
    sl_admin_get_activity_events filter)
  * Representative tools across each major section
"""
from __future__ import annotations

import json

import pytest

from mcp_servers import semantic_link
from tests.unit.mcp_servers.conftest import (
    FakeAsyncClient,
    install_fake_client,
    make_response,
)

# ── Header guard ────────────────────────────────────────────────────

def test_headers_raises_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FABRIC_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="FABRIC_API_TOKEN not set"):
        semantic_link._headers()


def test_pbi_headers_prefer_powerbi_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FABRIC_API_TOKEN", "fabric-token")
    monkeypatch.setenv("POWERBI_API_TOKEN", "powerbi-token")

    assert semantic_link._pbi_headers()["Authorization"] == "Bearer powerbi-token"


# ── sl_evaluate_dax ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sl_evaluate_dax_returns_first_table_rows(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200,
        json_body={"results": [{"tables": [{"rows": [{"a": 1}, {"a": 2}]}]}]},
    ))
    install_fake_client(monkeypatch, semantic_link, fake)

    result = await semantic_link.sl_evaluate_dax("ws-1", "ds-1", "EVALUATE 1")
    assert json.loads(result) == [{"a": 1}, {"a": 2}]
    method, url, kwargs = fake.calls[0]
    assert method == "POST"
    assert url.endswith("/groups/ws-1/datasets/ds-1/executeQueries")
    assert kwargs["json"]["queries"][0]["query"] == "EVALUATE 1"


@pytest.mark.asyncio
async def test_sl_evaluate_dax_error(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(400, text="bad dax"))
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_evaluate_dax("ws-1", "ds-1", "EVALUATE 1")
    assert result.startswith("Error: 400")


# ── sl_list_semantic_models / sl_list_reports / sl_list_lakehouses ─

@pytest.mark.asyncio
async def test_sl_list_semantic_models(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200,
        json_body={"value": [{
            "id": "ds-1", "name": "Sales", "configuredBy": "u@x", "isRefreshable": True,
            "isEffectiveIdentityRequired": False, "targetStorageMode": "Abf",
        }]},
    ))
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_list_semantic_models("ws-1")
    parsed = json.loads(result)
    assert parsed[0]["id"] == "ds-1"


@pytest.mark.asyncio
async def test_sl_list_reports(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200, json_body={"value": [{"id": "r-1", "name": "Report", "datasetId": "ds-1"}]},
    ))
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_list_reports("ws-1")
    parsed = json.loads(result)
    assert parsed[0]["id"] == "r-1"


@pytest.mark.asyncio
async def test_sl_list_lakehouses(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200, json_body={"value": [{"id": "lh-1"}]},
    ))
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_list_lakehouses("ws-1")
    assert json.loads(result) == [{"id": "lh-1"}]


# ── sl_refresh_semantic_model ───────────────────────────────────────

@pytest.mark.asyncio
async def test_sl_refresh_semantic_model_triggered(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(202))
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_refresh_semantic_model("ws-1", "ds-1")
    parsed = json.loads(result)
    assert parsed["status"] == "refresh_triggered"


# ── sl_get_semantic_model_definition (LRO path) ─────────────────────

@pytest.mark.asyncio
async def test_sl_get_semantic_model_definition_inline_200(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200, json_body={"definition": {"parts": [{"path": "model.tmdl", "payload": "abc"}]}},
    ))
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_get_semantic_model_definition("ws-1", "ds-1")
    assert json.loads(result) == [{"path": "model.tmdl", "payload": "abc"}]


@pytest.mark.asyncio
async def test_sl_get_semantic_model_definition_lro_path(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    """202 → poll Location/result → 200 with definition."""
    monkeypatch.setattr(semantic_link, "_async_sleep", _zero_sleep)
    fake = FakeAsyncClient(responses_by_method={
        "POST": [make_response(202, headers={
            "Location": "https://api.fabric.microsoft.com/v1/operations/op-1",
            "Retry-After": "0",
        })],
        "GET": [make_response(
            200, json_body={"definition": {"parts": [{"path": "x", "payload": "y"}]}},
        )],
    })
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_get_semantic_model_definition("ws-1", "ds-1")
    assert json.loads(result) == [{"path": "x", "payload": "y"}]


# ── sl_get_git_status (REGRESSION: client lifetime) ─────────────────

@pytest.mark.asyncio
async def test_sl_get_git_status_inline_200(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200, json_body={"changes": [], "remoteCommitHash": "abc"},
    ))
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_get_git_status("ws-1")
    parsed = json.loads(result)
    assert parsed["remoteCommitHash"] == "abc"


@pytest.mark.asyncio
async def test_sl_get_git_status_lro_path_uses_live_client(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    """REGRESSION: previously the polling GET used ``client`` outside the
    ``async with`` block — which raised ``RuntimeError: client closed``.
    After the fix, the GET must succeed on the same client.
    """
    monkeypatch.setattr(semantic_link, "_async_sleep", _zero_sleep)
    fake = FakeAsyncClient(responses_by_method={
        "POST": [make_response(202, headers={
            "Location": "https://api.fabric.microsoft.com/v1/operations/op-7",
        })],
        "GET": [make_response(200, json_body={"status": "Completed", "changes": []})],
    })
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_get_git_status("ws-1")
    parsed = json.loads(result)
    assert parsed["status"] == "Completed"
    # Must have made BOTH the POST and the polling GET
    methods = [c[0] for c in fake.calls]
    assert methods == ["POST", "GET"]


# ── sl_admin_get_activity_events (REGRESSION: filter applied) ───────

@pytest.mark.asyncio
async def test_sl_admin_get_activity_events_with_filter(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    """REGRESSION: pre-Phase-5 the activity_type parameter built ``filter_str``
    locally and never used it. After the fix, the URL must include the filter."""
    fake = FakeAsyncClient(default_response=make_response(
        200, json_body={"activityEventEntities": [{"Id": "ev-1"}]},
    ))
    install_fake_client(monkeypatch, semantic_link, fake)
    await semantic_link.sl_admin_get_activity_events(
        "2026-04-17T00:00:00Z", "2026-04-18T00:00:00Z", activity_type="ViewReport",
    )
    _, url, _ = fake.calls[0]
    assert "Activity eq 'ViewReport'" in url
    assert "$filter=" in url


@pytest.mark.asyncio
async def test_sl_admin_get_activity_events_without_filter(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200, json_body={"activityEventEntities": []},
    ))
    install_fake_client(monkeypatch, semantic_link, fake)
    await semantic_link.sl_admin_get_activity_events(
        "2026-04-17T00:00:00Z", "2026-04-18T00:00:00Z",
    )
    _, url, _ = fake.calls[0]
    assert "$filter" not in url


# ── sl_create_shortcut ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sl_create_shortcut_body_shape(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        201, json_body={"name": "sc-1", "path": "Tables"},
    ))
    install_fake_client(monkeypatch, semantic_link, fake)

    await semantic_link.sl_create_shortcut(
        "ws-1", "lh-1", "sc-1", "Tables", "ws-2", "lh-2", "Tables/orders",
    )
    _, _, kwargs = fake.calls[0]
    body = kwargs["json"]
    assert body["name"] == "sc-1"
    assert body["path"] == "Tables"
    assert body["target"]["oneLake"]["workspaceId"] == "ws-2"
    assert body["target"]["oneLake"]["itemId"] == "lh-2"
    assert body["target"]["oneLake"]["path"] == "Tables/orders"


# ── sl_add_workspace_user / sl_assign_workspace_to_capacity ─────────

@pytest.mark.asyncio
async def test_sl_add_workspace_user(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(201))
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_add_workspace_user(
        "ws-1", "user-1", "User", "Member",
    )
    parsed = json.loads(result)
    assert parsed["status"] == "added"
    _, _, kwargs = fake.calls[0]
    assert kwargs["json"]["principal"]["type"] == "User"
    assert kwargs["json"]["role"] == "Member"


@pytest.mark.asyncio
async def test_sl_assign_workspace_to_capacity(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(202))
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_assign_workspace_to_capacity("ws-1", "cap-1")
    parsed = json.loads(result)
    assert parsed["status"] == "assigned"


# ── sl_run_table_maintenance ────────────────────────────────────────

@pytest.mark.asyncio
async def test_sl_run_table_maintenance_full(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(202))
    install_fake_client(monkeypatch, semantic_link, fake)
    result = await semantic_link.sl_run_table_maintenance(
        "ws-1", "lh-1", "orders", optimize=True, vacuum=True, v_order=True, retention_hours=24,
    )
    parsed = json.loads(result)
    assert parsed["status"] == "maintenance_triggered"
    _, _, kwargs = fake.calls[0]
    config = kwargs["json"]["executionData"]["configuration"]
    assert config["tableName"] == "orders"
    assert config["optimizeSettings"] == {"vOrder": True}
    assert config["vacuumSettings"] == {"retentionPeriod": "24:00:00"}


@pytest.mark.asyncio
async def test_sl_run_table_maintenance_skip_vacuum(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(202))
    install_fake_client(monkeypatch, semantic_link, fake)
    await semantic_link.sl_run_table_maintenance(
        "ws-1", "lh-1", "orders", optimize=True, vacuum=False,
    )
    _, _, kwargs = fake.calls[0]
    config = kwargs["json"]["executionData"]["configuration"]
    assert "vacuumSettings" not in config
    assert "optimizeSettings" in config


async def _zero_sleep(_seconds: float) -> None:
    """Helper to short-circuit ``_async_sleep`` in LRO-poll tests."""
    return None
