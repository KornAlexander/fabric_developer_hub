"""Request-scoped correlation IDs for logs and outbound HTTP calls.

Every inbound HTTP request gets an ``X-Request-ID`` (minted by the
middleware in ``main.py`` if the caller didn't supply one). After auth
we also bind the caller's ``user_id`` (Entra oid), and session-scoped
endpoints bind the current ``session_id``. All three are stashed in
``ContextVar``s so that:

* The ``logging.Filter`` below injects ``%(request_id)s``,
  ``%(user_id)s``, ``%(session_id)s`` and ``%(log_category)s`` into
  every log record emitted during the scope — no caller threading
  required.
* ``asyncio.create_task`` and ``asyncio.to_thread`` inherit the current
  ``ContextVar`` snapshot (Python ≥3.7/3.9), so background tasks
  spawned from a request carry the caller's identifiers automatically.
* Outbound HTTP clients (httpx, MCP tools, Copilot) can read the
  current IDs and propagate them downstream.

The default value (``"-"``) is visible in log output for background
tasks and startup code that aren't bound to any scope, so grep-like
filtering stays trivial.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from services.logging_categories import (
    DEFAULT_BACKEND_LOG_CATEGORY,
    get_log_category,
    normalize_log_category,
)

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_user_id_var: ContextVar[str] = ContextVar("user_id", default="-")
_session_id_var: ContextVar[str] = ContextVar("session_id", default="-")


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


def get_user_id() -> str:
    """Return the current caller's user id (Entra oid), or ``"-"``."""
    return _user_id_var.get()


def set_user_id(value: str | None) -> Token[str]:
    """Bind the current caller's user id for correlation logs.

    Accepts the raw Entra ``oid`` (UUID) or any stable string; we store
    the first 8 chars in logs to keep lines readable. Pass ``None`` or
    an empty string to fall back to the default ``"-"``.
    """
    return _user_id_var.set(_short_id(value))


def reset_user_id(token: Token[str]) -> None:
    _user_id_var.reset(token)


def get_session_id() -> str:
    """Return the current session id, or ``"-"`` outside a session scope."""
    return _session_id_var.get()


def set_session_id(value: str | None) -> Token[str]:
    """Bind the current session id for correlation logs. ``None`` clears."""
    return _session_id_var.set(_short_id(value))


def reset_session_id(token: Token[str]) -> None:
    _session_id_var.reset(token)


def _short_id(value: str | None) -> str:
    """Return the first 8 chars of ``value`` or ``"-"`` for unset.

    Keeping the short form in the log prefix keeps lines scannable.
    When full ids are needed (e.g. joining traces), callers can still
    log them explicitly in the message body.
    """
    if not value:
        return "-"
    v = str(value).strip()
    if not v:
        return "-"
    # Strip a single ``oid:`` prefix so user keys built by
    # ``_user_key_from_context`` collapse cleanly back to their oid
    # prefix in logs.
    if v.startswith("oid:"):
        v = v[4:]
    return v[:8]


class RequestIdLogFilter(logging.Filter):
    """Attach current correlation ids and category to every ``LogRecord``.

    Populates four attributes on each record, all defaulting to
    ``"-"`` when unbound so formatters never raise ``KeyError``:

    * ``request_id`` — HTTP X-Request-ID, bound by the Starlette
      middleware.
    * ``user_id``    — short form of the authenticated caller's Entra
      oid, bound by ``require_user`` / the chat OBO path.
    * ``session_id`` — short form of the AgentHub session id, bound
      by session-scoped endpoints and inherited by background
      orchestrator tasks via asyncio's contextvar propagation.
        * ``log_category`` — audience/detail category, independent from
            ``levelname``. Unspecified backend logs default to diagnostic;
            high-level, detailed, and trace logs must be explicit.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.request_id = get_request_id()
        record.user_id = get_user_id()
        record.session_id = get_session_id()
        trace_id, span_id = _current_otel_ids()
        record.otel_trace_id = trace_id
        record.otel_span_id = span_id
        explicit_category = getattr(record, "log_category", None) or getattr(record, "logCategory", None)
        context_category = get_log_category()
        record.log_category = normalize_log_category(
            explicit_category or context_category,
            default=DEFAULT_BACKEND_LOG_CATEGORY,
        )
        return True


def _current_otel_ids() -> tuple[str, str]:
    """Return current OpenTelemetry trace/span ids, or dashes when unbound."""
    try:
        from opentelemetry import trace

        span_context = trace.get_current_span().get_span_context()
        if not getattr(span_context, "is_valid", False):
            return "-", "-"
        return (
            format(span_context.trace_id, "032x"),
            format(span_context.span_id, "016x"),
        )
    except Exception:
        return "-", "-"


def get_current_otel_ids() -> tuple[str, str]:
    """Return current OpenTelemetry trace/span ids for structured records."""
    return _current_otel_ids()
