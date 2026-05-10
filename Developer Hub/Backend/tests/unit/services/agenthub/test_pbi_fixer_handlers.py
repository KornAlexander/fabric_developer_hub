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
        # WS-E-IBCS step #6 (v0.103)
        "Fix_IBCSVariance",
        # WS-E-TEMPLATES (v0.52)
        "Add_MeasureTable_Empty", "Add_LastRefresh_LocalNow", "Add_CalcCalendar_Rich",
        "Add_LastRefresh_EuropeMEZ", "Add_PqCalendar_LarsSchreiber",
        "Add_MeasureTables_3WithIcons", "Add_ModelDocumentation",
    }
    assert expected.issubset(set(FIXER_HANDLERS.keys()))


# ---------- IBCS Variance (WS-E-IBCS step #6, v0.103) ----------------------


def _ibcs_visual(visual_type: str, *measures: tuple[str, str]) -> dict:
    """Build a PBIR visual.json dict with N Y-axis measure projections."""
    projections = [
        {
            "field": {
                "Measure": {
                    "Expression": {"SourceRef": {"Entity": tbl}},
                    "Property": meas,
                },
            },
            "queryRef": f"{tbl}.{meas}",
            "nativeQueryRef": meas,
        }
        for tbl, meas in measures
    ]
    return {
        "visual": {
            "visualType": visual_type,
            "query": {"queryState": {"Y": {"projections": projections}}},
        },
    }


def test_ibcs_variance_skips_single_measure_visuals():
    from services.agenthub.pbi_fixer_handlers import fix_ibcs_variance

    parts = [_visual_part(
        "page1", "v1",
        _ibcs_visual("clusteredColumnChart", ("Sales", "Revenue AC")),
    )]
    res = fix_ibcs_variance(parts, scan_only=False)
    # Single-measure visual: surfaced as candidate, NOT mutated.
    assert len(res.findings) == 1
    assert "needs >=2 Y measures" in (res.findings[0].detail or "")
    new_doc = json.loads(_decode(res.parts[0]))
    assert "objects" not in new_doc.get("visual", {})


def test_ibcs_variance_applies_to_two_measure_chart():
    from services.agenthub.pbi_fixer_handlers import fix_ibcs_variance

    parts = [_visual_part(
        "page1", "v1",
        _ibcs_visual(
            "stackedColumnChart",
            ("Sales", "Revenue AC"),
            ("Sales", "Revenue PY"),
        ),
    )]
    res = fix_ibcs_variance(parts, scan_only=False)
    assert len(res.findings) == 1
    new_doc = json.loads(_decode(res.parts[0]))
    visual = new_doc["visual"]
    # Stacked → Clustered swap
    assert visual["visualType"] == "clusteredColumnChart"
    objects = visual["objects"]
    # IBCS error bars: red (#FF0000) + green (#92D050) present
    error_blob = json.dumps(objects["error"])
    assert "#FF0000" in error_blob
    assert "#92D050" in error_blob
    # IBCS data point colors: AC=#404040, PY=#A0A0A0
    dp_blob = json.dumps(objects["dataPoint"])
    assert "#404040" in dp_blob
    assert "#A0A0A0" in dp_blob
    # % data label alignment: horizontalAlignment: 'right' on global labels
    labels_blob = json.dumps(objects["labels"])
    assert "horizontalAlignment" in labels_blob
    assert "'right'" in labels_blob
    # Chart cleanup carried over
    assert "categoryAxis" in objects and "valueAxis" in objects


def test_ibcs_variance_scan_only_does_not_mutate():
    from services.agenthub.pbi_fixer_handlers import fix_ibcs_variance

    visual = _ibcs_visual(
        "clusteredBarChart",
        ("Sales", "Revenue AC"),
        ("Sales", "Revenue PY"),
    )
    parts = [_visual_part("page1", "v1", visual)]
    original_payload = parts[0]["payload"]
    res = fix_ibcs_variance(parts, scan_only=True)
    assert len(res.findings) == 1
    # Payload unchanged in scan mode.
    assert res.parts[0]["payload"] == original_payload


def test_ibcs_macro_steps_registered():
    from services.agenthub.pbi_fixer_handlers import IBCS_MACRO_STEPS

    for step in IBCS_MACRO_STEPS:
        assert step in FIXER_HANDLERS, f"IBCS macro step {step!r} not registered"


# ---------- WS-E-TEMPLATES (v0.52) ------------------------------------------


from services.agenthub.pbi_fixer_handlers import (  # noqa: E402
    add_measure_table_empty,
    add_last_refresh_local_now,
    add_calc_calendar_rich,
    add_last_refresh_europe_mez,
    add_pq_calendar_lars_schreiber,
    add_measure_tables_3_with_icons,
    add_model_documentation,
)


def _empty_model_parts() -> list[dict]:
    return [_table_part("Sales", "\tcolumn Amount\n\t\tdataType: int64\n")]


def _decode_part_at(parts: list[dict], path_suffix: str) -> str:
    for p in parts:
        if p["path"].endswith(path_suffix):
            return _decode(p)
    raise AssertionError(f"part ending with {path_suffix} not found")


def test_add_measure_table_empty_creates_part_and_rewrites_lineage():
    parts = _empty_model_parts()
    res = add_measure_table_empty(parts, scan_only=False)
    assert any(f.object_path == "'Measure'" for f in res.findings)
    body = _decode_part_at(res.parts, "definition/tables/Measure.tmdl")
    assert "table 'Measure'" in body
    assert "11111111-1111-1111-1111-111111111111" not in body
    assert "lineageTag:" in body


def test_add_measure_table_empty_skip_collision():
    parts = [_table_part("Measure", "\tcolumn X\n\t\tdataType: string\n")]
    res = add_measure_table_empty(parts, scan_only=False)
    assert len(res.findings) == 1
    assert "skipped" in (res.findings[0].detail or "")
    assert len(res.parts) == 1


def test_add_last_refresh_local_now():
    parts = _empty_model_parts()
    res = add_last_refresh_local_now(parts, scan_only=False)
    body = _decode_part_at(res.parts, "definition/tables/Last Refresh.tmdl")
    assert "DateTime.LocalNow()" in body


def test_add_calc_calendar_rich_includes_hierarchies():
    parts = _empty_model_parts()
    res = add_calc_calendar_rich(parts, scan_only=False)
    body = _decode_part_at(res.parts, "definition/tables/CalcCalendar.tmdl")
    assert "hierarchy 'Date Hierarchy'" in body
    assert "hierarchy 'Fiscal Date Hierarchy'" in body
    assert "displayFolder: 1. Favorites" in body
    assert "MonthStartFiscalYear" in body


def test_add_last_refresh_europe_mez_creates_table_and_expression():
    parts = _empty_model_parts()
    res = add_last_refresh_europe_mez(parts, scan_only=False)
    table_body = _decode_part_at(res.parts, "definition/tables/Last Refresh.tmdl")
    expr_body = _decode_part_at(res.parts, "definition/expressions/UTC to CEST/CET.tmdl")
    assert "DateTimeZone.UtcNow()" in table_body
    assert "expression 'UTC to CEST/CET'" in expr_body


def test_add_last_refresh_europe_mez_atomic_collision():
    parts = [_table_part("Last Refresh", "\tcolumn X\n\t\tdataType: string\n")]
    res = add_last_refresh_europe_mez(parts, scan_only=False)
    assert len(res.findings) == 1
    paths = [p["path"] for p in res.parts]
    assert not any("UTC to CEST/CET" in p for p in paths)


def test_add_pq_calendar_lars_schreiber_writes_expression_only():
    parts = _empty_model_parts()
    res = add_pq_calendar_lars_schreiber(parts, scan_only=False)
    expr_body = _decode_part_at(res.parts, "definition/expressions/Kalenderfunktion.tmdl")
    assert "expression 'Kalenderfunktion'" in expr_body
    table_count = sum(1 for p in res.parts if p["path"].startswith("definition/tables/"))
    assert table_count == 1


def test_add_measure_tables_3_with_icons_atomic_all_or_nothing():
    parts = _empty_model_parts()
    res = add_measure_tables_3_with_icons(parts, scan_only=False)
    assert len(res.findings) == 3
    paths = [p["path"] for p in res.parts]
    assert any("1.📈KPIs" in p for p in paths)


def test_add_measure_tables_3_with_icons_skip_if_one_collides():
    parts = [_table_part("🎯Measures | 1.📈KPIs", "\tcolumn X\n\t\tdataType: string\n")]
    res = add_measure_tables_3_with_icons(parts, scan_only=False)
    assert len(res.findings) == 1
    assert "skipped" in (res.findings[0].detail or "")
    assert len(res.parts) == 1


def test_add_model_documentation_adds_four_calc_tables():
    parts = _empty_model_parts()
    res = add_model_documentation(parts, scan_only=False)
    assert len(res.findings) == 4
    for suffix in ("_Tables.tmdl", "_Columns.tmdl", "_DAX Measures.tmdl", "_Relationships.tmdl"):
        body = _decode_part_at(res.parts, f"definition/tables/{suffix}")
        assert "INFO.VIEW." in body


def test_template_scan_only_does_not_write_payload():
    parts = _empty_model_parts()
    original_paths = {p["path"] for p in parts}
    res = add_calc_calendar_rich(parts, scan_only=True)
    assert len(res.findings) == 1
    assert {p["path"] for p in res.parts} == original_paths


def test_template_lineage_uuids_unique_across_two_calls():
    import re as _re
    parts1 = _empty_model_parts()
    parts2 = _empty_model_parts()
    body1 = _decode_part_at(add_calc_calendar_rich(parts1, scan_only=False).parts, "CalcCalendar.tmdl")
    body2 = _decode_part_at(add_calc_calendar_rich(parts2, scan_only=False).parts, "CalcCalendar.tmdl")
    tags1 = set(_re.findall(r"lineageTag:\s*([0-9a-f-]{36})", body1))
    tags2 = set(_re.findall(r"lineageTag:\s*([0-9a-f-]{36})", body2))
    assert tags1 and tags2
    assert tags1.isdisjoint(tags2)
