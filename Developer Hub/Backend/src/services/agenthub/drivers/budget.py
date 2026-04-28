"""BudgetTracker — thread-safe session-level budget accounting."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from domain.models.composition import Budget

logger = logging.getLogger(__name__)


@dataclass
class BudgetTracker:
    """Thread-safe session-level budget accounting.

    The driver and all slot runners reference the same instance.
    """

    budget: Budget
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    turns_used: int = 0
    tool_calls_used: int = 0
    _started_at: float = 0.0

    def check_budget(self) -> str | None:
        """Return a reason string if budget is blown, else None."""
        with self._lock:
            if self._started_at == 0.0:
                self._started_at = time.monotonic()
            if self.turns_used >= self.budget.max_turns:
                return f"turns exhausted ({self.turns_used}/{self.budget.max_turns})"
            if self.tool_calls_used >= self.budget.max_tool_calls:
                return f"tool calls exhausted ({self.tool_calls_used}/{self.budget.max_tool_calls})"
            elapsed = time.monotonic() - self._started_at
            if elapsed >= self.budget.max_wallclock_s:
                return f"wallclock exhausted ({elapsed:.0f}s/{self.budget.max_wallclock_s}s)"
        return None

    def record_turn(self) -> None:
        with self._lock:
            self.turns_used += 1

    def record_tool_call(self) -> None:
        with self._lock:
            self.tool_calls_used += 1

    def remaining_turns(self) -> int:
        with self._lock:
            return max(0, self.budget.max_turns - self.turns_used)

    def allocate(self, turns: int) -> int:
        """Return the smaller of ``turns`` and remaining budget."""
        return min(turns, self.remaining_turns())
