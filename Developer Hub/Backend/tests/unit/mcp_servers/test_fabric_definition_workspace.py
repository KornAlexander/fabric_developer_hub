"""Unit tests for the Fabric definition workspace MCP server."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest

from mcp_servers import fabric_definition_workspace as fdw


def _part(path: str, payload: dict) -> dict:
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return {
        "path": path,
        "payload": encoded,
        "payloadType": "InlineBase64",
        "contentType": "application/json",
    }


def _checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[str, Path]:
    monkeypatch.setenv("FABRIC_DEFINITION_WORKSPACE_ROOT", str(tmp_path))
    checkout_id = str(uuid4())
    checkout_path = fdw._checkout_dir(checkout_id)
    checkout_path.mkdir(parents=True)
    return checkout_id, checkout_path


def test_materialize_item_decodes_definition_parts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    checkout_id, checkout_path = _checkout(monkeypatch, tmp_path)
    item = {"id": "report-1", "displayName": "Sales Report", "type": "Report"}
    parts = [_part("definition/report.json", {"name": "Sales"})]

    materialized = fdw._materialize_item(checkout_path, item, parts)
    manifest = {"checkoutId": checkout_id, "workspaceId": "workspace-1", "root": str(checkout_path), "items": [materialized]}
    fdw._write_manifest(checkout_path, manifest)

    entry = materialized["parts"][0]
    file_path = checkout_path / entry["file"]
    baseline_path = checkout_path / entry["baselineFile"]

    assert file_path.exists()
    assert baseline_path.exists()
    assert json.loads(file_path.read_text(encoding="utf-8")) == {"name": "Sales"}
    assert fdw._collect_file_entries(manifest)[0]["changed"] is False


def test_diff_detects_modified_added_and_deleted_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    checkout_id, checkout_path = _checkout(monkeypatch, tmp_path)
    item = {"id": "model-1", "displayName": "Model", "type": "SemanticModel"}
    materialized = fdw._materialize_item(checkout_path, item, [_part("model.bim", {"model": {"tables": []}})])
    manifest = {"checkoutId": checkout_id, "workspaceId": "workspace-1", "root": str(checkout_path), "items": [materialized]}
    fdw._write_manifest(checkout_path, manifest)

    tracked_file = checkout_path / materialized["parts"][0]["file"]
    tracked_file.write_text(json.dumps({"model": {"tables": [{"name": "T"}]}}), encoding="utf-8")
    added_file = checkout_path / materialized["directory"] / "new.tmdl"
    added_file.write_text("table T\n", encoding="utf-8")

    diff = fdw._diff_manifest(manifest)

    assert diff["changed"] is True
    assert {change["kind"] for change in diff["changes"]} == {"modified", "added"}

    tracked_file.unlink()
    diff = fdw._diff_manifest(manifest)
    assert "deleted" in {change["kind"] for change in diff["changes"]}


def test_validate_blocks_invalid_json_and_overlapping_report_visuals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    checkout_id, checkout_path = _checkout(monkeypatch, tmp_path)
    item = {"id": "report-1", "displayName": "Report", "type": "Report"}
    materialized = fdw._materialize_item(
        checkout_path,
        item,
        [
            _part("definition/pages/Page1/visuals/VisualA/visual.json", {"name": "VisualA", "position": {"x": 0, "y": 0, "width": 100, "height": 100}}),
            _part("definition/pages/Page1/visuals/VisualB/visual.json", {"name": "VisualB", "position": {"x": 50, "y": 50, "width": 100, "height": 100}}),
            _part("definition/report.json", {"name": "Report"}),
        ],
    )
    manifest = {"checkoutId": checkout_id, "workspaceId": "workspace-1", "root": str(checkout_path), "items": [materialized]}
    fdw._write_manifest(checkout_path, manifest)

    report_file = checkout_path / materialized["parts"][2]["file"]
    report_file.write_text("{broken", encoding="utf-8")

    validation = fdw._validate_manifest(manifest)

    assert validation["valid"] is False
    assert {blocker["code"] for blocker in validation["blockers"]} == {"invalid_json", "visual_overlap"}


@pytest.mark.asyncio
async def test_plan_publish_reports_ready_operations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    checkout_id, checkout_path = _checkout(monkeypatch, tmp_path)
    item = {"id": "notebook-1", "displayName": "Notebook", "type": "Notebook"}
    materialized = fdw._materialize_item(checkout_path, item, [_part("notebook-content.py", {"cells": []})])
    manifest = {"checkoutId": checkout_id, "workspaceId": "workspace-1", "root": str(checkout_path), "items": [materialized]}
    fdw._write_manifest(checkout_path, manifest)

    raw = await fdw.fabric_definition_plan_publish(checkout_id)
    body = json.loads(raw)

    assert body["ok"] is True
    assert body["ready"] is True
    assert body["operations"] == [{
        "mode": "update",
        "itemId": "notebook-1",
        "sourceItemId": "notebook-1",
        "displayName": "Notebook",
        "type": "Notebook",
        "changedPartCount": 0,
        "partCount": 1,
    }]


def test_parts_for_item_reencodes_inline_base64(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, checkout_path = _checkout(monkeypatch, tmp_path)
    item = {"id": "model-1", "displayName": "Model", "type": "SemanticModel"}
    materialized = fdw._materialize_item(checkout_path, item, [_part("model.bim", {"model": {"tables": []}})])

    file_path = checkout_path / materialized["parts"][0]["file"]
    file_path.write_text(json.dumps({"model": {"tables": [{"name": "Sales"}]}}), encoding="utf-8")

    parts = fdw._parts_for_item(checkout_path, materialized)
    decoded = json.loads(base64.b64decode(parts[0]["payload"]).decode("utf-8"))

    assert parts[0]["payloadType"] == "InlineBase64"
    assert decoded == {"model": {"tables": [{"name": "Sales"}]}}
