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
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.correlation import (
    RequestIdLogFilter,
    get_request_id,
    get_session_id,
    request_id_scope,
    reset_request_id,
    set_request_id,
)
from services.logging_categories import (
    DEFAULT_BACKEND_LOG_CATEGORY,
    get_log_category,
    log_category_visible_in_view,
    log_category_scope,
    public_log_categories_for_view,
    reset_log_category,
    set_log_category,
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
        assert record.otel_trace_id == "-"
        assert record.otel_span_id == "-"


def test_log_filter_outside_scope_falls_back_to_dash() -> None:
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    RequestIdLogFilter().filter(record)
    assert record.request_id == "-"


def test_middleware_binds_agenthub_correlation_headers(app: FastAPI, client: TestClient) -> None:
    route_path = "/__test/agenthub-correlation-headers"
    if not any(getattr(route, "path", None) == route_path for route in app.routes):
        @app.get(route_path)
        async def _agenthub_correlation_probe():
            return {"request_id": get_request_id(), "session_id": get_session_id()}

    session_id = "11111111-1111-1111-1111-111111111111"
    response = client.get(
        route_path,
        headers={
            "X-AgentHub-Request-ID": "req-agenthub-1",
            "X-AgentHub-Session-ID": session_id,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"request_id": "req-agenthub-1", "session_id": session_id[:8]}
    assert response.headers["X-Request-ID"] == "req-agenthub-1"


def test_log_filter_default_category_is_independent_of_severity() -> None:
    records = [
        logging.LogRecord(
            name="t", level=level, pathname=__file__, lineno=1,
            msg="hello", args=(), exc_info=None,
        )
        for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR)
    ]

    request_filter = RequestIdLogFilter()
    for record in records:
        request_filter.filter(record)

    assert {record.levelname for record in records} == {"DEBUG", "INFO", "WARNING", "ERROR"}
    assert {record.log_category for record in records} == {DEFAULT_BACKEND_LOG_CATEGORY}


def test_log_filter_respects_explicit_log_category() -> None:
    record = logging.LogRecord(
        name="t", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    record.log_category = "high_level"

    RequestIdLogFilter().filter(record)

    assert record.levelname == "WARNING"
    assert record.log_category == "high_level"


def test_log_filter_respects_camel_case_explicit_log_category() -> None:
    record = logging.LogRecord(
        name="t", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    record.logCategory = "detailed"

    RequestIdLogFilter().filter(record)

    assert record.levelname == "ERROR"
    assert record.log_category == "detailed"


def test_log_category_scope_binds_and_restores() -> None:
    assert get_log_category() is None
    with log_category_scope("diagnostic"):
        assert get_log_category() == "diagnostic"
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname=__file__, lineno=1,
            msg="hello", args=(), exc_info=None,
        )
        RequestIdLogFilter().filter(record)
        assert record.log_category == "diagnostic"
    assert get_log_category() is None


def test_manual_log_category_set_reset_round_trip() -> None:
    token = set_log_category("trace")
    try:
        assert get_log_category() == "trace"
    finally:
        reset_log_category(token)
    assert get_log_category() is None


def test_public_log_category_views_are_cumulative() -> None:
    assert public_log_categories_for_view("high_level") == ("high_level",)
    assert public_log_categories_for_view("detailed") == ("high_level", "detailed")
    assert public_log_categories_for_view("diagnostic") == ("high_level", "detailed", "diagnostic")


def test_trace_is_never_visible_in_public_log_category_views() -> None:
    assert log_category_visible_in_view("high_level", "diagnostic") is True
    assert log_category_visible_in_view("detailed", "diagnostic") is True
    assert log_category_visible_in_view("diagnostic", "diagnostic") is True
    assert log_category_visible_in_view("trace", "diagnostic") is False
    assert log_category_visible_in_view("trace", "trace") is False


def test_formatter_uses_request_id_without_keyerror() -> None:
    """End-to-end: the format string we installed in ``main.py`` must not
    raise even when the record was emitted outside a request scope."""
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    RequestIdLogFilter().filter(record)
    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [%(log_category)s req %(request_id)s tr:%(otel_trace_id)s sp:%(otel_span_id)s] - %(message)s"
    )
    rendered = fmt.format(record)
    assert f"[{DEFAULT_BACKEND_LOG_CATEGORY} req - tr:- sp:-]" in rendered
    assert "hello" in rendered
