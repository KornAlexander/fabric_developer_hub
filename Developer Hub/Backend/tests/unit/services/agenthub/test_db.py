"""Unit tests for ``services.agenthub._db`` path resolution and connection."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.agenthub import _db


@pytest.fixture(autouse=True)
def _reset_db_cache() -> None:
    _db.reset_path_cache()
    yield
    _db.reset_path_cache()


def test_db_path_uses_env_when_writable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "subdir" / "agenthub.db"
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(target))
    resolved = _db.db_path()
    assert resolved == str(target)
    assert target.parent.exists()


def test_db_path_cached_between_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "cached.db"
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(target))
    p1 = _db.db_path()
    # Changing env after first resolution must not affect the cached path.
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(tmp_path / "different.db"))
    p2 = _db.db_path()
    assert p1 == p2


def test_db_path_falls_back_when_parent_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Build a parent we can't write to by chmod-ing it.
    ro = tmp_path / "readonly"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        monkeypatch.setenv("AGENTHUB_DB_PATH", str(ro / "agenthub.db"))
        # Redirect the default path to a temp location we control.
        monkeypatch.setattr(
            _db, "_default_db_path", lambda: str(tmp_path / "fallback.db")
        )
        resolved = _db.db_path()
        assert resolved == str(tmp_path / "fallback.db")
    finally:
        ro.chmod(0o700)  # allow cleanup


def test_db_path_falls_back_when_makedirs_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(tmp_path / "x" / "agenthub.db"))
    # Force makedirs to fail with OSError to exercise the except branch.
    def _boom(*_a, **_kw):
        raise OSError("synthetic")
    monkeypatch.setattr(_db.os, "makedirs", _boom)
    monkeypatch.setattr(
        _db, "_default_db_path", lambda: str(tmp_path / "fallback.db")
    )
    resolved = _db.db_path()
    assert resolved == str(tmp_path / "fallback.db")


def test_db_path_defaults_without_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENTHUB_DB_PATH", raising=False)
    monkeypatch.setattr(
        _db, "_default_db_path", lambda: str(tmp_path / "default.db")
    )
    resolved = _db.db_path()
    assert resolved == str(tmp_path / "default.db")


def test_connect_opens_usable_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(tmp_path / "conn.db"))
    conn = _db.connect()
    try:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        rows = list(conn.execute("SELECT id FROM t"))
        assert rows[0]["id"] == 1
    finally:
        conn.close()


def test_default_db_path_creates_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Posix branch: HOME -> tmp_path so the default is computed under our control.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    # os.name is readonly-ish; we can't easily monkeypatch it cross-branch.
    # But on Linux the posix branch is what will run.
    path = _db._default_db_path()
    assert Path(path).parent.is_dir()
    assert path.endswith("agenthub.db")
