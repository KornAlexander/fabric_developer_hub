from __future__ import annotations

import json

import pytest

from mcp_servers import browser_visual as bv


def test_normalize_url_allows_powerbi_report_url() -> None:
    url = "https://app.powerbi.com/groups/workspace/reports/report"

    assert bv._normalize_url(url) == url


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
    assert body["visualSummary"]["visibleVisualLikeElementCount"] == 3


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
