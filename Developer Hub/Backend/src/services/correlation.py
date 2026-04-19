"""Request-scoped correlation ID for logs and outbound HTTP calls.

Every inbound HTTP request gets an ``X-Request-ID`` (minted by the middleware
in ``main.py`` if the caller didn't supply one). We stash it in a
``ContextVar`` so that:

* The ``logging.Filter`` below injects ``%(request_id)s`` into every log
  record emitted during the request — no caller threading required.
* Outbound HTTP clients (httpx, MCP tools, Copilot) can read the current
  ID via ``get_request_id()`` and propagate it downstream.

The default value (``"-"``) is visible in log output for background tasks
and startup code that aren't bound to an HTTP request.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Return the current request ID, or ``"-"`` outside a request scope."""
    return _request_id_var.get()


def set_request_id(value: str) -> Token[str]:
    """Bind ``value`` as the current request ID and return a reset token.

    Prefer :func:`request_id_scope` unless you need manual control (e.g.
    Starlette middleware that must survive the ``await call_next`` hop).
    """
    return _request_id_var.set(value)


def reset_request_id(token: Token[str]) -> None:
    """Undo a previous :func:`set_request_id` call."""
    _request_id_var.reset(token)


@contextmanager
def request_id_scope(value: str) -> Iterator[None]:
    """Context manager: bind ``value`` for the duration of the ``with`` block."""
    token = _request_id_var.set(value)
    try:
        yield
    finally:
        _request_id_var.reset(token)


class RequestIdLogFilter(logging.Filter):
    """Attach the current request ID to every ``LogRecord`` as ``request_id``.

    Install on any handler whose formatter references ``%(request_id)s``.
    Records emitted outside a request scope (startup, shutdown, background
    tasks) carry ``"-"`` so the formatter never raises ``KeyError``.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.request_id = get_request_id()
        return True
