"""Sanity tests for ``services.http_client.HttpClientService``.

Covers the deterministic helpers (header construction, content shaping,
SubjectAndAppToken handling) and the retry-on-5xx contract using a
``MockTransport`` to avoid real network I/O.
"""
from __future__ import annotations

import httpx
import pytest

from services.http_client import HttpClientService


@pytest.fixture
def svc():
    s = HttpClientService()
    yield s
    # close() is async; run it to release the underlying httpx AsyncClient.
    import asyncio
    asyncio.run(s.close())


def test_get_headers_bearer_for_jwt(svc: HttpClientService) -> None:
    h = svc._get_headers("eyJhbGc.token")
    assert h["Authorization"] == "Bearer eyJhbGc.token"
    assert h["User-Agent"].startswith("Microsoft-Fabric-Workload/")


def test_get_headers_passthrough_for_subjectandapptoken(svc: HttpClientService) -> None:
    """REGRESSION: SubjectAndAppToken must be passed through verbatim — no
    extra "Bearer " prefix — or the workload host rejects the auth header."""
    raw = "SubjectAndAppToken1.0 subjectToken=\"abc\", appToken=\"xyz\""
    h = svc._get_headers(raw)
    assert h["Authorization"] == raw


@pytest.mark.asyncio
async def test_make_request_retries_on_500_then_succeeds(monkeypatch) -> None:
    """5xx responses should trigger up to 2 retries before succeeding on
    the third attempt; backoff is monkeypatched to a no-op."""
    monkeypatch.setattr("asyncio.sleep", lambda *_a, **_k: _no_op())

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text="ok")

    svc = HttpClientService()
    await svc._client.aclose()
    svc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        resp = await svc.get("https://api.test/x", token="t")
        assert resp.status_code == 200
        assert calls["n"] == 3
    finally:
        await svc._client.aclose()
        svc._closed = True


@pytest.mark.asyncio
async def test_make_request_raises_after_max_retries_on_5xx(monkeypatch) -> None:
    monkeypatch.setattr("asyncio.sleep", lambda *_a, **_k: _no_op())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    svc = HttpClientService()
    await svc._client.aclose()
    svc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await svc.get("https://api.test/x", token="t")
        assert exc_info.value.response.status_code == 503
    finally:
        await svc._client.aclose()
        svc._closed = True


@pytest.mark.asyncio
async def test_4xx_does_not_retry(monkeypatch) -> None:
    """REGRESSION: client errors (4xx) must NOT be retried — that would
    waste tokens for unauthorized/forbidden calls."""
    monkeypatch.setattr("asyncio.sleep", lambda *_a, **_k: _no_op())

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text="forbidden")

    svc = HttpClientService()
    await svc._client.aclose()
    svc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await svc.get("https://api.test/x", token="t")
        assert calls["n"] == 1
    finally:
        await svc._client.aclose()
        svc._closed = True


@pytest.mark.asyncio
async def test_post_dict_content_serialises_as_json() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["content"] = request.content
        return httpx.Response(200)

    svc = HttpClientService()
    await svc._client.aclose()
    svc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await svc.post("https://api.test/x", content={"k": "v"}, token="t")
        assert captured["headers"].get("content-type") == "application/json"
        assert b'"k"' in captured["content"]
        assert b'"v"' in captured["content"]
    finally:
        await svc._client.aclose()
        svc._closed = True


@pytest.mark.asyncio
async def test_put_empty_string_sends_empty_body() -> None:
    """Some Fabric APIs require a 0-byte PUT body (e.g. file commit)."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content"] = request.content
        return httpx.Response(200)

    svc = HttpClientService()
    await svc._client.aclose()
    svc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await svc.put("https://api.test/x", content="", token="t")
        assert captured["content"] == b""
    finally:
        await svc._client.aclose()
        svc._closed = True


async def _no_op() -> None:
    """Awaitable used to monkeypatch asyncio.sleep to a no-op."""
    return None


# ---------------------------------------------------------------------------
# Request-ID propagation (Batch 13 — outbound correlation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_request_id_header_set_from_contextvar() -> None:
    """When a request-ID is bound via ``request_id_scope``, the shared
    client must stamp it onto every outbound call so downstream services
    (Fabric, Copilot, MCP) can correlate their logs back to the inbound
    user request."""
    from services.correlation import request_id_scope

    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req_id"] = request.headers.get("X-Request-ID")
        return httpx.Response(200, json={})

    svc = HttpClientService()
    # Swap in a MockTransport but keep the real event_hooks so the hook runs.
    await svc._client.aclose()
    svc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [svc._inject_request_id]},
    )
    try:
        with request_id_scope("req-outbound-abc"):
            await svc._client.get("https://api.test/ping")
        assert captured["req_id"] == "req-outbound-abc"
    finally:
        await svc._client.aclose()
        svc._closed = True


@pytest.mark.asyncio
async def test_outbound_request_id_absent_when_no_scope() -> None:
    """Outside any request scope, the hook must NOT stamp the placeholder
    ``"-"`` onto the header — keep the header absent entirely so downstream
    services can't confuse "no request context" with a real ID."""
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req_id"] = request.headers.get("X-Request-ID")
        return httpx.Response(200, json={})

    svc = HttpClientService()
    await svc._client.aclose()
    svc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [svc._inject_request_id]},
    )
    try:
        # No request_id_scope — the ContextVar is the default "-".
        await svc._client.get("https://api.test/ping")
        assert captured["req_id"] is None
    finally:
        await svc._client.aclose()
        svc._closed = True


@pytest.mark.asyncio
async def test_outbound_request_id_respects_explicit_caller_header() -> None:
    """If a caller has already set ``X-Request-ID`` on the request (e.g.
    explicitly forwarding an ID from a non-HTTP source), the hook must not
    overwrite it."""
    from services.correlation import request_id_scope

    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req_id"] = request.headers.get("X-Request-ID")
        return httpx.Response(200, json={})

    svc = HttpClientService()
    await svc._client.aclose()
    svc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [svc._inject_request_id]},
    )
    try:
        with request_id_scope("req-from-ctx"):
            await svc._client.get(
                "https://api.test/ping",
                headers={"X-Request-ID": "req-explicit-override"},
            )
        assert captured["req_id"] == "req-explicit-override"
    finally:
        await svc._client.aclose()
        svc._closed = True
