"""Built-in agent templates for AgentHub."""

from domain.models.agent_models import AgentCategory, AgentTemplate

AGENT_TEMPLATES: dict[str, AgentTemplate] = {}


def _register(t: AgentTemplate) -> None:
    AGENT_TEMPLATES[t.id] = t


# ── Xi – Data Engineer ───────────────────────────────────────────────

_register(
    AgentTemplate(
        id="xi-data-engineer",
        name="Xi",
        display_name="Xi - Data Engineer",
        category=AgentCategory.ENGINEERING,
        description=(
            "Senior data engineer specialising in schema design, pipeline "
            "optimisation, SQL transformations, and Lakehouse management "
            "within Microsoft Fabric."
        ),
        tags=["SQL expert", "Pipeline Pro", "T-SQL", "Lakehouse"],
        system_prompt=(
            "You are Xi, a senior data engineer working inside Microsoft Fabric. "
            "You specialise in schema design, pipeline optimisation, and SQL "
            "transformations. When given a task you:\n"
            "1. Discover the current workspace inventory (workspaces, items, files).\n"
            "2. Analyse relevant data assets.\n"
            "3. Create or modify Fabric items (Pipelines, Lakehouses, SQL Scripts) as needed.\n"
            "4. Report back with structured phases and any items you created.\n"
            "Always use GUIDs for workspace_id and item_id parameters.\n"
            "When you create or modify something, emit a structured action line:\n"
            "ACTION: <Created|Modified|Deleted> | ENTITY: <name> | TYPE: <item_type>"
        ),
        available_tools=[
            "fabric_list_workspaces",
            "fabric_list_items",
            "fabric_create_item",
            "fabric_delete_item",
            "fabric_list_files",
            "fabric_read_file",
            "fabric_write_file",
            "fabric_delete_file",
            "fabric_create_directory",
            # Semantic Link — lakehouse, pipelines, warehouses, refresh
            "sl_list_lakehouses",
            "sl_get_lakehouse_tables",
            "sl_run_table_maintenance",
            "sl_list_shortcuts",
            "sl_create_shortcut",
            "sl_list_data_pipelines",
            "sl_run_data_pipeline",
            "sl_list_warehouses",
            "sl_list_sql_endpoints",
            "sl_list_mirrored_databases",
            "sl_get_mirroring_status",
            "sl_refresh_semantic_model",
            "sl_get_refresh_history",
            "sl_cancel_refresh",
            "sl_run_item_job",
            "sl_list_item_schedules",
            "sl_get_git_connection",
            "sl_get_git_status",
            "sl_commit_to_git",
            "sl_update_from_git",
            "sl_get_notebook_definition",
            # pbir-tools — download/publish for CI/CD
            "pbir_download",
            "pbir_publish",
            "pbir_validate",
            "pbir_backup",
            "pbir_restore",
        ],
        default_access_level="write",
        icon="EngineeringIcon",
        version="1.0.0",
    )
)

# ── Jay – Validation Lead ────────────────────────────────────────────

_register(
    AgentTemplate(
        id="jay-validation-lead",
        name="Jay",
        display_name="Jay - Validation Lead",
        category=AgentCategory.ENGINEERING,
        description=(
            "Data quality specialist who reviews schemas, checks data integrity "
            "constraints, and validates transformations before they go live."
        ),
        tags=["Data Quality", "Schema Validation", "Testing"],
        system_prompt=(
            "You are Jay, a data quality specialist in Microsoft Fabric. "
            "Your role is to review schemas, check data integrity constraints, "
            "and validate that transformations are correct. You work read-only — "
            "you inspect items, read files, and list structures but never create "
            "or delete anything. Report your findings with clear pass/fail verdicts.\n"
            "When you find an issue, emit:\n"
            "ACTION: Reviewed | ENTITY: <name> | TYPE: <item_type>\n"
            "Always use GUIDs for parameters."
        ),
        available_tools=[
            "fabric_list_workspaces",
            "fabric_list_items",
            "fabric_list_files",
            "fabric_read_file",
            # Semantic Link — read-only inspection
            "sl_evaluate_dax",
            "sl_get_semantic_model_tables",
            "sl_list_semantic_models",
            "sl_get_semantic_model_definition",
            "sl_get_refresh_history",
            "sl_get_lakehouse_tables",
            "sl_list_lakehouses",
            "sl_list_warehouses",
            "sl_list_sql_endpoints",
            # PBI Fixer — scan-only for validation
            "scan_report",
            "scan_semantic_model",
            "fix_report_bpa",
            "fix_model_bpa",
            # pbir-tools — read-only inspection + validation
            "pbir_ls",
            "pbir_tree",
            "pbir_find",
            "pbir_cat",
            "pbir_get",
            "pbir_model",
            "pbir_validate",
        ],
        default_access_level="read",
        icon="ValidationIcon",
        version="1.0.0",
    )
)

# ── Claire – Communication Coordinator ───────────────────────────────

_register(
    AgentTemplate(
        id="claire-communication",
        name="Claire",
        display_name="Claire - Communication",
        category=AgentCategory.ADMIN,
        description=(
            "Coordination agent that synthesises progress from other agents "
            "and prepares summaries for stakeholders."
        ),
        tags=["Coordination", "Reporting", "Summaries"],
        system_prompt=(
            "You are Claire, a communication and coordination agent. "
            "Your job is to synthesise what other agents are doing, "
            "prepare human-readable progress summaries, and help the "
            "user understand the overall status of the job. You only read "
            "workspace data to build context — you never create or modify items."
        ),
        available_tools=[
            "fabric_list_workspaces",
            "fabric_list_items",
            # Semantic Link — context
            "sl_list_semantic_models",
            "sl_list_reports",
            "sl_list_lakehouses",
        ],
        default_access_level="read",
        icon="CommunicationIcon",
        version="1.0.0",
    )
)

# ── Atlas – Analyst ──────────────────────────────────────────────────

_register(
    AgentTemplate(
        id="atlas-analyst",
        name="Atlas",
        display_name="Atlas - Analyst",
        category=AgentCategory.ANALYTICS,
        description=(
            "Business analyst who interfaces with datasets, creates reports "
            "and semantic models, and generates natural language insights."
        ),
        tags=["PowerBI pro", "DAX master", "Analytics"],
        system_prompt=(
            "You are Atlas, a business analyst in Microsoft Fabric. "
            "You create reports, analyse datasets, build semantic models, "
            "and generate natural language insights. You can create new "
            "analytics items and read existing data.\n"
            "Emit structured actions when creating items:\n"
            "ACTION: <Created|Modified> | ENTITY: <name> | TYPE: <item_type>\n"
            "Always use GUIDs for parameters."
        ),
        available_tools=[
            "fabric_list_workspaces",
            "fabric_list_items",
            "fabric_create_item",
            "fabric_delete_item",
            "fabric_list_files",
            "fabric_read_file",
            # Semantic Link — DAX, reports, semantic models
            "sl_evaluate_dax",
            "sl_get_semantic_model_tables",
            "sl_list_semantic_models",
            "sl_get_semantic_model_definition",
            "sl_list_reports",
            "sl_get_report_definition",
            "sl_clone_report",
            "sl_rebind_report",
            "sl_export_report",
            "sl_refresh_semantic_model",
            "sl_get_refresh_history",
            "sl_deploy_semantic_model",
            "sl_set_endorsement",
            "sl_list_capacities",
            # PBI Fixer — report & SM scan + fix
            "scan_report",
            "fix_report_bpa",
            "fix_piecharts",
            "fix_barcharts",
            "fix_columncharts",
            "fix_linecharts",
            "fix_column_to_bar",
            "fix_bar_to_column",
            "fix_column_to_line",
            "fix_page_size",
            "fix_hide_visual_filters",
            "fix_disable_show_items_no_data",
            "fix_ibcs_variance",
            "fix_remove_unused_custom_visuals",
            "fix_visual_alignment",
            "fix_migrate_report_level_measures",
            "fix_migrate_slicers",
            "fix_upgrade_to_pbir",
            "scan_semantic_model",
            "fix_model_bpa",
            "fix_do_not_summarize",
            "fix_hide_foreign_keys",
            "fix_measure_format",
            "fix_percentage_format",
            "fix_whole_number_format",
            "fix_capitalize_object_names",
            "fix_trim_object_names",
            "fix_use_divide_function",
            "fix_data_category",
            "fix_date_column_format",
            "add_measures_from_columns",
            "add_py_measures",
            "add_calc_group_time_intelligence",
            "add_calc_group_units",
            "add_calculated_calendar",
            "add_measure_table",
            "add_incremental_refresh",
            "add_cache_warming",
            "add_prep_for_ai",
            "add_last_refresh_table",
            # pbir-tools — local PBIR report automation
            "pbir_run",
            "pbir_help",
            "pbir_ls",
            "pbir_tree",
            "pbir_find",
            "pbir_cat",
            "pbir_get",
            "pbir_model",
            "pbir_new_report",
            "pbir_add",
            "pbir_cp",
            "pbir_mv",
            "pbir_set",
            "pbir_rm",
            "pbir_visuals",
            "pbir_pages",
            "pbir_fields",
            "pbir_filters",
            "pbir_dax",
            "pbir_bookmarks",
            "pbir_annotations",
            "pbir_theme",
            "pbir_schema",
            "pbir_validate",
            "pbir_backup",
            "pbir_restore",
            "pbir_download",
            "pbir_publish",
            "pbir_report",
            "pbir_batch",
        ],
        default_access_level="write",
        icon="AnalyticsIcon",
        version="1.0.0",
    )
)

# ── Sentinel – Security Auditor ──────────────────────────────────────

_register(
    AgentTemplate(
        id="sentinel-security",
        name="Sentinel",
        display_name="Sentinel - Security",
        category=AgentCategory.ADMIN,
        description=(
            "Security auditor that scans configurations, audits access "
            "patterns, detects PII, and flags compliance issues across "
            "OneLake datasets."
        ),
        tags=["PII Masking", "Privacy First", "Compliance"],
        system_prompt=(
            "You are Sentinel, a security auditor in Microsoft Fabric. "
            "You scan workspace configurations, audit file contents for PII "
            "or sensitive data, and flag compliance issues. You operate "
            "read-only and never modify items.\n"
            "Report findings as:\n"
            "ACTION: Audited | ENTITY: <name> | TYPE: <item_type>\n"
            "Always use GUIDs for parameters."
        ),
        available_tools=[
            "fabric_list_workspaces",
            "fabric_list_items",
            "fabric_list_files",
            "fabric_read_file",
            # Semantic Link — admin audit & governance
            "sl_admin_list_workspaces",
            "sl_admin_list_datasets",
            "sl_admin_get_activity_events",
            "sl_admin_list_workspace_users",
            "sl_admin_list_dataset_users",
            "sl_list_workspace_users",
            "sl_list_connections",
            "sl_list_gateways",
            "sl_get_git_connection",
        ],
        default_access_level="read",
        icon="SecurityIcon",
        version="1.0.0",
    )
)

# ── Dash – Power BI Expert ───────────────────────────────────────────

_register(
    AgentTemplate(
        id="dash-powerbi-expert",
        name="Dash",
        display_name="Dash - Power BI Expert",
        category=AgentCategory.ANALYTICS,
        description=(
            "Power BI specialist who builds, reviews, and fixes reports and "
            "semantic models end-to-end — from DAX optimisation and best-practice "
            "enforcement to visual layout, page structure, and PBIR authoring."
        ),
        tags=["Power BI", "DAX", "Reports", "Semantic Model", "PBIR"],
        system_prompt=(
            "You are Dash, a Power BI expert working inside Microsoft Fabric. "
            "You combine deep DAX knowledge, report design best practices, and "
            "automated fixers to build and maintain world-class Power BI assets.\n\n"
            "Your workflow:\n"
            "1. Scan the report and semantic model for issues (BPA, formatting, "
            "   unused visuals, slicer mess, missing measure tables).\n"
            "2. Propose a fix plan with prioritised actions.\n"
            "3. Execute fixes using the PBI Fixer tools and pbir-tools.\n"
            "4. Validate the result and report a summary.\n\n"
            "Rules:\n"
            "- Always scan before fixing — never apply blind fixes.\n"
            "- Use pbir-tools for structural report changes (pages, visuals, layout).\n"
            "- Use PBI Fixer for semantic model changes (measures, calc groups, BPA).\n"
            "- Use Semantic Link for DAX evaluation and refresh management.\n"
            "- Emit structured actions:\n"
            "  ACTION: <Scanned|Fixed|Created|Modified> | ENTITY: <name> | TYPE: <item_type>\n"
            "- Always use GUIDs for workspace_id and item_id parameters."
        ),
        available_tools=[
            # Fabric core — workspace + file access
            "fabric_list_workspaces",
            "fabric_list_items",
            "fabric_list_files",
            "fabric_read_file",
            "fabric_write_file",
            # Semantic Link — DAX, semantic models, reports, refresh
            "sl_evaluate_dax",
            "sl_get_semantic_model_tables",
            "sl_list_semantic_models",
            "sl_get_semantic_model_definition",
            "sl_list_reports",
            "sl_get_report_definition",
            "sl_clone_report",
            "sl_rebind_report",
            "sl_export_report",
            "sl_refresh_semantic_model",
            "sl_get_refresh_history",
            "sl_cancel_refresh",
            "sl_deploy_semantic_model",
            "sl_set_endorsement",
            # PBI Fixer — report scan + fix
            "scan_report",
            "fix_report_bpa",
            "fix_piecharts",
            "fix_barcharts",
            "fix_columncharts",
            "fix_linecharts",
            "fix_column_to_bar",
            "fix_bar_to_column",
            "fix_column_to_line",
            "fix_page_size",
            "fix_hide_visual_filters",
            "fix_disable_show_items_no_data",
            "fix_ibcs_variance",
            "fix_remove_unused_custom_visuals",
            "fix_visual_alignment",
            "fix_migrate_report_level_measures",
            "fix_migrate_slicers",
            "fix_upgrade_to_pbir",
            # PBI Fixer — semantic model scan + fix
            "scan_semantic_model",
            "fix_model_bpa",
            "fix_do_not_summarize",
            "fix_hide_foreign_keys",
            "fix_measure_format",
            "fix_percentage_format",
            "fix_whole_number_format",
            "fix_capitalize_object_names",
            "fix_trim_object_names",
            "fix_use_divide_function",
            "fix_data_category",
            "fix_date_column_format",
            # PBI Fixer — add / enrich
            "add_measures_from_columns",
            "add_py_measures",
            "add_calc_group_time_intelligence",
            "add_calc_group_units",
            "add_calculated_calendar",
            "add_measure_table",
            "add_incremental_refresh",
            "add_cache_warming",
            "add_prep_for_ai",
            "add_last_refresh_table",
            # pbir-tools — PBIR report authoring & inspection
            "pbir_run",
            "pbir_ls",
            "pbir_tree",
            "pbir_find",
            "pbir_cat",
            "pbir_get",
            "pbir_set",
            "pbir_model",
            "pbir_new_report",
            "pbir_add",
            "pbir_cp",
            "pbir_mv",
            "pbir_rm",
            "pbir_visuals",
            "pbir_pages",
            "pbir_fields",
            "pbir_filters",
            "pbir_dax",
            "pbir_bookmarks",
            "pbir_annotations",
            "pbir_theme",
            "pbir_schema",
            "pbir_validate",
            "pbir_download",
            "pbir_publish",
            "pbir_report",
            "pbir_batch",
            "pbir_backup",
            "pbir_restore",
        ],
        default_access_level="write",
        icon="PowerBIIcon",
        version="1.0.0",
    )
)


def get_template(template_id: str) -> AgentTemplate | None:
    return AGENT_TEMPLATES.get(template_id)


def list_templates() -> list[AgentTemplate]:
    return list(AGENT_TEMPLATES.values())
