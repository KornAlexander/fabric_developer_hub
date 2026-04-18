"""Unit tests for the per-user workspace cache (reconcile + TTL)."""

from datetime import UTC, datetime, timedelta

import pytest

from services.agenthub import _db, session_store, workspaces_cache


pytestmark = [pytest.mark.unit, pytest.mark.services]


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the store at a per-test SQLite file."""
    db = tmp_path / "agenthub.db"
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(db))
    monkeypatch.setattr(_db, "_DB_PATH", None)
    session_store.init_db()
    yield
    monkeypatch.setattr(_db, "_DB_PATH", None)


def test_get_cached_returns_empty_when_no_rows():
    items, newest = workspaces_cache.get_cached("user-A")
    assert items == []
    assert newest is None


def test_reconcile_inserts_then_returns_cached():
    fresh = [
        {"id": "ws-1", "name": "Workspace One"},
        {"id": "ws-2", "name": "Workspace Two"},
    ]
    result = workspaces_cache.reconcile("user-A", fresh)
    assert result.inserted == 2
    assert result.updated == 0
    assert result.deleted == 0

    items, newest = workspaces_cache.get_cached("user-A")
    assert {(w.id, w.name) for w in items} == {("ws-1", "Workspace One"), ("ws-2", "Workspace Two")}
    assert newest is not None
    assert workspaces_cache.is_fresh(newest) is True


def test_reconcile_detects_inserts_updates_and_deletes():
    workspaces_cache.reconcile("user-A", [
        {"id": "ws-1", "name": "Workspace One"},
        {"id": "ws-2", "name": "Workspace Two"},
        {"id": "ws-3", "name": "Workspace Three"},
    ])

    # ws-1 unchanged, ws-2 renamed, ws-3 deleted, ws-4 new.
    result = workspaces_cache.reconcile("user-A", [
        {"id": "ws-1", "name": "Workspace One"},
        {"id": "ws-2", "name": "Workspace Two (renamed)"},
        {"id": "ws-4", "name": "Workspace Four"},
    ])
    assert result.inserted == 1
    assert result.updated == 1
    assert result.deleted == 1

    items, _ = workspaces_cache.get_cached("user-A")
    by_id = {w.id: w.name for w in items}
    assert by_id == {
        "ws-1": "Workspace One",
        "ws-2": "Workspace Two (renamed)",
        "ws-4": "Workspace Four",
    }


def test_reconcile_is_per_user():
    workspaces_cache.reconcile("user-A", [{"id": "ws-1", "name": "A1"}])
    workspaces_cache.reconcile("user-B", [{"id": "ws-1", "name": "B1"}, {"id": "ws-2", "name": "B2"}])

    a, _ = workspaces_cache.get_cached("user-A")
    b, _ = workspaces_cache.get_cached("user-B")
    assert [w.name for w in a] == ["A1"]
    assert sorted(w.name for w in b) == ["B1", "B2"]


def test_reconcile_skips_entries_without_id():
    result = workspaces_cache.reconcile("user-A", [
        {"id": "ws-1", "name": "Keep"},
        {"name": "Skip — no id"},
        {"id": "", "name": "Skip — empty id"},
    ])
    assert result.inserted == 1
    items, _ = workspaces_cache.get_cached("user-A")
    assert [w.id for w in items] == ["ws-1"]


def test_is_fresh_ttl_boundary():
    now = datetime.now(UTC)
    assert workspaces_cache.is_fresh(now) is True
    assert workspaces_cache.is_fresh(now - workspaces_cache.CACHE_TTL + timedelta(seconds=1)) is True
    assert workspaces_cache.is_fresh(now - workspaces_cache.CACHE_TTL - timedelta(seconds=1)) is False
    assert workspaces_cache.is_fresh(None) is False
