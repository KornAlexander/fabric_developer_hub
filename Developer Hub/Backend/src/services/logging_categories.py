"""Category helpers for AgentHub logging.

Python log levels and AgentHub log categories are intentionally
orthogonal:

* ``levelname`` (INFO, WARNING, ERROR, ...) describes severity.
* ``log_category`` (high_level, detailed, diagnostic, trace) describes
  audience/detail depth.

Never infer one from the other. A high-level event may be a WARNING,
and a diagnostic event may be INFO. Trace must be explicit because it is
internal-only and must never be exposed through user-facing log surfaces.

For user-facing filters, categories behave like logging levels: selecting
``detailed`` includes ``high_level`` entries, and selecting ``diagnostic``
includes both ``high_level`` and ``detailed`` entries. ``trace`` remains
internal-only regardless of the selected public category.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Literal

PublicLogCategory = Literal["high_level", "detailed", "diagnostic"]
LogCategory = Literal["high_level", "detailed", "diagnostic", "trace"]

LOG_CATEGORY_HIGH_LEVEL: PublicLogCategory = "high_level"
LOG_CATEGORY_DETAILED: PublicLogCategory = "detailed"
LOG_CATEGORY_DIAGNOSTIC: PublicLogCategory = "diagnostic"
LOG_CATEGORY_TRACE: Literal["trace"] = "trace"

PUBLIC_LOG_CATEGORIES = frozenset({
    LOG_CATEGORY_HIGH_LEVEL,
    LOG_CATEGORY_DETAILED,
    LOG_CATEGORY_DIAGNOSTIC,
})
LOG_CATEGORIES = frozenset({*PUBLIC_LOG_CATEGORIES, LOG_CATEGORY_TRACE})
LOG_CATEGORY_DEPTH: dict[PublicLogCategory, int] = {
    LOG_CATEGORY_HIGH_LEVEL: 0,
    LOG_CATEGORY_DETAILED: 1,
    LOG_CATEGORY_DIAGNOSTIC: 2,
}

# Backend logs without an explicit category are operational diagnostics.
# This default is category-only; severity remains whatever the caller set.
DEFAULT_BACKEND_LOG_CATEGORY: PublicLogCategory = LOG_CATEGORY_DIAGNOSTIC

_log_category_var: ContextVar[LogCategory | None] = ContextVar("log_category", default=None)


def normalize_log_category(value: object, *, default: LogCategory | None = DEFAULT_BACKEND_LOG_CATEGORY) -> LogCategory | None:
    """Normalize user/input spelling to a canonical category."""
    if value is None:
        return default
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in LOG_CATEGORIES:
        return normalized  # type: ignore[return-value]
    return default


def public_log_categories_for_view(value: object) -> tuple[PublicLogCategory, ...]:
    """Return the public categories visible for a selected detail level."""
    selected = normalize_log_category(value, default=LOG_CATEGORY_HIGH_LEVEL)
    if selected not in PUBLIC_LOG_CATEGORIES:
        selected = LOG_CATEGORY_HIGH_LEVEL
    return tuple(
        category for category, depth in LOG_CATEGORY_DEPTH.items()
        if depth <= LOG_CATEGORY_DEPTH[selected]  # type: ignore[index]
    )


def log_category_visible_in_view(entry_category: object, selected_category: object) -> bool:
    """Return whether an entry category is visible at the selected public level."""
    normalized_entry = normalize_log_category(entry_category, default=None)
    if normalized_entry not in PUBLIC_LOG_CATEGORIES:
        return False
    return normalized_entry in public_log_categories_for_view(selected_category)


def get_log_category() -> LogCategory | None:
    """Return the category currently bound to this execution context."""
    return _log_category_var.get()


def set_log_category(value: object) -> Token[LogCategory | None]:
    """Bind a category for all log records emitted in this context."""
    return _log_category_var.set(normalize_log_category(value, default=None))


def reset_log_category(token: Token[LogCategory | None]) -> None:
    _log_category_var.reset(token)


@contextmanager
def log_category_scope(value: object) -> Iterator[None]:
    """Temporarily bind a category for log records in the current context."""
    token = set_log_category(value)
    try:
        yield
    finally:
        reset_log_category(token)


def log_extra(category: LogCategory, **extra: Any) -> dict[str, Any]:
    """Build ``extra=...`` for categorized logging calls."""
    return {"log_category": normalize_log_category(category), **extra}