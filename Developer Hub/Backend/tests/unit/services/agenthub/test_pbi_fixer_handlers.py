"""Smoke tests for PBI Fixer handlers (TMDL + PBIR JSON mutations)."""

from __future__ import annotations

import base64
import json

from services.agenthub.pbi_fixer_handlers import (
    FIXER_HANDLERS,
    fix_floating_point_datatype,
    fix_do_not_summarize,
    fix_measure_format,
    fix_percentage_format,
    fix_whole_number_format,
    fix_is_available_in_mdx_false,
    fix_hide_foreign_keys,
    fix_pie_chart,
    fix_page_size,
    fix_hide_visual_filters,
    fix_disable_show_items_no_data,
    fix_remove_unused_custom_visuals,
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _decode(part: dict) -> str:
    return base64.b64decode(part["payload"]).decode("utf-8")


# ---------- TMDL fixtures --------------------------------------------------


def _table_part(table_name: str, body: str) -> dict:
    text = f"table {table_name}\n{body}"
    return {"path": f"definition/tables/{table_name}.tmdl", "payload": _b64(text), "payloadType": "InlineBase64"}


def test_floating_point_datatype():
    body = (
        "\tcolumn Amount\n"
        "\t\tdataType: double\n"
        "\t\tsourceColumn: Amount\n"
    )
    parts = [_table_part("Sales", body)]
    res = fix_floating_point_datatype(parts, scan_only=False)
    assert len(res.findings) == 1
    assert "Amount" in res.findings[0].object_path
    out = _decode(res.parts[0])
    assert "dataType: decimal" in out
    assert "dataType: double" not in out


def test_do_not_summarize():
    body = (
        "\tcolumn Amount\n"
        "\t\tdataType: int64\n"
        "\t\tsummarizeBy: sum\n"
    )
    parts = [_table_part("Sales", body)]
    res = fix_do_not_summarize(parts, scan_only=False)
    assert len(res.findings) == 1
    out = _decode(res.parts[0])
    assert "summarizeBy: none" in out


def test_measure_format_added_only_when_missing():
    body = (
        "\tmeasure Sales = SUM(Sales[Amount])\n"
        "\t\tlineageTag: aaaaa\n"
        "\n"
        "\tmeasure 'Total Tax' = SUM(Sales[Tax])\n"
        "\t\tformatString: \"#,0.00\"\n"
        "\t\tlineageTag: bbbbb\n"
    )
    parts = [_table_part("Sales", body)]
    res = fix_measure_format(parts, scan_only=False)
    paths = {f.object_path for f in res.findings}
    # Only "Sales" is missing formatString.
    assert any("Sales" in p and "Total Tax" not in p for p in paths)
    assert not any("Total Tax" in p for p in paths)
    out = _decode(res.parts[0])
    assert 'formatString: "#,0"' in out
    # Existing format preserved
    assert 'formatString: "#,0.00"' in out


def test_percentage_format():
    body = (
        "\tmeasure 'Margin %' = DIVIDE([Profit], [Revenue])\n"
        "\t\tlineageTag: ccccc\n"
        "\n"
        "\tmeasure Sales = SUM(Sales[Amount])\n"
        "\t\tformatString: \"#,0\"\n"
    )
    parts = [_table_part("Sales", body)]
    res = fix_percentage_format(parts, scan_only=False)
    assert len(res.findings) == 1
    assert "Margin %" in res.findings[0].object_path
    out = _decode(res.parts[0])
    assert '#,0.0%;-#,0.0%;#,0.0%' in out


def test_whole_number_format():
    body = (
        "\tcolumn Quantity\n"
        "\t\tdataType: int64\n"
        "\t\tsummarizeBy: sum\n"
        "\n"
        "\tcolumn Price\n"
        "\t\tdataType: decimal\n"
        "\t\tformatString: \"$#,0.00\"\n"
    )
    parts = [_table_part("Sales", body)]
    res = fix_whole_number_format(parts, scan_only=False)
    assert len(res.findings) == 1
    assert "Quantity" in res.findings[0].object_path
    out = _decode(res.parts[0])
    assert 'formatString: "#,0"' in out


def test_is_available_in_mdx_false_for_hidden():
    body = (
        "\tcolumn HiddenCol\n"
        "\t\tdataType: string\n"
        "\t\tisHidden: true\n"
        "\n"
        "\tcolumn VisibleCol\n"
        "\t\tdataType: string\n"
    )
    parts = [_table_part("Sales", body)]
    res = fix_is_available_in_mdx_false(parts, scan_only=False)
    assert len(res.findings) == 1
    assert "HiddenCol" in res.findings[0].object_path
    out = _decode(res.parts[0])
    assert "isAvailableInMdx: false" in out


def test_hide_foreign_keys():
    rel_text = (
        "relationship abcd-1234\n"
        "\tfromColumn: 'Sales'.'CustomerKey'\n"
        "\ttoColumn: 'Customer'.'CustomerKey'\n"
    )
    table = _table_part(
        "Sales",
        "\tcolumn CustomerKey\n\t\tdataType: int64\n\n\tcolumn Amount\n\t\tdataType: decimal\n",
    )
    rel = {"path": "definition/relationships.tmdl", "payload": _b64(rel_text), "payloadType": "InlineBase64"}
    res = fix_hide_foreign_keys([table, rel], scan_only=False)
    assert len(res.findings) == 1
    assert "CustomerKey" in res.findings[0].object_path
    out_table = _decode([p for p in res.parts if p["path"].endswith("Sales.tmdl")][0])
    assert "isHidden: true" in out_table


def test_scan_only_does_not_mutate_payload():
    body = "\tcolumn Amount\n\t\tdataType: double\n"
    parts = [_table_part("Sales", body)]
    original_payload = parts[0]["payload"]
    res = fix_floating_point_datatype(parts, scan_only=True)
    assert len(res.findings) == 1
    # Payload is unchanged
    assert res.parts[0]["payload"] == original_payload


# ---------- PBIR fixtures --------------------------------------------------


def _visual_part(page: str, vis: str, doc: dict) -> dict:
    return {
        "path": f"definition/pages/{page}/visuals/{vis}/visual.json",
        "payload": _b64(json.dumps(doc)),
        "payloadType": "InlineBase64",
    }


def _page_part(page: str, doc: dict) -> dict:
    return {
        "path": f"definition/pages/{page}/page.json",
        "payload": _b64(json.dumps(doc)),
        "payloadType": "InlineBase64",
    }


def test_pie_chart_replaced():
    parts = [_visual_part("page1", "v1", {"visual": {"visualType": "pieChart"}})]
    res = fix_pie_chart(parts, scan_only=False)
    assert len(res.findings) == 1
    new_doc = json.loads(_decode(res.parts[0]))
    assert new_doc["visual"]["visualType"] == "barChart"


def test_page_size_resized():
    parts = [_page_part("page1", {"width": 800, "height": 600, "displayName": "p1"})]
    res = fix_page_size(parts, scan_only=False)
    assert len(res.findings) == 1
    new_doc = json.loads(_decode(res.parts[0]))
    assert new_doc["width"] == 1280 and new_doc["height"] == 720


def test_hide_visual_filters_sets_flag():
    doc = {
        "visual": {"visualType": "barChart"},
        "filterConfig": {"filters": [{"name": "f1"}, {"name": "f2", "isHiddenInViewMode": True}]},
    }
    parts = [_visual_part("page1", "v1", doc)]
    res = fix_hide_visual_filters(parts, scan_only=False)
    # Only the un-hidden one is a finding
    assert len(res.findings) == 1
    new_doc = json.loads(_decode(res.parts[0]))
    assert all(f.get("isHiddenInViewMode") for f in new_doc["filterConfig"]["filters"])


def test_disable_show_items_no_data():
    doc = {
        "visual": {
            "visualType": "barChart",
            "query": {"queryState": {"Category": {"projections": [{"showAll": True, "field": {}}]}}},
        }
    }
    parts = [_visual_part("page1", "v1", doc)]
    res = fix_disable_show_items_no_data(parts, scan_only=False)
    assert len(res.findings) == 1
    new_doc = json.loads(_decode(res.parts[0]))
    proj = new_doc["visual"]["query"]["queryState"]["Category"]["projections"][0]
    assert "showAll" not in proj


def test_remove_unused_custom_visuals():
    report = {
        "path": "definition/report.json",
        "payload": _b64(json.dumps({"publicCustomVisuals": ["used-guid", "unused-guid"]})),
        "payloadType": "InlineBase64",
    }
    visual = _visual_part("page1", "v1", {"visual": {"visualType": "used-guid"}})
    res = fix_remove_unused_custom_visuals([report, visual], scan_only=False)
    assert len(res.findings) == 1
    assert "unused-guid" in res.findings[0].object_path
    new_report = json.loads(_decode([p for p in res.parts if p["path"].endswith("report.json")][0]))
    assert new_report["publicCustomVisuals"] == ["used-guid"]


def test_registry_completeness():
    expected = {
        "Fix_FloatingPointDataType", "Fix_DoNotSummarize", "Fix_DiscourageImplicitMeasures",
        "Fix_IsAvailableInMdxFalse", "Fix_MeasureFormat", "Fix_PercentageFormat",
        "Fix_WholeNumberFormat", "Fix_HideForeignKeys",
        "Fix_PieChart", "Fix_PageSize", "Fix_HideVisualFilters",
        "Fix_DisableShowItemsNoData", "Fix_RemoveUnusedCustomVisuals",
    }
    assert expected.issubset(set(FIXER_HANDLERS.keys()))
