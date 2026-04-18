"""pbir.tools MCP Server — wraps the `pbir` CLI for PBIR report manipulation.

Design goals:
  1. **Zero-maintenance generic tool** (`pbir_run`) that forwards any CLI
     command verbatim, so new pbir features work without MCP changes.
  2. **Curated high-level tools** for the most common operations with
     proper argument validation — easier for agents to discover and use.
  3. Machine-readable output via `--json` flag wherever supported.

Prerequisites:
  pip install pbir-cli          # installs the `pbir` binary
  pbir --version                # verify

Environment:
  PBIR_QUIET=1 is always set to suppress spinners/tips in subprocess output.
  PBIR_PATH can override the `pbir` binary location (default: found on PATH).
"""

import os
import shutil
import subprocess
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pbir-tools")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PBIR_BIN = os.environ.get("PBIR_PATH", "pbir")


def _find_pbir() -> str:
    """Resolve the pbir binary path."""
    custom = os.environ.get("PBIR_PATH")
    if custom and os.path.isfile(custom):
        return custom
    found = shutil.which("pbir")
    if found:
        return found
    # Check alongside the current Python interpreter (same venv)
    import sys
    venv_bin = os.path.join(os.path.dirname(sys.executable), "pbir.exe")
    if os.path.isfile(venv_bin):
        return venv_bin
    venv_bin_unix = os.path.join(os.path.dirname(sys.executable), "pbir")
    if os.path.isfile(venv_bin_unix):
        return venv_bin_unix
    return PBIR_BIN  # fall back, let subprocess raise if missing


def _run(args: list[str], cwd: str | None = None, timeout: int = 120) -> str:
    """Run a pbir CLI command and return combined stdout+stderr."""
    cmd = [_find_pbir()] + args
    env = {**os.environ, "PBIR_QUIET": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=cwd,
            timeout=timeout,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            err = result.stderr.strip()
            return f"[exit {result.returncode}] {err}\n{output}".strip()
        return output or "(no output)"
    except FileNotFoundError:
        return (
            "Error: pbir CLI not found. Install with: pip install pbir-cli"
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"


def _run_json(args: list[str], cwd: str | None = None) -> str:
    """Run with --json and return the output (already JSON-formatted)."""
    if "--json" not in args:
        args = args + ["--json"]
    return _run(args, cwd=cwd)


# ═══════════════════════════════════════════════════════════════════════
#  GENERIC — forward any pbir command (zero-maintenance)
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def pbir_run(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 120,
) -> str:
    """Run any pbir CLI command verbatim.

    This is the generic escape hatch — it forwards the full command string
    to the pbir CLI. Use this for commands not covered by dedicated tools,
    or when a new pbir version adds features not yet wrapped.

    Examples:
      command='ls "Sales.Report" -v'
      command='visuals title "Sales.Report/**/*.Visual" --fontSize 14 -f'
      command='batch run spec.json --root "Report.Report"'
      command='report convert "Report.Report" --format pbir'

    Args:
        command: The full pbir command string (everything after 'pbir').
        cwd: Working directory (where the PBIR folders live).
        timeout: Max seconds to wait (default 120).
    """
    # Split respecting quotes — use the shell's own parsing
    import shlex
    try:
        args = shlex.split(command)
    except ValueError:
        # Fallback: naive split
        args = command.split()
    return _run(args, cwd=cwd, timeout=timeout)


@mcp.tool()
def pbir_help(command: Optional[str] = None) -> str:
    """Get help for a pbir command or subcommand.

    Use this to discover available commands, flags, and usage patterns.
    Useful when the agent needs to learn what options are available.

    Args:
        command: Command or subcommand to get help for (e.g. 'visuals', 'add', 'set').
                 Omit for top-level help.
    """
    args = ["--help"]
    if command:
        import shlex
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()
        args = parts + ["--help"]
    return _run(args)


# ═══════════════════════════════════════════════════════════════════════
#  BROWSE — read-only inspection
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def pbir_ls(
    path: Optional[str] = None,
    verbose: bool = False,
    tree: bool = False,
    all_type: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """List reports, pages, or visuals in PBIR format.

    Args:
        path: Report/page path (e.g. 'Sales.Report', 'Sales.Report/Overview.Page').
              Omit to list all reports in cwd.
        verbose: Show fields used in visuals.
        tree: Display as tree (same as pbir tree).
        all_type: Flat listing across all reports: 'pages' or 'visuals'.
        cwd: Working directory.
    """
    args = ["ls"]
    if path:
        args.append(path)
    if verbose:
        args.append("-v")
    if tree:
        args.append("--tree")
    if all_type:
        args.extend(["--all", all_type])
    args.append("--json")
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_tree(
    path: str,
    verbose: bool = False,
    include: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Display report structure as a tree.

    Args:
        path: Report path (e.g. 'Sales.Report').
        verbose: Include fields used in visuals.
        include: What to include: 'filters', 'pages', 'visuals', 'fields' (comma-separated).
        cwd: Working directory.
    """
    args = ["tree", path]
    if verbose:
        args.append("-v")
    if include:
        args.extend(["--include", include])
    args.append("--json")
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_find(
    pattern: str,
    type_filter: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Find reports, pages, or visuals using glob patterns.

    Args:
        pattern: Glob pattern (e.g. '**/*.Visual', '**/card*.Visual', '*.Report').
        type_filter: Filter by type: 'report', 'page', or 'visual'.
        cwd: Working directory.
    """
    args = ["find", pattern]
    if type_filter:
        args.extend(["--type", type_filter])
    args.append("--json")
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_cat(
    path: str,
    cwd: Optional[str] = None,
) -> str:
    """Output raw JSON for a page, visual, theme, or annotations.

    Args:
        path: Object path (e.g. 'Report.Report/Page.Page/Visual.Visual',
              'Report.Report/theme', 'Report.Report/annotations').
        cwd: Working directory.
    """
    return _run(["cat", path], cwd=cwd)


@mcp.tool()
def pbir_get(
    path: str,
    cwd: Optional[str] = None,
) -> str:
    """Get properties using dot notation.

    Supports aliases: bg=background, border, title, legend, axis.x, axis.y, colors, labels.

    Args:
        path: Property path (e.g. 'Report.Report/Page.Page/Visual.Visual.title.show',
              'Visual.Visual.bg.color').
        cwd: Working directory.
    """
    return _run_json(["get", path], cwd=cwd)


@mcp.tool()
def pbir_model(
    path: Optional[str] = None,
    definition: bool = False,
    table: Optional[str] = None,
    query: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Explore the semantic model connected to a report.

    Args:
        path: Report path. Omit to list all reports and their models.
        definition: Get full model definition (tables, columns, measures).
        table: Filter definition to a specific table.
        query: Execute a DAX query (e.g. "EVALUATE VALUES('Date'[Year])").
        cwd: Working directory.
    """
    args = ["model"]
    if path:
        args.append(path)
    if definition:
        args.append("-d")
    if table:
        args.extend(["-t", table])
    if query:
        args.extend(["-q", query, "-F", "json"])
    args.append("--json")
    return _run(args, cwd=cwd)


# ═══════════════════════════════════════════════════════════════════════
#  CREATE — new objects
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def pbir_new_report(
    name: str,
    connection: Optional[str] = None,
    from_template: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Create a new empty PBIR report.

    Args:
        name: Report name (e.g. 'Sales.Report').
        connection: Semantic model connection (e.g. 'Workspace/Model.SemanticModel').
        from_template: Template name. Use pbir_help('new report --list-templates') to see options.
        cwd: Working directory.
    """
    args = ["new", "report", name]
    if connection:
        args.extend(["--connection", connection])
    if from_template:
        args.extend(["--from-template", from_template])
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_add(
    object_type: str,
    path: str,
    name: Optional[str] = None,
    title: Optional[str] = None,
    data_bindings: Optional[list[str]] = None,
    cwd: Optional[str] = None,
) -> str:
    """Add a page, visual, title, subtitle, filter, annotation, or image.

    Args:
        object_type: What to add: 'page', 'visual card', 'visual columnChart',
                     'visual barChart', 'visual table', 'title', 'subtitle',
                     'filter', 'annotation', 'image'.
        path: Target path (e.g. 'Sales.Report/Overview.Page').
        name: Name for the object (used with page, annotation).
        title: Title text (used with visual).
        data_bindings: Field bindings as list (e.g. ['Values:Sales.Revenue', 'Category:Products.Name']).
        cwd: Working directory.
    """
    import shlex
    args = ["add"] + shlex.split(object_type) + [path]
    if name:
        args.extend(["-n", name])
    if title:
        args.extend(["--title", title])
    if data_bindings:
        for binding in data_bindings:
            args.extend(["-d", binding])
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_cp(
    source: str,
    target: str,
    cwd: Optional[str] = None,
) -> str:
    """Copy a report, page, visual, theme, or measures.

    Args:
        source: Source path.
        target: Target path.
        cwd: Working directory.
    """
    return _run(["cp", source, target], cwd=cwd)


@mcp.tool()
def pbir_mv(
    source: str,
    target: str,
    cwd: Optional[str] = None,
) -> str:
    """Move or rename a page, visual, or move pages between reports.

    Args:
        source: Source path.
        target: Target path.
        cwd: Working directory.
    """
    return _run(["mv", source, target], cwd=cwd)


# ═══════════════════════════════════════════════════════════════════════
#  MODIFY — set, rm, visuals, pages
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def pbir_set(
    path: str,
    value: Optional[str] = None,
    json_value: Optional[str] = None,
    force: bool = False,
    cwd: Optional[str] = None,
) -> str:
    """Set a property on a report, page, or visual using dot notation.

    Use force=True for glob/bulk operations (e.g. '**/*.Visual.title.show').

    Args:
        path: Property path (e.g. 'Report.Report/Page.Page/Visual.Visual.title.text').
        value: Property value (string, number, or boolean).
        json_value: Set value using raw JSON string.
        force: Required for glob patterns (bulk operations).
        cwd: Working directory.
    """
    args = ["set", path]
    if json_value:
        args.extend(["--json", json_value])
    elif value is not None:
        args.extend(["--value", str(value)])
    if force:
        args.append("-f")
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_rm(
    path: str,
    measure: Optional[str] = None,
    remove_all_measures: bool = False,
    remove_theme: bool = False,
    remove_fields: bool = False,
    remove_annotations: bool = False,
    remove_all: bool = False,
    cwd: Optional[str] = None,
) -> str:
    """Remove pages, visuals, filters, bookmarks, measures, or other objects.

    Always runs with --force (destructive operation).

    Args:
        path: Target path (page, visual, filter, bookmark, or report for bulk ops).
        measure: Remove a single measure by name.
        remove_all_measures: Remove ALL extension measures.
        remove_theme: Remove custom theme.
        remove_fields: Clear all field bindings.
        remove_annotations: Remove all annotations.
        remove_all: Remove filters, bookmarks, and annotations.
        cwd: Working directory.
    """
    args = ["rm", path, "-f"]
    if measure:
        args.extend(["--measure", measure])
    if remove_all_measures:
        args.append("--measures")
    if remove_theme:
        args.append("--theme")
    if remove_fields:
        args.append("--fields")
    if remove_annotations:
        args.append("--annotations")
    if remove_all:
        args.append("--all")
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_visuals(
    subcommand: str,
    path: str,
    extra_args: Optional[str] = None,
    force: bool = False,
    cwd: Optional[str] = None,
) -> str:
    """Visual operations: layout, formatting, chart config, data binding, and more.

    Subcommands:
      Layout: position, resize, align, snap, z-order
      Formatting: title, subtitle, background, border, shadow, padding, spacing, header, divider
      Chart: legend, axis, labels, sort
      Data: bind (with -a/-r/-c for add/remove/clear)
      Conditional: cf
      Scripted: deneb, python, r
      Inspection: format, properties, hide, query, clear-formatting

    Args:
        subcommand: Visual subcommand (e.g. 'title', 'position', 'bind', 'format').
        path: Visual or page path.
        extra_args: Additional flags as a string (e.g. '--text "Revenue" --fontSize 14 --show --bold').
        force: Add -f flag for bulk operations.
        cwd: Working directory.
    """
    import shlex
    args = ["visuals", subcommand, path]
    if extra_args:
        try:
            args.extend(shlex.split(extra_args))
        except ValueError:
            args.extend(extra_args.split())
    if force:
        args.append("-f")
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_pages(
    subcommand: str,
    path: str,
    extra_args: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Page operations: rename, resize, type, background, wallpaper, move, hide, interactions.

    Subcommands: rename, resize, type, background, wallpaper, move, active-page,
                 display, hide, interactions

    Args:
        subcommand: Page subcommand (e.g. 'rename', 'resize', 'type', 'hide').
        path: Page path (e.g. 'Report.Report/Page.Page').
        extra_args: Additional flags as a string (e.g. '--width 1920 --height 1080').
        cwd: Working directory.
    """
    import shlex
    args = ["pages", subcommand, path]
    if extra_args:
        try:
            args.extend(shlex.split(extra_args))
        except ValueError:
            args.extend(extra_args.split())
    return _run(args, cwd=cwd)


# ═══════════════════════════════════════════════════════════════════════
#  DATA — fields, filters, dax, bookmarks, annotations
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def pbir_fields(
    path: str,
    extra_args: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Manage field bindings in visuals.

    Args:
        path: Visual or report path.
        extra_args: Additional flags.
        cwd: Working directory.
    """
    import shlex
    args = ["fields", path]
    if extra_args:
        try:
            args.extend(shlex.split(extra_args))
        except ValueError:
            args.extend(extra_args.split())
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_filters(
    path: str,
    extra_args: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Manage filters on reports, pages, or visuals.

    Args:
        path: Target path.
        extra_args: Additional flags.
        cwd: Working directory.
    """
    import shlex
    args = ["filters", path]
    if extra_args:
        try:
            args.extend(shlex.split(extra_args))
        except ValueError:
            args.extend(extra_args.split())
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_dax(
    path: str,
    extra_args: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Manage DAX extension measures and visual calculations.

    Args:
        path: Report or visual path.
        extra_args: Additional flags (e.g. '--add "MeasureName" "SUM(Sales[Amount])"').
        cwd: Working directory.
    """
    import shlex
    args = ["dax", path]
    if extra_args:
        try:
            args.extend(shlex.split(extra_args))
        except ValueError:
            args.extend(extra_args.split())
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_bookmarks(
    path: str,
    extra_args: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Manage bookmarks in a report.

    Args:
        path: Report path.
        extra_args: Additional flags.
        cwd: Working directory.
    """
    import shlex
    args = ["bookmarks", path]
    if extra_args:
        try:
            args.extend(shlex.split(extra_args))
        except ValueError:
            args.extend(extra_args.split())
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_annotations(
    path: str,
    extra_args: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Manage report annotations (key-value metadata).

    Args:
        path: Report path.
        extra_args: Additional flags.
        cwd: Working directory.
    """
    import shlex
    args = ["annotations", path]
    if extra_args:
        try:
            args.extend(shlex.split(extra_args))
        except ValueError:
            args.extend(extra_args.split())
    return _run(args, cwd=cwd)


# ═══════════════════════════════════════════════════════════════════════
#  THEME & SCHEMA
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def pbir_theme(
    subcommand: str,
    path: str,
    extra_args: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Theme operations: apply, export, inspect, and customize report themes.

    Args:
        subcommand: Theme subcommand.
        path: Report path.
        extra_args: Additional flags.
        cwd: Working directory.
    """
    import shlex
    args = ["theme", subcommand, path]
    if extra_args:
        try:
            args.extend(shlex.split(extra_args))
        except ValueError:
            args.extend(extra_args.split())
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_schema(
    subcommand: str,
    extra_args: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Discover valid property names, visual types, and configuration schemas.

    Args:
        subcommand: Schema subcommand.
        extra_args: Additional arguments.
        cwd: Working directory.
    """
    import shlex
    args = ["schema", subcommand]
    if extra_args:
        try:
            args.extend(shlex.split(extra_args))
        except ValueError:
            args.extend(extra_args.split())
    return _run(args, cwd=cwd)


# ═══════════════════════════════════════════════════════════════════════
#  OPERATIONS — validate, backup, restore, download, publish
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def pbir_validate(
    path: str,
    level: Optional[str] = None,
    strict: bool = False,
    cwd: Optional[str] = None,
) -> str:
    """Validate a PBIR report. Run after every mutation.

    Args:
        path: Report path (e.g. 'Sales.Report').
        level: Validation level: 'fields' (check field bindings),
               'qa' (layout/formatting quality), 'all' (everything).
        strict: Treat warnings as errors.
        cwd: Working directory.
    """
    args = ["validate", path]
    if level == "fields":
        args.append("--fields")
    elif level == "qa":
        args.append("--qa")
    elif level == "all":
        args.append("--all")
    if strict:
        args.append("--strict")
    args.append("--json")
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_backup(
    path: str,
    message: Optional[str] = None,
    list_backups: bool = False,
    cwd: Optional[str] = None,
) -> str:
    """Create or list backups of a PBIR report.

    Args:
        path: Report path.
        message: Backup message/description.
        list_backups: List existing backups instead of creating one.
        cwd: Working directory.
    """
    args = ["backup"]
    if list_backups:
        args.extend(["--list", path])
    else:
        args.append(path)
        if message:
            args.extend(["-m", message])
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_restore(
    path: str,
    backup_id: Optional[str] = None,
    force: bool = False,
    cwd: Optional[str] = None,
) -> str:
    """Restore a PBIR report from a backup.

    Args:
        path: Report path.
        backup_id: Specific backup ID (e.g. '20260312T143000Z'). Omit for latest.
        force: Skip confirmation.
        cwd: Working directory.
    """
    args = ["restore", path]
    if backup_id:
        args.extend(["--backup-id", backup_id])
    if force:
        args.append("-f")
    return _run(args, cwd=cwd)


@mcp.tool()
def pbir_download(
    path: str,
    output: Optional[str] = None,
    format: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Download a report from Fabric to a local PBIR folder.

    Requires Fabric CLI to be installed and authenticated.

    Args:
        path: Workspace/report path (e.g. 'My Workspace.Workspace/Sales.Report').
        output: Output directory.
        format: Output format: 'pbir' or 'pbip'.
        cwd: Working directory.
    """
    args = ["download", path]
    if output:
        args.extend(["-o", output])
    if format:
        args.extend(["--format", format])
    return _run(args, cwd=cwd, timeout=300)


@mcp.tool()
def pbir_publish(
    source: str,
    target: str,
    force: bool = False,
    cwd: Optional[str] = None,
) -> str:
    """Publish a local PBIR report to a Fabric workspace.

    Requires Fabric CLI to be installed and authenticated.

    Args:
        source: Local report path (e.g. 'Sales.Report').
        target: Workspace target (e.g. 'My Workspace.Workspace/Sales.Report').
        force: Overwrite existing report.
        cwd: Working directory.
    """
    args = ["publish", source, target]
    if force:
        args.extend(["-f", "-o"])
    return _run(args, cwd=cwd, timeout=300)


# ═══════════════════════════════════════════════════════════════════════
#  REPORT-WIDE — rename, rebind, convert, merge, split
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def pbir_report(
    subcommand: str,
    path: str,
    extra_args: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Report-wide operations: rename, rebind, convert, merge, split, clear-diagram.

    Subcommands: rename, rebind, convert, merge, merge-to-thick,
                 split-pages, split-from-thick, clear-diagram

    Args:
        subcommand: Report subcommand.
        path: Report path.
        extra_args: Additional flags (e.g. '--format pbir', '--name "New Name"').
        cwd: Working directory.
    """
    import shlex
    args = ["report", subcommand, path]
    if extra_args:
        try:
            args.extend(shlex.split(extra_args))
        except ValueError:
            args.extend(extra_args.split())
    return _run(args, cwd=cwd)


# ═══════════════════════════════════════════════════════════════════════
#  BATCH — declarative multi-step automation
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
def pbir_batch(
    action: str,
    spec_or_name: Optional[str] = None,
    root: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Run declarative batch automation specs.

    Args:
        action: Batch action: 'examples', 'example <name>', 'validate', 'plan', 'run'.
        spec_or_name: Spec file path or example name.
        root: Report root path for plan/run (e.g. 'Report.Report').
        cwd: Working directory.
    """
    import shlex
    args = ["batch"]
    try:
        args.extend(shlex.split(action))
    except ValueError:
        args.extend(action.split())
    if spec_or_name:
        args.append(spec_or_name)
    if root:
        args.extend(["--root", root])
    return _run(args, cwd=cwd)


# ═══════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run()
