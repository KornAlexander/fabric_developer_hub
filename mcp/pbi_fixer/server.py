"""
PBI Fixer MCP Server â€” Exposes PBI Fixer capabilities as MCP tools.

Wraps sempy_labs fixer functions so any MCP-compatible agent (AgentHub,
Copilot Studio, Claude, etc.) can scan and fix Power BI reports and
semantic models in Microsoft Fabric.

Install:  pip install mcp[cli] sempy-labs
Run:      python -m mcp_pbi_fixer.server
"""

from __future__ import annotations

import io
import sys
import json
import logging
from contextlib import redirect_stdout
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "pbi-fixer",
    version="0.1.0",
    description="Scan and fix Power BI reports & semantic models in Microsoft Fabric",
)

log = logging.getLogger("pbi-fixer")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture(fn, **kwargs) -> str:
    """Run a fixer function and capture its stdout as a string."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(**kwargs)
    output = buf.getvalue().strip()
    if result is not None:
        output += f"\n[return: {result}]"
    return output or "No output produced."


def _import_fixer(module_path: str, func_name: str):
    """Lazy-import a fixer function from sempy_labs."""
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, func_name)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Report Tools
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@mcp.tool()
def scan_report(
    report: str,
    workspace: str | None = None,
    page_name: str | None = None,
) -> str:
    """Scan a Power BI report for all fixable issues.

    Runs every report fixer in scan-only mode and returns a combined
    list of detected problems (pie charts, non-FHD pages, visible
    filters, bar/column chart formatting, slicer migration candidates).

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID. If omitted, uses the default workspace.
        page_name: Optional page name to limit the scan to a single page.

    Returns:
        A text summary of all issues found.
    """
    fixers = [
        ("sempy_labs.report._Fix_PieChart", "fix_piecharts"),
        ("sempy_labs.report._Fix_Charts", "fix_barcharts"),
        ("sempy_labs.report._Fix_Charts", "fix_columncharts"),
        ("sempy_labs.report._Fix_PageSize", "fix_page_size"),
        ("sempy_labs.report._Fix_HideVisualFilters", "fix_hide_visual_filters"),
    ]
    parts: list[str] = []
    for mod, func_name in fixers:
        fn = _import_fixer(mod, func_name)
        kwargs: dict[str, Any] = dict(report=report, scan_only=True)
        if workspace:
            kwargs["workspace"] = workspace
        if page_name:
            kwargs["page_name"] = page_name
        out = _capture(fn, **kwargs)
        if out and out != "No output produced.":
            parts.append(f"â”€â”€ {func_name} â”€â”€\n{out}")
    return "\n\n".join(parts) if parts else "No issues found."


@mcp.tool()
def fix_piecharts(
    report: str,
    workspace: str | None = None,
    page_name: str | None = None,
    target_visual_type: str = "clusteredBarChart",
    scan_only: bool = False,
) -> str:
    """Replace pie chart visuals with bar charts (or another type).

    Requires PBIR format. Use scan_only=True to preview changes.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        target_visual_type: Replacement visual type (default: clusteredBarChart).
        scan_only: If True, only report findings without applying changes.
    """
    fn = _import_fixer("sempy_labs.report._Fix_PieChart", "fix_piecharts")
    kwargs: dict[str, Any] = dict(
        report=report, target_visual_type=target_visual_type, scan_only=scan_only
    )
    if workspace:
        kwargs["workspace"] = workspace
    if page_name:
        kwargs["page_name"] = page_name
    return _capture(fn, **kwargs)


@mcp.tool()
def fix_barcharts(
    report: str,
    workspace: str | None = None,
    page_name: str | None = None,
    scan_only: bool = False,
) -> str:
    """Fix bar chart formatting to best practices.

    Standardizes data labels, gridlines, axis formatting, and IBCS
    type swaps. Requires PBIR format.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: If True, only report findings without applying changes.
    """
    fn = _import_fixer("sempy_labs.report._Fix_Charts", "fix_barcharts")
    kwargs: dict[str, Any] = dict(report=report, scan_only=scan_only)
    if workspace:
        kwargs["workspace"] = workspace
    if page_name:
        kwargs["page_name"] = page_name
    return _capture(fn, **kwargs)


@mcp.tool()
def fix_columncharts(
    report: str,
    workspace: str | None = None,
    page_name: str | None = None,
    scan_only: bool = False,
) -> str:
    """Fix column chart formatting to best practices.

    Standardizes data labels, gridlines, axis formatting, and IBCS
    type swaps. Requires PBIR format.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: If True, only report findings without applying changes.
    """
    fn = _import_fixer("sempy_labs.report._Fix_Charts", "fix_columncharts")
    kwargs: dict[str, Any] = dict(report=report, scan_only=scan_only)
    if workspace:
        kwargs["workspace"] = workspace
    if page_name:
        kwargs["page_name"] = page_name
    return _capture(fn, **kwargs)


@mcp.tool()
def fix_page_size(
    report: str,
    workspace: str | None = None,
    page_name: str | None = None,
    scan_only: bool = False,
) -> str:
    """Standardize report pages to Full HD (1920x1080).

    Only changes pages that use the default 1280x720 size.
    Requires PBIR format.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: If True, only report findings without applying changes.
    """
    fn = _import_fixer("sempy_labs.report._Fix_PageSize", "fix_page_size")
    kwargs: dict[str, Any] = dict(report=report, scan_only=scan_only)
    if workspace:
        kwargs["workspace"] = workspace
    if page_name:
        kwargs["page_name"] = page_name
    return _capture(fn, **kwargs)


@mcp.tool()
def fix_hide_visual_filters(
    report: str,
    workspace: str | None = None,
    page_name: str | None = None,
    scan_only: bool = False,
) -> str:
    """Hide all visual-level filter panes in the report.

    Sets isHiddenInViewMode=True on every visual-level filter.
    Auto-creates filterConfig from query projections if missing.
    Requires PBIR format.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
        scan_only: If True, only report findings without applying changes.
    """
    fn = _import_fixer(
        "sempy_labs.report._Fix_HideVisualFilters", "fix_hide_visual_filters"
    )
    kwargs: dict[str, Any] = dict(report=report, scan_only=scan_only)
    if workspace:
        kwargs["workspace"] = workspace
    if page_name:
        kwargs["page_name"] = page_name
    return _capture(fn, **kwargs)


@mcp.tool()
def fix_upgrade_to_pbir(
    report: str,
    workspace: str | None = None,
    scan_only: bool = False,
) -> str:
    """Upgrade a report from PBIRLegacy to PBIR format.

    Uses REST API round-trip (getDefinition â†’ updateDefinition).
    This is a prerequisite for all other report fixers.

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        scan_only: If True, only check upgrade eligibility without converting.
    """
    fn = _import_fixer(
        "sempy_labs.report._Fix_UpgradeToPbir", "fix_upgrade_to_pbir"
    )
    kwargs: dict[str, Any] = dict(report=report, scan_only=scan_only)
    if workspace:
        kwargs["workspace"] = workspace
    return _capture(fn, **kwargs)


@mcp.tool()
def fix_migrate_slicers(
    report: str,
    page_name: str,
    workspace: str | None = None,
    scan_only: bool = False,
) -> str:
    """Migrate native slicers to SlicerBar custom visual.

    Extracts slicer fields, adds them to the existing SlicerBar visual,
    and removes the original slicer visuals. Requires PBIR format.

    Args:
        report: Report name or UUID.
        page_name: Page name (required â€” slicerbar migration is per-page).
        workspace: Workspace name or UUID.
        scan_only: If True, only report findings without applying changes.
    """
    fn = _import_fixer(
        "sempy_labs.report._Fix_MigrateSlicerToSlicerbar",
        "fix_migrate_slicer_to_slicerbar",
    )
    kwargs: dict[str, Any] = dict(
        report=report, page_name=page_name, scan_only=scan_only
    )
    if workspace:
        kwargs["workspace"] = workspace
    return _capture(fn, **kwargs)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Semantic Model Tools
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@mcp.tool()
def scan_semantic_model(
    dataset: str,
    workspace: str | None = None,
) -> str:
    """Scan a semantic model for fixable issues.

    Checks for columns with SummarizeBy that lack explicit measures,
    and measures that could benefit from PY time intelligence variants.

    Args:
        dataset: Semantic model name or UUID.
        workspace: Workspace name or UUID.
    """
    parts: list[str] = []
    # Check measures from columns
    fn1 = _import_fixer(
        "sempy_labs.semantic_model._Add_MeasuresFromColumns",
        "add_measures_from_columns",
    )
    kwargs1: dict[str, Any] = dict(dataset=dataset, scan_only=True)
    if workspace:
        kwargs1["workspace"] = workspace
    out1 = _capture(fn1, **kwargs1)
    if out1 and out1 != "No output produced.":
        parts.append(f"â”€â”€ Measures from SummarizeBy columns â”€â”€\n{out1}")

    # Check PY measures
    fn2 = _import_fixer(
        "sempy_labs.semantic_model._Add_PYMeasures", "add_py_measures"
    )
    kwargs2: dict[str, Any] = dict(dataset=dataset, scan_only=True)
    if workspace:
        kwargs2["workspace"] = workspace
    out2 = _capture(fn2, **kwargs2)
    if out2 and out2 != "No output produced.":
        parts.append(f"â”€â”€ PY time intelligence candidates â”€â”€\n{out2}")

    return "\n\n".join(parts) if parts else "No issues found."


@mcp.tool()
def add_measures_from_columns(
    dataset: str,
    workspace: str | None = None,
    target_table: str | None = None,
    scan_only: bool = False,
) -> str:
    """Create explicit measures from columns that have SummarizeBy set.

    For each column with SummarizeBy (SUM, COUNT, MIN, MAX, etc.),
    creates a DAX measure and hides the source column. Uses XMLA.

    Args:
        dataset: Semantic model name or UUID.
        workspace: Workspace name or UUID.
        target_table: Table to place new measures in. If omitted, uses source table.
        scan_only: If True, only list candidates without creating measures.
    """
    fn = _import_fixer(
        "sempy_labs.semantic_model._Add_MeasuresFromColumns",
        "add_measures_from_columns",
    )
    kwargs: dict[str, Any] = dict(dataset=dataset, scan_only=scan_only)
    if workspace:
        kwargs["workspace"] = workspace
    if target_table:
        kwargs["target_table"] = target_table
    return _capture(fn, **kwargs)


@mcp.tool()
def add_py_measures(
    dataset: str,
    workspace: str | None = None,
    measures: list[str] | None = None,
    calendar_table: str | None = None,
    date_column: str | None = None,
    target_table: str | None = None,
    scan_only: bool = False,
) -> str:
    """Add PY (Prior Year) time intelligence measures.

    Creates 5 measures per source measure: PY, Delta PY, Delta PY %,
    Max Green PY, Max Red AC. Uses SAMEPERIODLASTYEAR via XMLA.

    Args:
        dataset: Semantic model name or UUID.
        workspace: Workspace name or UUID.
        measures: Specific measure names to process. If omitted, processes all.
        calendar_table: Name of the date/calendar table.
        date_column: Name of the date column in the calendar table.
        target_table: Table to place new measures in.
        scan_only: If True, only list candidates without creating measures.
    """
    fn = _import_fixer(
        "sempy_labs.semantic_model._Add_PYMeasures", "add_py_measures"
    )
    kwargs: dict[str, Any] = dict(dataset=dataset, scan_only=scan_only)
    if workspace:
        kwargs["workspace"] = workspace
    if measures:
        kwargs["measures"] = measures
    if calendar_table:
        kwargs["calendar_table"] = calendar_table
    if date_column:
        kwargs["date_column"] = date_column
    if target_table:
        kwargs["target_table"] = target_table
    return _capture(fn, **kwargs)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Compound Tools (convenience)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@mcp.tool()
def fix_all_report_issues(
    report: str,
    workspace: str | None = None,
    page_name: str | None = None,
) -> str:
    """Apply all available report fixes at once.

    Runs: fix_piecharts, fix_barcharts, fix_columncharts, fix_page_size,
    fix_hide_visual_filters. Does NOT run upgrade_to_pbir or slicer
    migration (those require explicit invocation).

    Args:
        report: Report name or UUID.
        workspace: Workspace name or UUID.
        page_name: Limit to a specific page.
    """
    fixers = [
        ("sempy_labs.report._Fix_PieChart", "fix_piecharts"),
        ("sempy_labs.report._Fix_Charts", "fix_barcharts"),
        ("sempy_labs.report._Fix_Charts", "fix_columncharts"),
        ("sempy_labs.report._Fix_PageSize", "fix_page_size"),
        ("sempy_labs.report._Fix_HideVisualFilters", "fix_hide_visual_filters"),
    ]
    parts: list[str] = []
    for mod, func_name in fixers:
        fn = _import_fixer(mod, func_name)
        kwargs: dict[str, Any] = dict(report=report, scan_only=False)
        if workspace:
            kwargs["workspace"] = workspace
        if page_name:
            kwargs["page_name"] = page_name
        out = _capture(fn, **kwargs)
        if out and out != "No output produced.":
            parts.append(f"â”€â”€ {func_name} â”€â”€\n{out}")
    return "\n\n".join(parts) if parts else "All fixes applied (no output)."


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Entry point
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

if __name__ == "__main__":
    mcp.run()
