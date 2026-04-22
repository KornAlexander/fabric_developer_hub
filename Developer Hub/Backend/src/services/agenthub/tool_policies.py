"""Declared tool policies for every MCP tool the LLM may reference.

Run :func:`register_all` once at startup. Any tool not listed here is
**denied by default** at runtime — adding a new MCP tool without a policy
makes it unreachable, which is the safer failure mode.

Sensitivity classification rationale
------------------------------------
* ``READ_SAFE`` — listings, discovery, metadata. No row-level content.
  Auto-allowed.
* ``READ_SENSITIVE`` — reads that return arbitrary user content (file
  bytes, DAX query results, notebook source, semantic-model definitions).
  Auto-allowed but every call is audited and counts toward the
  per-session rate cap.
* ``WRITE`` — create / update / post / rebind / assign / commit / refresh /
  run. Not dispatched in v1 without a confirmation token (v1 does not
  issue tokens).
* ``DESTRUCTIVE`` — delete / drop / overwrite. Same as WRITE plus requires
  a second-factor confirmation (v2 UX; v1 denies).
"""
from __future__ import annotations

import logging

from services.agenthub.tool_runtime import (
    ToolPolicy,
    ToolSensitivity,
    register_tool,
)

logger = logging.getLogger(__name__)


_POLICIES: tuple[ToolPolicy, ...] = (
    # ── Fabric (first-party) ────────────────────────────────────────
    ToolPolicy("fabric_list_workspaces", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List workspaces the caller can see"),
    ToolPolicy("fabric_create_workspace", ToolSensitivity.WRITE,
               description="Create a new Fabric workspace. Dispatched via the "
                           "/api/workspaces endpoint which gates the call behind "
                           "the standard user-initiated UI flow; the LLM is not "
                           "permitted to call it autonomously."),
    ToolPolicy("fabric_list_items", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List items in a workspace"),
    ToolPolicy("fabric_list_folders", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List folders in a workspace"),
    ToolPolicy("fabric_list_files", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List files under a OneLake path"),
    ToolPolicy("fabric_read_file", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Read a OneLake file's bytes"),
    ToolPolicy("fabric_create_item", ToolSensitivity.WRITE,
               description="Create a Fabric item"),
    ToolPolicy("fabric_create_directory", ToolSensitivity.WRITE,
               description="Create a OneLake directory"),
    ToolPolicy("fabric_write_file", ToolSensitivity.WRITE,
               description="Write bytes into a OneLake file"),
    ToolPolicy("fabric_delete_file", ToolSensitivity.DESTRUCTIVE,
               description="Delete a OneLake file"),
    ToolPolicy("fabric_delete_item", ToolSensitivity.DESTRUCTIVE,
               description="Delete a Fabric item"),

    # ── semantic-link admin listings (READ_SAFE) ─────────────────────
    ToolPolicy("sl_admin_list_workspaces", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_admin_list_datasets", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_admin_list_dataset_users", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_admin_list_workspace_users", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_admin_get_activity_events", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Tenant activity feed — admin-only, per-row content"),

    # ── semantic-link listings ───────────────────────────────────────
    ToolPolicy("sl_list_workspace_users", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_capacities", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_connections", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_data_pipelines", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_gateways", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_item_schedules", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_lakehouses", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_mirrored_databases", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_reports", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_semantic_models", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_shortcuts", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_sql_endpoints", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_list_warehouses", ToolSensitivity.READ_SAFE, auto_allowed=True),

    # ── semantic-link reads that return content (READ_SENSITIVE) ────
    ToolPolicy("sl_evaluate_dax", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Execute read-only DAX; returns row data"),
    ToolPolicy("sl_get_lakehouse_tables", ToolSensitivity.READ_SENSITIVE, auto_allowed=True),
    ToolPolicy("sl_get_semantic_model_tables", ToolSensitivity.READ_SENSITIVE, auto_allowed=True),
    ToolPolicy("sl_get_semantic_model_definition", ToolSensitivity.READ_SENSITIVE, auto_allowed=True),
    ToolPolicy("sl_get_notebook_definition", ToolSensitivity.READ_SENSITIVE, auto_allowed=True),
    ToolPolicy("sl_get_report_definition", ToolSensitivity.READ_SENSITIVE, auto_allowed=True),
    ToolPolicy("sl_get_refresh_history", ToolSensitivity.READ_SENSITIVE, auto_allowed=True),
    ToolPolicy("sl_get_mirroring_status", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_get_git_status", ToolSensitivity.READ_SAFE, auto_allowed=True),
    ToolPolicy("sl_get_git_connection", ToolSensitivity.READ_SAFE, auto_allowed=True),

    # ── semantic-link WRITE (not dispatched in v1) ───────────────────
    ToolPolicy("sl_add_workspace_user", ToolSensitivity.WRITE),
    ToolPolicy("sl_assign_workspace_to_capacity", ToolSensitivity.WRITE),
    ToolPolicy("sl_commit_to_git", ToolSensitivity.WRITE),
    ToolPolicy("sl_update_from_git", ToolSensitivity.WRITE),
    ToolPolicy("sl_create_shortcut", ToolSensitivity.WRITE),
    ToolPolicy("sl_deploy_semantic_model", ToolSensitivity.WRITE),
    ToolPolicy("sl_clone_report", ToolSensitivity.WRITE),
    ToolPolicy("sl_rebind_report", ToolSensitivity.WRITE),
    ToolPolicy("sl_export_report", ToolSensitivity.WRITE),
    ToolPolicy("sl_set_endorsement", ToolSensitivity.WRITE),
    ToolPolicy("sl_run_data_pipeline", ToolSensitivity.WRITE),
    ToolPolicy("sl_run_item_job", ToolSensitivity.WRITE),
    ToolPolicy("sl_run_table_maintenance", ToolSensitivity.WRITE),
    ToolPolicy("sl_refresh_semantic_model", ToolSensitivity.WRITE),
    ToolPolicy("sl_cancel_refresh", ToolSensitivity.WRITE),

    # ── pbir-tools (CLI wrapper around the ``pbir`` binary) ──────────
    # Read-only discovery / listings — no PBIR content returned.
    ToolPolicy("pbir_help", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Show pbir CLI help"),
    ToolPolicy("pbir_ls", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List files under a PBIR project path"),
    ToolPolicy("pbir_tree", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Tree view of a PBIR project"),
    ToolPolicy("pbir_find", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Find entries in a PBIR project by name/pattern"),
    ToolPolicy("pbir_schema", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Show the PBIR JSON schema structure"),
    ToolPolicy("pbir_validate", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Validate PBIR project integrity"),
    ToolPolicy("pbir_visuals", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List visuals declared in a PBIR report"),
    ToolPolicy("pbir_pages", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List pages in a PBIR report"),
    ToolPolicy("pbir_fields", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List fields referenced by PBIR visuals"),
    ToolPolicy("pbir_filters", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List filters declared on a PBIR report"),
    ToolPolicy("pbir_bookmarks", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List bookmarks in a PBIR report"),
    ToolPolicy("pbir_annotations", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List annotations on PBIR objects"),

    # Reads that return PBIR source content or semantic-model data.
    ToolPolicy("pbir_cat", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Print a PBIR file's contents"),
    ToolPolicy("pbir_get", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Get a specific value from a PBIR project"),
    ToolPolicy("pbir_model", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Inspect the PBIR semantic model"),
    ToolPolicy("pbir_dax", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Execute read-only DAX against a PBIR/semantic model"),
    ToolPolicy("pbir_report", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Return the full PBIR report definition"),
    ToolPolicy("pbir_download", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Download a PBIR project's bytes"),

    # Writes — mutate PBIR files or remote state; denied in v1 (no
    # confirmation tokens yet).
    ToolPolicy("pbir_run", ToolSensitivity.WRITE,
               description="Generic pbir CLI passthrough; treated as WRITE "
                           "because the subcommand may mutate state"),
    ToolPolicy("pbir_new_report", ToolSensitivity.WRITE,
               description="Scaffold a new PBIR report"),
    ToolPolicy("pbir_add", ToolSensitivity.WRITE,
               description="Add an item to a PBIR project"),
    ToolPolicy("pbir_cp", ToolSensitivity.WRITE,
               description="Copy PBIR files"),
    ToolPolicy("pbir_mv", ToolSensitivity.WRITE,
               description="Move/rename PBIR files"),
    ToolPolicy("pbir_set", ToolSensitivity.WRITE,
               description="Update a value inside a PBIR project"),
    ToolPolicy("pbir_theme", ToolSensitivity.WRITE,
               description="Apply / export PBIR themes"),
    ToolPolicy("pbir_backup", ToolSensitivity.WRITE,
               description="Create a backup archive of a PBIR project"),
    ToolPolicy("pbir_restore", ToolSensitivity.WRITE,
               description="Restore a PBIR project from backup"),
    ToolPolicy("pbir_publish", ToolSensitivity.WRITE,
               description="Publish a PBIR report to a Fabric workspace"),
    ToolPolicy("pbir_batch", ToolSensitivity.WRITE,
               description="Run a batch of PBIR operations"),

    # Destructive — file deletion in a PBIR project.
    ToolPolicy("pbir_rm", ToolSensitivity.DESTRUCTIVE,
               description="Delete files from a PBIR project"),

    # ── pbi-fixer (Best-Practice-Analyzer remediations) ──────────────
    # Scans surface findings; they don't return row-level content.
    ToolPolicy("scan_report", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Scan a PBIR report for BPA issues"),
    ToolPolicy("scan_semantic_model", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Scan a semantic model for BPA issues"),
    ToolPolicy("scan_workspaces", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Scan workspaces for BPA issues"),
    ToolPolicy("scan_everything", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Run all available pbi-fixer scans"),
    ToolPolicy("get_semantic_model_size", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Return size metadata of a semantic model"),
    ToolPolicy("get_semantic_model_bim", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Return the semantic model BIM definition"),

    # All ``fix_*`` tools mutate either a PBIR project or a semantic
    # model. They support a ``scan_only`` flag on the tool surface, but
    # the default behaviour is to write, so we classify WRITE and
    # require a confirmation token (not issued in v1).
    ToolPolicy("fix_report_bpa", ToolSensitivity.WRITE),
    ToolPolicy("fix_piecharts", ToolSensitivity.WRITE),
    ToolPolicy("fix_barcharts", ToolSensitivity.WRITE),
    ToolPolicy("fix_columncharts", ToolSensitivity.WRITE),
    ToolPolicy("fix_linecharts", ToolSensitivity.WRITE),
    ToolPolicy("fix_column_to_bar", ToolSensitivity.WRITE),
    ToolPolicy("fix_bar_to_column", ToolSensitivity.WRITE),
    ToolPolicy("fix_column_to_line", ToolSensitivity.WRITE),
    ToolPolicy("fix_page_size", ToolSensitivity.WRITE),
    ToolPolicy("fix_hide_visual_filters", ToolSensitivity.WRITE),
    ToolPolicy("fix_disable_show_items_no_data", ToolSensitivity.WRITE),
    ToolPolicy("fix_ibcs_variance", ToolSensitivity.WRITE),
    ToolPolicy("fix_remove_unused_custom_visuals", ToolSensitivity.WRITE),
    ToolPolicy("fix_visual_alignment", ToolSensitivity.WRITE),
    ToolPolicy("fix_migrate_report_level_measures", ToolSensitivity.WRITE),
    ToolPolicy("fix_migrate_slicers", ToolSensitivity.WRITE),
    ToolPolicy("fix_upgrade_to_pbir", ToolSensitivity.WRITE),
    ToolPolicy("fix_model_bpa", ToolSensitivity.WRITE),
    ToolPolicy("fix_avoid_adding_zero", ToolSensitivity.WRITE),
    ToolPolicy("fix_capitalize_object_names", ToolSensitivity.WRITE),
    ToolPolicy("fix_data_category", ToolSensitivity.WRITE),
    ToolPolicy("fix_date_column_format", ToolSensitivity.WRITE),
    ToolPolicy("fix_default_datasource_version", ToolSensitivity.WRITE),
    ToolPolicy("fix_discourage_implicit_measures", ToolSensitivity.WRITE),
    ToolPolicy("fix_do_not_summarize", ToolSensitivity.WRITE),
    ToolPolicy("fix_flag_column_format", ToolSensitivity.WRITE),
    ToolPolicy("fix_floating_point_datatype", ToolSensitivity.WRITE),
    ToolPolicy("fix_hide_foreign_keys", ToolSensitivity.WRITE),
    ToolPolicy("fix_isavailable_in_mdx", ToolSensitivity.WRITE),
    ToolPolicy("fix_isavailable_in_mdx_true", ToolSensitivity.WRITE),
    ToolPolicy("fix_mark_primary_keys", ToolSensitivity.WRITE),
    ToolPolicy("fix_measure_descriptions", ToolSensitivity.WRITE),
    ToolPolicy("fix_measure_format", ToolSensitivity.WRITE),
    ToolPolicy("fix_month_column_format", ToolSensitivity.WRITE),
    ToolPolicy("fix_percentage_format", ToolSensitivity.WRITE),
    ToolPolicy("fix_sort_month_column", ToolSensitivity.WRITE),
    ToolPolicy("fix_trim_object_names", ToolSensitivity.WRITE),
    ToolPolicy("fix_use_divide_function", ToolSensitivity.WRITE),
    ToolPolicy("fix_whole_number_format", ToolSensitivity.WRITE),
    ToolPolicy("fix_all_report_issues", ToolSensitivity.WRITE,
               description="Apply every report-level BPA fix"),
    ToolPolicy("fix_all_semantic_model_issues", ToolSensitivity.WRITE,
               description="Apply every semantic-model BPA fix"),

    # ── pbi-fixer add_* / create_* / migrate_* / deploy_* / export_* ─
    # Extra sempy_labs wrappers exposed by pbi-fixer beyond the
    # ``fix_*`` surface. All mutate semantic models, reports, or
    # workspace state. Classified WRITE so they stay gated behind the
    # v1 confirmation-token flow (same as ``sl_*`` writes).
    ToolPolicy("add_cache_warming", ToolSensitivity.WRITE,
               description="Configure cache warming for a semantic model"),
    ToolPolicy("add_calc_group_time_intelligence", ToolSensitivity.WRITE,
               description="Add a time-intelligence calculation group"),
    ToolPolicy("add_calc_group_units", ToolSensitivity.WRITE,
               description="Add a units calculation group"),
    ToolPolicy("add_calculated_calendar", ToolSensitivity.WRITE,
               description="Add a calculated calendar table"),
    ToolPolicy("add_incremental_refresh", ToolSensitivity.WRITE,
               description="Configure incremental refresh on a table"),
    ToolPolicy("add_last_refresh_table", ToolSensitivity.WRITE,
               description="Add a Last Refresh metadata table"),
    ToolPolicy("add_measure_table", ToolSensitivity.WRITE,
               description="Add a dedicated measures table"),
    ToolPolicy("add_measures_from_columns", ToolSensitivity.WRITE,
               description="Create measures from existing columns"),
    ToolPolicy("add_prep_for_ai", ToolSensitivity.WRITE,
               description="Prepare a model for AI consumption"),
    ToolPolicy("add_py_measures", ToolSensitivity.WRITE,
               description="Add Python-generated measures to a model"),
    ToolPolicy("create_blank_semantic_model", ToolSensitivity.WRITE,
               description="Create an empty semantic model"),
    ToolPolicy("create_model_bpa_report", ToolSensitivity.WRITE,
               description="Create a BPA report artefact for a model"),
    ToolPolicy("create_pqt_file", ToolSensitivity.WRITE,
               description="Create a .pqt (Power Query template) file"),
    ToolPolicy("deploy_semantic_model", ToolSensitivity.WRITE,
               description="Deploy a semantic model to a Fabric workspace"),
    ToolPolicy("export_report", ToolSensitivity.WRITE,
               description="Export a report to disk (creates files)"),
    ToolPolicy("generate_shared_expression", ToolSensitivity.WRITE,
               description="Generate a shared-expression definition"),
    ToolPolicy("migrate_calc_tables_to_lakehouse", ToolSensitivity.WRITE,
               description="Migrate calculated tables into a Lakehouse"),
    ToolPolicy("migrate_calc_tables_to_semantic_model", ToolSensitivity.WRITE,
               description="Migrate calculated tables into a semantic model"),
    ToolPolicy("migrate_model_objects_to_semantic_model", ToolSensitivity.WRITE,
               description="Migrate model objects into a semantic model"),
    ToolPolicy("migrate_tables_columns_to_semantic_model", ToolSensitivity.WRITE,
               description="Migrate tables and columns into a semantic model"),
    ToolPolicy("upgrade_to_pbir_bulk", ToolSensitivity.WRITE,
               description="Bulk-upgrade reports to PBIR format"),

    # ── fabric-docs (Microsoft Fabric MCP — grounding-only) ──────────
    # The @microsoft/fabric-mcp server exposes a single umbrella
    # ``docs`` tool (with ``onelake`` / ``core`` filtered by the
    # allowlist since those duplicate our OBO-aware fabric_* tools).
    # The subcommand is selected via an input parameter on the tool.
    ToolPolicy("docs", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Microsoft Fabric documentation / OpenAPI / best-practices lookup"),
)


def register_all() -> None:
    """Register every declared policy with the runtime registry."""
    for p in _POLICIES:
        register_tool(p)
    logger.info(
        "[TOOL_RUNTIME] registered %d tool policies (read_safe=%d, read_sensitive=%d, write=%d, destructive=%d)",
        len(_POLICIES),
        sum(1 for p in _POLICIES if p.sensitivity is ToolSensitivity.READ_SAFE),
        sum(1 for p in _POLICIES if p.sensitivity is ToolSensitivity.READ_SENSITIVE),
        sum(1 for p in _POLICIES if p.sensitivity is ToolSensitivity.WRITE),
        sum(1 for p in _POLICIES if p.sensitivity is ToolSensitivity.DESTRUCTIVE),
    )


def warn_about_unregistered(discovered_tool_names: list[str]) -> list[str]:
    """Log a warning for every discovered MCP tool that has no policy.

    Returns the list of unknown names so callers can surface them in
    startup output / healthchecks. Dispatching an unregistered tool is
    still denied at runtime — this is an early-warning signal for
    operators adding new tools without a policy entry.
    """
    registered = {p.tool_name for p in _POLICIES}
    unknown = sorted(set(discovered_tool_names) - registered)
    for name in unknown:
        logger.warning(
            "[TOOL_RUNTIME] MCP discovered tool %r with no policy — runtime "
            "will DENY every dispatch. Add an entry to tool_policies.py.",
            name,
        )
    return unknown
