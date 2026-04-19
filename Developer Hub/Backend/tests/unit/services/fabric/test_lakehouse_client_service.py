"""Targeted tests for ``services.fabric.lakehouse_client_service``.

Covers:
  * ``get_lakehouse_tables`` — the path-parsing logic that converts OneLake
    DFS path lists into LakehouseTable models, including:
      - bare-table layout: ``<lhId>/Tables/<tableName>``
      - schema-aware layout: ``<lhId>/Tables/<schema>/<tableName>``
      - shortcut entries (ADLS-backed)
      - filtering by ``_delta_log`` suffix
  * ``get_lakehouse_files`` — directory-stripping logic
  * ``get_fabric_lakehouse`` — happy path + exception → None contract
  * ``_get_path_list`` — URL construction (encoded directory, recursive flag)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from app.core.service_registry import ServiceRegistry
from services.fabric.lakehouse_client_service import (
    LakehouseClientService,
    get_lakehouse_client_service,
)


def _resp(status: int, json_body: dict | None = None, raise_for_status: bool = False) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.json.return_value = json_body or {}
    if raise_for_status:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=r,
        )
    else:
        r.raise_for_status.return_value = None
    return r


@pytest.fixture
def svc() -> LakehouseClientService:
    s = LakehouseClientService()
    s._http_client_service = AsyncMock()
    return s


WS = UUID("11111111-1111-1111-1111-111111111111")
LH = UUID("22222222-2222-2222-2222-222222222222")


# ── get_lakehouse_tables ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_lakehouse_tables_bare_layout(svc) -> None:
    svc._http_client_service.get.return_value = _resp(200, {
        "paths": [
            {
                "name": f"{LH}/Tables/customers/_delta_log",
                "isDirectory": True,
                "isShortcut": False,
                "accountType": "",
            },
            {
                "name": f"{LH}/Tables/orders/_delta_log",
                "isDirectory": True,
                "isShortcut": False,
                "accountType": "",
            },
        ],
    })
    tables = await svc.get_lakehouse_tables("tok", WS, LH)
    names = sorted(t.name for t in tables)
    assert names == ["customers", "orders"]
    # Schema-less layout → schema field is None
    assert all(t.schema_name is None for t in tables)


@pytest.mark.asyncio
async def test_get_lakehouse_tables_schema_aware_layout(svc) -> None:
    """REGRESSION: 4-part paths set the schema field; 3-part paths leave it None."""
    svc._http_client_service.get.return_value = _resp(200, {
        "paths": [
            {
                "name": f"{LH}/Tables/dbo/customers/_delta_log",
                "isDirectory": True,
                "isShortcut": False,
                "accountType": "",
            },
        ],
    })
    tables = await svc.get_lakehouse_tables("tok", WS, LH)
    assert len(tables) == 1
    assert tables[0].name == "customers"
    assert tables[0].schema_name == "dbo"


@pytest.mark.asyncio
async def test_get_lakehouse_tables_includes_adls_shortcuts(svc) -> None:
    """ADLS shortcuts should be surfaced as tables even without a _delta_log."""
    svc._http_client_service.get.return_value = _resp(200, {
        "paths": [
            {
                "name": f"{LH}/Tables/external_table",
                "isDirectory": True,
                "isShortcut": True,
                "accountType": "ADLS",
            },
            {
                # Non-ADLS shortcut → ignored
                "name": f"{LH}/Tables/onelake_shortcut",
                "isDirectory": True,
                "isShortcut": True,
                "accountType": "OneLake",
            },
        ],
    })
    tables = await svc.get_lakehouse_tables("tok", WS, LH)
    assert [t.name for t in tables] == ["external_table"]


@pytest.mark.asyncio
async def test_get_lakehouse_tables_returns_empty_list_when_no_match(svc) -> None:
    svc._http_client_service.get.return_value = _resp(200, {"paths": []})
    assert await svc.get_lakehouse_tables("tok", WS, LH) == []


# ── get_lakehouse_files ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_lakehouse_files_strips_directory_prefix(svc) -> None:
    directory = f"{LH}/Files/"
    svc._http_client_service.get.return_value = _resp(200, {
        "paths": [
            {"name": f"{directory}data.csv", "isDirectory": False},
            {"name": f"{directory}sub/nested.txt", "isDirectory": False},
            {"name": f"{directory}emptydir", "isDirectory": True},
        ],
    })
    files = await svc.get_lakehouse_files("tok", WS, LH)
    by_name = {f.name: f for f in files}
    assert by_name["data.csv"].path == "data.csv"
    assert by_name["data.csv"].is_directory is False
    assert by_name["nested.txt"].path == "sub/nested.txt"
    assert by_name["emptydir"].is_directory is True


# ── get_fabric_lakehouse ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_fabric_lakehouse_returns_item_on_200(svc) -> None:
    svc._http_client_service.get.return_value = _resp(200, {
        "id": str(LH),
        "displayName": "MyLakehouse",
        "type": "Lakehouse",
        "workspaceId": str(WS),
    })
    item = await svc.get_fabric_lakehouse("tok", WS, LH)
    assert item is not None
    assert item.display_name == "MyLakehouse"


@pytest.mark.asyncio
async def test_get_fabric_lakehouse_returns_none_on_http_error(svc) -> None:
    svc._http_client_service.get.return_value = _resp(404, raise_for_status=True)
    assert await svc.get_fabric_lakehouse("tok", WS, LH) is None


@pytest.mark.asyncio
async def test_get_fabric_lakehouse_returns_none_on_network_error(svc) -> None:
    svc._http_client_service.get.side_effect = httpx.ConnectError("net")
    assert await svc.get_fabric_lakehouse("tok", WS, LH) is None


# ── _get_path_list URL construction ─────────────────────────────────


@pytest.mark.asyncio
async def test_get_path_list_encodes_directory(svc) -> None:
    svc._http_client_service.get.return_value = _resp(200, {"paths": []})
    await svc._get_path_list("tok", WS, "Tables/has space/sub", recursive=True)
    args, _ = svc._http_client_service.get.call_args
    url = args[0]
    assert "recursive=true" in url
    assert "resource=filesystem" in url
    assert "getShortcutMetadata=true" in url
    # urllib.parse.quote leaves '/' unencoded by default; spaces become %20.
    assert "directory=Tables/has%20space/sub" in url


@pytest.mark.asyncio
async def test_get_path_list_propagates_http_error(svc) -> None:
    """REGRESSION: non-2xx must surface to the caller — only the public
    methods (get_lakehouse_tables / get_fabric_lakehouse) decide whether
    to swallow it."""
    svc._http_client_service.get.return_value = _resp(500, raise_for_status=True)
    with pytest.raises(httpx.HTTPStatusError):
        await svc._get_path_list("tok", WS, "Tables/")


# ── module-level singleton accessor ─────────────────────────────────


def test_get_lakehouse_client_service_raises_when_not_registered() -> None:

    ServiceRegistry._instance = None
    with pytest.raises(RuntimeError, match="not initialized"):
        get_lakehouse_client_service()
