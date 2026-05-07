"""Shared helpers for MCP-server unit tests.

The MCP-server modules (``mcp_servers.fabric`` and ``mcp_servers.semantic_link``)
construct ``httpx.AsyncClient`` instances inline inside each tool function
(per the MCP execution model — one short-lived client per tool call). To test
those tools without real HTTP traffic, we patch the module's ``httpx.AsyncClient``
attribute with a factory that returns an async-context-manager wrapping an
``AsyncMock`` client. Tests configure the mock client's responses and assert
on the calls made.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest


def make_response(
    status_code: int = 200,
    json_body: Any = None,
    text: str | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a fake ``httpx.Response``-like mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_body is not None:
        resp.json.return_value = json_body
        resp.text = text if text is not None else ""
    else:
        resp.json.side_effect = ValueError("no json body configured")
        resp.text = text or ""
    resp.content = content if content is not None else (text or "").encode()
    return resp


class FakeAsyncClient:
    """Mimics enough of ``httpx.AsyncClient`` for the MCP tools.

    Each method (``get``, ``post``, ``put``, ``patch``, ``delete``) returns
    the next response from ``responses_by_method[method]`` (FIFO), or a generic
    ``default_response`` if the queue is empty. All calls are recorded in
    ``self.calls`` for assertion.
    """

    def __init__(
        self,
        responses_by_method: dict[str, list[MagicMock]] | None = None,
        default_response: MagicMock | None = None,
    ) -> None:
        self.responses_by_method = responses_by_method or {}
        self.default_response = default_response or make_response(200, json_body={})
        self.calls: list[tuple[str, str, dict]] = []

    async def _call(self, method: str, url: str, **kwargs: Any) -> MagicMock:
        self.calls.append((method, url, kwargs))
        queue = self.responses_by_method.get(method, [])
        if queue:
            return queue.pop(0)
        return self.default_response

    async def get(self, url: str, **kwargs: Any) -> MagicMock:
        return await self._call("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> MagicMock:
        return await self._call("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> MagicMock:
        return await self._call("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> MagicMock:
        return await self._call("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> MagicMock:
        return await self._call("DELETE", url, **kwargs)


def install_fake_client(monkeypatch: pytest.MonkeyPatch, module: Any, fake: FakeAsyncClient) -> None:
    """Patch ``module.shared_client`` so tools receive ``fake`` inside their
    ``async with shared_client(...) as client:`` blocks.

    The MCP-server modules (``fabric``, ``semantic_link``) call the shared
    ``shared_client(timeout)`` helper from ``mcp_servers._common`` to obtain
    a pooled ``httpx.AsyncClient``. Tests swap that symbol with an async
    context manager that yields this fake so request behaviour can be
    asserted without real HTTP traffic.
    """

    @asynccontextmanager
    async def _factory(*args: Any, **kwargs: Any):
        yield fake

    monkeypatch.setattr(module, "shared_client", _factory)


@pytest.fixture
def fabric_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject the per-request tokens that MCP tools read from os.environ."""
    monkeypatch.setenv("FABRIC_API_TOKEN", "fake-fabric-token")
    monkeypatch.setenv("POWERBI_API_TOKEN", "fake-powerbi-token")
    monkeypatch.setenv("ONELAKE_TOKEN", "fake-onelake-token")
