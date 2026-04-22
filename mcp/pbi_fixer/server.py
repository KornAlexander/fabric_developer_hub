"""
PBI Fixer MCP Server â€” Exposes ALL sempy_labs fixer/add/migration/scan
capabilities as MCP tools.

Wraps 80+ sempy_labs functions so any MCP-compatible agent (AgentHub,
Copilot Studio, Claude, etc.) can scan and fix Power BI reports,
semantic models, run admin tasks, migrations, and more in Microsoft Fabric.

Install:  pip install mcp[cli] semantic-link-labs
Run:      python -m mcp.pbi_fixer.server
"""

from __future__ import annotations

import importlib
import inspect
import io
import logging
from contextlib import redirect_stdout
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

# Note: newer versions of the ``mcp`` SDK (>=1.x) dropped the
# ``version`` / ``description`` kwargs from ``FastMCP.__init__`` — only the
# server name is accepted. Passing the extras causes a TypeError at import
# time, which in turn makes ``MCPClientManager`` log
# "Connection closed" during discovery. Keep this call minimal.
#
# ``log_level="WARNING"`` stops FastMCP from calling
# ``logging.basicConfig(level=INFO, format="%(message)s")`` at import time.
# That default spams bare ``Processing request of type ListToolsRequest``
# lines on stderr of the subprocess, which Docker surfaces under
# ``backend-1 |`` without our timestamp/level/request-id formatter.
mcp = FastMCP("pbi-fixer", log_level="WARNING")

log = logging.getLogger("pbi-fixer")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture(fn, **kwargs) -> str:
    """Run a function and capture its stdout as a string."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(**kwargs)
    output = buf.getvalue().strip()
    if result is not None:
        output += f"\n[return: {result}]"
    return output or "No output produced."


def _import(module_path: str, func_name: str):
    """Lazy-import a function from sempy_labs."""
    mod = importlib.import_module(module_path)
    return getattr(mod, func_name)


def _build_kwargs(explicit: dict[str, Any]) -> dict[str, Any]:
    """Filter out None values so optional params aren't passed."""
    return {k: v for k, v in explicit.items() if v is not None}


def _sm_fix(mod: str, func: str, dataset: str, workspace: str | None, scan_only: bool) -> str:
    """Generic handler for SM fixers that take (dataset|report, workspace, scan_only)."""
    fn = _import(mod, func)
    first_param = list(inspect.signature(fn).parameters.keys())[0]
    kw: dict[str, Any] = {first_param: dataset, "scan_only": scan_only}
    if workspace:
        kw["workspace"] = workspace
    return _capture(fn, **kw)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  REPORT â€” SCAN  (2 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def scan_report(
    report: str,
    workspace: str | None = None,
    page_name: str | None = None,
) -> str:
    """Scan a Power BI report for ALL fixable issues at once.

    Runs every report fixer in scan-only mode and returns a combined
    list of detected problems.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID (omit for default).
        page_name: Limit scan to a single page.
    """
    fixers = [
        ("sempy_labs.report._Fix_PieChart", "fix_piecharts"),
        ("sempy_labs.report._Fix_Charts", "fix_barcharts"),
        ("sempy_labs.report._Fix_Charts", "fix_columncharts"),
        ("sempy_labs.report._Fix_Charts", "fix_linecharts"),
        ("sempy_labs.report._Fix_Charts", "fix_column_to_bar"),
        ("sempy_labs.report._Fix_Charts", "fix_bar_to_column"),
        ("sempy_labs.report._Fix_ColumnToLine", "fix_column_to_line"),
        ("sempy_labs.report._Fix_PageSize", "fix_page_size"),
        ("sempy_labs.report._Fix_HideVisualFilters", "fix_hide_visual_filters"),
        ("sempy_labs.report._Fix_DisableShowItemsNoData", "fix_disable_show_items_no_data"),
        ("sempy_labs.report._Fix_IBCSVariance", "fix_ibcs_variance"),
        ("sempy_labs.report._Fix_RemoveUnusedCustomVisuals", "fix_remove_unused_custom_visuals"),
        ("sempy_labs.report._Fix_MigrateReportLevelMeasures", "fix_migrate_report_level_measures"),
        ("sempy_labs.report._Fix_VisualAlignment", "fix_visual_alignment"),
        ("sempy_labs.report._fix_report_bpa", "fix_report_bpa"),
    ]
    parts: list[str] = []
    kw = _build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=True))
    for mod, func_name in fixers:
        try:
            fn = _import(mod, func_name)
            out = _capture(fn, **kw)
            if out and out != "No output produced.":
                parts.append(f"â”€â”€ {func_name} â”€â”€\n{out}")
        except Exception as e:
            parts.append(f"â”€â”€ {func_name} â”€â”€ ERROR: {e}")
    return "\n\n".join(parts) if parts else "âœ“ No issues found."


@mcp.tool()
def fix_report_bpa(
    report: str,
    workspace: str | None = None,
    scan_only: bool = False,
) -> str:
    """Run Best Practice Analyzer on a report and auto-fix violations.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        scan_only: Only report violations without fixing.
    """
    fn = _import("sempy_labs.report._fix_report_bpa", "fix_report_bpa")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, scan_only=scan_only)))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  REPORT â€” CHART FIXERS  (7 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def fix_piecharts(
    report: str, workspace: str | None = None, page_name: str | None = None,
    target_visual_type: str = "clusteredBarChart", scan_only: bool = False,
) -> str:
    """Replace pie chart visuals with bar charts (or another type).

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        target_visual_type: Replacement visual type (default: clusteredBarChart).
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_PieChart", "fix_piecharts")
    return _capture(fn, **_build_kwargs(dict(
        report=report, workspace=workspace, page_name=page_name,
        target_visual_type=target_visual_type, scan_only=scan_only)))


@mcp.tool()
def fix_barcharts(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Fix bar chart formatting to best practices (data labels, gridlines, axes).

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_Charts", "fix_barcharts")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


@mcp.tool()
def fix_columncharts(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Fix column chart formatting to best practices.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_Charts", "fix_columncharts")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


@mcp.tool()
def fix_linecharts(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Fix line chart formatting (keeps Y axis visible).

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_Charts", "fix_linecharts")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


@mcp.tool()
def fix_column_to_bar(
    report: str, workspace: str | None = None, page_name: str | None = None,
    scan_only: bool = False, force_clustered: bool = False,
) -> str:
    """Convert column charts to bar charts (IBCS: categorical data â†’ bars).

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
        force_clustered: Force conversion to clustered bar chart.
    """
    fn = _import("sempy_labs.report._Fix_Charts", "fix_column_to_bar")
    return _capture(fn, **_build_kwargs(dict(
        report=report, workspace=workspace, page_name=page_name,
        scan_only=scan_only, force_clustered=force_clustered)))


@mcp.tool()
def fix_bar_to_column(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Convert bar charts to column charts.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_Charts", "fix_bar_to_column")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


@mcp.tool()
def fix_column_to_line(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Convert column charts with date axes to line charts (IBCS best practice).

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_ColumnToLine", "fix_column_to_line")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  REPORT â€” LAYOUT & VISUAL FIXERS  (7 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def fix_page_size(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Standardize report pages to Full HD (1920x1080). Only changes default 1280x720 pages.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_PageSize", "fix_page_size")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


@mcp.tool()
def fix_hide_visual_filters(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Hide all visual-level filter panes (set isHiddenInViewMode=True).

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_HideVisualFilters", "fix_hide_visual_filters")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


@mcp.tool()
def fix_disable_show_items_no_data(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Disable 'Show items with no data' on all visuals (performance optimization).

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_DisableShowItemsNoData", "fix_disable_show_items_no_data")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


@mcp.tool()
def fix_ibcs_variance(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Apply IBCS variance chart formatting standards.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_IBCSVariance", "fix_ibcs_variance")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


@mcp.tool()
def fix_remove_unused_custom_visuals(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Remove custom visual registrations not used by any visual.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_RemoveUnusedCustomVisuals", "fix_remove_unused_custom_visuals")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


@mcp.tool()
def fix_visual_alignment(
    report: str, workspace: str | None = None, page_name: str | None = None,
    scan_only: bool = False, tolerance_pct: float = 2.0,
) -> str:
    """Auto-align visuals that are nearly aligned (within tolerance).

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
        tolerance_pct: Alignment tolerance as % of page size (default: 2.0).
    """
    fn = _import("sempy_labs.report._Fix_VisualAlignment", "fix_visual_alignment")
    return _capture(fn, **_build_kwargs(dict(
        report=report, workspace=workspace, page_name=page_name, scan_only=scan_only, tolerance_pct=tolerance_pct)))


@mcp.tool()
def fix_migrate_report_level_measures(
    report: str, workspace: str | None = None, page_name: str | None = None, scan_only: bool = False,
) -> str:
    """Migrate report-level measures to the semantic model.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_MigrateReportLevelMeasures", "fix_migrate_report_level_measures")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=scan_only)))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  REPORT â€” SLICER & FORMAT MIGRATION  (2 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def fix_migrate_slicers(
    report: str, page_name: str, workspace: str | None = None, scan_only: bool = False,
) -> str:
    """Migrate native slicers to SlicerBar custom visual on a specific page.

    Args:
        report: Report name or UUID.
        page_name: Page name (required â€” migration is per-page).
        workspace: Workspace name or UUID.
        scan_only: Preview changes without applying.
    """
    fn = _import("sempy_labs.report._Fix_MigrateSlicerToSlicerbar", "fix_migrate_slicer_to_slicerbar")
    return _capture(fn, **_build_kwargs(dict(report=report, page_name=page_name, workspace=workspace, scan_only=scan_only)))


@mcp.tool()
def fix_upgrade_to_pbir(
    report: str, workspace: str | None = None, scan_only: bool = False,
) -> str:
    """Upgrade a report from PBIRLegacy to PBIR format via REST API.

    Prerequisite for all other report fixers.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        scan_only: Check eligibility without converting.
    """
    fn = _import("sempy_labs.report._Fix_UpgradeToPbir", "fix_upgrade_to_pbir")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace, scan_only=scan_only)))


@mcp.tool()
def upgrade_to_pbir_bulk(
    report: str | list[str] | None = None,
    workspace: str | list[str] | None = None,
) -> str:
    """Bulk upgrade reports to PBIR format across one or more workspaces.

    Args:
        report: Report name(s) or UUID(s). Omit to upgrade all in workspace.
        workspace: Workspace name(s) or UUID(s). Omit for default.
    """
    fn = _import("sempy_labs.report._upgrade_to_pbir", "upgrade_to_pbir")
    return _capture(fn, **_build_kwargs(dict(report=report, workspace=workspace)))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  REPORT â€” EXPORT & GENERATION  (2 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def export_report(
    report: str, export_format: str, workspace: str | None = None,
    file_name: str | None = None, page_name: str | None = None, bookmark_name: str | None = None,
) -> str:
    """Export a report to PDF, PNG, PPTX, or other formats.

    Args:
        report: Report name.
        export_format: PDF, PNG, PPTX, XLSX, CSV, XML, MHTML, IMAGE, ACCESSIBLEPDF.
        workspace: Workspace name or UUID.
        file_name: Output file name.
        page_name: Export a specific page only.
        bookmark_name: Apply a bookmark before exporting.
    """
    fn = _import("sempy_labs.report._export_report", "export_report")
    return _capture(fn, **_build_kwargs(dict(
        report=report, export_format=export_format, workspace=workspace,
        file_name=file_name, page_name=page_name, bookmark_name=bookmark_name)))


@mcp.tool()
def create_model_bpa_report(
    report: str | None = None, dataset: str | None = None, dataset_workspace: str | None = None,
) -> str:
    """Create a Best Practice Analyzer report for a semantic model.

    Args:
        report: Report name (default: 'Model BPA').
        dataset: Semantic model name (default: 'Model BPA').
        dataset_workspace: Workspace for the BPA dataset.
    """
    fn = _import("sempy_labs.report._generate_report", "create_model_bpa_report")
    return _capture(fn, **_build_kwargs(dict(report=report, dataset=dataset, dataset_workspace=dataset_workspace)))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  SEMANTIC MODEL â€” SCAN  (2 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def scan_semantic_model(
    dataset: str, workspace: str | None = None,
) -> str:
    """Scan a semantic model for ALL fixable issues at once.

    Runs every SM fixer + add script in scan-only mode.

    Args:
        dataset: Semantic model name or UUID.
        workspace: Workspace name or UUID.
    """
    fixers = [
        ("sempy_labs.semantic_model._Fix_AvoidAdding0", "fix_avoid_adding_zero"),
        ("sempy_labs.semantic_model._Fix_CapitalizeObjectNames", "fix_capitalize_object_names"),
        ("sempy_labs.semantic_model._Fix_DataCategory", "fix_data_category"),
        ("sempy_labs.semantic_model._Fix_DateColumnFormat", "fix_date_column_format"),
        ("sempy_labs.semantic_model._Fix_DefaultDataSourceVersion", "fix_default_datasource_version"),
        ("sempy_labs.semantic_model._Fix_DiscourageImplicitMeasures", "fix_discourage_implicit_measures"),
        ("sempy_labs.semantic_model._Fix_DoNotSummarize", "fix_do_not_summarize"),
        ("sempy_labs.semantic_model._Fix_FlagColumnFormat", "fix_flag_column_format"),
        ("sempy_labs.semantic_model._Fix_FloatingPointDataType", "fix_floating_point_datatype"),
        ("sempy_labs.semantic_model._Fix_HideForeignKeys", "fix_hide_foreign_keys"),
        ("sempy_labs.semantic_model._Fix_IsAvailableInMdx", "fix_isavailable_in_mdx"),
        ("sempy_labs.semantic_model._Fix_IsAvailableInMdxTrue", "fix_isavailable_in_mdx_true"),
        ("sempy_labs.semantic_model._Fix_MarkPrimaryKeys", "fix_mark_primary_keys"),
        ("sempy_labs.semantic_model._Fix_MeasureDescriptions", "fix_measure_descriptions"),
        ("sempy_labs.semantic_model._Fix_MeasureFormat", "fix_measure_format"),
        ("sempy_labs.semantic_model._Fix_MonthColumnFormat", "fix_month_column_format"),
        ("sempy_labs.semantic_model._Fix_PercentageFormat", "fix_percentage_format"),
        ("sempy_labs.semantic_model._Fix_SortMonthColumn", "fix_sort_month_column"),
        ("sempy_labs.semantic_model._Fix_TrimObjectNames", "fix_trim_object_names"),
        ("sempy_labs.semantic_model._Fix_UseDivideFunction", "fix_use_divide_function"),
        ("sempy_labs.semantic_model._Fix_WholeNumberFormat", "fix_whole_number_format"),
        ("sempy_labs.semantic_model._Add_MeasuresFromColumns", "add_measures_from_columns"),
    ]
    parts: list[str] = []
    for mod, func_name in fixers:
        try:
            fn = _import(mod, func_name)
            first_param = list(inspect.signature(fn).parameters.keys())[0]
            kw: dict[str, Any] = {first_param: dataset, "scan_only": True}
            if workspace:
                kw["workspace"] = workspace
            out = _capture(fn, **kw)
            if out and out != "No output produced.":
                parts.append(f"â”€â”€ {func_name} â”€â”€\n{out}")
        except Exception as e:
            parts.append(f"â”€â”€ {func_name} â”€â”€ ERROR: {e}")
    return "\n\n".join(parts) if parts else "âœ“ No issues found."


@mcp.tool()
def fix_model_bpa(
    dataset: str, workspace: str | None = None, scan_only: bool = False,
) -> str:
    """Run Best Practice Analyzer on a semantic model and auto-fix violations.

    Args:
        dataset: Semantic model name or UUID.
        workspace: Workspace name or UUID.
        scan_only: Only report violations without fixing.
    """
    fn = _import("sempy_labs._fix_model_bpa", "fix_model_bpa")
    return _capture(fn, **_build_kwargs(dict(dataset=dataset, workspace=workspace, scan_only=scan_only)))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  SEMANTIC MODEL â€” INDIVIDUAL FIXERS  (21 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def fix_avoid_adding_zero(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Fix measures that add +0 unnecessarily (performance anti-pattern).

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_AvoidAdding0", "fix_avoid_adding_zero", dataset, workspace, scan_only)


@mcp.tool()
def fix_capitalize_object_names(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Capitalize first letter of all table, column, and measure names.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_CapitalizeObjectNames", "fix_capitalize_object_names", dataset, workspace, scan_only)


@mcp.tool()
def fix_data_category(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set appropriate data categories on columns (City, Country, URL, etc.).

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_DataCategory", "fix_data_category", dataset, workspace, scan_only)


@mcp.tool()
def fix_date_column_format(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set date columns to proper date formatting.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_DateColumnFormat", "fix_date_column_format", dataset, workspace, scan_only)


@mcp.tool()
def fix_default_datasource_version(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set default data source version to PowerBI_V3.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_DefaultDataSourceVersion", "fix_default_datasource_version", dataset, workspace, scan_only)


@mcp.tool()
def fix_discourage_implicit_measures(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set DiscourageImplicitMeasures=True on all tables.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_DiscourageImplicitMeasures", "fix_discourage_implicit_measures", dataset, workspace, scan_only)


@mcp.tool()
def fix_do_not_summarize(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set SummarizeBy=None on non-numeric columns to prevent accidental aggregation.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_DoNotSummarize", "fix_do_not_summarize", dataset, workspace, scan_only)


@mcp.tool()
def fix_flag_column_format(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Format boolean/flag columns properly.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_FlagColumnFormat", "fix_flag_column_format", dataset, workspace, scan_only)


@mcp.tool()
def fix_floating_point_datatype(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Replace Double with Decimal data type to avoid floating point errors.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_FloatingPointDataType", "fix_floating_point_datatype", dataset, workspace, scan_only)


@mcp.tool()
def fix_hide_foreign_keys(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Hide foreign key columns used in relationships.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_HideForeignKeys", "fix_hide_foreign_keys", dataset, workspace, scan_only)


@mcp.tool()
def fix_isavailable_in_mdx(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set IsAvailableInMdx=False on non-measure columns (performance).

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_IsAvailableInMdx", "fix_isavailable_in_mdx", dataset, workspace, scan_only)


@mcp.tool()
def fix_isavailable_in_mdx_true(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set IsAvailableInMdx=True on measure columns.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_IsAvailableInMdxTrue", "fix_isavailable_in_mdx_true", dataset, workspace, scan_only)


@mcp.tool()
def fix_mark_primary_keys(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Mark relationship 'one' side columns as primary keys.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_MarkPrimaryKeys", "fix_mark_primary_keys", dataset, workspace, scan_only)


@mcp.tool()
def fix_measure_descriptions(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Auto-generate descriptions for measures that lack them.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_MeasureDescriptions", "fix_measure_descriptions", dataset, workspace, scan_only)


@mcp.tool()
def fix_measure_format(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set proper format strings on measures that lack formatting.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_MeasureFormat", "fix_measure_format", dataset, workspace, scan_only)


@mcp.tool()
def fix_month_column_format(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Format month columns properly (month name display).

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_MonthColumnFormat", "fix_month_column_format", dataset, workspace, scan_only)


@mcp.tool()
def fix_percentage_format(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set percentage format on measures returning percentage values.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_PercentageFormat", "fix_percentage_format", dataset, workspace, scan_only)


@mcp.tool()
def fix_sort_month_column(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set SortByColumn on month name columns to sort by month number.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_SortMonthColumn", "fix_sort_month_column", dataset, workspace, scan_only)


@mcp.tool()
def fix_trim_object_names(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Trim leading/trailing whitespace from all object names.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_TrimObjectNames", "fix_trim_object_names", dataset, workspace, scan_only)


@mcp.tool()
def fix_use_divide_function(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Replace division operator (/) with DIVIDE() function in DAX measures.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_UseDivideFunction", "fix_use_divide_function", dataset, workspace, scan_only)


@mcp.tool()
def fix_whole_number_format(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Set whole number format on integer measures.

    Args: dataset: SM name/UUID. workspace: Workspace name/UUID. scan_only: Preview only.
    """
    return _sm_fix("sempy_labs.semantic_model._Fix_WholeNumberFormat", "fix_whole_number_format", dataset, workspace, scan_only)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  SEMANTIC MODEL â€” ENHANCEMENTS / ADD  (10 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def add_measures_from_columns(
    dataset: str, workspace: str | None = None, target_table: str | None = None, scan_only: bool = False,
) -> str:
    """Create explicit measures from columns with SummarizeBy set. Hides source column.

    Args: dataset: SM name/UUID. workspace: Workspace. target_table: Measure destination. scan_only: Preview.
    """
    fn = _import("sempy_labs.semantic_model._Add_MeasuresFromColumns", "add_measures_from_columns")
    return _capture(fn, **_build_kwargs(dict(dataset=dataset, workspace=workspace, target_table=target_table, scan_only=scan_only)))


@mcp.tool()
def add_py_measures(
    dataset: str, workspace: str | None = None, measures: list[str] | None = None,
    calendar_table: str | None = None, date_column: str | None = None,
    target_table: str | None = None, scan_only: bool = False,
) -> str:
    """Add PY time intelligence measures (PY, Delta, Delta %, Max Green/Red). 5 per source measure.

    Args: dataset: SM name/UUID. workspace: Workspace. measures: Specific measures. calendar_table/date_column: Date table. target_table: Destination. scan_only: Preview.
    """
    fn = _import("sempy_labs.semantic_model._Add_PYMeasures", "add_py_measures")
    return _capture(fn, **_build_kwargs(dict(
        dataset=dataset, workspace=workspace, measures=measures, calendar_table=calendar_table,
        date_column=date_column, target_table=target_table, scan_only=scan_only)))


@mcp.tool()
def add_cache_warming(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Add cache warming queries to speed up initial report loads.

    Args: dataset: SM name/UUID. workspace: Workspace. scan_only: Preview.
    """
    return _sm_fix("sempy_labs.semantic_model._Add_CacheWarming", "add_cache_warming", dataset, workspace, scan_only)


@mcp.tool()
def add_calc_group_time_intelligence(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Add a Time Intelligence calculation group (YTD, QTD, MTD, PY, etc.).

    Args: dataset: SM name/UUID. workspace: Workspace. scan_only: Preview.
    """
    return _sm_fix("sempy_labs.semantic_model._Add_CalcGroup_TimeIntelligence", "add_calc_group_time_intelligence", dataset, workspace, scan_only)


@mcp.tool()
def add_calc_group_units(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Add a Units calculation group (K, M, B formatting).

    Args: dataset: SM name/UUID. workspace: Workspace. scan_only: Preview.
    """
    return _sm_fix("sempy_labs.semantic_model._Add_CalcGroup_Units", "add_calc_group_units", dataset, workspace, scan_only)


@mcp.tool()
def add_calculated_calendar(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Add a calculated calendar/date table with auto-detected relationships.

    Args: dataset: SM name/UUID. workspace: Workspace. scan_only: Preview.
    """
    return _sm_fix("sempy_labs.semantic_model._Add_CalculatedTable_Calendar", "add_calculated_calendar", dataset, workspace, scan_only)


@mcp.tool()
def add_measure_table(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Create a dedicated '_Measures' table and move all measures into it.

    Args: dataset: SM name/UUID. workspace: Workspace. scan_only: Preview.
    """
    return _sm_fix("sempy_labs.semantic_model._Add_CalculatedTable_MeasureTable", "add_measure_table", dataset, workspace, scan_only)


@mcp.tool()
def add_incremental_refresh(
    dataset: str, table_name: str, workspace: str | None = None,
    column_name: str | None = None, rolling_window_years: int = 3,
    incremental_days: int = 30, only_refresh_complete_days: bool = False, scan_only: bool = False,
) -> str:
    """Configure incremental refresh on a table.

    Args: dataset: SM name/UUID. table_name: Table to configure. workspace: Workspace. column_name: Date column. rolling_window_years: Years to keep. incremental_days: Days to refresh. scan_only: Preview.
    """
    fn = _import("sempy_labs.semantic_model._Add_IncrementalRefresh", "add_incremental_refresh")
    return _capture(fn, **_build_kwargs(dict(
        dataset=dataset, table_name=table_name, workspace=workspace, column_name=column_name,
        rolling_window_years=rolling_window_years, incremental_days=incremental_days,
        only_refresh_complete_days=only_refresh_complete_days, scan_only=scan_only)))


@mcp.tool()
def add_prep_for_ai(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Add AI preparation metadata (Q&A synonyms, descriptions, linguistic schema).

    Args: dataset: SM name/UUID. workspace: Workspace. scan_only: Preview.
    """
    fn = _import("sempy_labs.semantic_model._Add_PrepForAI", "add_prep_for_ai")
    return _capture(fn, **_build_kwargs(dict(dataset=dataset, workspace=workspace, scan_only=scan_only)))


@mcp.tool()
def add_last_refresh_table(dataset: str, workspace: str | None = None, scan_only: bool = False) -> str:
    """Add a '_LastRefresh' table showing when the model was last refreshed.

    Args: dataset: SM name/UUID. workspace: Workspace. scan_only: Preview.
    """
    return _sm_fix("sempy_labs.semantic_model._Add_Table_LastRefresh", "add_last_refresh_table", dataset, workspace, scan_only)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  MIGRATION  (5 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def migrate_calc_tables_to_lakehouse(
    dataset: str, new_dataset: str, workspace: str | None = None,
    new_dataset_workspace: str | None = None, lakehouse: str | None = None,
    lakehouse_workspace: str | None = None,
) -> str:
    """Migrate calculated tables from an import model to Lakehouse delta tables.

    Args: dataset: Source SM. new_dataset: Target SM. workspace/new_dataset_workspace: Workspaces. lakehouse/lakehouse_workspace: Target Lakehouse.
    """
    fn = _import("sempy_labs.migration._migrate_calctables_to_lakehouse", "migrate_calc_tables_to_lakehouse")
    return _capture(fn, **_build_kwargs(dict(
        dataset=dataset, new_dataset=new_dataset, workspace=workspace,
        new_dataset_workspace=new_dataset_workspace, lakehouse=lakehouse, lakehouse_workspace=lakehouse_workspace)))


@mcp.tool()
def migrate_calc_tables_to_semantic_model(
    dataset: str, new_dataset: str, workspace: str | None = None,
    new_dataset_workspace: str | None = None, lakehouse: str | None = None,
    lakehouse_workspace: str | None = None,
) -> str:
    """Migrate calculated tables to a new semantic model.

    Args: dataset: Source SM. new_dataset: Target SM. workspace/new_dataset_workspace: Workspaces. lakehouse/lakehouse_workspace: Lakehouse.
    """
    fn = _import("sempy_labs.migration._migrate_calctables_to_semantic_model", "migrate_calc_tables_to_semantic_model")
    return _capture(fn, **_build_kwargs(dict(
        dataset=dataset, new_dataset=new_dataset, workspace=workspace,
        new_dataset_workspace=new_dataset_workspace, lakehouse=lakehouse, lakehouse_workspace=lakehouse_workspace)))


@mcp.tool()
def migrate_model_objects_to_semantic_model(
    dataset: str, new_dataset: str, workspace: str | None = None, new_dataset_workspace: str | None = None,
) -> str:
    """Migrate model objects (measures, calc columns, hierarchies) to a new model.

    Args: dataset: Source SM. new_dataset: Target SM. workspace/new_dataset_workspace: Workspaces.
    """
    fn = _import("sempy_labs.migration._migrate_model_objects_to_semantic_model", "migrate_model_objects_to_semantic_model")
    return _capture(fn, **_build_kwargs(dict(
        dataset=dataset, new_dataset=new_dataset, workspace=workspace, new_dataset_workspace=new_dataset_workspace)))


@mcp.tool()
def migrate_tables_columns_to_semantic_model(
    dataset: str, new_dataset: str, workspace: str | None = None,
    new_dataset_workspace: str | None = None, lakehouse: str | None = None,
    lakehouse_workspace: str | None = None,
) -> str:
    """Migrate table and column definitions to a new semantic model.

    Args: dataset: Source SM. new_dataset: Target SM. workspace/new_dataset_workspace: Workspaces. lakehouse/lakehouse_workspace: Lakehouse.
    """
    fn = _import("sempy_labs.migration._migrate_tables_columns_to_semantic_model", "migrate_tables_columns_to_semantic_model")
    return _capture(fn, **_build_kwargs(dict(
        dataset=dataset, new_dataset=new_dataset, workspace=workspace,
        new_dataset_workspace=new_dataset_workspace, lakehouse=lakehouse, lakehouse_workspace=lakehouse_workspace)))


@mcp.tool()
def create_pqt_file(dataset: str, workspace: str | None = None, file_name: str = "PowerQueryTemplate") -> str:
    """Create a Power Query Template (.pqt) file from a semantic model.

    Args: dataset: SM name/UUID. workspace: Workspace. file_name: Output name (without extension).
    """
    fn = _import("sempy_labs.migration._create_pqt_file", "create_pqt_file")
    return _capture(fn, **_build_kwargs(dict(dataset=dataset, workspace=workspace, file_name=file_name)))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  ADMIN / UTILITY  (6 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def scan_workspaces(
    workspace: str | list[str] | None = None, data_source_details: bool = False,
    dataset_schema: bool = False, dataset_expressions: bool = False,
    lineage: bool = False, artifact_users: bool = False,
) -> str:
    """Scan workspaces using the Admin Scanner API. Returns metadata about all items.

    Args: workspace: Name(s)/UUID(s). data_source_details/dataset_schema/dataset_expressions/lineage/artifact_users: Toggle extra metadata.
    """
    fn = _import("sempy_labs.admin._scanner", "scan_workspaces")
    return _capture(fn, **_build_kwargs(dict(
        workspace=workspace, data_source_details=data_source_details, dataset_schema=dataset_schema,
        dataset_expressions=dataset_expressions, lineage=lineage, artifact_users=artifact_users)))


@mcp.tool()
def generate_shared_expression(
    item_name: str | None = None, item_type: str = "Lakehouse",
    workspace: str | None = None, use_sql_endpoint: bool = True,
) -> str:
    """Generate a shared expression for Direct Lake connection.

    Args: item_name: Lakehouse/Warehouse name. item_type: 'Lakehouse' or 'Warehouse'. workspace: Workspace. use_sql_endpoint: Use SQL endpoint.
    """
    fn = _import("sempy_labs.directlake._generate_shared_expression", "generate_shared_expression")
    return _capture(fn, **_build_kwargs(dict(
        item_name=item_name, item_type=item_type, workspace=workspace, use_sql_endpoint=use_sql_endpoint)))


@mcp.tool()
def get_semantic_model_size(dataset: str, workspace: str | None = None) -> str:
    """Get the memory size of a semantic model (Vertipaq analysis).

    Args: dataset: SM name/UUID. workspace: Workspace.
    """
    fn = _import("sempy_labs._generate_semantic_model", "get_semantic_model_size")
    return _capture(fn, **_build_kwargs(dict(dataset=dataset, workspace=workspace)))


@mcp.tool()
def get_semantic_model_bim(dataset: str, workspace: str | None = None, save_to_file_name: str | None = None) -> str:
    """Get the BIM (model.bim) definition of a semantic model as JSON.

    Args: dataset: SM name/UUID. workspace: Workspace. save_to_file_name: Optional file to save to.
    """
    fn = _import("sempy_labs._generate_semantic_model", "get_semantic_model_bim")
    return _capture(fn, **_build_kwargs(dict(dataset=dataset, workspace=workspace, save_to_file_name=save_to_file_name)))


@mcp.tool()
def deploy_semantic_model(
    source_dataset: str, source_workspace: str | None = None, target_dataset: str | None = None,
    target_workspace: str | None = None, refresh_target_dataset: bool = True, overwrite: bool = False,
) -> str:
    """Deploy (copy) a semantic model from one workspace to another.

    Args: source_dataset: Source SM. source_workspace/target_workspace: Workspaces. target_dataset: Target name. refresh_target_dataset: Refresh after deploy. overwrite: Overwrite existing.
    """
    fn = _import("sempy_labs._generate_semantic_model", "deploy_semantic_model")
    return _capture(fn, **_build_kwargs(dict(
        source_dataset=source_dataset, source_workspace=source_workspace,
        target_dataset=target_dataset, target_workspace=target_workspace,
        refresh_target_dataset=refresh_target_dataset, overwrite=overwrite)))


@mcp.tool()
def create_blank_semantic_model(
    dataset: str, compatibility_level: int = 1702,
    workspace: str | None = None, overwrite: bool = True,
) -> str:
    """Create a new blank semantic model in a workspace.

    Args: dataset: SM name. compatibility_level: TOM compat level. workspace: Workspace. overwrite: Overwrite existing.
    """
    fn = _import("sempy_labs._generate_semantic_model", "create_blank_semantic_model")
    return _capture(fn, **_build_kwargs(dict(
        dataset=dataset, compatibility_level=compatibility_level, workspace=workspace, overwrite=overwrite)))


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  COMPOUND TOOLS  (3 tools)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•


@mcp.tool()
def fix_all_report_issues(
    report: str, workspace: str | None = None, page_name: str | None = None,
) -> str:
    """Apply ALL available report fixes at once (excludes upgrade_to_pbir and slicer migration).

    Args: report: Report name/UUID. workspace: Workspace. page_name: Limit to a page.
    """
    fixers = [
        ("sempy_labs.report._Fix_PieChart", "fix_piecharts"),
        ("sempy_labs.report._Fix_Charts", "fix_barcharts"),
        ("sempy_labs.report._Fix_Charts", "fix_columncharts"),
        ("sempy_labs.report._Fix_Charts", "fix_linecharts"),
        ("sempy_labs.report._Fix_PageSize", "fix_page_size"),
        ("sempy_labs.report._Fix_HideVisualFilters", "fix_hide_visual_filters"),
        ("sempy_labs.report._Fix_DisableShowItemsNoData", "fix_disable_show_items_no_data"),
        ("sempy_labs.report._Fix_IBCSVariance", "fix_ibcs_variance"),
        ("sempy_labs.report._Fix_RemoveUnusedCustomVisuals", "fix_remove_unused_custom_visuals"),
        ("sempy_labs.report._Fix_MigrateReportLevelMeasures", "fix_migrate_report_level_measures"),
        ("sempy_labs.report._Fix_VisualAlignment", "fix_visual_alignment"),
    ]
    parts: list[str] = []
    kw = _build_kwargs(dict(report=report, workspace=workspace, page_name=page_name, scan_only=False))
    for mod, func_name in fixers:
        try:
            out = _capture(_import(mod, func_name), **kw)
            if out and out != "No output produced.":
                parts.append(f"â”€â”€ {func_name} â”€â”€\n{out}")
        except Exception as e:
            parts.append(f"â”€â”€ {func_name} â”€â”€ ERROR: {e}")
    return "\n\n".join(parts) if parts else "All fixes applied."


@mcp.tool()
def fix_all_semantic_model_issues(dataset: str, workspace: str | None = None) -> str:
    """Apply ALL semantic model fixes at once (all 21 individual fixers).

    Args: dataset: SM name/UUID. workspace: Workspace.
    """
    fixers = [
        ("sempy_labs.semantic_model._Fix_AvoidAdding0", "fix_avoid_adding_zero"),
        ("sempy_labs.semantic_model._Fix_CapitalizeObjectNames", "fix_capitalize_object_names"),
        ("sempy_labs.semantic_model._Fix_DataCategory", "fix_data_category"),
        ("sempy_labs.semantic_model._Fix_DateColumnFormat", "fix_date_column_format"),
        ("sempy_labs.semantic_model._Fix_DefaultDataSourceVersion", "fix_default_datasource_version"),
        ("sempy_labs.semantic_model._Fix_DiscourageImplicitMeasures", "fix_discourage_implicit_measures"),
        ("sempy_labs.semantic_model._Fix_DoNotSummarize", "fix_do_not_summarize"),
        ("sempy_labs.semantic_model._Fix_FlagColumnFormat", "fix_flag_column_format"),
        ("sempy_labs.semantic_model._Fix_FloatingPointDataType", "fix_floating_point_datatype"),
        ("sempy_labs.semantic_model._Fix_HideForeignKeys", "fix_hide_foreign_keys"),
        ("sempy_labs.semantic_model._Fix_IsAvailableInMdx", "fix_isavailable_in_mdx"),
        ("sempy_labs.semantic_model._Fix_IsAvailableInMdxTrue", "fix_isavailable_in_mdx_true"),
        ("sempy_labs.semantic_model._Fix_MarkPrimaryKeys", "fix_mark_primary_keys"),
        ("sempy_labs.semantic_model._Fix_MeasureDescriptions", "fix_measure_descriptions"),
        ("sempy_labs.semantic_model._Fix_MeasureFormat", "fix_measure_format"),
        ("sempy_labs.semantic_model._Fix_MonthColumnFormat", "fix_month_column_format"),
        ("sempy_labs.semantic_model._Fix_PercentageFormat", "fix_percentage_format"),
        ("sempy_labs.semantic_model._Fix_SortMonthColumn", "fix_sort_month_column"),
        ("sempy_labs.semantic_model._Fix_TrimObjectNames", "fix_trim_object_names"),
        ("sempy_labs.semantic_model._Fix_UseDivideFunction", "fix_use_divide_function"),
        ("sempy_labs.semantic_model._Fix_WholeNumberFormat", "fix_whole_number_format"),
    ]
    parts: list[str] = []
    for mod, func_name in fixers:
        try:
            out = _sm_fix(mod, func_name, dataset, workspace, False)
            if out and out != "No output produced.":
                parts.append(f"â”€â”€ {func_name} â”€â”€\n{out}")
        except Exception as e:
            parts.append(f"â”€â”€ {func_name} â”€â”€ ERROR: {e}")
    return "\n\n".join(parts) if parts else "All fixes applied."


@mcp.tool()
def scan_everything(
    report: str | None = None, dataset: str | None = None, workspace: str | None = None,
) -> str:
    """Scan both a report AND its semantic model for all fixable issues.

    Args: report: Report name/UUID (omit to skip). dataset: SM name/UUID (omit to skip). workspace: Workspace.
    """
    parts: list[str] = []
    if report:
        parts.append("â•â•â• REPORT SCAN â•â•â•")
        parts.append(scan_report(report=report, workspace=workspace))
    if dataset:
        parts.append("\nâ•â•â• SEMANTIC MODEL SCAN â•â•â•")
        parts.append(scan_semantic_model(dataset=dataset, workspace=workspace))
    if not report and not dataset:
        return "Please provide at least a report or dataset name."
    return "\n".join(parts)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  Entry point
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

if __name__ == "__main__":
    mcp.run()
