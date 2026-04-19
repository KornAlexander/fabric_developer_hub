"""Short-lived one-shot download tokens for file attachments.

Why this exists: Fabric runs our workload inside a cross-origin iframe
sandboxed without ``allow-downloads`` / ``allow-popups``. That kills
every in-frame path a browser normally offers for saving a file
(``<a download>``, ``window.open``, ``showSaveFilePicker``). The only
survivor is ``workloadClient.navigation.openBrowserTab({url})``, which
opens an **http(s)** URL in a fresh top-level tab — outside the
sandbox, so the browser honours ``Content-Disposition: attachment``
and triggers its native download flow.

So the flow is:

  1.  Frontend ``POST /api/attachments/download-token`` (authenticated)
      with ``{name, mime, content}`` (content may be a data URI or raw
      text). Body size is bounded by the existing attachment limits.
  2.  Backend stashes the bytes in a process-local dict keyed by a
      random URL-safe token, TTL ≈ 60 s, single-use.
  3.  Backend returns ``{url, token}`` where ``url`` is the absolute
      ``/api/attachments/download/{token}`` route.
  4.  Frontend calls ``openBrowserTab({url})``. The new tab hits
      ``GET /api/attachments/download/{token}`` (anonymous — the token
      *is* the auth), gets the bytes back with
      ``Content-Disposition: attachment``, and the browser saves them.
  5.  The token is consumed on first use. Expired tokens return 404.

This keeps the attachment bytes out of browser-reachable query strings
and avoids any persistent server-side storage. The backend just owns
a short-lived hand-off.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
import time
from dataclasses import dataclass

_TTL_SECONDS = 60
# Cap how many pending tokens live in the process at once. A misuse
# (script) can otherwise grow memory unbounded. A single user generally
# clicks one file at a time; 256 is comfortable headroom.
_MAX_PENDING = 256


@dataclass
class _PendingDownload:
    name: str
    mime: str
    content: bytes
    expires_at: float


_pending: dict[str, _PendingDownload] = {}
_lock = asyncio.Lock()


def _sweep_expired() -> None:
    """Drop expired entries. Cheap enough to run on every touch."""
    now = time.time()
    stale = [tok for tok, p in _pending.items() if p.expires_at <= now]
    for tok in stale:
        _pending.pop(tok, None)


def _decode_content(content: str, mime: str) -> bytes:
    """Decode whatever the frontend shipped into raw bytes.

    Accepts either a ``data:<mime>[;base64],<payload>`` URI or a raw
    string (treated as UTF-8 text).
    """
    if content.startswith("data:"):
        comma = content.find(",")
        if comma < 0:
            raise ValueError("Malformed data URI: missing comma")
        header = content[5:comma]
        payload = content[comma + 1 :]
        if ";base64" in header:
            return base64.b64decode(payload, validate=True)
        # URL-encoded text.
        import urllib.parse
        return urllib.parse.unquote(payload).encode("utf-8")
    # Raw text.
    return content.encode("utf-8")


async def issue_token(name: str, mime: str, content: str) -> str:
    """Issue a single-use token for the given attachment.

    Returns the URL-safe token string. Caller builds the final URL.
    """
    async with _lock:
        _sweep_expired()
        if len(_pending) >= _MAX_PENDING:
            # Evict the oldest entry to keep memory bounded. In practice
            # this only fires if the same user rapid-fires hundreds of
            # downloads; the oldest are safe to drop because the
            # frontend has already forgotten about them.
            oldest = min(_pending, key=lambda t: _pending[t].expires_at)
            _pending.pop(oldest, None)

        blob = _decode_content(content, mime)
        token = secrets.token_urlsafe(24)
        _pending[token] = _PendingDownload(
            name=name,
            mime=mime or "application/octet-stream",
            content=blob,
            expires_at=time.time() + _TTL_SECONDS,
        )
    return token


async def consume_token(token: str) -> _PendingDownload | None:
    """Look up and remove a token. Returns ``None`` if unknown/expired."""
    async with _lock:
        _sweep_expired()
        entry = _pending.pop(token, None)
    return entry
