"""Per-tool tests for ``mcp_servers.fabric``.

These tests exercise each tool with mocked HTTP responses to catch:
  * connection-leak / wrong-context-manager bugs (the Phase-5 fallback bug)
  * URL/header construction errors
  * JSON shape regressions in the strings returned to the LLM
"""
from __future__ import annotations

import json

import pytest

from mcp_servers import fabric
from tests.unit.mcp_servers.conftest import (
    FakeAsyncClient,
    install_fake_client,
    make_response,
)

# ── Token guards ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fabric_headers_raises_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FABRIC_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="FABRIC_API_TOKEN not set"):
        fabric._fabric_headers()


@pytest.mark.asyncio
async def test_onelake_headers_raises_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ONELAKE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="ONELAKE_TOKEN not set"):
        fabric._onelake_headers()


# ── _validate_path ──────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["../etc/passwd", "Files/../../secret", "Files/./x", "~/x"])
def test_validate_path_rejects_traversal(bad: str) -> None:
    with pytest.raises(ValueError, match="Path traversal not allowed"):
        fabric._validate_path(bad)


@pytest.mark.parametrize("ok", ["Files/data.csv", "Tables/orders", "deeply/nested/file"])
def test_validate_path_accepts_safe(ok: str) -> None:
    assert fabric._validate_path(ok) == ok.strip("/")


# ── fabric_list_workspaces ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_fabric_list_workspaces_ok(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200,
        json_body={"value": [
            {"id": "ws-1", "displayName": "WS One", "type": "Workspace", "capacityId": "cap-1"},
            {"id": "ws-2", "displayName": "WS Two", "type": "Workspace", "capacityId": None},
        ]},
    ))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_list_workspaces()

    parsed = json.loads(result)
    assert len(parsed) == 2
    assert parsed[0] == {"id": "ws-1", "displayName": "WS One", "type": "Workspace", "capacityId": "cap-1"}
    # Verify URL + auth header
    method, url, kwargs = fake.calls[0]
    assert method == "GET"
    assert url.endswith("/v1/workspaces")
    assert kwargs["headers"]["Authorization"] == "Bearer fake-fabric-token"


@pytest.mark.asyncio
async def test_fabric_list_workspaces_error(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(500, text="boom"))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_list_workspaces()
    assert result.startswith("Error listing workspaces: 500")
    assert "boom" in result


# ── fabric_list_items ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fabric_list_items_with_type_filter(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200,
        json_body={"value": [{"id": "lh-1", "displayName": "LH", "type": "Lakehouse"}]},
    ))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_list_items("ws-1", item_type="Lakehouse")

    parsed = json.loads(result)
    assert parsed[0]["id"] == "lh-1"
    # _build_item_links should add hostPath + webUrl
    assert "hostPath" in parsed[0]
    assert "webUrl" in parsed[0]
    assert parsed[0]["hostPath"].endswith("/lakehouses/lh-1")

    _, url, _ = fake.calls[0]
    assert "?type=Lakehouse" in url


@pytest.mark.asyncio
async def test_fabric_list_items_without_filter(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(200, json_body={"value": []}))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_list_items("ws-1")
    assert json.loads(result) == []
    _, url, _ = fake.calls[0]
    assert "?type=" not in url


# ── _get_item_route_segment ─────────────────────────────────────────

@pytest.mark.parametrize("item_type,expected", [
    ("Lakehouse", "lakehouses"),
    ("Notebook", "notebooks"),
    ("SemanticModel", "semanticmodels"),
    # Unknown type → camelCase split + plural
    ("MyCustomItem", "my-custom-items"),
])
def test_get_item_route_segment(item_type: str, expected: str) -> None:
    assert fabric._get_item_route_segment(item_type) == expected


# ── fabric_create_item ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fabric_create_item_with_description(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        201,
        json_body={"id": "lh-new", "type": "Lakehouse", "displayName": "New LH"},
    ))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_create_item(
        "ws-1", "New LH", "Lakehouse", description="my desc"
    )

    parsed = json.loads(result)
    assert parsed["id"] == "lh-new"
    assert "hostPath" in parsed
    method, _, kwargs = fake.calls[0]
    assert method == "POST"
    assert kwargs["json"] == {"displayName": "New LH", "type": "Lakehouse", "description": "my desc"}


@pytest.mark.asyncio
async def test_fabric_create_item_error(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(400, text="bad request"))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_create_item("ws-1", "X", "Lakehouse")
    assert result.startswith("Error creating item: 400")


# ── fabric_delete_item ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fabric_delete_item_ok(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(204))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_delete_item("ws-1", "lh-1")
    parsed = json.loads(result)
    assert parsed == {"status": "deleted", "item_id": "lh-1"}


# ── fabric_list_files (regression: client-leak fallback) ────────────

@pytest.mark.asyncio
async def test_fabric_list_files_root_ok(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200,
        json_body={"paths": [
            {"name": "Files/a.csv", "isDirectory": "false", "contentLength": 123, "lastModified": "now"},
            {"name": "Tables", "isDirectory": "true", "contentLength": None, "lastModified": "now"},
        ]},
    ))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_list_files("ws-1", "lh-1")
    parsed = json.loads(result)
    assert len(parsed) == 2
    assert parsed[0]["name"] == "a.csv"
    assert parsed[0]["isDirectory"] is False
    assert parsed[1]["isDirectory"] is True


@pytest.mark.asyncio
async def test_fabric_list_files_404_fallback_uses_single_client(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    """REGRESSION: the fallback used to call ``httpx.AsyncClient(...).__aenter__()``
    inline per loop iteration, leaking connections. After the Phase-5 fix the
    same outer client must be reused for all fallback probes.
    """
    fake = FakeAsyncClient(responses_by_method={
        "GET": [
            # Initial root list returns 404
            make_response(404, text="not found"),
            # Files/ probe succeeds
            make_response(200, json_body={"paths": [
                {"name": "Files/x", "isDirectory": "false", "contentLength": 1, "lastModified": "t"}
            ]}),
            # Tables/ probe succeeds
            make_response(200, json_body={"paths": [
                {"name": "Tables/y", "isDirectory": "true", "contentLength": None, "lastModified": "t"}
            ]}),
        ],
    })
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_list_files("ws-1", "lh-1")
    parsed = json.loads(result)
    assert len(parsed) == 2
    # The whole sequence of 3 GETs must have happened on the same client
    # (i.e. our single FakeAsyncClient instance saw all 3 calls).
    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_fabric_list_files_404_no_fallback_with_path(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(404))
    install_fake_client(monkeypatch, fabric, fake)
    result = await fabric.fabric_list_files("ws-1", "lh-1", path="Files/sub")
    assert result.startswith("Not found:")


@pytest.mark.asyncio
async def test_fabric_list_files_path_traversal_rejected(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient()
    install_fake_client(monkeypatch, fabric, fake)
    with pytest.raises(ValueError, match="Path traversal"):
        await fabric.fabric_list_files("ws-1", "lh-1", path="../etc")


# ── fabric_read_file ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fabric_read_file_text(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        200, text="col1,col2\n1,2", headers={"content-type": "text/csv"},
    ))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_read_file("ws-1", "lh-1", "Files/a.csv")
    assert result == "col1,col2\n1,2"
    _, _, kwargs = fake.calls[0]
    assert kwargs["headers"]["Range"].startswith("bytes=0-")


@pytest.mark.asyncio
async def test_fabric_read_file_binary_base64(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(
        206,
        text="",
        content=b"\x89PNG\r\n",
        headers={"content-type": "image/png"},
    ))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_read_file("ws-1", "lh-1", "Files/img.png")
    assert result.startswith("[binary,")
    assert "base64" in result


@pytest.mark.asyncio
async def test_fabric_read_file_error(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(404, text="missing"))
    install_fake_client(monkeypatch, fabric, fake)
    result = await fabric.fabric_read_file("ws-1", "lh-1", "Files/none")
    assert result.startswith("Error reading file: 404")


# ── fabric_write_file ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fabric_write_file_three_step_succeeds(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    """Write requires PUT (create) → PATCH (append) → PATCH (flush)."""
    fake = FakeAsyncClient(responses_by_method={
        "PUT": [make_response(201)],
        "PATCH": [make_response(202), make_response(200)],
    })
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_write_file(
        "ws-1", "lh-1", "Files/out.csv", "hello world", overwrite=True
    )
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert parsed["bytes_written"] == len(b"hello world")

    # Three calls: PUT then 2x PATCH
    methods = [c[0] for c in fake.calls]
    assert methods == ["PUT", "PATCH", "PATCH"]
    # Without overwrite, If-None-Match should be sent on the PUT
    put_kwargs = fake.calls[0][2]
    assert "If-None-Match" not in put_kwargs["headers"]


@pytest.mark.asyncio
async def test_fabric_write_file_no_overwrite_sends_if_none_match(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(responses_by_method={
        "PUT": [make_response(201)],
        "PATCH": [make_response(202), make_response(200)],
    })
    install_fake_client(monkeypatch, fabric, fake)

    await fabric.fabric_write_file("ws-1", "lh-1", "Files/x", "data")
    put_kwargs = fake.calls[0][2]
    assert put_kwargs["headers"].get("If-None-Match") == "*"


@pytest.mark.asyncio
async def test_fabric_write_file_create_fails(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(responses_by_method={
        "PUT": [make_response(409, text="exists")],
    })
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_write_file("ws-1", "lh-1", "Files/x", "data")
    assert result.startswith("Error creating file: 409")


# ── fabric_delete_file ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fabric_delete_file_ok(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(202))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_delete_file("ws-1", "lh-1", "Files/old")
    parsed = json.loads(result)
    assert parsed == {"status": "deleted", "path": "Files/old"}


# ── fabric_create_directory ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_fabric_create_directory_ok(
    monkeypatch: pytest.MonkeyPatch, fabric_token_env: None
) -> None:
    fake = FakeAsyncClient(default_response=make_response(201))
    install_fake_client(monkeypatch, fabric, fake)

    result = await fabric.fabric_create_directory("ws-1", "lh-1", "Files/reports")
    parsed = json.loads(result)
    assert parsed == {"status": "created", "path": "Files/reports"}
    _, url, _ = fake.calls[0]
    assert url.endswith("?resource=directory")
