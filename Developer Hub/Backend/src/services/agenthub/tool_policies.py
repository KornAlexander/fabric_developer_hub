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
