"""Shared SQLite connection helpers for AgentHub persistence modules.

Extracted so ``session_store`` and ``workspaces_cache`` can both use the
same DB without circular imports.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading

from domain.constants.workload_constants import WorkloadConstants

logger = logging.getLogger(__name__)

_DB_PATH: str | None = None

# Whether the persistent PRAGMAs (journal_mode, synchronous, etc.) have
# already been applied to the database file by THIS process. Most of
# them are persisted across connections (journal_mode=WAL writes a flag
# in the header; synchronous is per-connection but cheap to set), but
# running them on every connect was visible in benchmark traces because
# `PRAGMA journal_mode=WAL` does a write transaction to flip the header
# even if the mode is already WAL.
_PRAGMAS_APPLIED = False
_PRAGMA_LOCK = threading.Lock()


def _default_db_path() -> str:
    workload = WorkloadConstants.WORKLOAD_NAME.replace(" ", "_")
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config")
    db_dir = os.path.join(base, workload)
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "agenthub.db")


def db_path() -> str:
    """Resolve the SQLite path; ensure its parent directory exists.

    If ``AGENTHUB_DB_PATH`` is set but the parent directory cannot be created
    (e.g. the .env contains a host path that doesn't exist inside the
    container), fall back to the default user-config location so the app
    stays usable instead of failing every request with
    ``unable to open database file``.
    """
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH

    env_path = os.environ.get("AGENTHUB_DB_PATH")
    if env_path:
        parent = os.path.dirname(env_path) or "."
        try:
            os.makedirs(parent, exist_ok=True)
            # Probe writability — makedirs can succeed on a parent we can't
            # actually write to (mounted read-only, etc.).
            if os.access(parent, os.W_OK):
                _DB_PATH = env_path
                return _DB_PATH
            logger.warning(
                "AGENTHUB_DB_PATH=%s parent dir %s is not writable; falling back to default",
                env_path, parent,
            )
        except OSError as exc:
            logger.warning(
                "AGENTHUB_DB_PATH=%s parent dir %s could not be created (%s); falling back to default",
                env_path, parent, exc,
            )

    _DB_PATH = _default_db_path()
    return _DB_PATH


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Per-connection PRAGMAs. These are cheap (header + connection
    # cache) but must be re-applied on every new connection.
    conn.execute("PRAGMA foreign_keys=ON")
    # synchronous=NORMAL is the standard production tuning when paired
    # with WAL: still crash-safe, but avoids fsync on every commit
    # which dominated event-write latency.
    conn.execute("PRAGMA synchronous=NORMAL")
    # Wait up to 5s on lock contention before raising SQLITE_BUSY.
    # Without this, concurrent writers (event appender + mission saver
    # + session updater) can fail intermittently under load.
    conn.execute("PRAGMA busy_timeout=5000")
    # Negative value = KB; -32000 = 32 MB page cache per connection.
    conn.execute("PRAGMA cache_size=-32000")
    # Keep temp tables/indexes in RAM, not on disk.
    conn.execute("PRAGMA temp_store=MEMORY")
    # 64 MB mmap turns most reads into cheap memory reads.
    conn.execute("PRAGMA mmap_size=67108864")
    # Only journal_mode is a true DB-level (header) pragma, so we
    # only need to write it once per process. Re-running it does a
    # write transaction even when already in WAL mode.
    global _PRAGMAS_APPLIED
    if not _PRAGMAS_APPLIED:
        with _PRAGMA_LOCK:
            if not _PRAGMAS_APPLIED:
                conn.execute("PRAGMA journal_mode=WAL")
                _PRAGMAS_APPLIED = True
    return conn


def reset_path_cache() -> None:
    """Test hook: forget the resolved ``_DB_PATH`` so the next call re-reads env."""
    global _DB_PATH, _PRAGMAS_APPLIED
    _DB_PATH = None
    _PRAGMAS_APPLIED = False
