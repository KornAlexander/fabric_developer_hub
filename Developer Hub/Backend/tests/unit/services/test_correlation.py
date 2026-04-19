"""Unit tests for the request-ID correlation helpers.

Locks the three guarantees the rest of the stack relies on:

1. Outside any request scope, ``get_request_id()`` returns ``"-"``.
2. ``request_id_scope`` / ``set_request_id`` bind the value for the
   duration of the scope, including across ``await`` boundaries inside
   the same task.
3. ``RequestIdLogFilter`` attaches ``request_id`` to every ``LogRecord``
   so format strings referencing ``%(request_id)s`` never raise.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from services.correlation import (
    RequestIdLogFilter,
    get_request_id,
    request_id_scope,
    reset_request_id,
    set_request_id,
)


def test_default_is_dash_outside_scope() -> None:
    assert get_request_id() == "-"


def test_scope_binds_and_restores() -> None:
    assert get_request_id() == "-"
    with request_id_scope("req-123"):
        assert get_request_id() == "req-123"
    assert get_request_id() == "-"


def test_manual_set_reset_round_trip() -> None:
    token = set_request_id("req-abc")
    try:
        assert get_request_id() == "req-abc"
    finally:
        reset_request_id(token)
    assert get_request_id() == "-"


@pytest.mark.asyncio
async def test_value_survives_await() -> None:
    """Binding must persist across ``await`` hops inside the same task."""
    with request_id_scope("req-async"):
        await asyncio.sleep(0)
        assert get_request_id() == "req-async"


@pytest.mark.asyncio
async def test_concurrent_tasks_see_independent_values() -> None:
    """Each task sees its own ContextVar copy — no cross-talk."""
    observed: list[str] = []

    async def worker(tag: str) -> None:
        with request_id_scope(tag):
            await asyncio.sleep(0.01)
            observed.append(get_request_id())

    await asyncio.gather(worker("a"), worker("b"), worker("c"))
    assert sorted(observed) == ["a", "b", "c"]


def test_log_filter_attaches_request_id_field() -> None:
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    with request_id_scope("req-log"):
        assert RequestIdLogFilter().filter(record) is True
        assert record.request_id == "req-log"


def test_log_filter_outside_scope_falls_back_to_dash() -> None:
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    RequestIdLogFilter().filter(record)
    assert record.request_id == "-"


def test_formatter_uses_request_id_without_keyerror() -> None:
    """End-to-end: the format string we installed in ``main.py`` must not
    raise even when the record was emitted outside a request scope."""
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    RequestIdLogFilter().filter(record)
    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [req %(request_id)s] - %(message)s"
    )
    rendered = fmt.format(record)
    assert "[req -]" in rendered
    assert "hello" in rendered
