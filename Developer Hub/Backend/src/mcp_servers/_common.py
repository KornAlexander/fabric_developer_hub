"""Shared helpers for MCP server tools."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx


def format_http_error(resp: httpx.Response, op_name: str | None = None) -> str:
    """Format an HTTP error response in the canonical MCP-tool style.

    The output preserves the historical shapes used across `fabric.py` and
    `semantic_link.py`:

    * with ``op_name``: ``"Error <op>: <status> — <text[:500]>"`` (fabric)
    * without:           ``"Error: <status> — <text[:500]>"``    (semantic_link)

    Args:
        resp: The httpx response that signalled failure.
        op_name: Optional short verb-phrase like "listing workspaces".
    """
    if op_name:
        return f"Error {op_name}: {resp.status_code} — {resp.text[:500]}"
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ---------------------------------------------------------------------------
# Shared httpx client pool
# ---------------------------------------------------------------------------
#
# Each MCP server module (``fabric.py``, ``semantic_link.py``) runs as a
# long-lived stdio subprocess and used to create/destroy a new
# ``httpx.AsyncClient`` on every tool call (50+ call-sites). That paid a
# TLS-handshake cost for every request even though consecutive calls hit the
# same hosts (``api.fabric.microsoft.com``, ``onelake.dfs.fabric.microsoft.com``,
# ``api.powerbi.com``).
#
# ``shared_client`` is an ``async with``-compatible helper that returns a
# process-wide pooled client **without closing it** when the block exits —
# the client lives for the lifetime of the subprocess, so repeat calls
# reuse pooled TLS connections. Callers keep the existing
# ``async with shared_client(30.0) as client:`` shape; only the target of
# the context manager changes.

_CLIENTS: dict[float, httpx.AsyncClient] = {}
_LIMITS = httpx.Limits(max_keepalive_connections=20, max_connections=50)


def _get_or_create(timeout: float) -> httpx.AsyncClient:
    client = _CLIENTS.get(timeout)
    if client is None:
        client = httpx.AsyncClient(timeout=timeout, limits=_LIMITS)
        _CLIENTS[timeout] = client
    return client


@asynccontextmanager
async def shared_client(timeout: float) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a process-wide pooled ``httpx.AsyncClient`` for this timeout.

    Unlike ``async with httpx.AsyncClient(...)``, the returned client is
    NOT closed when the ``async with`` block exits. It is cached per
    ``timeout`` value and reused for the lifetime of the (MCP-subprocess)
    process, so consecutive tool calls share pooled TLS connections.

    Args:
        timeout: Per-request timeout in seconds. Each distinct value gets
            its own pooled client (the Fabric modules use 30.0 and 60.0).
    """
    yield _get_or_create(timeout)

