"""Unit tests for the in-memory token-bucket rate limiter."""
from __future__ import annotations

import time

import pytest

from services.agenthub.rate_limit import (
    DEFAULT_LIMITS,
    RateLimiter,
    RateLimitExceeded,
)


def test_acquire_allows_up_to_capacity() -> None:
    """A fresh bucket must accept exactly its capacity of calls before
    blocking — the classic burst budget."""
    lim = RateLimiter({"act": (3, 0.0)})  # no refill → pure capacity test
    for _ in range(3):
        lim.acquire("u", "act")
    with pytest.raises(RateLimitExceeded):
        lim.acquire("u", "act")


def test_acquire_is_isolated_per_user() -> None:
    """One user hitting the limit must not affect a different user."""
    lim = RateLimiter({"act": (2, 0.0)})
    lim.acquire("alice", "act")
    lim.acquire("alice", "act")
    # Alice is out; Bob is fresh.
    with pytest.raises(RateLimitExceeded):
        lim.acquire("alice", "act")
    lim.acquire("bob", "act")


def test_acquire_is_isolated_per_action() -> None:
    """Different actions have different buckets."""
    lim = RateLimiter({"a": (1, 0.0), "b": (1, 0.0)})
    lim.acquire("u", "a")
    with pytest.raises(RateLimitExceeded):
        lim.acquire("u", "a")
    # Different action → fresh bucket
    lim.acquire("u", "b")


def test_bucket_refills_over_time() -> None:
    """Refill rate must deposit tokens as wall-clock advances."""
    lim = RateLimiter({"act": (1, 1000.0)})  # 1000 tokens/sec → fast refill
    lim.acquire("u", "act")
    with pytest.raises(RateLimitExceeded):
        lim.acquire("u", "act")
    time.sleep(0.01)  # enough for >1 token at 1000/s
    lim.acquire("u", "act")  # should succeed now


def test_retry_after_is_reported() -> None:
    """A denied call must surface a positive retry_after."""
    lim = RateLimiter({"act": (1, 1.0)})
    lim.acquire("u", "act")
    with pytest.raises(RateLimitExceeded) as exc:
        lim.acquire("u", "act")
    assert exc.value.retry_after > 0
    assert exc.value.action == "act"


def test_anonymous_user_is_not_limited() -> None:
    """Dev / anonymous path (empty user_id) must skip rate limiting so
    local exploration doesn't hit 429s."""
    lim = RateLimiter({"act": (1, 0.0)})
    for _ in range(100):
        lim.acquire("", "act")  # no exception


def test_unknown_action_uses_safe_default() -> None:
    """An action that wasn't declared in limits gets a conservative default
    so new endpoints are still protected without code churn."""
    lim = RateLimiter({})  # no limits declared at all
    # Should not explode; default capacity is finite though.
    for _ in range(5):
        lim.acquire("u", "new_action")


def test_default_limits_cover_expensive_endpoints() -> None:
    """Regression guard: the four endpoints we protect must each have a
    named budget. If a future refactor renames an action without updating
    ``DEFAULT_LIMITS``, this test catches the drift."""
    for action in ("create_session", "generate_plan", "approve_plan", "send_message"):
        assert action in DEFAULT_LIMITS, f"Missing rate-limit budget for {action}"
