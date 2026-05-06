from __future__ import annotations

from services.agenthub.pi_backend_harness import (
    PI_HARNESS_TOOL_LIMIT,
    build_pi_harness_manifest,
    build_pi_session_context,
)


def test_pi_session_context_declares_active_context_mode_and_babysitter_packages() -> None:
    context = build_pi_session_context()
    pi_orchestration = context["pi_orchestration"]

    assert "npm:context-mode@1.0.103" in context["pi_extensions"]
    assert "npm:@a5c-ai/babysitter-pi@0.1.3" in context["pi_extensions"]
    assert ".pi/extensions/fabric-clawhub-log-compactor.ts" in context["pi_extensions"]
    assert ".pi/extensions/fabric-clawhub-agentic-engineering.ts" in context["pi_extensions"]
    assert context["pi_governed_optional_extensions"] == []
    assert pi_orchestration["context_mode_package"] == "npm:context-mode@1.0.103"
    assert pi_orchestration["context_mode_mcp_server"] == {
        "name": "context-mode",
        "command": "npx",
        "args": ["-y", "context-mode@1.0.103"],
        "config_path": ".pi/mcp.json",
    }
    assert pi_orchestration["process_governor_package"] == "npm:@a5c-ai/babysitter-pi@0.1.3"
    assert pi_orchestration["process_governor"] == "babysitter-pi"
    assert pi_orchestration["agentic_engineering_extension"] == "@fabric-clawhub/pi-agentic-engineering"
    assert pi_orchestration["agentic_engineering_source"] == ".pi/extensions/fabric-clawhub-agentic-engineering.ts"
    assert pi_orchestration["rpi_protocol"] == "research-plan-implement-context-gates"
    assert pi_orchestration["context_pack_schema"] == "ContextPackV2"
    assert pi_orchestration["subagent_work_model"] == "context-window-fork"
    assert pi_orchestration["context_window_policy"] == {
        "agent_id_role": "execution-template",
        "context_pack_role": "primary-work-unit",
        "implementation_context": "approved-plan-plus-selected-snippets",
        "verification_context": "fresh-plan-evidence-receipts",
    }
    assert pi_orchestration["context_mode_facade"] == "agenthub-governed-context-mode"
    assert pi_orchestration["mcp_governance"] == {
        "direct_tool_promotion": False,
        "max_tools_per_request": 120,
        "server_tool_allowlists_required": True,
        "skill_scoped_tool_selection": True,
    }
    governed_servers = {server["name"]: server for server in pi_orchestration["governed_mcp_servers"]}
    assert governed_servers["microsoft-learn-docs"]["tools"] == [
        "microsoft_docs_search",
        "microsoft_docs_fetch",
        "microsoft_code_sample_search",
    ]
    assert governed_servers["microsoft-learn-docs"]["directToolPromotion"] is False
    assert governed_servers["azure-mcp-guidance"]["tools"] == [
        "get_azure_bestpractices_get",
        "get_azure_bestpractices_ai_app",
    ]
    assert governed_servers["azure-mcp-guidance"]["directToolPromotion"] is False
    assert pi_orchestration["context_mode_events"] == [
        "pi.context.mode.indexed",
        "pi.context.mode.retrieved",
        "pi.context.mode.compacted",
        "pi.context.mode.rehydrated",
        "pi.context.mode.savings",
    ]
    assert pi_orchestration["context_mode_controls"] == {
        "tenant_storage_isolation": True,
        "session_storage_isolation": True,
        "cross_tenant_search": False,
        "secret_bearing_files": False,
        "purge_required": True,
    }
    assert pi_orchestration["log_compaction_extension"] == "@fabric-clawhub/pi-log-compactor"
    assert pi_orchestration["log_compaction_policy"] == {
        "recent_window_ms": 8000,
        "refresh_ms": 1500,
        "max_recent_rows": 8,
        "strategy": "agent-kind-level-contiguous-rollup",
        "collapsed_detail_visibility": "details-summary",
    }
    assert pi_orchestration["governed_optional_packages"] == []
    active_extensions = {extension["source"]: extension["adoption"] for extension in pi_orchestration["extensions"]}
    assert active_extensions["npm:context-mode@1.0.103"] == "active"
    assert active_extensions["npm:@a5c-ai/babysitter-pi@0.1.3"] == "active"
    assert active_extensions[".pi/extensions/fabric-clawhub-log-compactor.ts"] == "active"
    assert active_extensions[".pi/extensions/fabric-clawhub-agentic-engineering.ts"] == "active"


def test_pi_harness_manifest_never_emits_more_than_tool_limit() -> None:
    manifest = build_pi_harness_manifest()

    assert manifest["toolLimit"] == PI_HARNESS_TOOL_LIMIT == 120
    assert manifest["emittedToolCount"] <= manifest["toolLimit"]
    assert len(manifest["tools"]) == manifest["emittedToolCount"]