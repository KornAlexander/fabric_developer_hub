"""Shared helpers for MCP server tools."""
from __future__ import annotations

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
