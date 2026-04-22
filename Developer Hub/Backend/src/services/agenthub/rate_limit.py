"""Per-user token-bucket rate limiter for expensive AgentHub endpoints.

Motivation
----------
Every call to ``/api/sessions``, ``/api/orchestrate/compose``, and
``/api/sessions/{id}/run`` triggers downstream work that costs money or
resources we care about:

* A Copilot chat completion (billed per token).
* OBO token exchanges against Entra (MSAL).
* Fabric API calls under the user's identity.
* Spawning per-tool MCP subprocesses.

Without a gate, a single authenticated but malicious (or merely buggy) user
can loop these endpoints and drive up Copilot cost, saturate MSAL, and
exhaust worker slots. The limiter here is a last-line defense — it does
*not* replace quota management in Fabric / Copilot — but it gives us a cheap
server-side fuse.

Design
------
In-memory token bucket keyed by ``(user_id, action)``. Each bucket refills
at ``rate`` tokens/second up to ``capacity``. Calls that cannot acquire a
token raise :class:`RateLimitExceeded`, which the controller maps to a 429.

In-memory is intentional: a single backend replica today, simple to reason
about, and survives the OBO-eager restart pattern we use. If/when we scale
to multiple replicas, swap this for a Redis-backed bucket — the interface
is stable.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when a user has no tokens available for the requested action."""

    def __init__(self, action: str, retry_after: float) -> None:
        super().__init__(f"Rate limit exceeded for action {action!r}")
        self.action = action
        # Seconds until the next token becomes available. Surfaced to the
        # caller via the ``Retry-After`` header.
        self.retry_after = retry_after


@dataclass
class _Bucket:
    capacity: float
    rate: float  # tokens/second
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


# Default budgets. Chosen so normal interactive use (a user clicking "Run"
# every few seconds) never hits them, but a scripted loop does within a
# second. Revisit when we have real usage telemetry.
DEFAULT_LIMITS: dict[str, tuple[int, float]] = {
    # action          (burst capacity, refill tokens/sec)
    "create_session":   (5, 0.2),   # 5 composed sessions, refills 1 every 5s
    "compose":          (10, 0.5),  # compose-only is cheaper (one LLM call)
    "run_session":      (5, 0.2),   # starts real work — same as create
    "send_message":     (30, 2.0),  # in-session chat, should feel snappy
    # Download-token minting is cheap (just stashes bytes) but we cap it
    # so a misbehaving client can't fill the in-memory token dict.
    "attachment_download": (20, 2.0),

    # ── Cheap read endpoints — bursty on page load, server-side cached ──
    # Mission Control / session creation fetches items for every
    # workspace chip in parallel on page load. A user with a dozen
    # workspaces immediately fires ~12 requests; refreshing the view
    # doubles that. Results are cached per (user, workspace) so real
    # downstream cost is near-zero after the first call. Budget
    # generously so normal UI traffic never gets 429'd.
    "list_workspace_items": (60, 5.0),
    # Plan approval is a click — allow a healthy burst so a user
    # resolving a plan with several steps in rapid succession isn't
    # blocked. Fell through to the conservative default (10, 1.0)
    # previously.
    "approve_plan": (20, 1.0),
}


class RateLimiter:
    """Thread-safe in-memory token-bucket rate limiter."""

    def __init__(self, limits: dict[str, tuple[int, float]] | None = None) -> None:
        self._limits = dict(limits or DEFAULT_LIMITS)
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()

    def _bucket(self, user_id: str, action: str) -> _Bucket:
        key = (user_id, action)
        b = self._buckets.get(key)
        if b is None:
            cap, rate = self._limits.get(action, (10, 1.0))
            b = _Bucket(capacity=float(cap), rate=float(rate), tokens=float(cap))
            self._buckets[key] = b
        return b

    def acquire(self, user_id: str, action: str, cost: float = 1.0) -> None:
        """Consume ``cost`` tokens from the ``(user_id, action)`` bucket.

        Raises :class:`RateLimitExceeded` if not enough tokens are available.
        Pure function on the bucket state — no blocking, no backoff.
        """
        if not user_id:
            # Anonymous / dev context: don't rate limit.
            return
        with self._lock:
            b = self._bucket(user_id, action)
            now = time.monotonic()
            elapsed = now - b.last_refill
            if elapsed > 0:
                b.tokens = min(b.capacity, b.tokens + elapsed * b.rate)
                b.last_refill = now
            if b.tokens < cost:
                deficit = cost - b.tokens
                retry_after = deficit / b.rate if b.rate > 0 else 60.0
                logger.warning(
                    "[RATE_LIMIT] user=%s action=%s denied (tokens=%.2f, need=%.2f, retry_after=%.2fs)",
                    user_id, action, b.tokens, cost, retry_after,
                )
                raise RateLimitExceeded(action, retry_after)
            b.tokens -= cost

    def reset(self) -> None:
        """Drop all buckets. Intended for tests."""
        with self._lock:
            self._buckets.clear()


# Module-level singleton — controllers just import and use.
_limiter = RateLimiter()


def acquire(user_id: str, action: str, cost: float = 1.0) -> None:
    """Functional front door to the module singleton."""
    _limiter.acquire(user_id, action, cost)


def reset_for_tests() -> None:
    _limiter.reset()
