"""Backend Pi harness contract for AgentHub sessions.

The browser Pi surface is not enough by itself: the backend must also
declare that Pi is the orchestration runtime and expose a real tool
surface. This module builds that backend-side contract from the same
AgentHub tool policies that gate runtime dispatch.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from services.agenthub import tool_policies
from services.agenthub.tool_runtime import ToolPolicy

PI_AGENT_CORE_PACKAGE = "npm:@mariozechner/pi-agent-core@0.71.1"
PI_AGENT_CORE_PACKAGE_NAME = "@mariozechner/pi-agent-core"
PI_WEB_UI_PACKAGE = "npm:@mariozechner/pi-web-ui@0.71.1"
PI_WEB_UI_PACKAGE_NAME = "@mariozechner/pi-web-ui"
PI_CODING_AGENT_PACKAGE = "npm:@mariozechner/pi-coding-agent@0.71.1"
PI_TUI_PACKAGE = "npm:@mariozechner/pi-tui@0.71.1"
PI_ASK_USER_PACKAGE = "npm:pi-ask-user@0.8.0"
PI_SUBAGENTS_PACKAGE = "npm:pi-subagents@0.21.3"
PI_MCP_ADAPTER_PACKAGE = "npm:pi-mcp-adapter@2.5.2"
PI_CONTEXT_MODE_PACKAGE = "npm:context-mode@1.0.103"
PI_BABYSITTER_PACKAGE = "npm:@a5c-ai/babysitter-pi@0.1.3"
PI_MISSION_UI_EXTENSION = ".pi/extensions/fabric-clawhub-mission-ui.ts"
PI_MISSION_UI_PACKAGE_NAME = "@fabric-clawhub/pi-mission-ui"
PI_LOG_COMPACTION_EXTENSION = ".pi/extensions/fabric-clawhub-log-compactor.ts"
PI_LOG_COMPACTION_PACKAGE_NAME = "@fabric-clawhub/pi-log-compactor"
PI_AGENTIC_ENGINEERING_EXTENSION = ".pi/extensions/fabric-clawhub-agentic-engineering.ts"
PI_AGENTIC_ENGINEERING_PACKAGE_NAME = "@fabric-clawhub/pi-agentic-engineering"
PI_CONTEXT_MODE_EVENTS = [
    "pi.context.mode.indexed",
    "pi.context.mode.retrieved",
    "pi.context.mode.compacted",
    "pi.context.mode.rehydrated",
    "pi.context.mode.savings",
]
PI_QRSPI_PROTOCOL = "question-research-design-structure-plan-implement-verify-review"
PI_QRSPI_PHASE_MODEL = [
    "question",
    "research",
    "design",
    "structure",
    "plan",
    "worktree",
    "implement",
    "verify",
    "review",
]
PI_MICROSOFT_DOCS_MCP_TOOLS = [
    "microsoft_docs_search",
    "microsoft_docs_fetch",
    "microsoft_code_sample_search",
]
PI_AZURE_MCP_GUIDANCE_TOOLS = [
    "get_azure_bestpractices_get",
    "get_azure_bestpractices_ai_app",
]

PI_ACTIVE_EXTENSIONS = [
    PI_WEB_UI_PACKAGE,
    PI_AGENT_CORE_PACKAGE,
    PI_CODING_AGENT_PACKAGE,
    PI_TUI_PACKAGE,
    PI_ASK_USER_PACKAGE,
    PI_SUBAGENTS_PACKAGE,
    PI_MCP_ADAPTER_PACKAGE,
    PI_CONTEXT_MODE_PACKAGE,
    PI_BABYSITTER_PACKAGE,
    PI_MISSION_UI_EXTENSION,
    PI_LOG_COMPACTION_EXTENSION,
    PI_AGENTIC_ENGINEERING_EXTENSION,
]

PI_GOVERNED_OPTIONAL_EXTENSIONS: list[str] = []

PI_HARNESS_TOOL_LIMIT = 120
PI_GOVERNED_MCP_SERVERS = [
    {
        "name": "microsoft-learn-docs",
        "aliases": ["microsoft-learn", "microsoft-docs"],
        "transport": "streamable_http",
        "url": "https://learn.microsoft.com/api/mcp",
        "tools": PI_MICROSOFT_DOCS_MCP_TOOLS,
        "access": "agenthub-skill-scoped-public-docs",
        "directToolPromotion": False,
        "config_path": ".pi/mcp.json",
    },
    {
        "name": "azure-mcp-guidance",
        "package": "npm:@azure/mcp@latest",
        "command": "npx",
        "args": [
            "-y",
            "@azure/mcp@latest",
            "server",
            "start",
            "--tool",
            "get_azure_bestpractices_get",
            "--tool",
            "get_azure_bestpractices_ai_app",
            "--read-only",
        ],
        "tools": PI_AZURE_MCP_GUIDANCE_TOOLS,
        "access": "agenthub-skill-scoped-read-only-guidance",
        "directToolPromotion": False,
        "config_path": ".pi/mcp.json",
    },
]


def _tool_label(name: str) -> str:
    return name.replace("_", " ").strip().title() or name


def _pi_tool(policy: ToolPolicy) -> dict[str, Any]:
    return {
        "name": policy.tool_name,
        "label": _tool_label(policy.tool_name),
        "description": policy.description or f"AgentHub backend tool {policy.tool_name}",
        "sensitivity": str(policy.sensitivity),
        "autoAllowed": bool(policy.auto_allowed),
        "execution": "agenthub-tool-runtime-proxy",
        "parameters": {"type": "object", "additionalProperties": True},
    }


def build_pi_harness_manifest(*, limit: int = PI_HARNESS_TOOL_LIMIT) -> dict[str, Any]:
    policies = tool_policies.declared_policies()
    sensitivity_counts = Counter(str(policy.sensitivity) for policy in policies)
    emitted_tools = [_pi_tool(policy) for policy in policies[: max(1, limit)]]
    return {
        "orchestrationHarness": "pi-agent-core",
        "harnessPackage": PI_AGENT_CORE_PACKAGE,
        "subagentHarness": "pi-subagents",
        "subagentPackage": PI_SUBAGENTS_PACKAGE,
        "subagentRuntimeMode": "foreground-status-control-results",
        "subagentObservability": {
            "package": PI_SUBAGENTS_PACKAGE,
            "events": [
                "pi.subagents.status",
                "pi.subagents.control",
                "pi.subagents.result",
                "pi.subagents.async",
                "pi.turn.*",
                "pi.tool.*",
            ],
            "streaming": "container-callback-to-agenthub-sse",
            "statusOverlay": "subagents-status-compatible",
        },
        "toolRegistry": "agenthub-tool-runtime",
        "toolExecutionBridge": "agenthub-tool-runtime-proxy",
        "toolCount": len(policies),
        "emittedToolCount": len(emitted_tools),
        "toolLimit": limit,
        "toolPolicySummary": {
            "readSafe": sensitivity_counts.get("read_safe", 0),
            "readSensitive": sensitivity_counts.get("read_sensitive", 0),
            "write": sensitivity_counts.get("write", 0),
            "destructive": sensitivity_counts.get("destructive", 0),
            "autoAllowed": sum(1 for policy in policies if policy.auto_allowed),
        },
        "tools": emitted_tools,
    }


def build_pi_session_context(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing_context = dict(existing or {})
    existing_pi = existing_context.get("pi_orchestration") if isinstance(existing_context.get("pi_orchestration"), dict) else {}
    harness = build_pi_harness_manifest()
    pi_context: dict[str, Any] = {
        "runtime": "pi",
        "runtime_package": PI_AGENT_CORE_PACKAGE,
        "runtime_package_name": PI_AGENT_CORE_PACKAGE_NAME,
        "subagent_runtime": "pi-subagents",
        "subagent_package": PI_SUBAGENTS_PACKAGE,
        "subagent_observability": harness.get("subagentObservability"),
        "frontend_runtime_package": PI_WEB_UI_PACKAGE,
        "frontend_runtime_package_name": PI_WEB_UI_PACKAGE_NAME,
        "coding_agent_package": PI_CODING_AGENT_PACKAGE,
        "tui_package": PI_TUI_PACKAGE,
        "execution_surface_extension": PI_MISSION_UI_PACKAGE_NAME,
        "agentic_engineering_extension": PI_AGENTIC_ENGINEERING_PACKAGE_NAME,
        "agentic_engineering_source": PI_AGENTIC_ENGINEERING_EXTENSION,
        "rpi_protocol": "research-plan-implement-context-gates",
        "qrspi_protocol": PI_QRSPI_PROTOCOL,
        "qrspi_phase_model": PI_QRSPI_PHASE_MODEL,
        "qrspi_question_policy": {
            "question_first": True,
            "neutral_questions_required": True,
            "implementation_opinion_allowed": False,
        },
        "qrspi_research_policy": {
            "blind_factual_research": True,
            "source_refs_required": True,
            "recommendations_deferred_until_design": True,
        },
        "qrspi_design_structure_policy": {
            "design_before_plan": True,
            "structure_before_plan": True,
            "review_events": [
                "pi.qrspi.design.review_requested",
                "pi.qrspi.design.approved",
                "pi.qrspi.structure.created",
                "pi.qrspi.structure.approved",
            ],
        },
        "qrspi_instruction_budget": {
            "budget_basis": "instructions-not-only-tokens",
            "max_phase_directives": 6,
            "no_magic_words_required": True,
            "compact_handoff_required": True,
        },
        "qrspi_vertical_slice_policy": {
            "strategy": "thin-end-to-end-slice-before-horizontal-layers",
            "checkpoint_before_mutation": True,
            "slice_evidence_required": True,
        },
        "qrspi_backtrack_policy": {
            "allowed": True,
            "event": "pi.qrspi.phase.backtrack_requested",
            "valid_previous_phases": PI_QRSPI_PHASE_MODEL,
            "evidence_required": ["missing fact", "failed criterion", "diff or screenshot receipt"],
        },
        "qrspi_review_policy": {
            "plan_review_is_alignment_gate": True,
            "code_review_required_before_finish": True,
            "review_context": "fresh-design-structure-plan-diff-evidence",
        },
        "context_pack_schema": "ContextPackV2",
        "subagent_work_model": "context-window-fork",
        "context_window_policy": {
            "agent_id_role": "execution-template",
            "context_pack_role": "primary-work-unit",
            "implementation_context": "approved-plan-plus-selected-snippets",
            "verification_context": "fresh-plan-evidence-receipts",
        },
        "log_compaction_extension": PI_LOG_COMPACTION_PACKAGE_NAME,
        "log_compaction_source": PI_LOG_COMPACTION_EXTENSION,
        "log_compaction_policy": {
            "recent_window_ms": 8000,
            "refresh_ms": 1500,
            "max_recent_rows": 8,
            "strategy": "agent-kind-level-contiguous-rollup",
            "collapsed_detail_visibility": "details-summary",
        },
        "stream_transport": "agenthub-sse-to-pi-extension",
        "mcp_access_mode": "pi-mcp-adapter-proxy-via-agenthub-policy",
        "mcp_direct_tools_default": False,
        "mcp_governance": {
            "direct_tool_promotion": False,
            "max_tools_per_request": PI_HARNESS_TOOL_LIMIT,
            "server_tool_allowlists_required": True,
            "skill_scoped_tool_selection": True,
        },
        "governed_mcp_servers": PI_GOVERNED_MCP_SERVERS,
        "context_mode_facade": "agenthub-governed-context-mode",
        "context_mode_events": PI_CONTEXT_MODE_EVENTS,
        "context_mode_controls": {
            "tenant_storage_isolation": True,
            "session_storage_isolation": True,
            "cross_tenant_search": False,
            "secret_bearing_files": False,
            "purge_required": True,
        },
        "context_mode_package": PI_CONTEXT_MODE_PACKAGE,
        "context_mode_package_name": "context-mode",
        "context_mode_mcp_server": {
            "name": "context-mode",
            "command": "npx",
            "args": ["-y", "context-mode@1.0.103"],
            "config_path": ".pi/mcp.json",
        },
        "process_governor_package": PI_BABYSITTER_PACKAGE,
        "process_governor_package_name": "@a5c-ai/babysitter-pi",
        "process_governor": "babysitter-pi",
        "extensions": [{"source": source, "adoption": "active"} for source in PI_ACTIVE_EXTENSIONS],
        "governed_optional_packages": [],
        **harness,
    }
    pi_context.update(existing_pi)
    pi_context.update(harness)
    existing_context.update({
        "runtime": "pi",
        "orchestration_runtime": "pi",
        "subagent_runtime": "pi-subagents",
        "execution_stream_interface": "pi-extension",
        "ai_orchestration_harness": "pi-agent-core",
        "pi_extensions": PI_ACTIVE_EXTENSIONS,
        "pi_governed_optional_extensions": PI_GOVERNED_OPTIONAL_EXTENSIONS,
        "pi_orchestration": pi_context,
    })
    return existing_context