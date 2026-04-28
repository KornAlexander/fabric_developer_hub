"""First-party web access MCP server.

The tools here are intentionally narrow: public HTTPS fetch and public web
search. URL validation defends against SSRF by blocking credentials, non-HTTPS
schemes, localhost/private/link-local IPs, and redirect targets that fail the
same checks.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("web-access", log_level="WARNING")

_USER_AGENT = "AgentHub-WebAccess/1.0 (+https://github.com/github/copilot)"
_MAX_FETCH_BYTES = 1_000_000
_MAX_TEXT_CHARS = 24_000
_MAX_REDIRECTS = 5
_SEARCH_URL = "https://duckduckgo.com/html/"
_ALLOWED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/rss+xml",
    "application/atom+xml",
)


class WebAccessError(ValueError):
    """Raised when a web access request violates the safety profile."""


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _bounded_chars(value: int | None) -> int:
    try:
        requested = int(value or 12_000)
    except (TypeError, ValueError):
        requested = 12_000
    return max(500, min(requested, _MAX_TEXT_CHARS))


def _normalize_url(url: str) -> str:
    value = (url or "").strip()
    if len(value) > 2_000:
        raise WebAccessError("url is too long")
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise WebAccessError("only https URLs are allowed")
    if not parsed.hostname:
        raise WebAccessError("url must include a hostname")
    if parsed.username or parsed.password:
        raise WebAccessError("embedded credentials in URLs are not allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise WebAccessError("localhost URLs are not allowed")
    port = parsed.port
    if port is not None and port not in {443, 8443}:
        raise WebAccessError("only standard HTTPS ports are allowed")
    return value


def _is_blocked_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any((
        ip.is_private,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ))


async def _assert_public_host(url: str) -> None:
    parsed = urlparse(_normalize_url(url))
    host = parsed.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is None:
        try:
            infos = await asyncio.to_thread(
                socket.getaddrinfo,
                host,
                parsed.port or 443,
                proto=socket.IPPROTO_TCP,
            )
        except OSError as exc:
            raise WebAccessError("hostname did not resolve") from exc
        addresses = {info[4][0] for info in infos}
        if not addresses:
            raise WebAccessError("hostname did not resolve")
        if any(_is_blocked_ip(address) for address in addresses):
            raise WebAccessError("hostname resolves to a blocked private or local address")
        return

    if _is_blocked_ip(str(ip)):
        raise WebAccessError("IP address is private, local, reserved, or otherwise blocked")


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title = (self.title + " " + text).strip()
        self.parts.append(text)

    def readable_text(self) -> str:
        text = unescape(" ".join(self.parts))
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()


def _extract_text(content: str, content_type: str) -> tuple[str, str]:
    if "html" not in content_type.lower():
        text = re.sub(r"[ \t\f\v]+", " ", content)
        return "", text.strip()
    parser = _ReadableHTMLParser()
    parser.feed(content)
    return parser.title, parser.readable_text()


async def _fetch_public_url(url: str, *, max_chars: int | None = None) -> dict[str, Any]:
    current = _normalize_url(url)
    await _assert_public_host(current)
    max_text = _bounded_chars(max_chars)
    redirects: list[str] = []
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html, text/plain, application/json;q=0.9, */*;q=0.1"}

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, headers=headers) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            response = await client.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    break
                next_url = _normalize_url(urljoin(current, location))
                await _assert_public_host(next_url)
                redirects.append(next_url)
                current = next_url
                continue

            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if response.status_code >= 400:
                raise WebAccessError(f"fetch failed with HTTP {response.status_code}")
            if content_type and not content_type.startswith(_ALLOWED_CONTENT_TYPES):
                raise WebAccessError(f"content type {content_type!r} is not allowed")
            content = response.content[:_MAX_FETCH_BYTES].decode(response.encoding or "utf-8", errors="replace")
            title, text = _extract_text(content, content_type)
            truncated = len(text) > max_text or len(response.content) > _MAX_FETCH_BYTES
            if len(text) > max_text:
                text = text[:max_text] + f"\n... truncated at {max_text} chars"
            return {
                "ok": True,
                "url": str(response.url),
                "statusCode": response.status_code,
                "contentType": content_type or "unknown",
                "title": title,
                "text": text,
                "redirects": redirects,
                "truncated": truncated,
            }

    raise WebAccessError("too many redirects")


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            href = attr.get("href") or ""
            self._current = {"title": "", "url": _clean_ddg_url(href), "snippet": ""}
            self._capture_title = True
        elif self._current is not None and "result__snippet" in classes:
            self._capture_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            self._capture_title = False
        elif self._capture_snippet and tag in {"a", "div"}:
            self._capture_snippet = False
            if self._current is not None:
                self._current["snippet"] = " ".join(self._snippet_parts).strip()
                if self._current.get("title") and self._current.get("url"):
                    self.results.append(self._current)
                self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        text = data.strip()
        if not text:
            return
        if self._capture_title:
            self._current["title"] = (self._current["title"] + " " + text).strip()
        elif self._capture_snippet:
            self._snippet_parts.append(text)


def _clean_ddg_url(href: str) -> str:
    if not href:
        return ""
    parsed = urlparse(unescape(href))
    qs = parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return unquote(qs["uddg"][0])
    if parsed.scheme in {"http", "https"}:
        return href
    return href


async def _web_search_impl(query: str, *, max_results: int | None = 5) -> dict[str, Any]:
    value = (query or "").strip()
    if not value:
        raise WebAccessError("query is empty")
    if len(value) > 500:
        raise WebAccessError("query is too long")
    limit = max(1, min(int(max_results or 5), 10))
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}) as client:
        response = await client.get(_SEARCH_URL, params={"q": value})
    if response.status_code >= 400:
        raise WebAccessError(f"search failed with HTTP {response.status_code}")
    parser = _DuckDuckGoParser()
    parser.feed(response.text)
    results = parser.results[:limit]
    return {
        "ok": True,
        "query": value,
        "provider": "DuckDuckGo HTML",
        "results": results,
        "searchUrl": f"https://duckduckgo.com/?q={quote_plus(value)}",
    }


@mcp.tool()
async def web_fetch_url(url: str, max_chars: int | None = 12_000) -> str:
    """Fetch a public HTTPS URL and return readable text.

    Blocks localhost/private/reserved network targets, embedded credentials,
    non-HTTPS URLs, disallowed ports, binary content, and unsafe redirects.
    """
    try:
        return _json(await _fetch_public_url(url, max_chars=max_chars))
    except Exception as exc:
        return _json({"ok": False, "errorType": type(exc).__name__, "error": str(exc)})


@mcp.tool()
async def web_search(query: str, max_results: int | None = 5) -> str:
    """Search the public web and return titles, URLs, and snippets."""
    try:
        return _json(await _web_search_impl(query, max_results=max_results))
    except Exception as exc:
        return _json({"ok": False, "errorType": type(exc).__name__, "error": str(exc)})


if __name__ == "__main__":
    mcp.run()
