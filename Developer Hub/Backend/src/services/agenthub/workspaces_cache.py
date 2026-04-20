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

from services.agenthub._db import connect as _connect

logger = logging.getLogger(__name__)

# Cache TTL: refetch from Fabric if the newest row in the cache is older
# than this. The frontend can override with ?refresh=true.
CACHE_TTL = timedelta(hours=6)


@dataclass(frozen=True)
class CachedWorkspace:
    workspace_id: str
    workspace_name: str
    cached_at: datetime
    # Git integration status. ``git_connected`` is None when we have not
    # yet probed the workspace (fresh row, background job pending). The
    # frontend treats None as "unknown" and the source-workspace picker
    # may either hide or grey out these rows depending on its toggle.
    git_connected: bool | None = None
    git_provider: str | None = None
    git_branch: str | None = None
    git_repo_name: str | None = None
    git_checked_at: datetime | None = None


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the workspace_cache table if it doesn't exist.

    Called from ``session_store.init_db`` so all schema is set up in one
    place.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS workspace_cache (
            user_id        TEXT NOT NULL,
            user_upn       TEXT,
            workspace_id   TEXT NOT NULL,
            workspace_name TEXT NOT NULL,
            cached_at      TEXT NOT NULL,
            PRIMARY KEY (user_id, workspace_id)
        );

        CREATE INDEX IF NOT EXISTS idx_workspace_cache_user
            ON workspace_cache(user_id);
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(workspace_cache)").fetchall()}
    # Idempotent migration for DBs that predate the user_upn column.
    if "user_upn" not in cols:
        conn.execute("ALTER TABLE workspace_cache ADD COLUMN user_upn TEXT")
        logger.info("Added user_upn column to workspace_cache")
    # Idempotent migration: the workspace identifier was originally named
    # ``id`` / ``name`` — ambiguous in a table whose own primary key IS a
    # workspace id. Rename to ``workspace_id`` / ``workspace_name`` so the
    # schema is self-describing.
    if "id" in cols and "workspace_id" not in cols:
        conn.execute("ALTER TABLE workspace_cache RENAME COLUMN id TO workspace_id")
        logger.info("Renamed workspace_cache.id -> workspace_id")
    if "name" in cols and "workspace_name" not in cols:
        conn.execute("ALTER TABLE workspace_cache RENAME COLUMN name TO workspace_name")
        logger.info("Renamed workspace_cache.name -> workspace_name")
    # Idempotent migration: git integration columns. These are populated
    # by a background probe (``refresh_git_status``) after the initial
    # workspace list reconcile. NULL = not yet probed.
    for col, ddl in (
        ("git_connected",   "ALTER TABLE workspace_cache ADD COLUMN git_connected INTEGER"),
        ("git_provider",    "ALTER TABLE workspace_cache ADD COLUMN git_provider TEXT"),
        ("git_branch",      "ALTER TABLE workspace_cache ADD COLUMN git_branch TEXT"),
        ("git_repo_name",   "ALTER TABLE workspace_cache ADD COLUMN git_repo_name TEXT"),
        ("git_checked_at",  "ALTER TABLE workspace_cache ADD COLUMN git_checked_at TEXT"),
    ):
        if col not in cols:
            conn.execute(ddl)
            logger.info("Added %s column to workspace_cache", col)
    # One-time cleanup: drop rows keyed by the old hashed-Authorization
    # identifier ('user-NNNNN'). Those are orphaned once we switched to
    # deriving user_id from the Fabric JWT's UPN / oid claim.
    conn.execute(
        "DELETE FROM workspace_cache WHERE user_id GLOB 'user-[0-9][0-9][0-9][0-9][0-9]'"
    )


def get_cached(user_id: str) -> tuple[list[CachedWorkspace], datetime | None]:
    """Return all cached workspaces for the user plus the newest cached_at.

    Returns ``([], None)`` when the cache is empty.
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT workspace_id, workspace_name, cached_at, "
            "git_connected, git_provider, git_branch, git_repo_name, git_checked_at "
            "FROM workspace_cache "
            "WHERE user_id = ? ORDER BY workspace_name COLLATE NOCASE",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return [], None
    items = [
        CachedWorkspace(
            workspace_id=r["workspace_id"],
            workspace_name=r["workspace_name"],
            cached_at=datetime.fromisoformat(r["cached_at"]),
            git_connected=None if r["git_connected"] is None else bool(r["git_connected"]),
            git_provider=r["git_provider"],
            git_branch=r["git_branch"],
            git_repo_name=r["git_repo_name"],
            git_checked_at=datetime.fromisoformat(r["git_checked_at"]) if r["git_checked_at"] else None,
        )
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


def reconcile(user_id: str, fresh: list[dict], user_upn: str | None = None) -> ReconcileResult:
    """Reconcile the cache for ``user_id`` against a fresh API response.

    ``fresh`` is a list of ``{"id": str, "name": str}`` dicts. Inserts
    new rows, updates renamed rows, and deletes rows missing from the
    fresh response. Returns counts for logging/telemetry.

    ``user_upn`` is stored for human readability only; all lookups remain
    keyed on ``user_id``.
    """
    now = datetime.now(UTC).isoformat()
    fresh_by_id = {w["id"]: w["name"] for w in fresh if w.get("id")}

    conn = _connect()
    try:
        existing = {
            row["workspace_id"]: row["workspace_name"]
            for row in conn.execute(
                "SELECT workspace_id, workspace_name FROM workspace_cache WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        }

        to_insert = [(user_id, user_upn, wid, name, now) for wid, name in fresh_by_id.items() if wid not in existing]
        to_update = [
            (name, now, user_upn, user_id, wid)
            for wid, name in fresh_by_id.items()
            if wid in existing and existing[wid] != name
        ]
        # Bump cached_at on unchanged rows too so the cache as a whole is
        # marked fresh after a successful reconcile.
        to_touch = [
            (now, user_upn, user_id, wid)
            for wid, name in fresh_by_id.items()
            if wid in existing and existing[wid] == name
        ]
        to_delete = [(user_id, wid) for wid in existing if wid not in fresh_by_id]

        if to_insert:
            conn.executemany(
                "INSERT INTO workspace_cache "
                "(user_id, user_upn, workspace_id, workspace_name, cached_at) "
                "VALUES (?, ?, ?, ?, ?)",
                to_insert,
            )
        if to_update:
            conn.executemany(
                "UPDATE workspace_cache "
                "SET workspace_name = ?, cached_at = ?, user_upn = COALESCE(?, user_upn) "
                "WHERE user_id = ? AND workspace_id = ?",
                to_update,
            )
        if to_touch:
            conn.executemany(
                "UPDATE workspace_cache SET cached_at = ?, user_upn = COALESCE(?, user_upn) "
                "WHERE user_id = ? AND workspace_id = ?",
                to_touch,
            )
        if to_delete:
            conn.executemany(
                "DELETE FROM workspace_cache WHERE user_id = ? AND workspace_id = ?",
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


# ── Git integration status ──────────────────────────────────────────

# How long a git-status check stays fresh before we re-probe. Users rarely
# connect / disconnect git from a workspace, so keeping this longer than
# the workspace-list TTL is fine.
GIT_STATUS_TTL = timedelta(hours=6)


def git_status_is_fresh(checked_at: datetime | None) -> bool:
    return checked_at is not None and datetime.now(UTC) - checked_at < GIT_STATUS_TTL


def update_git_status(
    user_id: str,
    workspace_id: str,
    *,
    connected: bool,
    provider: str | None = None,
    branch: str | None = None,
    repo_name: str | None = None,
) -> None:
    """Persist the outcome of a single ``sl_get_git_connection`` probe.

    ``connected=False`` is stored as 0 so the frontend knows the workspace
    was probed and is genuinely not git-connected (vs. "never probed"
    which is NULL).
    """
    now = datetime.now(UTC).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE workspace_cache "
            "SET git_connected = ?, git_provider = ?, git_branch = ?, "
            "    git_repo_name = ?, git_checked_at = ? "
            "WHERE user_id = ? AND workspace_id = ?",
            (1 if connected else 0, provider, branch, repo_name, now, user_id, workspace_id),
        )
        conn.commit()
    finally:
        conn.close()
