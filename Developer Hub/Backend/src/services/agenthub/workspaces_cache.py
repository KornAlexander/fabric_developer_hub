"""SQLite-backed cache of the user's Fabric workspaces.

Schema lives in the same DB as ``session_store`` so we get the same
WAL/journal config and bind-mount visibility.

A fresh fetch from the Fabric REST API is reconciled against the cache:
new rows are inserted, missing rows are deleted, and renamed rows are
updated. Each row carries its own ``cached_at`` timestamp so the API can
report cache age and decide when to refresh.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from services.agenthub.session_store import _connect

logger = logging.getLogger(__name__)

# Cache TTL: refetch from Fabric if the newest row in the cache is older
# than this. The frontend can override with ?refresh=true.
CACHE_TTL = timedelta(hours=1)


@dataclass(frozen=True)
class CachedWorkspace:
    id: str
    name: str
    cached_at: datetime


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the workspace_cache table if it doesn't exist.

    Called from ``session_store.init_db`` so all schema is set up in one
    place.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS workspace_cache (
            user_id    TEXT NOT NULL,
            id         TEXT NOT NULL,
            name       TEXT NOT NULL,
            cached_at  TEXT NOT NULL,
            PRIMARY KEY (user_id, id)
        );

        CREATE INDEX IF NOT EXISTS idx_workspace_cache_user
            ON workspace_cache(user_id);
        """
    )


def get_cached(user_id: str) -> tuple[list[CachedWorkspace], datetime | None]:
    """Return all cached workspaces for the user plus the newest cached_at.

    Returns ``([], None)`` when the cache is empty.
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, name, cached_at FROM workspace_cache "
            "WHERE user_id = ? ORDER BY name COLLATE NOCASE",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return [], None
    items = [
        CachedWorkspace(id=r["id"], name=r["name"], cached_at=datetime.fromisoformat(r["cached_at"]))
        for r in rows
    ]
    newest = max(item.cached_at for item in items)
    return items, newest


def is_fresh(cached_at: datetime | None) -> bool:
    """True if the cache was refreshed less than ``CACHE_TTL`` ago."""
    if cached_at is None:
        return False
    return datetime.now(UTC) - cached_at < CACHE_TTL


@dataclass(frozen=True)
class ReconcileResult:
    inserted: int
    updated: int
    deleted: int


def reconcile(user_id: str, fresh: list[dict]) -> ReconcileResult:
    """Reconcile the cache for ``user_id`` against a fresh API response.

    ``fresh`` is a list of ``{"id": str, "name": str}`` dicts. Inserts
    new rows, updates renamed rows, and deletes rows missing from the
    fresh response. Returns counts for logging/telemetry.
    """
    now = datetime.now(UTC).isoformat()
    fresh_by_id = {w["id"]: w["name"] for w in fresh if w.get("id")}

    conn = _connect()
    try:
        existing = {
            row["id"]: row["name"]
            for row in conn.execute(
                "SELECT id, name FROM workspace_cache WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        }

        to_insert = [(user_id, wid, name, now) for wid, name in fresh_by_id.items() if wid not in existing]
        to_update = [
            (name, now, user_id, wid)
            for wid, name in fresh_by_id.items()
            if wid in existing and existing[wid] != name
        ]
        # Bump cached_at on unchanged rows too so the cache as a whole is
        # marked fresh after a successful reconcile.
        to_touch = [
            (now, user_id, wid)
            for wid, name in fresh_by_id.items()
            if wid in existing and existing[wid] == name
        ]
        to_delete = [(user_id, wid) for wid in existing if wid not in fresh_by_id]

        if to_insert:
            conn.executemany(
                "INSERT INTO workspace_cache (user_id, id, name, cached_at) "
                "VALUES (?, ?, ?, ?)",
                to_insert,
            )
        if to_update:
            conn.executemany(
                "UPDATE workspace_cache SET name = ?, cached_at = ? "
                "WHERE user_id = ? AND id = ?",
                to_update,
            )
        if to_touch:
            conn.executemany(
                "UPDATE workspace_cache SET cached_at = ? "
                "WHERE user_id = ? AND id = ?",
                to_touch,
            )
        if to_delete:
            conn.executemany(
                "DELETE FROM workspace_cache WHERE user_id = ? AND id = ?",
                to_delete,
            )
        conn.commit()
    finally:
        conn.close()

    result = ReconcileResult(
        inserted=len(to_insert),
        updated=len(to_update),
        deleted=len(to_delete),
    )
    if result.inserted or result.updated or result.deleted:
        logger.info(
            "Workspace cache reconcile for user=%s: +%d / ~%d / -%d",
            user_id, result.inserted, result.updated, result.deleted,
        )
    return result
