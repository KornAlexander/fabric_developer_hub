"""Unit tests for the first-party Web Access MCP server."""
from __future__ import annotations

import json

import pytest

from mcp_servers import web_access as web


@pytest.mark.asyncio
async def test_web_fetch_rejects_non_https_url() -> None:
    raw = await web.web_fetch_url("http://example.com")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "WebAccessError"
    assert "https" in body["error"]


@pytest.mark.asyncio
async def test_web_fetch_rejects_localhost_url() -> None:
    raw = await web.web_fetch_url("https://localhost/docs")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "WebAccessError"
    assert "localhost" in body["error"]


@pytest.mark.asyncio
async def test_web_fetch_rejects_private_ip_url() -> None:
    raw = await web.web_fetch_url("https://10.0.0.1/status")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorType"] == "WebAccessError"
    assert "blocked" in body["error"]


def test_extract_text_removes_scripts_and_returns_title() -> None:
    title, text = web._extract_text(
        """
        <html><head><title>Example Page</title><script>secret()</script></head>
        <body><h1>Hello</h1><p>Readable text.</p></body></html>
        """,
        "text/html",
    )

    assert title == "Example Page"
    assert "Hello" in text
    assert "Readable text." in text
    assert "secret" not in text


def test_clean_duckduckgo_redirect_url() -> None:
    url = web._clean_ddg_url("/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&rut=abc")

    assert url == "https://example.com/docs"


def test_duckduckgo_parser_extracts_results() -> None:
    parser = web._DuckDuckGoParser()
    parser.feed(
        """
        <div class="result">
          <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Example A</a>
          <a class="result__snippet">A useful snippet</a>
        </div>
        """,
    )

    assert parser.results == [{
        "title": "Example A",
        "url": "https://example.com/a",
        "snippet": "A useful snippet",
    }]
