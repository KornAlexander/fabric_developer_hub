import type { PiMissionExtensionMetadata } from "../events";

export interface PiPackageSpec extends PiMissionExtensionMetadata {
    source: string;
    role: "frontend-runtime" | "orchestration-runtime" | "foundation-ai" | "cli-runtime" | "tui-runtime" | "mcp-adapter" | "context-optimizer" | "process-governor" | "extension" | "local-extension";
    features: string[];
    adoption?: "active";
    license?: string;
    securityNotes?: string[];
}

export interface PiArchitectureLayer {
    id: "application" | "core" | "foundation";
    label: string;
    packages: PiPackageSpec[];
    responsibilities: string[];
}

export const PI_QRSPI_PROTOCOL = "question-research-design-structure-plan-implement-verify-review";
export const PI_QRSPI_PHASE_MODEL = ["question", "research", "design", "structure", "plan", "worktree", "implement", "verify", "review"];

export const PI_FRONTEND_RUNTIME_PACKAGE: PiPackageSpec = {
    id: "pi-web-ui",
    label: "Pi Web UI",
    packageName: "@mariozechner/pi-web-ui",
    version: "0.71.1",
    source: "npm:@mariozechner/pi-web-ui@0.71.1",
    role: "frontend-runtime",
    features: ["ChatPanel", "AgentInterface", "MessageList", "tool renderers", "artifacts panel"],
};

export const PI_AI_PACKAGE: PiPackageSpec = {
    id: "pi-ai",
    label: "Pi AI",
    packageName: "@mariozechner/pi-ai",
    version: "0.71.1",
    source: "npm:@mariozechner/pi-ai@0.71.1",
    role: "foundation-ai",
    features: ["model provider API", "streaming message events", "tool-call message types", "usage accounting"],
};

export const PI_ORCHESTRATION_RUNTIME_PACKAGE: PiPackageSpec = {
    id: "pi-agent-core",
    label: "Pi Agent Core",
    packageName: "@mariozechner/pi-agent-core",
    version: "0.71.1",
    source: "npm:@mariozechner/pi-agent-core@0.71.1",
    role: "orchestration-runtime",
    features: ["Agent", "tool registration", "turn orchestration", "session replay"],
};

export const PI_CODING_AGENT_PACKAGE: PiPackageSpec = {
    id: "pi-coding-agent",
    label: "Pi Coding Agent CLI",
    packageName: "@mariozechner/pi-coding-agent",
    version: "0.71.1",
    source: "npm:@mariozechner/pi-coding-agent@0.71.1",
    role: "cli-runtime",
    features: ["pi CLI", "interactive mode", "session management", "tool execution stream"],
};

export const PI_TUI_PACKAGE: PiPackageSpec = {
    id: "pi-tui",
    label: "Pi TUI",
    packageName: "@mariozechner/pi-tui",
    version: "0.71.1",
    source: "npm:@mariozechner/pi-tui@0.71.1",
    role: "tui-runtime",
    features: ["terminal renderer", "message stream", "editor", "footer", "extension UI components"],
};

export const PI_ASK_USER_PACKAGE: PiPackageSpec = {
    id: "pi-ask-user",
    label: "pi-ask-user",
    packageName: "pi-ask-user",
    version: "0.8.0",
    source: "npm:pi-ask-user@0.8.0",
    role: "extension",
    features: ["ctx.ui questions", "select controls", "freeform input"],
};

export const PI_SUBAGENTS_PACKAGE: PiPackageSpec = {
    id: "pi-subagents",
    label: "pi-subagents",
    packageName: "pi-subagents",
    version: "0.21.3",
    source: "npm:pi-subagents@0.21.3",
    role: "extension",
    features: ["delegate tool", "foreground status stream", "control notifications", "result artifacts", "async status.json/events.jsonl"],
};

export const PI_MCP_ADAPTER_PACKAGE: PiPackageSpec = {
    id: "pi-mcp-adapter",
    label: "pi-mcp-adapter",
    packageName: "pi-mcp-adapter",
    version: "2.5.2",
    source: "npm:pi-mcp-adapter@2.5.2",
    role: "mcp-adapter",
    adoption: "active",
    license: "MIT",
    features: ["single MCP proxy tool", "lazy MCP server startup", "metadata cache", "MCP UI bridge", "direct tool promotion"],
    securityNotes: ["MCP calls stay behind AgentHub tool policy", "direct tools require explicit allowlist", "npm audit currently reports a moderate advisory through @mariozechner/pi-ai"],
};

export const PI_CONTEXT_MODE_PACKAGE: PiPackageSpec = {
    id: "context-mode",
    label: "context-mode",
    packageName: "context-mode",
    version: "1.0.103",
    source: "npm:context-mode@1.0.103",
    role: "context-optimizer",
    adoption: "active",
    license: "Elastic-2.0",
    features: ["Pi extension hooks", "FTS5 context index", "session continuity hooks", "sandboxed context tools", "context savings telemetry", "context-mode MCP server"],
    securityNotes: ["MCP server is registered in .pi/mcp.json with a pinned npx command", "sandboxed tools must stay inside the mission container/workspace boundary", "Elastic-2.0 license remains visible in package metadata"],
};

export const PI_BABYSITTER_PACKAGE: PiPackageSpec = {
    id: "babysitter-pi",
    label: "Babysitter Pi",
    packageName: "@a5c-ai/babysitter-pi",
    version: "0.1.3",
    source: "npm:@a5c-ai/babysitter-pi@0.1.3",
    role: "process-governor",
    adoption: "active",
    license: "MIT",
    features: ["Pi extension manifest", "Pi skill aliases", "process-as-code workflows", "quality gates", "breakpoints", "event-sourced journal", "resume/doctor commands"],
    securityNotes: ["AgentHub remains the orchestration authority for Fabric writes and approvals", ".a5c run storage must stay inside the isolated mission workspace", "Babysitter workflow commands run as Pi package capabilities, not browser code"],
};

export const PI_MISSION_UI_EXTENSION: PiPackageSpec = {
    id: "fabric-clawhub-mission-ui",
    label: "Fabric ClawHub Pi Mission UI",
    packageName: "@fabric-clawhub/pi-mission-ui",
    version: "0.1.0",
    source: ".pi/extensions/fabric-clawhub-mission-ui.ts",
    role: "local-extension",
    features: ["Mission Control event bridge", "execution surface", "approval projection", "artifact projection"],
};

export const PI_LOG_COMPACTION_EXTENSION: PiPackageSpec = {
    id: "fabric-clawhub-log-compactor",
    label: "Fabric ClawHub Pi Log Compactor",
    packageName: "@fabric-clawhub/pi-log-compactor",
    version: "0.1.0",
    source: ".pi/extensions/fabric-clawhub-log-compactor.ts",
    role: "local-extension",
    features: ["self-collapsing live log", "recent detail window", "older activity rollups", "summary/details replay"],
    securityNotes: ["Collapses presentation only; raw public SSE replay remains governed by AgentHub", "Trace-category backend logs remain excluded before UI compaction"],
};

export const PI_AGENTIC_ENGINEERING_EXTENSION: PiPackageSpec = {
    id: "fabric-clawhub-agentic-engineering",
    label: "Fabric ClawHub Agentic Engineering",
    packageName: "@fabric-clawhub/pi-agentic-engineering",
    version: "0.1.0",
    source: ".pi/extensions/fabric-clawhub-agentic-engineering.ts",
    role: "local-extension",
    adoption: "active",
    features: ["QRSPI phase gates", "RPI phase gates", "ContextPackV2 policy", "instruction-budget tracking", "context-window fork contract", "context-mode telemetry receipts", "verifier handoff digests", "backtracking receipts"],
    securityNotes: ["Declares protocol and telemetry only; Fabric writes still route through AgentHub tool policy", "context-mode calls must use the AgentHub-governed facade", "agent_id is treated as an execution template, not the primary context boundary"],
};

export const PI_EXTENSION_PACKAGES: PiPackageSpec[] = [
    PI_FRONTEND_RUNTIME_PACKAGE,
    PI_AI_PACKAGE,
    PI_ORCHESTRATION_RUNTIME_PACKAGE,
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
];

export const PI_GOVERNED_OPTIONAL_PACKAGES: PiPackageSpec[] = [];

export const PI_LAYERED_ARCHITECTURE: PiArchitectureLayer[] = [
    {
        id: "application",
        label: "Application layer",
        packages: [PI_CODING_AGENT_PACKAGE, PI_FRONTEND_RUNTIME_PACKAGE, PI_ASK_USER_PACKAGE, PI_SUBAGENTS_PACKAGE, PI_BABYSITTER_PACKAGE, PI_MISSION_UI_EXTENSION, PI_LOG_COMPACTION_EXTENSION, PI_AGENTIC_ENGINEERING_EXTENSION],
        responsibilities: ["mission entrypoints", "browser chat", "extension UI", "human prompts", "subagent presence", "process checkpoints", "self-collapsing observability", "QRSPI context gates"],
    },
    {
        id: "core",
        label: "Core layer",
        packages: [PI_ORCHESTRATION_RUNTIME_PACKAGE],
        responsibilities: ["agent loop", "tool registration", "turn orchestration", "session replay"],
    },
    {
        id: "foundation",
        label: "Foundation layer",
        packages: [PI_AI_PACKAGE, PI_TUI_PACKAGE, PI_MCP_ADAPTER_PACKAGE, PI_CONTEXT_MODE_PACKAGE],
        responsibilities: ["LLM streaming API", "terminal rendering", "MCP bridge", "AgentHub tool policy proxy", "context indexing"],
    },
];

export const PI_ARCHITECTURE_LAYER_IDS = PI_LAYERED_ARCHITECTURE.map((layer) => layer.id).join("->");

export function buildPiSessionOrchestrationContext() {
    return {
        runtime: "pi",
        orchestration_runtime: "pi",
        subagent_runtime: "pi-subagents",
        subagent_package: PI_SUBAGENTS_PACKAGE.source,
        subagent_observability: "pi-subagents-native-events",
        execution_stream_interface: "pi-extension",
        pi_orchestration: {
            runtime: "pi",
            subagent_runtime: "pi-subagents",
            subagent_package: PI_SUBAGENTS_PACKAGE.source,
            subagent_package_name: PI_SUBAGENTS_PACKAGE.packageName,
            subagent_runtime_mode: "foreground-status-control-results",
            subagent_observability: {
                event_source: "Agent.subscribe + pi-subagents status/control/result",
                transport: "agenthub-sse-to-pi-web-ui",
                container_callback: "/api/internal/events/emit",
                event_types: ["pi.subagents.status", "pi.subagents.control", "pi.subagents.result", "pi.subagents.async"],
            },
            runtime_package: PI_ORCHESTRATION_RUNTIME_PACKAGE.source,
            runtime_package_name: PI_ORCHESTRATION_RUNTIME_PACKAGE.packageName,
            foundation_ai_package: PI_AI_PACKAGE.source,
            foundation_ai_package_name: PI_AI_PACKAGE.packageName,
            cli_runtime_package: PI_CODING_AGENT_PACKAGE.source,
            cli_runtime_package_name: PI_CODING_AGENT_PACKAGE.packageName,
            tui_runtime_package: PI_TUI_PACKAGE.source,
            tui_runtime_package_name: PI_TUI_PACKAGE.packageName,
            mcp_adapter_package: PI_MCP_ADAPTER_PACKAGE.source,
            mcp_adapter_package_name: PI_MCP_ADAPTER_PACKAGE.packageName,
            mcp_access_mode: "pi-mcp-adapter-proxy-via-agenthub-policy",
            mcp_direct_tools_default: false,
            mcp_governance: {
                direct_tool_promotion: false,
                max_tools_per_request: 120,
                server_tool_allowlists_required: true,
                skill_scoped_tool_selection: true,
            },
            governed_mcp_servers: [
                {
                    name: "microsoft-learn-docs",
                    aliases: ["microsoft-learn", "microsoft-docs"],
                    transport: "streamable_http",
                    url: "https://learn.microsoft.com/api/mcp",
                    tools: ["microsoft_docs_search", "microsoft_docs_fetch", "microsoft_code_sample_search"],
                    access: "agenthub-skill-scoped-public-docs",
                    directToolPromotion: false,
                    config_path: ".pi/mcp.json",
                },
                {
                    name: "azure-mcp-guidance",
                    package: "npm:@azure/mcp@latest",
                    command: "npx",
                    args: ["-y", "@azure/mcp@latest", "server", "start", "--tool", "get_azure_bestpractices_get", "--tool", "get_azure_bestpractices_ai_app", "--read-only"],
                    tools: ["get_azure_bestpractices_get", "get_azure_bestpractices_ai_app"],
                    access: "agenthub-skill-scoped-read-only-guidance",
                    directToolPromotion: false,
                    config_path: ".pi/mcp.json",
                },
            ],
            context_mode_facade: "agenthub-governed-context-mode",
            context_mode_events: ["pi.context.mode.indexed", "pi.context.mode.retrieved", "pi.context.mode.compacted", "pi.context.mode.rehydrated", "pi.context.mode.savings"],
            context_mode_controls: {
                tenant_storage_isolation: true,
                session_storage_isolation: true,
                cross_tenant_search: false,
                secret_bearing_files: false,
                purge_required: true,
            },
            context_mode_package: PI_CONTEXT_MODE_PACKAGE.source,
            context_mode_package_name: PI_CONTEXT_MODE_PACKAGE.packageName,
            context_mode_mcp_server: {
                name: "context-mode",
                command: "npx",
                args: ["-y", "context-mode@1.0.103"],
                config_path: ".pi/mcp.json",
            },
            process_governor_package: PI_BABYSITTER_PACKAGE.source,
            process_governor_package_name: PI_BABYSITTER_PACKAGE.packageName,
            process_governor: "babysitter-pi",
            frontend_runtime_package: PI_FRONTEND_RUNTIME_PACKAGE.source,
            frontend_runtime_package_name: PI_FRONTEND_RUNTIME_PACKAGE.packageName,
            execution_surface_extension: PI_MISSION_UI_EXTENSION.packageName,
            execution_surface_source: PI_MISSION_UI_EXTENSION.source,
            agentic_engineering_extension: PI_AGENTIC_ENGINEERING_EXTENSION.packageName,
            agentic_engineering_source: PI_AGENTIC_ENGINEERING_EXTENSION.source,
            rpi_protocol: "research-plan-implement-context-gates",
            qrspi_protocol: PI_QRSPI_PROTOCOL,
            qrspi_phase_model: PI_QRSPI_PHASE_MODEL,
            qrspi_question_policy: {
                question_first: true,
                neutral_questions_required: true,
                implementation_opinion_allowed: false,
            },
            qrspi_research_policy: {
                blind_factual_research: true,
                source_refs_required: true,
                recommendations_deferred_until_design: true,
            },
            qrspi_design_structure_policy: {
                design_before_plan: true,
                structure_before_plan: true,
                review_events: ["pi.qrspi.design.review_requested", "pi.qrspi.design.approved", "pi.qrspi.structure.created", "pi.qrspi.structure.approved"],
            },
            qrspi_instruction_budget: {
                budget_basis: "instructions-not-only-tokens",
                max_phase_directives: 6,
                no_magic_words_required: true,
                compact_handoff_required: true,
            },
            qrspi_vertical_slice_policy: {
                strategy: "thin-end-to-end-slice-before-horizontal-layers",
                checkpoint_before_mutation: true,
                slice_evidence_required: true,
            },
            qrspi_backtrack_policy: {
                allowed: true,
                event: "pi.qrspi.phase.backtrack_requested",
                valid_previous_phases: PI_QRSPI_PHASE_MODEL,
                evidence_required: ["missing fact", "failed criterion", "diff or screenshot receipt"],
            },
            qrspi_review_policy: {
                plan_review_is_alignment_gate: true,
                code_review_required_before_finish: true,
                review_context: "fresh-design-structure-plan-diff-evidence",
            },
            context_pack_schema: "ContextPackV2",
            subagent_work_model: "context-window-fork",
            context_window_policy: {
                agent_id_role: "execution-template",
                context_pack_role: "primary-work-unit",
                implementation_context: "approved-plan-plus-selected-snippets",
                verification_context: "fresh-plan-evidence-receipts",
            },
            log_compaction_extension: PI_LOG_COMPACTION_EXTENSION.packageName,
            log_compaction_source: PI_LOG_COMPACTION_EXTENSION.source,
            log_compaction_policy: {
                recent_window_ms: 8000,
                refresh_ms: 1500,
                max_recent_rows: 8,
                strategy: "agent-kind-level-contiguous-rollup",
                collapsed_detail_visibility: "details-summary",
            },
            stream_transport: "agenthub-sse-to-pi-extension",
            extensions: PI_EXTENSION_PACKAGES.map((pkg) => ({
                id: pkg.id,
                label: pkg.label,
                packageName: pkg.packageName,
                version: pkg.version,
                source: pkg.source,
                role: pkg.role,
                features: pkg.features,
                adoption: pkg.adoption || "active",
                license: pkg.license,
                securityNotes: pkg.securityNotes,
            })),
            governed_optional_packages: PI_GOVERNED_OPTIONAL_PACKAGES.map((pkg) => ({
                id: pkg.id,
                label: pkg.label,
                packageName: pkg.packageName,
                version: pkg.version,
                source: pkg.source,
                role: pkg.role,
                features: pkg.features,
                adoption: pkg.adoption,
                license: pkg.license,
                securityNotes: pkg.securityNotes,
            })),
            architecture_layers: PI_LAYERED_ARCHITECTURE.map((layer) => ({
                id: layer.id,
                label: layer.label,
                packages: layer.packages.map((pkg) => pkg.source),
                responsibilities: layer.responsibilities,
            })),
        },
        pi_extensions: PI_EXTENSION_PACKAGES.map((pkg) => pkg.source),
        pi_governed_optional_extensions: PI_GOVERNED_OPTIONAL_PACKAGES.map((pkg) => pkg.source),
    };
}

export function piExtensionMetadata(packageSpec: PiPackageSpec): PiMissionExtensionMetadata {
    return {
        id: packageSpec.id,
        label: packageSpec.label,
        packageName: packageSpec.packageName,
        version: packageSpec.version,
    };
}