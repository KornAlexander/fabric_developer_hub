import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { initialMissionState, missionReducer } from "../../src/components/AgentHub/mission/missionReducer";
import { derivePiMissionEventsFromState } from "../../src/components/AgentHub/mission/pi/piMissionAdapter";
import { PI_AI_PACKAGE, PI_ARCHITECTURE_LAYER_IDS, PI_AGENTIC_ENGINEERING_EXTENSION, PI_BABYSITTER_PACKAGE, PI_CODING_AGENT_PACKAGE, PI_CONTEXT_MODE_PACKAGE, PI_EXTENSION_PACKAGES, PI_FRONTEND_RUNTIME_PACKAGE, PI_GOVERNED_OPTIONAL_PACKAGES, PI_LAYERED_ARCHITECTURE, PI_LOG_COMPACTION_EXTENSION, PI_MCP_ADAPTER_PACKAGE, PI_ORCHESTRATION_RUNTIME_PACKAGE, PI_TUI_PACKAGE, buildPiSessionOrchestrationContext } from "../../src/components/AgentHub/mission/pi/piExtensionPackages";
import { applyPiLogCompactionExtension, type PiLiveLogDetailRow } from "../../src/components/AgentHub/mission/pi/piLogCompactionExtension";
import { buildPiMissionViewModel } from "../../src/components/AgentHub/mission/pi/piMissionReducer";
import type { MissionEvent } from "../../src/components/AgentHub/mission/events";

function piEvent<T extends MissionEvent["type"]>(seq: number, type: T, extra: object = {}): MissionEvent {
    return {
        type,
        seq,
        sessionId: "pi-session-1",
        ts: new Date(2026, 4, 1, 12, 0, seq).toISOString(),
        schemaVersion: 1,
        ...extra,
    } as MissionEvent;
}

describe("Pi Mission UI event replay", () => {
    it("captures typed Pi events without turning them into legacy log rows", () => {
        let state = initialMissionState();
        state = missionReducer(state, piEvent(1, "pi.turn.start", {
            turnId: "turn-1",
            agentId: "planner",
            agentName: "MissionPlanner",
            model: "gpt-4o-mini",
            title: "Plan the Fabric repair",
        }));
        state = missionReducer(state, piEvent(2, "pi.turn.delta", {
            turnId: "turn-1",
            textDelta: "I will inspect the workspace before changing anything.",
        }));

        expect(state.piEvents).toHaveLength(2);
        expect(state.logs).toEqual([]);
        expect(state.activeAgentId).toBe("planner");
        expect(state.slotProgress.planner.currentStep).toBe("Plan the Fabric repair");
    });

    it("projects Pi approvals, artifacts, and subagents into Mission Control state", () => {
        let state = initialMissionState();
        state = missionReducer(state, piEvent(1, "pi.subagent.update", {
            agentId: "run-fde",
            agentName: "FabricDataEngineer",
            role: "Workspace repair",
            state: "running",
            task: "Patch PBIR files",
        }));
        state = missionReducer(state, piEvent(2, "pi.approval.request", {
            requestId: "approval-1",
            agentId: "run-fde",
            title: "Update report definition",
            summary: "Apply the safe PBIR change.",
            risk: "medium",
            toolCallId: "tool-1",
        }));
        state = missionReducer(state, piEvent(3, "pi.artifact.upsert", {
            artifactId: "artifact-1",
            agentId: "run-fde",
            kind: "diff",
            title: "PBIR diff",
            summary: "One visual binding changed.",
            webUrl: "https://fabric.example/report",
        }));

        expect(state.slotProgress["run-fde"].status).toBe("approval_required");
        expect(state.agentStatus["run-fde"]).toBe("waiting");
        expect(state.approvals["approval-1"].summary).toBe("Apply the safe PBIR change.");
        expect(state.artifacts["artifact-1"].name).toBe("PBIR diff");
    });

    it("builds a replayable Pi Mission view model", () => {
        const events = [
            piEvent(0.5 as any, "pi.orchestration.start", {
                runtime: "pi",
                runtimePackage: "@mariozechner/pi-agent-core",
                runtimePackageSource: "npm:@mariozechner/pi-agent-core@0.71.1",
                frontendRuntimePackage: "@mariozechner/pi-web-ui",
                executionSurfaceExtension: "@fabric-clawhub/pi-mission-ui",
                streamTransport: "agenthub-sse-to-pi-extension",
                extensions: ["npm:@mariozechner/pi-agent-core@0.71.1", "npm:pi-subagents@0.21.3"],
                backendBridge: "agenthub-fabric-runtime",
                orchestrationHarness: "pi-agent-core",
                harnessPackage: "npm:@mariozechner/pi-agent-core@0.71.1",
                toolRegistry: "agenthub-tool-runtime",
                toolExecutionBridge: "agenthub-tool-runtime-proxy",
                toolCount: 2,
                emittedToolCount: 2,
                toolPolicySummary: { readSafe: 1, readSensitive: 1, write: 0, destructive: 0, autoAllowed: 2 },
                tools: [
                    { name: "fabric_list_items", label: "Fabric List Items", description: "List items", sensitivity: "read_safe", autoAllowed: true, execution: "agenthub-tool-runtime-proxy", parameters: { type: "object", additionalProperties: true } },
                    { name: "fabric_get_item_definition", label: "Fabric Get Item Definition", description: "Read item definition", sensitivity: "read_sensitive", autoAllowed: true, execution: "agenthub-tool-runtime-proxy", parameters: { type: "object", additionalProperties: true } },
                ],
            }),
            piEvent(1, "pi.turn.start", { turnId: "turn-1", agentId: "planner", agentName: "Planner", model: "claude", title: "Inspect" }),
            piEvent(2, "pi.turn.delta", { turnId: "turn-1", textDelta: "Inspecting " }),
            piEvent(3, "pi.turn.delta", { turnId: "turn-1", textDelta: "workspace items." }),
            piEvent(4, "pi.tool.start", { toolCallId: "tool-1", turnId: "turn-1", agentId: "planner", toolName: "fabric_list_items", summary: "List Fabric items", extension: { id: "@fabric-clawhub/pi-fabric", label: "@fabric-clawhub/pi-fabric" } }),
            piEvent(5, "pi.tool.end", { toolCallId: "tool-1", turnId: "turn-1", status: "ok", durationMs: 420, display: { summary: "Found 24 items", trust: { level: "redacted", source: "fabric", redacted: true } } }),
            piEvent(6, "pi.turn.end", { turnId: "turn-1", status: "completed" }),
            piEvent(7, "pi.context.compaction", { status: "completed", summary: "Preserved workspace findings." }),
        ];

        const model = buildPiMissionViewModel(events as any);

        expect(model.turns).toHaveLength(1);
        expect(model.turns[0].text).toBe("Inspecting workspace items.");
        expect(model.turns[0].status).toBe("completed");
        expect(model.tools[0]).toMatchObject({ toolName: "fabric_list_items", status: "completed", displaySummary: "Found 24 items" });
        expect(model.orchestration).toMatchObject({ runtime: "pi", runtimePackage: "@mariozechner/pi-agent-core", executionSurfaceExtension: "@fabric-clawhub/pi-mission-ui", orchestrationHarness: "pi-agent-core", toolCount: 2 });
        expect(model.availableTools.map((tool) => tool.name)).toEqual(["fabric_list_items", "fabric_get_item_definition"]);
        expect(model.extensions).toContain("@fabric-clawhub/pi-fabric");
        expect(model.extensions).toContain("npm:@mariozechner/pi-agent-core@0.71.1");
        expect(model.markers[0]).toMatchObject({ kind: "compaction", title: "Context compaction completed" });
    });

    it("derives Pi Web UI events from legacy live mission telemetry", () => {
        let state = initialMissionState();
        state = missionReducer(state, piEvent(1, "composition_ready", {
            composition: {
                architecture: "dynamic",
                task: "Inspect a Fabric report with Pi",
                slots: [],
                handoffs: [],
            },
        }));
        state = missionReducer(state, piEvent(2, "log_line", {
            agentId: "generalist",
            agentName: "Generalist",
            level: "info",
            message: "Thinking through the report inspection plan",
        }));
        state = missionReducer(state, piEvent(3, "tool_call_started", {
            agentId: "generalist",
            agentName: "Generalist",
            callId: "tool-1",
            toolName: "fabric_get_item_definition",
            argsPreview: { item: "Workspace Inventory" },
        }));
        state = missionReducer(state, piEvent(4, "tool_call_ended", {
            agentId: "generalist",
            callId: "tool-1",
            toolName: "fabric_get_item_definition",
            durationMs: 42,
            status: "ok",
        }));

        const events = derivePiMissionEventsFromState(state);
        const model = buildPiMissionViewModel(events);

        expect(events.some((event) => event.type === "pi.orchestration.start" && event.runtimePackage === "@mariozechner/pi-agent-core")).toBe(true);
        expect(events.some((event) => event.type === "pi.turn.start" && event.extension?.packageName === "@mariozechner/pi-web-ui")).toBe(true);
        expect(model.turns[0].text).toContain("Thinking through the report inspection plan");
        expect(model.tools[0]).toMatchObject({ toolName: "fabric_get_item_definition", status: "completed" });
        expect(model.orchestration?.executionSurfaceExtension).toBe("@fabric-clawhub/pi-mission-ui");
        expect(model.extensions).toContain("Pi Web UI");
        expect(model.extensions).toContain("Fabric ClawHub Pi Mission UI");
    });

    it("keeps live transcript rows when the backend only emitted the Pi harness header", () => {
        let state = initialMissionState();
        state = missionReducer(state, piEvent(1, "pi.orchestration.start", {
            runtime: "pi",
            runtimePackage: "@mariozechner/pi-agent-core",
            runtimePackageSource: "npm:@mariozechner/pi-agent-core@0.71.1",
            frontendRuntimePackage: "@mariozechner/pi-web-ui",
            executionSurfaceExtension: "@fabric-clawhub/pi-mission-ui",
            streamTransport: "agenthub-sse-to-pi-extension",
            extensions: ["npm:@mariozechner/pi-agent-core@0.71.1"],
            orchestrationHarness: "pi-agent-core",
            toolRegistry: "agenthub-tool-runtime",
            toolExecutionBridge: "agenthub-tool-runtime-proxy",
            toolCount: 12,
            tools: [{ name: "fabric_list_workspaces", execution: "agenthub-tool-runtime-proxy" }],
        }));
        state = missionReducer(state, piEvent(2, "log_line", {
            agentId: "fabric-engineer",
            agentName: "FabricDataEngineer",
            level: "info",
            message: "Reading accessible Fabric workspaces",
        }));

        const events = derivePiMissionEventsFromState(state);
        const model = buildPiMissionViewModel(events);

        expect(events.filter((event) => event.type === "pi.orchestration.start")).toHaveLength(1);
        expect(events.some((event) => event.type === "pi.turn.start")).toBe(true);
        expect(model.rawEventCount).toBeGreaterThan(1);
        expect(model.turns[0].text).toContain("Reading accessible Fabric workspaces");
        expect(model.orchestration).toMatchObject({ orchestrationHarness: "pi-agent-core", toolCount: 12 });
        expect(model.availableTools.map((tool) => tool.name)).toContain("fabric_list_workspaces");
    });

    it("pins real Pi frontend runtime and extension packages", () => {
        expect(PI_FRONTEND_RUNTIME_PACKAGE).toMatchObject({ packageName: "@mariozechner/pi-web-ui", version: "0.71.1" });
        expect(PI_AI_PACKAGE).toMatchObject({ packageName: "@mariozechner/pi-ai", version: "0.71.1", role: "foundation-ai" });
        expect(PI_ORCHESTRATION_RUNTIME_PACKAGE).toMatchObject({ packageName: "@mariozechner/pi-agent-core", version: "0.71.1" });
        expect(PI_CODING_AGENT_PACKAGE).toMatchObject({ packageName: "@mariozechner/pi-coding-agent", version: "0.71.1" });
        expect(PI_TUI_PACKAGE).toMatchObject({ packageName: "@mariozechner/pi-tui", version: "0.71.1" });
        expect(PI_MCP_ADAPTER_PACKAGE).toMatchObject({ packageName: "pi-mcp-adapter", version: "2.5.2", adoption: "active", license: "MIT" });
        expect(PI_CONTEXT_MODE_PACKAGE).toMatchObject({ packageName: "context-mode", version: "1.0.103", adoption: "active", license: "Elastic-2.0" });
        expect(PI_BABYSITTER_PACKAGE).toMatchObject({ packageName: "@a5c-ai/babysitter-pi", version: "0.1.3", adoption: "active", license: "MIT" });
        expect(PI_LOG_COMPACTION_EXTENSION).toMatchObject({ packageName: "@fabric-clawhub/pi-log-compactor", version: "0.1.0", role: "local-extension" });
        expect(PI_AGENTIC_ENGINEERING_EXTENSION).toMatchObject({ packageName: "@fabric-clawhub/pi-agentic-engineering", version: "0.1.0", role: "local-extension", adoption: "active" });
        expect(PI_AGENTIC_ENGINEERING_EXTENSION.features).toEqual(expect.arrayContaining(["RPI phase gates", "ContextPackV2 policy", "context-window fork contract"]));
        expect(PI_EXTENSION_PACKAGES.map((pkg) => pkg.source)).toEqual(expect.arrayContaining([
            "npm:@mariozechner/pi-web-ui@0.71.1",
            "npm:@mariozechner/pi-ai@0.71.1",
            "npm:@mariozechner/pi-agent-core@0.71.1",
            "npm:@mariozechner/pi-coding-agent@0.71.1",
            "npm:@mariozechner/pi-tui@0.71.1",
            "npm:pi-ask-user@0.8.0",
            "npm:pi-subagents@0.21.3",
            "npm:pi-mcp-adapter@2.5.2",
            "npm:context-mode@1.0.103",
            "npm:@a5c-ai/babysitter-pi@0.1.3",
            ".pi/extensions/fabric-clawhub-log-compactor.ts",
            ".pi/extensions/fabric-clawhub-agentic-engineering.ts",
        ]));
        expect(PI_GOVERNED_OPTIONAL_PACKAGES).toEqual([]);
        expect(PI_ARCHITECTURE_LAYER_IDS).toBe("application->core->foundation");
        expect(PI_LAYERED_ARCHITECTURE.map((layer) => layer.id)).toEqual(["application", "core", "foundation"]);
        expect(PI_LAYERED_ARCHITECTURE.find((layer) => layer.id === "core")?.packages.map((pkg) => pkg.packageName)).toEqual(["@mariozechner/pi-agent-core"]);
        expect(PI_LAYERED_ARCHITECTURE.find((layer) => layer.id === "application")?.packages.map((pkg) => pkg.packageName)).toContain("@a5c-ai/babysitter-pi");
        expect(PI_LAYERED_ARCHITECTURE.find((layer) => layer.id === "application")?.packages.map((pkg) => pkg.packageName)).toContain("@fabric-clawhub/pi-log-compactor");
        expect(PI_LAYERED_ARCHITECTURE.find((layer) => layer.id === "application")?.packages.map((pkg) => pkg.packageName)).toContain("@fabric-clawhub/pi-agentic-engineering");
        expect(PI_LAYERED_ARCHITECTURE.find((layer) => layer.id === "foundation")?.packages.map((pkg) => pkg.packageName)).toEqual(["@mariozechner/pi-ai", "@mariozechner/pi-tui", "pi-mcp-adapter", "context-mode"]);
        expect(buildPiSessionOrchestrationContext()).toMatchObject({
            runtime: "pi",
            orchestration_runtime: "pi",
            execution_stream_interface: "pi-extension",
            pi_orchestration: {
                runtime_package_name: "@mariozechner/pi-agent-core",
                foundation_ai_package_name: "@mariozechner/pi-ai",
                cli_runtime_package_name: "@mariozechner/pi-coding-agent",
                tui_runtime_package_name: "@mariozechner/pi-tui",
                mcp_adapter_package_name: "pi-mcp-adapter",
                mcp_access_mode: "pi-mcp-adapter-proxy-via-agenthub-policy",
                mcp_direct_tools_default: false,
                mcp_governance: {
                    direct_tool_promotion: false,
                    max_tools_per_request: 120,
                    server_tool_allowlists_required: true,
                    skill_scoped_tool_selection: true,
                },
                governed_mcp_servers: expect.arrayContaining([
                    expect.objectContaining({
                        name: "microsoft-learn-docs",
                        tools: ["microsoft_docs_search", "microsoft_docs_fetch", "microsoft_code_sample_search"],
                        directToolPromotion: false,
                    }),
                    expect.objectContaining({
                        name: "azure-mcp-guidance",
                        tools: ["get_azure_bestpractices_get", "get_azure_bestpractices_ai_app"],
                        directToolPromotion: false,
                    }),
                ]),
                context_mode_package_name: "context-mode",
                context_mode_mcp_server: {
                    name: "context-mode",
                    command: "npx",
                    args: ["-y", "context-mode@1.0.103"],
                    config_path: ".pi/mcp.json",
                },
                process_governor_package_name: "@a5c-ai/babysitter-pi",
                process_governor: "babysitter-pi",
                log_compaction_extension: "@fabric-clawhub/pi-log-compactor",
                log_compaction_policy: {
                    recent_window_ms: 8000,
                    refresh_ms: 1500,
                    max_recent_rows: 8,
                    strategy: "agent-kind-level-contiguous-rollup",
                    collapsed_detail_visibility: "details-summary",
                },
                frontend_runtime_package_name: "@mariozechner/pi-web-ui",
                execution_surface_extension: "@fabric-clawhub/pi-mission-ui",
                agentic_engineering_extension: "@fabric-clawhub/pi-agentic-engineering",
                rpi_protocol: "research-plan-implement-context-gates",
                context_pack_schema: "ContextPackV2",
                subagent_work_model: "context-window-fork",
                context_mode_facade: "agenthub-governed-context-mode",
                context_mode_events: ["pi.context.mode.indexed", "pi.context.mode.retrieved", "pi.context.mode.compacted", "pi.context.mode.rehydrated", "pi.context.mode.savings"],
                context_window_policy: expect.objectContaining({
                    agent_id_role: "execution-template",
                    context_pack_role: "primary-work-unit",
                    implementation_context: "approved-plan-plus-selected-snippets",
                    verification_context: "fresh-plan-evidence-receipts",
                }),
                stream_transport: "agenthub-sse-to-pi-extension",
                governed_optional_packages: [],
                architecture_layers: [
                    expect.objectContaining({ id: "application" }),
                    expect.objectContaining({ id: "core", packages: ["npm:@mariozechner/pi-agent-core@0.71.1"] }),
                    expect.objectContaining({ id: "foundation" }),
                ],
            },
            pi_extensions: expect.arrayContaining(["npm:context-mode@1.0.103", "npm:@a5c-ai/babysitter-pi@0.1.3", ".pi/extensions/fabric-clawhub-log-compactor.ts", ".pi/extensions/fabric-clawhub-agentic-engineering.ts"]),
            pi_governed_optional_extensions: [],
        });
    });

    it("collapses older Pi live-log details into high-level rollups", () => {
        const baseRows: PiLiveLogDetailRow[] = Array.from({ length: 7 }, (_, index) => ({
            type: "detail" as const,
            key: `older-${index}`,
            seq: index + 1,
            ts: `2026-05-01T09:00:0${index}.000Z`,
            level: "info" as const,
            agent: "Planner",
            kind: index < 4 ? "tool" : "turn",
            message: index < 4 ? `Tool detail ${index + 1}` : `Reasoning detail ${index + 1}`,
        }));
        const recentRows: PiLiveLogDetailRow[] = [
            { type: "detail", key: "recent-8", seq: 8, ts: "2026-05-01T09:00:20.000Z", level: "info", agent: "Planner", kind: "tool", message: "Current tool progress" },
            { type: "detail", key: "recent-9", seq: 9, ts: "2026-05-01T09:00:21.000Z", level: "info", agent: "Verifier", kind: "subagent", message: "Verifier running now" },
        ];

        const rows = applyPiLogCompactionExtension([...baseRows, ...recentRows], {
            nowMs: new Date("2026-05-01T09:00:30.000Z").getTime(),
            recentWindowMs: 5000,
            maxRecentRows: 2,
        });

        const rollups = rows.filter((row) => row.type === "rollup");
        const details = rows.filter((row) => row.type === "detail");
        expect(rollups).toHaveLength(2);
        expect(rollups[0]).toMatchObject({ agent: "Planner", detailCount: 4, coveredSeqStart: 1, coveredSeqEnd: 4 });
        expect(rollups[0].message).toBe("Tool detail 4");
        expect(rollups[1].message).toContain("Reasoning detail 5 to Reasoning detail 7");
        expect(details.map((row) => row.key)).toEqual(["recent-8", "recent-9"]);
        expect(details.every((row) => row.ageState === "recent")).toBe(true);
    });

    it("keeps active Pi package config, npm installs, and extension manifests aligned", () => {
        const settings = JSON.parse(readFileSync("../.pi/settings.json", "utf8")) as { packages: string[]; extensions: string[] };
        const mcpConfig = JSON.parse(readFileSync("../.pi/mcp.json", "utf8")) as { mcpServers: Record<string, { type?: string; command?: string; url?: string; args?: string[]; tools?: string[] }> };
        const packageJson = JSON.parse(readFileSync("package.json", "utf8")) as { dependencies: Record<string, string> };
        const contextModeManifest = JSON.parse(readFileSync("node_modules/context-mode/package.json", "utf8")) as { bin: string | Record<string, string>; pi: unknown };
        const babysitterManifest = JSON.parse(readFileSync("node_modules/@a5c-ai/babysitter-pi/package.json", "utf8")) as { bin: string | Record<string, string>; peerDependencies?: Record<string, string>; pi: unknown };

        expect(settings.packages).toEqual(expect.arrayContaining([
            "npm:context-mode@1.0.103",
            "npm:@a5c-ai/babysitter-pi@0.1.3",
        ]));
        expect(settings.extensions).toContain(".pi/extensions/fabric-clawhub-mission-ui.ts");
        expect(settings.extensions).toContain(".pi/extensions/fabric-clawhub-log-compactor.ts");
        expect(settings.extensions).toContain(".pi/extensions/fabric-clawhub-agentic-engineering.ts");
        expect(mcpConfig.mcpServers["context-mode"]).toEqual({
            command: "npx",
            args: ["-y", "context-mode@1.0.103"],
        });
        expect(mcpConfig.mcpServers["microsoft-learn-docs"]).toMatchObject({
            type: "http",
            url: "https://learn.microsoft.com/api/mcp",
            tools: ["microsoft_docs_search", "microsoft_docs_fetch", "microsoft_code_sample_search"],
        });
        expect(mcpConfig.mcpServers["azure-mcp-guidance"]).toMatchObject({
            type: "local",
            command: "npx",
            tools: ["get_azure_bestpractices_get", "get_azure_bestpractices_ai_app"],
        });
        expect(mcpConfig.mcpServers["azure-mcp-guidance"].args).toEqual(expect.arrayContaining([
            "@azure/mcp@latest",
            "get_azure_bestpractices_get",
            "get_azure_bestpractices_ai_app",
            "--read-only",
        ]));
        expect(packageJson.dependencies["context-mode"]).toBe("1.0.103");
        expect(packageJson.dependencies["@a5c-ai/babysitter-pi"]).toBe("0.1.3");
        expect(typeof contextModeManifest.bin === "string" ? contextModeManifest.bin : contextModeManifest.bin["context-mode"]).toBeTruthy();
        expect(typeof babysitterManifest.bin === "string" ? babysitterManifest.bin : babysitterManifest.bin["babysitter-pi"]).toBeTruthy();
        expect(JSON.stringify(contextModeManifest.pi)).toContain("./build/pi-extension.js");
        expect(JSON.stringify(contextModeManifest.pi)).toContain("./skills");
        expect(JSON.stringify(babysitterManifest.pi)).toContain("./extensions");
        expect(JSON.stringify(babysitterManifest.pi)).toContain("./skills");
        expect(babysitterManifest.peerDependencies?.["@mariozechner/pi-coding-agent"]).toBe("*");
        expect(existsSync("node_modules/.bin/context-mode")).toBe(true);
        expect(existsSync("node_modules/.bin/babysitter-pi")).toBe(true);
    });
});