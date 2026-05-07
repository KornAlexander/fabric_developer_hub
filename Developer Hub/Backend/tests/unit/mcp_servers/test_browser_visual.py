from __future__ import annotations

import json

import pytest

from mcp_servers import browser_visual as bv


def test_normalize_url_allows_powerbi_report_url() -> None:
    url = "https://app.powerbi.com/groups/workspace/reports/report"

    assert bv._normalize_url(url) == url


def test_node_env_includes_existing_node_path_candidates(monkeypatch, tmp_path) -> None:
    existing = tmp_path / "node_modules"
    missing = tmp_path / "missing_node_modules"
    existing.mkdir()
    monkeypatch.setenv("NODE_PATH", "/already/configured")
    monkeypatch.setattr(bv, "_NODE_PATH_CANDIDATES", (missing, existing))

    env = bv._node_env()

    assert env["NODE_PATH"] == f"/already/configured:{existing}"


@pytest.mark.asyncio
async def test_browser_visual_rejects_non_https_url() -> None:
    raw = await bv.browser_verify_visual_render("http://app.powerbi.com/groups/w/reports/r")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorCode"] == "BROWSER_VISUAL_POLICY_ERROR"
    assert "https" in body["error"]


@pytest.mark.asyncio
async def test_browser_visual_rejects_non_fabric_host() -> None:
    raw = await bv.browser_verify_visual_render("https://example.com/report")
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["errorCode"] == "BROWSER_VISUAL_POLICY_ERROR"
    assert "host" in body["error"]


@pytest.mark.asyncio
async def test_browser_visual_returns_passed_capture(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bv, "_DEFAULT_EVIDENCE_DIR", tmp_path)

    async def fake_run_node_capture(request: dict, timeout: int) -> dict:
        return {
            "ok": True,
            "httpStatus": 200,
            "finalUrl": request["url"],
            "title": "Inventory report",
            "bodyTextSample": "Inventory Overview with actual Fabric items",
            "visualSignals": {
                "viewport": {"width": 1440, "height": 1000},
                "elementCount": 3,
                "visibleElementCount": 3,
                "colorSamples": ["rgb(255, 255, 255)"],
                "elements": [{"tag": "svg", "visible": True}],
            },
            "screenshotPath": request["screenshotPath"],
            "screenshotBytes": 25_000,
            "usedStorageState": False,
        }

    monkeypatch.setattr(bv, "_run_node_capture", fake_run_node_capture)

    raw = await bv.browser_verify_visual_render(
        "https://app.fabric.microsoft.com/groups/w/reports/r",
        expected_text="Inventory Overview",
        screenshot_name="inventory-report",
    )
    body = json.loads(raw)

    assert body["ok"] is True
    assert body["status"] == "passed"
    assert body["screenshotPath"].endswith("inventory-report.png")
    assert body["expectedTextMatched"] is True
    assert body["visualSummary"]["visibleVisualLikeElementCount"] == 3


@pytest.mark.asyncio
async def test_browser_visual_uses_default_storage_state_candidate(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "browser-visual-storage-state.json"
    state_path.write_text('{"cookies": [], "origins": []}')
    monkeypatch.delenv("BROWSER_VISUAL_AUTH_STATE_PATH", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_STORAGE_STATE_PATH", raising=False)
    monkeypatch.setattr(bv, "_DEFAULT_EVIDENCE_DIR", tmp_path)
    monkeypatch.setattr(bv, "_DEFAULT_STORAGE_STATE_CANDIDATES", (state_path,))

    async def fake_run_node_capture(request: dict, timeout: int) -> dict:
        return {
            "ok": True,
            "httpStatus": 200,
            "finalUrl": request["url"],
            "title": "Inventory report",
            "bodyTextSample": "Inventory Overview with actual Fabric items",
            "visualSignals": {"elementCount": 2, "visibleElementCount": 2},
            "screenshotPath": request["screenshotPath"],
            "screenshotBytes": 25_000,
            "usedStorageState": request["storageStatePath"] == str(state_path),
        }

    monkeypatch.setattr(bv, "_run_node_capture", fake_run_node_capture)

    raw = await bv.browser_verify_visual_render(
        "https://app.fabric.microsoft.com/groups/w/reports/r",
        expected_text="Inventory Overview",
    )
    body = json.loads(raw)

    assert body["ok"] is True
    assert body["usedStorageState"] is True


@pytest.mark.asyncio
async def test_browser_visual_warns_when_expected_text_missing_but_visuals_render(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bv, "_DEFAULT_EVIDENCE_DIR", tmp_path)

    async def fake_run_node_capture(request: dict, timeout: int) -> dict:
        assert request["waitForText"] == "item count"
        return {
            "ok": True,
            "httpStatus": 200,
            "finalUrl": request["url"],
            "title": "Power BI report",
            "bodyTextSample": "Report canvas",
            "visualSignals": {"elementCount": 4, "visibleElementCount": 4, "colorSamples": ["rgb(255, 255, 255)"]},
            "screenshotPath": request["screenshotPath"],
            "screenshotBytes": 25_000,
            "usedStorageState": False,
        }

    monkeypatch.setattr(bv, "_run_node_capture", fake_run_node_capture)

    raw = await bv.browser_verify_visual_render(
        "https://app.powerbi.com/groups/w/reports/r",
        expected_text="Fabric items inventory",
    )
    body = json.loads(raw)

    assert body["ok"] is True
    assert body["status"] == "passed"
    assert body["expectedTextMatched"] is False
    assert body["warnings"] == ["expected text was not visible in scrapeable page text: 'Fabric items inventory'"]


@pytest.mark.asyncio
async def test_browser_visual_accepts_inventory_measure_alias_for_hidden_page_label(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bv, "_DEFAULT_EVIDENCE_DIR", tmp_path)

    async def fake_run_node_capture(request: dict, timeout: int) -> dict:
        return {
            "ok": True,
            "httpStatus": 200,
            "finalUrl": request["url"] + "/InventoryOverview",
            "title": "Inventory report",
            "expectedTextMatched": False,
            "bodyTextSample": "Power BI Report\n503\nItem Count\nFilters",
            "visualSignals": {"elementCount": 4, "visibleElementCount": 0, "colorSamples": ["rgb(255, 255, 255)"]},
            "screenshotPath": request["screenshotPath"],
            "screenshotBytes": 47_000,
            "usedStorageState": True,
        }

    monkeypatch.setattr(bv, "_run_node_capture", fake_run_node_capture)

    raw = await bv.browser_verify_visual_render(
        "https://app.powerbi.com/groups/w/reports/r",
        expected_text="Fabric Items",
    )
    body = json.loads(raw)

    assert body["ok"] is True
    assert body["status"] == "passed"
    assert body["expectedTextMatched"] is True


@pytest.mark.asyncio
async def test_browser_visual_marks_login_capture_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bv, "_DEFAULT_EVIDENCE_DIR", tmp_path)

    async def fake_run_node_capture(request: dict, timeout: int) -> dict:
        return {
            "ok": True,
            "finalUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            "title": "Sign in to your account",
            "bodyTextSample": "Pick an account to sign in",
            "visualSignals": {"elementCount": 1, "visibleElementCount": 1},
            "screenshotPath": request["screenshotPath"],
            "screenshotBytes": 20_000,
            "usedStorageState": False,
        }

    monkeypatch.setattr(bv, "_run_node_capture", fake_run_node_capture)

    raw = await bv.browser_verify_visual_render(
        "https://app.powerbi.com/groups/w/reports/r",
    )
    body = json.loads(raw)

    assert body["ok"] is False
    assert body["status"] == "unavailable"
    assert body["errorCode"] == "BROWSER_AUTH_REQUIRED"
