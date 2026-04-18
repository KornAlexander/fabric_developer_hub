"""Parametrized sweep of the simple ``sl_list_*`` and ``sl_get_*`` tools.

Most untested ``sl_*`` tools follow an identical 3-line pattern:
    1. Build URL from ``workspace_id`` (and optional item id)
    2. ``await client.get(url, headers=_headers())``
    3. Return ``json.dumps(resp.json().get("value", []))`` or similar

Rather than write 30 near-identical 5-line tests, this file parametrizes:
  * the tool callable
  * args
  * which ``.get(...)`` key the tool extracts (``"value"`` vs ``"data"``)
  * a URL substring assertion

Adds coverage for ~16 additional tools at ~3 LOC per tool.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from mcp_servers import semantic_link
from tests.unit.mcp_servers.conftest import (
    FakeAsyncClient,
    install_fake_client,
    make_response,
)


# (tool, args, response_body, expected_value, url_must_contain)
SIMPLE_LIST_TOOLS: list[tuple[Callable[..., Any], tuple[Any, ...], dict, list, str]] = [
    # name suggests list of value-shaped objects
    (semantic_link.sl_list_workspace_users, ("ws-1",),
     {"value": [{"principal": {"id": "u-1"}, "role": "Admin"}]},
     [{"principal": {"id": "u-1"}, "role": "Admin"}],
     "/workspaces/ws-1/roleAssignments"),
    (semantic_link.sl_list_warehouses, ("ws-1",),
     {"value": [{"id": "wh-1"}]}, [{"id": "wh-1"}],
     "/workspaces/ws-1/warehouses"),
    (semantic_link.sl_list_sql_endpoints, ("ws-1",),
     {"value": [{"id": "sqle-1"}]}, [{"id": "sqle-1"}],
     "/workspaces/ws-1/sqlEndpoints"),
    (semantic_link.sl_list_data_pipelines, ("ws-1",),
     {"value": [{"id": "p-1"}]}, [{"id": "p-1"}],
     "/workspaces/ws-1/dataPipelines"),
    (semantic_link.sl_list_mirrored_databases, ("ws-1",),
     {"value": [{"id": "m-1"}]}, [{"id": "m-1"}],
     "/workspaces/ws-1/mirroredDatabases"),
    (semantic_link.sl_list_capacities, (),
     {"value": [{"id": "c-1"}]}, [{"id": "c-1"}],
     "/capacities"),
    (semantic_link.sl_list_connections, (),
     {"value": [{"id": "conn-1"}]}, [{"id": "conn-1"}],
     "/connections"),
    (semantic_link.sl_list_gateways, (),
     {"value": [{"id": "g-1"}]}, [{"id": "g-1"}],
     "/gateways"),
    # Lakehouse tables uses "data" not "value"
    (semantic_link.sl_get_lakehouse_tables, ("ws-1", "lh-1"),
     {"data": [{"name": "orders"}]}, [{"name": "orders"}],
     "/lakehouses/lh-1/tables"),
    # List-shortcuts also uses "value"
    (semantic_link.sl_list_shortcuts, ("ws-1", "lh-1"),
     {"value": [{"name": "sc-1"}]}, [{"name": "sc-1"}],
     "/items/lh-1/shortcuts"),
    # Refresh history → value
    (semantic_link.sl_get_refresh_history, ("ws-1", "ds-1"),
     {"value": [{"id": "ref-1", "status": "Completed"}]},
     [{"id": "ref-1", "status": "Completed"}],
     "/datasets/ds-1/refreshes"),
    # Item schedules → value
    (semantic_link.sl_list_item_schedules, ("ws-1", "i-1"),
     {"value": [{"id": "s-1"}]}, [{"id": "s-1"}],
     "/items/i-1/jobs/DefaultJob/schedules"),
    # Admin list datasets
    (semantic_link.sl_admin_list_datasets, (),
     {"value": [{"id": "ds-1"}]}, [{"id": "ds-1"}],
     "/admin/datasets"),
    # Admin list workspace users
    (semantic_link.sl_admin_list_workspace_users, ("ws-1",),
     {"value": [{"emailAddress": "u@x"}]}, [{"emailAddress": "u@x"}],
     "/admin/groups/ws-1/users"),
    # Admin list dataset users
    (semantic_link.sl_admin_list_dataset_users, ("ds-1",),
     {"value": [{"emailAddress": "u@x"}]}, [{"emailAddress": "u@x"}],
     "/admin/datasets/ds-1/users"),
]


@pytest.mark.parametrize(
    "tool,args,response_body,expected_value,url_must_contain",
    SIMPLE_LIST_TOOLS,
    ids=[t[0].__name__ for t in SIMPLE_LIST_TOOLS],
)
@pytest.mark.asyncio
async def test_simple_list_tool(
    tool: Callable[..., Any],
    args: tuple[Any, ...],
    response_body: dict,
    expected_value: list,
    url_must_contain: str,
    monkeypatch: pytest.MonkeyPatch,
    fabric_token_env: None,
) -> None:
    fake = FakeAsyncClient(default_response=make_response(200, json_body=response_body))
    install_fake_client(monkeypatch, semantic_link, fake)

    result = await tool(*args)

    assert json.loads(result) == expected_value
    assert len(fake.calls) == 1
    method, url, kwargs = fake.calls[0]
    assert method == "GET"
    assert url_must_contain in url, f"expected {url_must_contain!r} in {url!r}"
    assert kwargs["headers"]["Authorization"] == "Bearer fake-fabric-token"


@pytest.mark.parametrize(
    "tool,args",
    [(t[0], t[1]) for t in SIMPLE_LIST_TOOLS],
    ids=[t[0].__name__ for t in SIMPLE_LIST_TOOLS],
)
@pytest.mark.asyncio
async def test_simple_list_tool_error_path(
    tool: Callable[..., Any],
    args: tuple[Any, ...],
    monkeypatch: pytest.MonkeyPatch,
    fabric_token_env: None,
) -> None:
    """Every list tool MUST return an ``"Error: <code> ..."`` string on
    non-200 — never raise."""
    fake = FakeAsyncClient(default_response=make_response(500, text="upstream"))
    install_fake_client(monkeypatch, semantic_link, fake)

    result = await tool(*args)
    assert result.startswith("Error: 500")
    assert "upstream" in result


# ── Action tools (POST/PATCH/DELETE returning a status JSON envelope) ─

# (tool, args, http_method, expected_status_value, success_status_code)
ACTION_TOOLS: list[tuple[Callable[..., Any], tuple[Any, ...], str, str, int]] = [
    (semantic_link.sl_cancel_refresh, ("ws-1", "ds-1", "ref-1"), "DELETE", "cancelled", 200),
    (semantic_link.sl_rebind_report, ("ws-1", "r-1", "ds-2"), "POST", "rebound", 200),
    (semantic_link.sl_run_data_pipeline, ("ws-1", "p-1"), "POST", "pipeline_triggered", 202),
    (semantic_link.sl_run_item_job, ("ws-1", "i-1"), "POST", "job_triggered", 202),
    (semantic_link.sl_set_endorsement, ("ws-1", "i-1", "Promoted"), "PATCH", "endorsed", 200),
    (semantic_link.sl_commit_to_git, ("ws-1", "checkpoint"), "POST", "committed", 202),
    (semantic_link.sl_update_from_git, ("ws-1",), "POST", "update_triggered", 202),
]


@pytest.mark.parametrize(
    "tool,args,method,status_value,success_code",
    ACTION_TOOLS,
    ids=[t[0].__name__ for t in ACTION_TOOLS],
)
@pytest.mark.asyncio
async def test_action_tool_success_envelope(
    tool: Callable[..., Any],
    args: tuple[Any, ...],
    method: str,
    status_value: str,
    success_code: int,
    monkeypatch: pytest.MonkeyPatch,
    fabric_token_env: None,
) -> None:
    fake = FakeAsyncClient(default_response=make_response(success_code))
    install_fake_client(monkeypatch, semantic_link, fake)

    result = await tool(*args)
    parsed = json.loads(result)
    assert parsed["status"] == status_value
    assert fake.calls[0][0] == method


# ── Definition tools (LRO 202 → poll path) ──────────────────────────

@pytest.mark.parametrize(
    "tool,args,parts_key",
    [
        (semantic_link.sl_get_report_definition, ("ws-1", "r-1"), [{"path": "report.json", "payload": "abc"}]),
        (semantic_link.sl_get_notebook_definition, ("ws-1", "nb-1"), [{"path": "notebook.ipynb", "payload": "xyz"}]),
    ],
    ids=["sl_get_report_definition", "sl_get_notebook_definition"],
)
@pytest.mark.asyncio
async def test_definition_tool_inline_200(
    tool: Callable[..., Any],
    args: tuple[Any, ...],
    parts_key: list,
    monkeypatch: pytest.MonkeyPatch,
    fabric_token_env: None,
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200, json_body={"definition": {"parts": parts_key}},
    ))
    install_fake_client(monkeypatch, semantic_link, fake)

    result = await tool(*args)
    assert json.loads(result) == parts_key
