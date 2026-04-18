"""Targeted tests for ``services.fabric.onelake_client_service``.

This module is the OneLake DFS REST adapter — most coverage misses came
from broad ``except Exception`` blocks that swallow error paths. Tests
exercise both the success branches and the documented error contracts:
``check_if_file_exists`` returning False on 404 / network error,
``get_onelake_file`` returning ``""`` on non-2xx, etc.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from services.fabric.onelake_client_service import (
    OneLakeClientService,
    get_onelake_client_service,
)


def _resp(status: int, text: str = "", json_body: dict | None = None) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.text = text
    if json_body is not None:
        import json as _json
        r.text = _json.dumps(json_body)
    return r


@pytest.fixture
def svc() -> OneLakeClientService:
    s = OneLakeClientService()
    s._http_client_service = AsyncMock()
    return s


# ── check_if_file_exists ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_if_file_exists_returns_true_on_200(svc) -> None:
    svc._http_client_service.head.return_value = _resp(200)
    assert await svc.check_if_file_exists("tok", "ws/item/Files/x.csv") is True


@pytest.mark.asyncio
async def test_check_if_file_exists_returns_false_on_404(svc) -> None:
    svc._http_client_service.head.return_value = _resp(404)
    assert await svc.check_if_file_exists("tok", "ws/item/Files/missing") is False


@pytest.mark.asyncio
async def test_check_if_file_exists_returns_false_on_unexpected_status(svc) -> None:
    """500 is not 200/404 — log and return False per the contract."""
    svc._http_client_service.head.return_value = _resp(500, "boom")
    assert await svc.check_if_file_exists("tok", "ws/item/x") is False


@pytest.mark.asyncio
async def test_check_if_file_exists_returns_false_on_exception(svc) -> None:
    svc._http_client_service.head.side_effect = httpx.ConnectError("network down")
    assert await svc.check_if_file_exists("tok", "ws/item/x") is False


# ── get_onelake_folder_names ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_onelake_folder_names_filters_directories(svc) -> None:
    svc._http_client_service.get.return_value = _resp(200, json_body={
        "paths": [
            {"name": "ws/item/Tables/foo", "isDirectory": True},
            {"name": "ws/item/Tables/foo/bar.parquet", "isDirectory": False},
            {"name": "ws/item/Tables/baz", "isDirectory": True},
        ],
    })
    names = await svc.get_onelake_folder_names(
        "tok",
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
    )
    assert names == ["ws/item/Tables/foo", "ws/item/Tables/baz"]


@pytest.mark.asyncio
async def test_get_onelake_folder_names_returns_none_on_404(svc) -> None:
    svc._http_client_service.get.return_value = _resp(404)
    assert await svc.get_onelake_folder_names(
        "tok",
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
    ) is None


@pytest.mark.asyncio
async def test_get_onelake_folder_names_returns_none_on_exception(svc) -> None:
    svc._http_client_service.get.side_effect = RuntimeError("oops")
    assert await svc.get_onelake_folder_names(
        "tok",
        UUID("11111111-1111-1111-1111-111111111111"),
        UUID("22222222-2222-2222-2222-222222222222"),
    ) is None


# ── get_onelake_file ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_onelake_file_returns_text_on_200(svc) -> None:
    svc._http_client_service.get.return_value = _resp(200, "hello world")
    assert await svc.get_onelake_file("tok", "ws/item/Files/x") == "hello world"


@pytest.mark.asyncio
async def test_get_onelake_file_returns_empty_on_non_2xx(svc) -> None:
    """REGRESSION: contract says return ``""`` on error, not raise — callers
    distinguish empty file from missing file by also calling
    check_if_file_exists()."""
    svc._http_client_service.get.return_value = _resp(403, "forbidden")
    assert await svc.get_onelake_file("tok", "ws/item/Files/x") == ""


@pytest.mark.asyncio
async def test_get_onelake_file_returns_empty_on_exception(svc) -> None:
    svc._http_client_service.get.side_effect = httpx.ReadTimeout("slow")
    assert await svc.get_onelake_file("tok", "ws/item/Files/x") == ""


# ── delete_onelake_file ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_onelake_file_uses_recursive_query(svc) -> None:
    svc._http_client_service.delete.return_value = _resp(200)
    await svc.delete_onelake_file("tok", "ws/item/Files/dir")
    args, _ = svc._http_client_service.delete.call_args
    url = args[0]
    assert "?recursive=true" in url


@pytest.mark.asyncio
async def test_delete_onelake_file_swallows_errors(svc) -> None:
    """Caller-fire-and-forget contract: never raise."""
    svc._http_client_service.delete.return_value = _resp(500, "boom")
    await svc.delete_onelake_file("tok", "ws/item/Files/dir")  # no exception
    svc._http_client_service.delete.side_effect = RuntimeError("net")
    await svc.delete_onelake_file("tok", "ws/item/Files/dir")  # still no exception


# ── write_to_onelake_file (orchestrates create + append + flush) ────


@pytest.mark.asyncio
async def test_write_to_onelake_file_happy_path_calls_put_then_two_patches(svc) -> None:
    svc._http_client_service.put.return_value = _resp(201)
    svc._http_client_service.patch.return_value = _resp(200)
    await svc.write_to_onelake_file("tok", "ws/item/Files/x", "hello")
    assert svc._http_client_service.put.call_count == 1
    # _append_to_onelake_file does two patches: append + flush
    assert svc._http_client_service.patch.call_count == 2

    append_args, _ = svc._http_client_service.patch.call_args_list[0]
    assert "action=append" in append_args[0]
    flush_args, _ = svc._http_client_service.patch.call_args_list[1]
    assert "action=flush" in flush_args[0]
    # Flush position should equal the byte length of the encoded content
    assert "position=5" in flush_args[0]  # len(b"hello") == 5


@pytest.mark.asyncio
async def test_write_to_onelake_file_aborts_when_create_fails(svc) -> None:
    """If PUT (create) fails, the append/flush PATCHes must NOT run."""
    svc._http_client_service.put.return_value = _resp(403, "no perm")
    await svc.write_to_onelake_file("tok", "ws/item/Files/x", "hi")
    svc._http_client_service.patch.assert_not_called()


# ── helpers ─────────────────────────────────────────────────────────


def test_get_onelake_file_path_format() -> None:
    svc = OneLakeClientService()
    assert svc.get_onelake_file_path("ws-1", "item-1", "data.csv") == \
        "ws-1/item-1/Files/data.csv"


def test_build_query_parameter_helpers() -> None:
    svc = OneLakeClientService()
    assert svc._build_append_query_parameters() == "position=0&action=append"
    assert svc._build_flush_query_parameters(42) == "position=42&action=flush"
    folders_q = svc._build_get_onelake_folders_query_parameters(
        UUID("33333333-3333-3333-3333-333333333333"),
    )
    assert "directory=33333333-3333-3333-3333-333333333333" in folders_q
    assert "resource=filesystem" in folders_q
    assert "recursive=false" in folders_q


# ── module-level singleton accessor ─────────────────────────────────


def test_get_onelake_client_service_raises_when_not_registered(monkeypatch) -> None:
    from app.core.service_registry import ServiceRegistry

    ServiceRegistry._instance = None
    with pytest.raises(RuntimeError, match="not initialized"):
        get_onelake_client_service()
