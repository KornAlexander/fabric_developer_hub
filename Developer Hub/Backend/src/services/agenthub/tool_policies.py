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
       ToolPolicy("fabric_validate_workspace_capacity", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Verify the workspace capacity is Active before accepting Fabric report artifacts"),
       ToolPolicy("fabric_verify_report_renderable", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Start and poll a Power BI report render/export proof without downloading bytes"),
       ToolPolicy("fabric_verify_workspace_inventory_solution", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Verify an inventory solution folder against the original task, item set, semantic data, and report render proof"),
       ToolPolicy("fabric_get_semantic_model_refresh_history", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Read recent Power BI refresh history and decoded serviceExceptionJson for a semantic model"),
       ToolPolicy("fabric_diagnose_workspace_artifacts", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Collect read-only Fabric diagnostics: item metadata, owners, workspace roles, capacity, refresh history, and cross-artifact risks"),
       ToolPolicy("browser_verify_visual_render", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Capture a Fabric/Power BI page screenshot in a controlled browser and return visual evidence for verification"),
    ToolPolicy("fabric_list_files", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="List files under a OneLake path"),
    ToolPolicy("fabric_read_file", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Read a OneLake file's bytes"),
    ToolPolicy("fabric_create_item", ToolSensitivity.WRITE, auto_allowed=True,
               description="Create a Fabric item. Auto-allowed: the "
                           "orchestrator dispatches this directly when a "
                           "user prompt asks for an artifact to be created."),
    ToolPolicy("fabric_create_workspace_inventory_solution", ToolSensitivity.WRITE, auto_allowed=True,
           description="Create a run-folder workspace inventory solution with "
                 "Lakehouse, Notebook, SemanticModel, and Report artifacts"),
    ToolPolicy("fabric_create_folder", ToolSensitivity.WRITE, auto_allowed=True,
           description="Create a Fabric workspace folder. Auto-allowed "
                 "so orchestration runs can group produced "
                 "artifacts under a per-run folder."),
    ToolPolicy("fabric_create_directory", ToolSensitivity.WRITE, auto_allowed=True,
               description="Create a OneLake directory. Auto-allowed for "
                           "the same reason as fabric_create_item."),
    ToolPolicy("fabric_write_file", ToolSensitivity.WRITE, auto_allowed=True,
               description="Write bytes into a OneLake file. Auto-allowed "
                           "so notebook / data-engineering agents can "
                           "produce artefacts without a separate UX step."),
    ToolPolicy("fabric_delete_file", ToolSensitivity.DESTRUCTIVE,
               description="Delete a OneLake file"),
    ToolPolicy("fabric_delete_item", ToolSensitivity.DESTRUCTIVE,
               description="Delete a Fabric item"),

       # ── Fabric definition workspace (canonical checkout/edit/publish) ──
       ToolPolicy("fabric_definition_checkout", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Download Fabric item definitions into a local editable checkout"),
       ToolPolicy("fabric_definition_list_files", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="List files in a local Fabric definition checkout"),
       ToolPolicy("fabric_definition_diff", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Summarize local definition changes against the checkout baseline"),
       ToolPolicy("fabric_definition_validate", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Validate local Fabric definition files before publish"),
       ToolPolicy("fabric_definition_plan_publish", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Build a dry-run publish plan for a definition checkout"),
       ToolPolicy("fabric_definition_publish", ToolSensitivity.WRITE,
                        description="Publish validated Fabric definition checkout changes back to Fabric"),
       ToolPolicy("fabric_definition_discard_checkout", ToolSensitivity.WRITE,
                        description="Delete a local Fabric definition checkout"),

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
       ToolPolicy("sl_create_report_from_reportjson", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Create a Power BI report from a report.json definition"),
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

    # ── Azure Resource Manager (first-party OBO tools) ──────────────
    ToolPolicy("azure_list_subscriptions", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List Azure subscriptions visible to the authenticated user"),
    ToolPolicy("azure_list_resource_groups", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List Azure resource groups in a subscription"),
    ToolPolicy("azure_list_resources", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List Azure Resource Manager resources in a subscription or resource group"),
    ToolPolicy("azure_get_resource", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Get an Azure resource by full ARM resource ID"),
    ToolPolicy("azure_check_permissions", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List/check effective Azure RBAC permissions at an ARM scope"),
    ToolPolicy("azure_list_role_assignments", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List Azure RBAC role assignments at an ARM scope"),
    ToolPolicy("azure_list_role_definitions", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List Azure RBAC role definitions at an ARM scope"),
    ToolPolicy("azure_get_activity_log", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Read recent Azure Activity Log management events"),
    ToolPolicy("azure_get_resource_health", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Get Azure Resource Health availability status for a resource"),
    ToolPolicy("azure_list_diagnostic_settings", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List Azure Monitor diagnostic settings for a resource"),
    ToolPolicy("azure_list_metric_definitions", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List Azure Monitor metric definitions for a resource"),
    ToolPolicy("azure_query_metrics", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Query recent Azure Monitor metric summaries for a resource"),
    ToolPolicy("azure_network_inventory", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List Azure networking resources relevant to connectivity diagnostics"),
    ToolPolicy("azure_diagnose_resource", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Run a read-only Azure diagnostic bundle for a resource"),
    ToolPolicy("azure_list_fabric_capacities", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List Microsoft Fabric capacity Azure resources"),
    ToolPolicy("azure_get_fabric_capacity", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Get Microsoft Fabric capacity Azure resource state"),
    ToolPolicy("azure_resume_fabric_capacity", ToolSensitivity.WRITE, auto_allowed=True,
           description="Resume/start a Microsoft Fabric capacity Azure resource"),
    ToolPolicy("entra_token_diagnostics", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Summarize available OBO tokens and identity/audience claims without exposing token values"),
    ToolPolicy("entra_get_signed_in_user", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Get the Microsoft Graph profile for the delegated mission user"),
    ToolPolicy("entra_get_user", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Get an Entra user by object id or user principal name"),
    ToolPolicy("entra_get_service_principal", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Get an Entra service principal by object id, app id, or display name"),
    ToolPolicy("entra_diagnose_principal_access", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Diagnose whether an item owner/effective identity still exists, is enabled, has memberships/app roles, and matches Azure RBAC at a scope"),
    ToolPolicy("entra_list_group_memberships", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List direct or transitive Entra group/directory-role memberships"),
    ToolPolicy("entra_list_app_role_assignments", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="List Graph app-role assignments for a user, group, or service principal"),

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

       # ── Microsoft Learn / Docs MCP (public documentation grounding) ──
       ToolPolicy("microsoft_docs_search", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Search official Microsoft Learn documentation"),
       ToolPolicy("microsoft_docs_fetch", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Fetch complete official Microsoft Learn documentation pages"),
       ToolPolicy("microsoft_code_sample_search", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Search official Microsoft Learn code samples"),

       # ── Azure MCP Server (scoped read-only guidance tools) ───────────
       ToolPolicy("get_azure_bestpractices_get", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Get current Azure best-practice guidance for a resource and action"),
       ToolPolicy("get_azure_bestpractices_ai_app", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Get current Azure AI application and agent best-practice guidance"),

       # ── Fabric Remote Core MCP (cloud-hosted official Fabric MCP) ────
       # These names intentionally stay unprefixed because they are the names
       # exposed by the Microsoft-hosted MCP server. Qualified log output still
       # renders them as ``fabric-remote-core::<tool>`` via MCPClientManager.
       ToolPolicy("list_workspaces", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Remote Core MCP: list Fabric workspaces visible to the caller"),
       ToolPolicy("get_workspace", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Remote Core MCP: get workspace metadata"),
       ToolPolicy("list_items", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Remote Core MCP: list workspace items"),
       ToolPolicy("get_item", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Remote Core MCP: get item metadata"),
       ToolPolicy("list_folders", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Remote Core MCP: list workspace folders"),
       ToolPolicy("get_folder", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Remote Core MCP: get folder metadata"),
       ToolPolicy("list_capacities", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Remote Core MCP: list Fabric capacities visible to the caller"),
       ToolPolicy("get_operation_state", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Remote Core MCP: check a long-running operation state"),
       ToolPolicy("get_operation_result", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Remote Core MCP: get a completed operation result"),
       ToolPolicy("get_knowledge", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Remote Core MCP: get Fabric item-type guidance and best practices"),
       ToolPolicy("list_workspace_roles", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Remote Core MCP: list workspace role assignments"),
       ToolPolicy("get_workspace_role", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Remote Core MCP: get a workspace role assignment"),
       ToolPolicy("get_item_definition", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Remote Core MCP: get an item schema or definition"),
       ToolPolicy("create_workspace", ToolSensitivity.WRITE,
                        description="Remote Core MCP: create a Fabric workspace"),
       ToolPolicy("update_workspace", ToolSensitivity.WRITE,
                        description="Remote Core MCP: update workspace metadata"),
       ToolPolicy("add_workspace_role", ToolSensitivity.WRITE,
                        description="Remote Core MCP: grant workspace access"),
       ToolPolicy("update_workspace_role", ToolSensitivity.WRITE,
                        description="Remote Core MCP: change a workspace role"),
       ToolPolicy("create_item", ToolSensitivity.WRITE,
                        description="Remote Core MCP: create a Fabric item"),
       ToolPolicy("update_item", ToolSensitivity.WRITE,
                        description="Remote Core MCP: update item metadata"),
       ToolPolicy("update_item_definition", ToolSensitivity.WRITE,
                        description="Remote Core MCP: update an item definition"),
       ToolPolicy("bulk_move_items", ToolSensitivity.WRITE,
                        description="Remote Core MCP: move multiple items to a folder"),
       ToolPolicy("create_folder", ToolSensitivity.WRITE,
                        description="Remote Core MCP: create a folder"),
       ToolPolicy("update_folder", ToolSensitivity.WRITE,
                        description="Remote Core MCP: rename a folder"),
       ToolPolicy("move_folder", ToolSensitivity.WRITE,
                        description="Remote Core MCP: move a folder"),
       ToolPolicy("delete_workspace", ToolSensitivity.DESTRUCTIVE,
                        description="Remote Core MCP: delete a Fabric workspace"),
       ToolPolicy("delete_workspace_role", ToolSensitivity.DESTRUCTIVE,
                        description="Remote Core MCP: remove workspace access"),
       ToolPolicy("delete_item", ToolSensitivity.DESTRUCTIVE,
                        description="Remote Core MCP: delete a Fabric item"),
       ToolPolicy("delete_folder", ToolSensitivity.DESTRUCTIVE,
                        description="Remote Core MCP: delete an empty folder"),

    # ── generalist bootstrap helpers (official MCP reference servers) ─
    ToolPolicy("run_python_code", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Run small Python snippets in the AgentHub sandbox"),
    ToolPolicy("run_shell_command", ToolSensitivity.WRITE, auto_allowed=True,
               description="Run allowlisted development shell commands under a pinned workspace root"),
       ToolPolicy("sequentialthinking", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Structured planning/revision helper with no external side effects"),
    ToolPolicy("get_current_time", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Get the current time for an IANA timezone"),
    ToolPolicy("convert_time", ToolSensitivity.READ_SAFE, auto_allowed=True,
           description="Convert a time between IANA timezones"),
    ToolPolicy("web_fetch_url", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
               description="Fetch a public HTTPS web page with SSRF protections"),
    ToolPolicy("web_search", ToolSensitivity.READ_SAFE, auto_allowed=True,
               description="Search the public web and return result metadata"),

       # ── Azure management MCP ───────────────────────────────────────
       ToolPolicy("azure_list_subscriptions", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="List Azure subscriptions visible to the caller"),
       ToolPolicy("azure_list_resource_groups", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="List Azure resource groups in a subscription"),
       ToolPolicy("azure_list_resources", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="List Azure resources in a subscription or resource group"),
       ToolPolicy("azure_get_resource", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Read Azure resource metadata"),
       ToolPolicy("azure_check_permissions", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Check caller Azure permissions at a scope"),
       ToolPolicy("azure_list_role_assignments", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="List Azure RBAC assignments at a scope"),
       ToolPolicy("azure_list_role_definitions", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="List Azure RBAC role definitions at a scope"),
       ToolPolicy("azure_get_activity_log", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Read Azure Activity Log events"),
       ToolPolicy("azure_get_resource_health", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Read Azure Resource Health for a resource"),
       ToolPolicy("azure_list_fabric_capacities", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="List Fabric capacities in Azure"),
       ToolPolicy("azure_get_fabric_capacity", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Read Azure Fabric capacity metadata"),
       ToolPolicy("azure_resume_fabric_capacity", ToolSensitivity.WRITE,
                        description="Resume a suspended Azure Fabric capacity"),

       # ── Microsoft Power BI Modeling MCP ────────────────────────────
       ToolPolicy("database_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP database operations"),
       ToolPolicy("model_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP model operations"),
       ToolPolicy("table_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP table operations"),
       ToolPolicy("column_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP column operations"),
       ToolPolicy("measure_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP measure operations"),
       ToolPolicy("named_expression_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP named expression operations"),
       ToolPolicy("function_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP function operations"),
       ToolPolicy("object_translation_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP object translation operations"),
       ToolPolicy("relationship_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP relationship operations"),
       ToolPolicy("dax_query_operations", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Power BI Modeling MCP DAX query / validation operations"),
       ToolPolicy("partition_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP partition operations"),
       ToolPolicy("calculation_group_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP calculation group operations"),
       ToolPolicy("security_role_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP security role operations"),
       ToolPolicy("calendar_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP calendar operations"),
       ToolPolicy("query_group_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP query group operations"),
       ToolPolicy("user_hierarchy_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP user hierarchy operations"),
       ToolPolicy("culture_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP culture operations"),
       ToolPolicy("perspective_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP perspective operations"),
       ToolPolicy("connection_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP connection operations"),
       ToolPolicy("transaction_operations", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Power BI Modeling MCP transaction operations"),
       ToolPolicy("trace_operations", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Power BI Modeling MCP trace operations"),

       # ── Power BI Design MCP (tmdaidevs/powerbi-creator-skill) ──────
       ToolPolicy("analyze_report_structure", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Analyze a PBIR report structure"),
       ToolPolicy("get_report_assets", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="List report assets/resources"),
       ToolPolicy("validate_report_definition", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Validate a report definition"),
       ToolPolicy("preview_changes", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Preview proposed PBIR changes"),
       ToolPolicy("diff_report_definition", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Diff two report definitions"),
       ToolPolicy("score_modernization_readiness", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Score report modernization readiness"),
       ToolPolicy("extract_style_guide_from_report", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Extract a style guide from an existing report"),
       ToolPolicy("get_default_style_guide", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Read the default Power BI style guide"),
       ToolPolicy("get_audit_log", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Read Power BI Design MCP audit logs"),
       ToolPolicy("list_backups", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="List report definition backups"),
       ToolPolicy("get_semantic_model_schema", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Read the report-bound semantic model schema"),
       ToolPolicy("suggest_visuals", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Suggest visuals for a report/model"),
       ToolPolicy("auto_layout", ToolSensitivity.READ_SAFE, auto_allowed=True,
                        description="Compute a non-overlapping visual layout"),
       ToolPolicy("compare_reports", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Compare two Power BI reports"),
       ToolPolicy("export_report_summary", ToolSensitivity.READ_SENSITIVE, auto_allowed=True,
                        description="Export a report summary"),
       ToolPolicy("apply_style_guide", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Apply a style guide to a report"),
       ToolPolicy("patch_report_properties", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Patch report-level properties"),
       ToolPolicy("patch_page_properties", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Patch report page properties"),
       ToolPolicy("patch_visual_properties", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Patch visual properties"),
       ToolPolicy("replace_theme_resource", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Replace a report theme resource"),
       ToolPolicy("update_report_definition", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Update a Power BI report definition"),
       ToolPolicy("backup_report_definition", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Create a report definition backup"),
       ToolPolicy("bulk_apply_style_guide", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Apply a style guide across multiple reports"),
       ToolPolicy("add_visual_to_page", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Add a visual to a Power BI report page"),
       ToolPolicy("rearrange_page_visuals", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Rearrange visuals on a page"),
       ToolPolicy("add_image_visual", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Add an image visual to a page"),
       ToolPolicy("build_page", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Build a Power BI report page from visual definitions"),
       ToolPolicy("set_default_style_guide", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Set the default style guide"),
       ToolPolicy("restore_report_definition", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Restore a report definition from backup"),
       ToolPolicy("apply_full_style", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Apply full report styling"),
       ToolPolicy("add_page", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Add a page to a Power BI report"),
       ToolPolicy("reorder_pages", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Reorder Power BI report pages"),
       ToolPolicy("full_modernization", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Run full Power BI report modernization"),
       ToolPolicy("inject_custom_theme", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Inject a custom theme into a report"),
       ToolPolicy("apply_conditional_format", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Apply conditional formatting to a visual"),
       ToolPolicy("rename_visual", ToolSensitivity.WRITE, auto_allowed=True,
                        description="Rename a report visual"),
       ToolPolicy("remove_visual", ToolSensitivity.DESTRUCTIVE,
                        description="Remove a visual from a report page"),
       ToolPolicy("remove_page", ToolSensitivity.DESTRUCTIVE,
                        description="Remove a page from a report"),
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


def declared_policies() -> tuple[ToolPolicy, ...]:
       """Return the declared AgentHub tool policies in deterministic order."""
       return tuple(sorted(_POLICIES, key=lambda policy: policy.tool_name))


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


def require_all_registered(discovered_tool_names: list[str]) -> None:
       """Fail startup if any discovered MCP tool lacks a policy."""
       unknown = warn_about_unregistered(discovered_tool_names)
       if unknown:
              raise RuntimeError(
                     "Discovered MCP tool(s) without tool policy: "
                     f"{unknown}. Add entries to tool_policies.py before deploying."
              )
