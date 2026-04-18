"""Unit tests for the file-backed ItemMetadataStore."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from domain.models.common_item_metadata import CommonItemMetadata
from domain.models.job_metadata import JobMetadata
from services.fabric.item_metadata_store import ItemMetadataStore, get_item_metadata_store


@pytest.fixture
def store(tmp_path, monkeypatch) -> ItemMetadataStore:
    """An ItemMetadataStore rooted at a tmp directory.

    Bypasses get_configuration_service() with a MagicMock returning standard
    file/dir names.
    """
    cfg = MagicMock()
    cfg.get_common_metadata_file_name.return_value = "common.json"
    cfg.get_type_specific_metadata_file_name.return_value = "type.json"
    cfg.get_jobs_directory_name.return_value = "jobs"
    monkeypatch.setattr(
        "services.fabric.item_metadata_store.get_configuration_service",
        lambda: cfg,
    )
    s = ItemMetadataStore()
    # Override the data_dir to live entirely under tmp_path so we never touch
    # the real ~/.local/share directory.
    s.data_dir = tmp_path / "store"
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


def _common(*, tenant: str, ws: str, item: str, name: str = "Demo") -> CommonItemMetadata:
    return CommonItemMetadata(
        type="LakehouseAgent",
        tenant_object_id=tenant,
        workspace_object_id=ws,
        item_object_id=item,
        display_name=name,
    )


# ── get_base_directory_path ─────────────────────────────────────────


def test_get_base_directory_path_posix(monkeypatch, store) -> None:
    monkeypatch.setattr("services.fabric.item_metadata_store.os.name", "posix")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/h")))
    p = store.get_base_directory_path("MyApp")
    assert p == Path("/h/.local/share/MyApp")


@pytest.mark.skipif(sys.platform != "win32", reason="WindowsPath not constructible on POSIX")
def test_get_base_directory_path_windows(monkeypatch, store) -> None:
    monkeypatch.setattr("services.fabric.item_metadata_store.os.name", "nt")
    monkeypatch.setenv("APPDATA", "C:/Users/x/AppData/Roaming")
    p = store.get_base_directory_path("MyApp")
    assert p == Path("C:/Users/x/AppData/Roaming") / "MyApp"


@pytest.mark.skipif(sys.platform != "win32", reason="WindowsPath not constructible on POSIX")
def test_get_base_directory_path_windows_appdata_fallback(monkeypatch, store) -> None:
    monkeypatch.setattr("services.fabric.item_metadata_store.os.name", "nt")
    monkeypatch.delenv("APPDATA", raising=False)
    p = store.get_base_directory_path("MyApp")
    # Falls back to expanduser; just sanity-check the suffix
    assert p.name == "MyApp"


# ── upsert / load / exists / delete ─────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_then_load_with_pydantic_type_specific(store) -> None:
    tenant, item = str(uuid4()), str(uuid4())
    common = _common(tenant=tenant, ws=str(uuid4()), item=item)
    type_meta = JobMetadata(job_type="X", job_instance_id=uuid4())

    await store.upsert(tenant, item, common, type_meta)
    loaded = await store.load(tenant, item, JobMetadata)

    assert loaded.common_metadata.item_object_id == common.item_object_id
    assert loaded.common_metadata.display_name == "Demo"
    assert loaded.type_specific_metadata.job_type == "X"


@pytest.mark.asyncio
async def test_upsert_then_load_with_dict_type_specific(store) -> None:
    tenant, item = str(uuid4()), str(uuid4())
    common = _common(tenant=tenant, ws=str(uuid4()), item=item)

    await store.upsert(tenant, item, common, {"foo": "bar", "n": 1})
    loaded = await store.load(tenant, item)
    assert loaded.type_specific_metadata == {"foo": "bar", "n": 1}


@pytest.mark.asyncio
async def test_upsert_writes_files_with_expected_names(store) -> None:
    tenant, item = str(uuid4()), str(uuid4())
    common = _common(tenant=tenant, ws=str(uuid4()), item=item)
    await store.upsert(tenant, item, common, {"foo": "bar"})

    item_dir = store.data_dir / tenant / item
    assert (item_dir / "common.json").exists()
    assert (item_dir / "type.json").exists()
    assert json.loads((item_dir / "type.json").read_text()) == {"foo": "bar"}


@pytest.mark.asyncio
async def test_load_raises_when_metadata_missing(store) -> None:
    with pytest.raises(FileNotFoundError):
        await store.load("missing-tenant", "missing-item")


@pytest.mark.asyncio
async def test_exists_true_after_upsert_false_otherwise(store) -> None:
    tenant, item = str(uuid4()), str(uuid4())
    assert await store.exists(tenant, item) is False

    common = _common(tenant=tenant, ws=str(uuid4()), item=item)
    await store.upsert(tenant, item, common, {})
    assert await store.exists(tenant, item) is True


@pytest.mark.asyncio
async def test_delete_removes_directory(store) -> None:
    tenant, item = str(uuid4()), str(uuid4())
    common = _common(tenant=tenant, ws=str(uuid4()), item=item)
    await store.upsert(tenant, item, common, {})
    assert await store.exists(tenant, item) is True

    await store.delete(tenant, item)
    assert await store.exists(tenant, item) is False
    assert not (store.data_dir / tenant / item).exists()


@pytest.mark.asyncio
async def test_delete_tolerates_missing_directory(store) -> None:
    """Should warn but not raise when directory doesn't exist."""
    await store.delete("never", "existed")  # must not raise


# ── upsert_job / load_job / exists_job / delete_job ────────────────


@pytest.mark.asyncio
async def test_job_metadata_roundtrip(store) -> None:
    tenant, item, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    job_meta = JobMetadata(job_type="ScheduledJob", job_instance_id=uuid4(), use_onelake=True)

    assert await store.exists_job(tenant, item, job_id) is False

    await store.upsert_job(tenant, item, job_id, job_meta)
    assert await store.exists_job(tenant, item, job_id) is True

    loaded = await store.load_job(tenant, item, job_id)
    assert loaded.job_type == "ScheduledJob"
    assert loaded.use_onelake is True
    assert loaded.is_canceled is False


@pytest.mark.asyncio
async def test_load_job_raises_when_missing(store) -> None:
    with pytest.raises(FileNotFoundError):
        await store.load_job("t", "i", "no-such-job")


@pytest.mark.asyncio
async def test_delete_job_removes_file_only(store) -> None:
    tenant, item, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    job_meta = JobMetadata(job_type="X", job_instance_id=uuid4())
    await store.upsert_job(tenant, item, job_id, job_meta)

    await store.delete_job(tenant, item, job_id)
    assert await store.exists_job(tenant, item, job_id) is False
    # The jobs directory itself should remain (delete_job is file-scoped)
    jobs_dir = store.data_dir / tenant / item / "jobs"
    assert jobs_dir.exists()


@pytest.mark.asyncio
async def test_delete_job_silent_when_missing(store) -> None:
    """delete_job tolerates missing file (no raise)."""
    await store.delete_job("t", "i", "missing")


# ── singleton accessor ─────────────────────────────────────────────


def test_get_item_metadata_store_raises_runtime_error_when_unregistered(monkeypatch) -> None:
    """When the registry has no ItemMetadataStore, the accessor must raise
    RuntimeError (not KeyError)."""
    from app.core.service_registry import ServiceRegistry
    monkeypatch.setattr(ServiceRegistry, "_instance", None)
    with pytest.raises(RuntimeError, match="ItemMetadataStore not initialized"):
        get_item_metadata_store()
