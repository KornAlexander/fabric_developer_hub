"""ContainerPool — bounded pool of concurrent agent containers.

Architecture drivers acquire slots from this pool before spawning
containers. The pool acts as backpressure — excess slots queue in the
driver coroutine rather than spawning unbounded containers.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT = 8


class ContainerPool:
    """Semaphore-guarded pool of agent container slots."""

    def __init__(self, max_concurrent: int | None = None):
        max_c = max_concurrent or int(
            os.environ.get("AGENT_CONTAINER_MAX_CONCURRENT", _DEFAULT_MAX_CONCURRENT)
        )
        self._semaphore = asyncio.Semaphore(max_c)
        self._max = max_c
        self._active = 0

    @property
    def max_concurrent(self) -> int:
        return self._max

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def waiting_count(self) -> int:
        # Semaphore doesn't expose waiter count directly; approximate
        # from the internal value.
        return max(0, self._active - self._max)

    async def acquire(self) -> None:
        """Block until a container slot is available."""
        await self._semaphore.acquire()
        self._active += 1
        logger.debug(
            "[POOL] Acquired slot (%d/%d active)", self._active, self._max,
        )

    def release(self) -> None:
        """Release a container slot back to the pool."""
        self._active = max(0, self._active - 1)
        self._semaphore.release()
        logger.debug(
            "[POOL] Released slot (%d/%d active)", self._active, self._max,
        )

    async def __aenter__(self) -> ContainerPool:
        await self.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        self.release()
