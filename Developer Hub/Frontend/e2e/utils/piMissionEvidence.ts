import { expect, type Page, type TestInfo } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

import {
    disableMissionAnimations,
    makeSessionRecord,
    missionEvent,
    runOverviewEvent,
    seedMissionAuth,
} from "./missionEvidence";

export const DEFAULT_PI_SESSION_ID = "mission-control-pi-execution-surface";
export const PI_SAMPLE_SESSION_ID = "mission-control-pi-sample-prompt";
export const PI_SAMPLE_WORKSPACE_ID = "pi-sample-workspace";
export const PI_SAMPLE_WORKSPACE_NAME = "AgentHub Pi Sample Workspace";
export const PI_SAMPLE_PROMPT = "Use Pi extensions to inspect the Workspace Inventory report, prepare one safe visual binding diff, ask for approval before writing, and produce verifier evidence.";

const PI_EXTENSION_MISSION_UI = { id: "@fabric-clawhub/pi-mission-ui", label: "@fabric-clawhub/pi-mission-ui" };
const PI_EXTENSION_FABRIC = { id: "@fabric-clawhub/pi-fabric", label: "@fabric-clawhub/pi-fabric" };
const PI_EXTENSION_ASK_USER = { id: "pi-ask-user", label: "pi-ask-user" };
const PI_EXTENSION_SUBAGENTS = { id: "pi-subagents", label: "pi-subagents" };
const PI_EXTENSION_MCP_ADAPTER = { id: "pi-mcp-adapter", label: "pi-mcp-adapter" };
const PI_EXTENSION_LOG_COMPACTOR = { id: "@fabric-clawhub/pi-log-compactor", label: "@fabric-clawhub/pi-log-compactor" };

export interface PiMissionEvidence {
    surfaceText: string;
    nativeMessageListText: string;
    nativeMessageListVisible: boolean;
    liveLogText: string;
    liveLogRowCount: number;
    liveLogMaxSeq: number;
    cliTerminalText: string;
    cliStartupText: string;
    cliTranscriptText: string;
    cliFooterText: string;
    cliLineCount: number;
    cliToolLineCount: number;
    cliToolResultCount: number;
    cliAssistantDeltaCount: number;
    cliEditorVisible: boolean;
    turnCount: number;
    toolCardCount: number;
    approvalCardCount: number;
    clarificationCardCount: number;
    artifactCardCount: number;
    markerCount: number;
    extensionChipCount: number;
    visibleExtensionChipCount: number;
    piWebComponentCount: number;
    piRuntimePackage: string | null;
    piArchitectureLayers: string | null;
    piApplicationLayer: string | null;
    piCoreLayer: string | null;
    piFoundationLayer: string | null;
    piCliPackage: string | null;
    piTuiPackage: string | null;
    piAiPackage: string | null;
    piMcpAdapterPackage: string | null;
    piMcpAccessMode: string | null;
    piContextModePackage: string | null;
    piContextModeStatus: string | null;
    piBabysitterPackage: string | null;
    piBabysitterStatus: string | null;
    piLogCompactionPackage: string | null;
    piLogCompactionStatus: string | null;
    piAgenticEngineeringPackage: string | null;
    piAgenticEngineeringStatus: string | null;
    piRpiProtocol: string | null;
    piQrspiProtocol: string | null;
    piQrspiPhaseModel: string | null;
    piQrspiQuestionPolicy: string | null;
    piQrspiResearchPolicy: string | null;
    piQrspiInstructionBudget: string | null;
    piQrspiVerticalSlicePolicy: string | null;
    piQrspiBacktrackPolicy: string | null;
    piQrspiReviewPolicy: string | null;
    piContextPackSchema: string | null;
    piContextModeFacade: string | null;
    piContextWindowPolicy: string | null;
    piSubagentWorkModel: string | null;
    piLogExecutionStreamingUi: string | null;
    piLogVisualLanguage: string | null;
    piLogCompactionChipVisible: boolean;
    piLogCompactionPolicyText: string;
    piLiveLogMetricsText: string;
    piLiveLogInlineTagCount: number;
    piLiveLogCollapsedCount: number;
    piLiveLogHiddenDetailCount: number;
    piLiveLogHiddenDetailAttribute: number;
    piOrchestrationRuntime: string | null;
    piOrchestrationPackage: string | null;
    piOrchestrationHarness: string | null;
    piBackendToolCount: string | null;
    piToolRegistry: string | null;
    piExtensionSurface: string | null;
    piExecutionStream: string | null;
    piStreamInterface: string | null;
    piExtensionPackages: string | null;
    piSubagentRuntime: string | null;
    piSubagentPackage: string | null;
    piSubagentStream: string | null;
    piObservabilityStream: string | null;
    subagentObservabilityText: string;
    subagentStatusCount: number;
    subagentControlCount: number;
    subagentResultCount: number;
    subagentAsyncCount: number;
    subagentMaxSeq: number;
    proofOpen: boolean;
    secondaryEventsOpen: boolean;
    rawTraceOpen: boolean;
    legacyRowCount: number;
    legacyLogStreamCount: number;
}

export interface PiMissionHarness {
    sessionId: string;
    approvalDecisions: any[];
    sentMessages: any[];
    setup: () => Promise<void>;
    capture: (testInfo: TestInfo, label: string) => Promise<PiMissionEvidence>;
}

export interface PiMissionPromptHarness extends PiMissionHarness {
    samplePrompt: string;
    createBodies: any[];
    runAttempts: () => number;
    startFromPrompt: () => Promise<void>;
}

function sseBody(events: any[]): string {
    return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
}

function piEvent(sessionId: string, seq: number, type: string, extra: Record<string, unknown> = {}) {
    return missionEvent(sessionId, seq, type, {
        schemaVersion: 1,
        logCategory: "high_level",
        ...extra,
    });
}

export function piMissionFixtureEvents(sessionId: string) {
    return [
        runOverviewEvent(sessionId, 1, "running"),
        piEvent(sessionId, 1.5, "pi.orchestration.start", {
            extension: PI_EXTENSION_MISSION_UI,
            runtime: "pi",
            runtimePackage: "@mariozechner/pi-agent-core",
            runtimePackageSource: "npm:@mariozechner/pi-agent-core@0.71.1",
            frontendRuntimePackage: "@mariozechner/pi-web-ui",
            executionSurfaceExtension: "@fabric-clawhub/pi-mission-ui",
            streamTransport: "agenthub-sse-to-pi-extension",
            extensions: [
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
                ".pi/extensions/fabric-clawhub-mission-ui.ts",
                ".pi/extensions/fabric-clawhub-log-compactor.ts",
                ".pi/extensions/fabric-clawhub-agentic-engineering.ts",
            ],
            logCompactionExtension: "@fabric-clawhub/pi-log-compactor",
            logCompactionPolicy: {
                recent_window_ms: 8000,
                refresh_ms: 1500,
                max_recent_rows: 8,
                strategy: "agent-kind-level-contiguous-rollup",
                collapsed_detail_visibility: "details-summary",
            },
            agenticEngineeringExtension: "@fabric-clawhub/pi-agentic-engineering",
            rpiProtocol: "research-plan-implement-context-gates",
            qrspiProtocol: "question-research-design-structure-plan-implement-verify-review",
            qrspiPhaseModel: ["question", "research", "design", "structure", "plan", "worktree", "implement", "verify", "review"],
            qrspiQuestionPolicy: {
                question_first: true,
                neutral_questions_required: true,
                implementation_opinion_allowed: false,
            },
            qrspiResearchPolicy: {
                blind_factual_research: true,
                source_refs_required: true,
                recommendations_deferred_until_design: true,
            },
            qrspiDesignStructurePolicy: {
                design_before_plan: true,
                structure_before_plan: true,
            },
            qrspiInstructionBudget: {
                budget_basis: "instructions-not-only-tokens",
                max_phase_directives: 6,
                no_magic_words_required: true,
                compact_handoff_required: true,
            },
            qrspiVerticalSlicePolicy: {
                strategy: "thin-end-to-end-slice-before-horizontal-layers",
                checkpoint_before_mutation: true,
                slice_evidence_required: true,
            },
            qrspiBacktrackPolicy: {
                allowed: true,
                event: "pi.qrspi.phase.backtrack_requested",
                valid_previous_phases: ["question", "research", "design", "structure", "plan", "worktree", "implement", "verify", "review"],
            },
            qrspiReviewPolicy: {
                plan_review_is_alignment_gate: true,
                code_review_required_before_finish: true,
                review_context: "fresh-design-structure-plan-diff-evidence",
            },
            contextPackSchema: "ContextPackV2",
            subagentWorkModel: "context-window-fork",
            contextWindowPolicy: {
                agent_id_role: "execution-template",
                context_pack_role: "primary-work-unit",
                implementation_context: "approved-plan-plus-selected-snippets",
                verification_context: "fresh-plan-evidence-receipts",
            },
            contextModeFacade: "agenthub-governed-context-mode",
            contextModeEvents: ["pi.context.mode.indexed", "pi.context.mode.retrieved", "pi.context.mode.compacted", "pi.context.mode.rehydrated", "pi.context.mode.savings"],
            contextModeControls: {
                tenant_storage_isolation: true,
                session_storage_isolation: true,
                cross_tenant_search: false,
                secret_bearing_files: false,
                purge_required: true,
            },
            backendBridge: "agenthub-fabric-runtime",
            orchestrationHarness: "pi-agent-core",
            harnessPackage: "npm:@mariozechner/pi-agent-core@0.71.1",
            subagentRuntime: "pi-subagents",
            subagentPackage: "npm:pi-subagents@0.21.3",
            subagentHarness: "pi-subagents delegate/status runtime",
            subagentRuntimeMode: "foreground-status-control-results",
            subagentObservability: {
                event_source: "Agent.subscribe + pi-subagents status/control/result",
                transport: "agenthub-sse-to-pi-web-ui",
                container_callback: "/api/internal/events/emit",
                event_types: ["pi.subagents.status", "pi.subagents.control", "pi.subagents.result", "pi.subagents.async"],
            },
            toolRegistry: "agenthub-tool-runtime",
            toolExecutionBridge: "agenthub-tool-runtime-proxy",
            toolCount: 3,
            emittedToolCount: 3,
            toolPolicySummary: { readSafe: 1, readSensitive: 1, write: 1, destructive: 0, autoAllowed: 2 },
            tools: [
                { name: "fabric_list_workspaces", label: "Fabric List Workspaces", description: "List workspaces the caller can see", sensitivity: "read_safe", autoAllowed: true, execution: "agenthub-tool-runtime-proxy", parameters: { type: "object", additionalProperties: true } },
                { name: "fabric_get_item_definition", label: "Fabric Get Item Definition", description: "Read Fabric item definition through AgentHub policy", sensitivity: "read_sensitive", autoAllowed: true, execution: "agenthub-tool-runtime-proxy", parameters: { type: "object", additionalProperties: true } },
                { name: "fabric_update_item_definition", label: "Fabric Update Item Definition", description: "Write Fabric item definition through AgentHub approval policy", sensitivity: "write", autoAllowed: false, execution: "agenthub-tool-runtime-proxy", parameters: { type: "object", additionalProperties: true } },
            ],
        }),
        piEvent(sessionId, 1.6, "pi.ui.request", {
            extension: PI_EXTENSION_LOG_COMPACTOR,
            requestId: "log-compaction-policy",
            title: "Self-collapsing logs active",
            message: "Recent details stay expanded while older activity rolls up into summaries.",
            control: "status",
            status: "success",
        }),
        piEvent(sessionId, 1.7, "pi.subagents.status", {
            extension: PI_EXTENSION_SUBAGENTS,
            runId: "run-planner",
            agent: "MissionPlanner",
            agentId: "planner",
            agentName: "MissionPlanner",
            mode: "single",
            state: "running",
            activityState: "active_long_running",
            task: "Plan the Pi-backed Fabric inspection",
            summary: "Foreground pi-subagents status stream started inside the container runtime.",
            currentTool: "fabric_list_workspaces",
            currentToolStartedAt: 1777626001000,
            toolCount: 0,
            turnCount: 1,
            durationMs: 180,
            sessionFile: "agenthub://sessions/mission-control-pi-sample-prompt/subagents/run-planner.jsonl",
            progress: [{ agent: "MissionPlanner", status: "running", task: "Plan the Pi-backed Fabric inspection", currentTool: "fabric_list_workspaces", toolCount: 0, turnCount: 1, durationMs: 180 }],
        }),
        piEvent(sessionId, 2, "pi.subagent.update", {
            extension: PI_EXTENSION_SUBAGENTS,
            agentId: "planner",
            agentName: "MissionPlanner",
            role: "Mission planning",
            state: "running",
            task: "Plan the Pi-backed Fabric inspection",
            summary: "Inspecting workspace context before any write operation.",
        }),
        piEvent(sessionId, 3, "pi.turn.start", {
            extension: PI_EXTENSION_MISSION_UI,
            turnId: "turn-plan",
            agentId: "planner",
            agentName: "MissionPlanner",
            model: "gpt-4o-mini",
            title: "Inspect workspace and prepare safe changes",
        }),
        piEvent(sessionId, 4, "pi.turn.delta", {
            extension: PI_EXTENSION_MISSION_UI,
            turnId: "turn-plan",
            textDelta: "I inspected the workspace first, found the Workspace Inventory report, and prepared a narrow repair plan. ",
        }),
        piEvent(sessionId, 5, "pi.tool.start", {
            extension: PI_EXTENSION_FABRIC,
            toolCallId: "tool-read-report",
            turnId: "turn-plan",
            agentId: "planner",
            agentName: "MissionPlanner",
            toolName: "fabric_get_item_definition",
            summary: "Read the current report definition through backend policy.",
            argsSummary: "Report: Workspace Inventory, scope: selected workspace",
            sensitivity: "read-sensitive",
        }),
        piEvent(sessionId, 5.5, "pi.subagents.status", {
            extension: PI_EXTENSION_SUBAGENTS,
            runId: "run-planner",
            agent: "MissionPlanner",
            agentId: "planner",
            agentName: "MissionPlanner",
            mode: "single",
            state: "running",
            activityState: "active_long_running",
            task: "Inspect Workspace Inventory report",
            summary: "Pi subagent is executing fabric_get_item_definition through AgentHub policy.",
            currentTool: "fabric_get_item_definition",
            currentToolStartedAt: 1777626003000,
            toolCount: 1,
            turnCount: 1,
            durationMs: 1420,
            sessionFile: "agenthub://sessions/mission-control-pi-sample-prompt/subagents/run-planner.jsonl",
        }),
        piEvent(sessionId, 6, "pi.tool.end", {
            extension: PI_EXTENSION_FABRIC,
            toolCallId: "tool-read-report",
            turnId: "turn-plan",
            status: "ok",
            durationMs: 640,
            display: {
                summary: "Report definition loaded and summarized for the model.",
                details: "The user-facing preview omits raw PBIR internals and only shows the inspected item summary.",
                trust: { level: "redacted", source: "fabric", redacted: true, summaryOnly: true },
            },
        }),
        piEvent(sessionId, 7, "pi.artifact.upsert", {
            extension: PI_EXTENSION_MISSION_UI,
            artifactId: "artifact-report-evidence",
            turnId: "turn-plan",
            toolCallId: "tool-read-report",
            agentId: "planner",
            kind: "verifier",
            title: "Workspace Inventory verifier evidence",
            summary: "Screenshot and definition evidence are linked for verifier review.",
            previewText: "visualsRendered=true\nreport=Workspace Inventory\nrawTrace=hidden",
            webUrl: "https://app.fabric.microsoft.com/groups/workspace/reports/report-id",
            trust: { level: "trusted", source: "runtime" },
        }),
        piEvent(sessionId, 8, "pi.tool.start", {
            extension: PI_EXTENSION_FABRIC,
            toolCallId: "tool-write-report",
            turnId: "turn-plan",
            agentId: "planner",
            agentName: "MissionPlanner",
            toolName: "fabric_update_item_definition",
            summary: "Prepare the scoped report definition update.",
            argsSummary: "One visual binding diff, no dataset rewrite",
            sensitivity: "write",
        }),
        piEvent(sessionId, 9, "pi.tool.end", {
            extension: PI_EXTENSION_FABRIC,
            toolCallId: "tool-write-report",
            turnId: "turn-plan",
            status: "confirm_required",
            durationMs: 210,
            display: {
                summary: "Backend policy requires explicit approval before applying the write.",
                trust: { level: "trusted", source: "runtime" },
            },
        }),
        piEvent(sessionId, 9.5, "pi.subagents.control", {
            extension: PI_EXTENSION_SUBAGENTS,
            runId: "run-planner",
            agent: "MissionPlanner",
            agentId: "planner",
            agentName: "MissionPlanner",
            controlType: "needs_attention",
            to: "needs_attention",
            message: "Write tool is paused behind AgentHub approval, matching pi-subagents control notifications.",
            reason: "fabric_update_item_definition requires explicit approval",
            currentTool: "fabric_update_item_definition",
            currentToolDurationMs: 210,
            elapsedMs: 2140,
            toolCount: 2,
            turnCount: 1,
            tokens: 915,
        }),
        piEvent(sessionId, 10, "pi.approval.request", {
            extension: PI_EXTENSION_ASK_USER,
            requestId: "approval-write-report",
            turnId: "turn-plan",
            toolCallId: "tool-write-report",
            agentId: "planner",
            title: "Apply report definition update",
            summary: "Approve the single visual binding diff for Workspace Inventory.",
            risk: "medium",
            actionLabel: "Apply diff",
            metadata: { workspace: "AgentHub Progress Lab", item: "Workspace Inventory", scope: "report definition" },
        }),
        piEvent(sessionId, 11, "pi.clarification.request", {
            extension: PI_EXTENSION_ASK_USER,
            requestId: "clarify-report",
            turnId: "turn-plan",
            agentId: "planner",
            title: "Choose report target",
            prompt: "Select the report the verifier should open after the update.",
            control: "select",
            options: [
                { label: "Workspace Inventory", value: "workspace-inventory" },
                { label: "Capacity Summary", value: "capacity-summary" },
            ],
        }),
        piEvent(sessionId, 12, "pi.ui.request", {
            extension: PI_EXTENSION_MISSION_UI,
            requestId: "notify-safe-mode",
            turnId: "turn-plan",
            agentId: "planner",
            title: "Safe mode active",
            message: "Write operations stay blocked until backend approval resolves.",
            control: "notify",
            status: "info",
        }),
        piEvent(sessionId, 13, "pi.ui.request", {
            extension: PI_EXTENSION_MISSION_UI,
            requestId: "status-verifier",
            turnId: "turn-plan",
            agentId: "planner",
            title: "Verifier standing by",
            message: "Verifier lane is waiting for approved report evidence.",
            control: "status",
            status: "warning",
        }),
        piEvent(sessionId, 14, "pi.ui.request", {
            extension: PI_EXTENSION_MISSION_UI,
            requestId: "widget-diff",
            turnId: "turn-plan",
            agentId: "planner",
            title: "Diff preview widget",
            message: "A compact diff preview is available in the artifact card.",
            control: "widget",
            widgetKind: "diff-preview",
        }),
        piEvent(sessionId, 15, "pi.subagent.update", {
            extension: PI_EXTENSION_SUBAGENTS,
            agentId: "run-verifier",
            agentName: "FabricVerifier",
            role: "Evidence verification",
            state: "running",
            task: "Verify report evidence after approval",
            summary: "Waiting for the approved diff to rerun browser evidence checks.",
        }),
        piEvent(sessionId, 15.5, "pi.subagents.async", {
            extension: PI_EXTENSION_SUBAGENTS,
            asyncId: "async-verifier-1",
            runId: "run-verifier",
            state: "running",
            mode: "single",
            agent: "FabricVerifier",
            summary: "Background verifier is streaming status.json and events.jsonl metadata.",
            asyncDir: ".pi/subagents/async-verifier-1",
            outputFile: ".pi/subagents/async-verifier-1/output-0.log",
        }),
        piEvent(sessionId, 15.7, "pi.subagents.status", {
            extension: PI_EXTENSION_SUBAGENTS,
            runId: "run-verifier",
            asyncId: "async-verifier-1",
            agent: "FabricVerifier",
            agentId: "run-verifier",
            agentName: "FabricVerifier",
            mode: "single",
            state: "running",
            activityState: "active_long_running",
            task: "Verify report evidence after approval",
            summary: "Verifier subagent is waiting for approved report evidence and browser proof.",
            currentTool: "browser_screenshot",
            toolCount: 1,
            turnCount: 1,
            durationMs: 3320,
            outputFile: ".pi/subagents/async-verifier-1/output-0.log",
        }),
        piEvent(sessionId, 16, "pi.retry", {
            extension: PI_EXTENSION_MISSION_UI,
            status: "started",
            reason: "Verifier screenshot needed a second capture after layout stabilization.",
        }),
        piEvent(sessionId, 17, "pi.retry", {
            extension: PI_EXTENSION_MISSION_UI,
            status: "completed",
            reason: "Second capture produced readable evidence.",
        }),
        piEvent(sessionId, 18, "pi.context.compaction", {
            extension: PI_EXTENSION_MISSION_UI,
            status: "started",
            summary: "Compacting inspected report evidence and pending approval state.",
        }),
        piEvent(sessionId, 19, "pi.context.compaction", {
            extension: PI_EXTENSION_MISSION_UI,
            status: "completed",
            summary: "Mission objective, selected workspace, pending approval, and verifier evidence were preserved.",
        }),
        piEvent(sessionId, 20, "pi.turn.end", {
            extension: PI_EXTENSION_MISSION_UI,
            turnId: "turn-plan",
            status: "completed",
        }),
        piEvent(sessionId, 21, "pi.subagents.result", {
            extension: PI_EXTENSION_SUBAGENTS,
            runId: "run-planner",
            agent: "MissionPlanner",
            agentId: "planner",
            agentName: "MissionPlanner",
            mode: "single",
            status: "completed",
            summary: "Planner subagent produced a safe diff, approval request, and verifier handoff with Pi-native artifacts.",
            sessionFile: "agenthub://sessions/mission-control-pi-sample-prompt/subagents/run-planner.jsonl",
            artifactPaths: {
                inputPath: "agenthub://sessions/mission-control-pi-sample-prompt/subagents/run-planner_input.md",
                outputPath: "agenthub://sessions/mission-control-pi-sample-prompt/subagents/run-planner_output.md",
                jsonlPath: "agenthub://sessions/mission-control-pi-sample-prompt/subagents/run-planner.jsonl",
                metadataPath: "agenthub://sessions/mission-control-pi-sample-prompt/subagents/run-planner_meta.json",
            },
            usage: { turns: 1, toolCount: 2, durationMs: 4210 },
        }),
    ];
}

export async function setupPiMissionHarness(page: Page, sessionId = DEFAULT_PI_SESSION_ID): Promise<PiMissionHarness> {
    const approvalDecisions: any[] = [];
    const sentMessages: any[] = [];
    const events = piMissionFixtureEvents(sessionId);
    await seedMissionAuth(page);
    await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        const pathName = url.pathname;

        if (method === "GET" && pathName.endsWith(`/api/sessions/${sessionId}/events`)) {
            await route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                headers: { "Cache-Control": "no-cache" },
                body: sseBody(events),
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${sessionId}/events.json`)) {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                json: {
                    sessionId,
                    source: "persisted",
                    liveExecution: true,
                    sessionStatus: "running",
                    persistedTotal: events.length,
                    count: events.length,
                    events,
                },
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${sessionId}`)) {
            await route.fulfill({
                status: 200,
                json: {
                    ...makeSessionRecord(sessionId, "running"),
                    runtime: "pi",
                    context: { workspace_name: "AgentHub Progress Lab", runtime: "pi" },
                },
            });
            return;
        }

        if (method === "POST" && pathName.endsWith(`/api/sessions/${sessionId}/approvals/approval-write-report`)) {
            const body = JSON.parse(request.postData() || "{}");
            approvalDecisions.push(body);
            await route.fulfill({ status: 200, json: { status: "recorded", ...body } });
            return;
        }

        if (method === "POST" && pathName.endsWith(`/api/sessions/${sessionId}/message`)) {
            const body = JSON.parse(request.postData() || "{}");
            sentMessages.push(body);
            await route.fulfill({
                status: 200,
                json: { status: "queued", steeringId: `pi-response-${sentMessages.length}`, targetCount: 1, messagePreview: body.message },
            });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });

    return {
        sessionId,
        approvalDecisions,
        sentMessages,
        setup: async () => {
            await page.goto(`/agent-hub/session/${sessionId}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
            await disableMissionAnimations(page);
            await expect(page.getByRole("log", { name: "Mission stream" })).toBeVisible({ timeout: 30_000 });
        },
        capture: (testInfo, label) => attachPiMissionEvidence(page, testInfo, label),
    };
}

function makePiSampleSession(sessionId: string, prompt: string, status: "planned" | "running" = "running") {
    const base = makeSessionRecord(sessionId, status);
    return {
        ...base,
        task_description: prompt,
        workspace_id: PI_SAMPLE_WORKSPACE_ID,
        runtime: "pi",
        context: {
            workspace_name: PI_SAMPLE_WORKSPACE_NAME,
            runtime: "pi",
            orchestration_runtime: "pi",
            subagent_runtime: "pi-subagents",
            subagent_package: "npm:pi-subagents@0.21.3",
            subagent_observability: "pi-subagents-native-events",
            execution_stream_interface: "pi-extension",
            pi_orchestration: {
                runtime: "pi",
                subagent_runtime: "pi-subagents",
                subagent_package: "npm:pi-subagents@0.21.3",
                subagent_package_name: "pi-subagents",
                subagent_runtime_mode: "foreground-status-control-results",
                subagent_observability: {
                    event_source: "Agent.subscribe + pi-subagents status/control/result",
                    transport: "agenthub-sse-to-pi-web-ui",
                    container_callback: "/api/internal/events/emit",
                    event_types: ["pi.subagents.status", "pi.subagents.control", "pi.subagents.result", "pi.subagents.async"],
                },
                runtime_package: "npm:@mariozechner/pi-agent-core@0.71.1",
                runtime_package_name: "@mariozechner/pi-agent-core",
                foundation_ai_package: "npm:@mariozechner/pi-ai@0.71.1",
                foundation_ai_package_name: "@mariozechner/pi-ai",
                mcp_adapter_package: "npm:pi-mcp-adapter@2.5.2",
                mcp_adapter_package_name: "pi-mcp-adapter",
                mcp_access_mode: "pi-mcp-adapter-proxy-via-agenthub-policy",
                mcp_direct_tools_default: false,
                context_mode_package: "npm:context-mode@1.0.103",
                context_mode_package_name: "context-mode",
                context_mode_facade: "agenthub-governed-context-mode",
                context_mode_events: ["pi.context.mode.indexed", "pi.context.mode.retrieved", "pi.context.mode.compacted", "pi.context.mode.rehydrated", "pi.context.mode.savings"],
                context_mode_controls: {
                    tenant_storage_isolation: true,
                    session_storage_isolation: true,
                    cross_tenant_search: false,
                    secret_bearing_files: false,
                    purge_required: true,
                },
                context_mode_mcp_server: {
                    name: "context-mode",
                    command: "npx",
                    args: ["-y", "context-mode@1.0.103"],
                    config_path: ".pi/mcp.json",
                },
                process_governor_package: "npm:@a5c-ai/babysitter-pi@0.1.3",
                process_governor_package_name: "@a5c-ai/babysitter-pi",
                process_governor: "babysitter-pi",
                frontend_runtime_package: "npm:@mariozechner/pi-web-ui@0.71.1",
                frontend_runtime_package_name: "@mariozechner/pi-web-ui",
                execution_surface_extension: "@fabric-clawhub/pi-mission-ui",
                execution_surface_source: ".pi/extensions/fabric-clawhub-mission-ui.ts",
                agentic_engineering_extension: "@fabric-clawhub/pi-agentic-engineering",
                agentic_engineering_source: ".pi/extensions/fabric-clawhub-agentic-engineering.ts",
                rpi_protocol: "research-plan-implement-context-gates",
                qrspi_protocol: "question-research-design-structure-plan-implement-verify-review",
                qrspi_phase_model: ["question", "research", "design", "structure", "plan", "worktree", "implement", "verify", "review"],
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
                    valid_previous_phases: ["question", "research", "design", "structure", "plan", "worktree", "implement", "verify", "review"],
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
                stream_transport: "agenthub-sse-to-pi-extension",
                orchestrationHarness: "pi-agent-core",
                harnessPackage: "npm:@mariozechner/pi-agent-core@0.71.1",
                toolRegistry: "agenthub-tool-runtime",
                toolExecutionBridge: "agenthub-tool-runtime-proxy",
                toolCount: 3,
                emittedToolCount: 3,
                toolPolicySummary: { readSafe: 1, readSensitive: 1, write: 1, destructive: 0, autoAllowed: 2 },
                tools: [
                    { name: "fabric_list_workspaces", label: "Fabric List Workspaces", description: "List workspaces the caller can see", sensitivity: "read_safe", autoAllowed: true, execution: "agenthub-tool-runtime-proxy", parameters: { type: "object", additionalProperties: true } },
                    { name: "fabric_get_item_definition", label: "Fabric Get Item Definition", description: "Read Fabric item definition through AgentHub policy", sensitivity: "read_sensitive", autoAllowed: true, execution: "agenthub-tool-runtime-proxy", parameters: { type: "object", additionalProperties: true } },
                    { name: "fabric_update_item_definition", label: "Fabric Update Item Definition", description: "Write Fabric item definition through AgentHub approval policy", sensitivity: "write", autoAllowed: false, execution: "agenthub-tool-runtime-proxy", parameters: { type: "object", additionalProperties: true } },
                ],
                extensions: [
                    { source: "npm:@mariozechner/pi-web-ui@0.71.1" },
                    { source: "npm:@mariozechner/pi-ai@0.71.1" },
                    { source: "npm:@mariozechner/pi-agent-core@0.71.1" },
                    { source: "npm:@mariozechner/pi-coding-agent@0.71.1" },
                    { source: "npm:@mariozechner/pi-tui@0.71.1" },
                    { source: "npm:pi-ask-user@0.8.0" },
                    { source: "npm:pi-subagents@0.21.3" },
                    { source: "npm:pi-mcp-adapter@2.5.2" },
                    { source: "npm:context-mode@1.0.103" },
                    { source: "npm:@a5c-ai/babysitter-pi@0.1.3" },
                    { source: ".pi/extensions/fabric-clawhub-mission-ui.ts" },
                    { source: ".pi/extensions/fabric-clawhub-agentic-engineering.ts" },
                ],
                governed_optional_packages: [],
                architecture_layers: [
                    { id: "application", packages: ["npm:@mariozechner/pi-coding-agent@0.71.1", "npm:@mariozechner/pi-web-ui@0.71.1", "npm:@a5c-ai/babysitter-pi@0.1.3", ".pi/extensions/fabric-clawhub-mission-ui.ts", ".pi/extensions/fabric-clawhub-agentic-engineering.ts"] },
                    { id: "core", packages: ["npm:@mariozechner/pi-agent-core@0.71.1"] },
                    { id: "foundation", packages: ["npm:@mariozechner/pi-ai@0.71.1", "npm:@mariozechner/pi-tui@0.71.1", "npm:pi-mcp-adapter@2.5.2", "npm:context-mode@1.0.103"] },
                ],
            },
            piExtensions: [
                PI_EXTENSION_MISSION_UI.id,
                PI_EXTENSION_FABRIC.id,
                PI_EXTENSION_ASK_USER.id,
                PI_EXTENSION_SUBAGENTS.id,
                PI_EXTENSION_MCP_ADAPTER.id,
                "context-mode",
                "@a5c-ai/babysitter-pi",
                "@fabric-clawhub/pi-agentic-engineering",
            ],
            pi_governed_optional_extensions: [],
        },
        composition: {
            ...base.composition,
            task: prompt,
            headline: "Pi extension mission",
        },
    };
}

async function seedPiSampleAuth(page: Page) {
    await page.addInitScript(({ workspaceId }) => {
        window.localStorage.clear();
        window.sessionStorage.clear();
        window.localStorage.setItem("github_token", "e2e-pi-sample-token");
        window.localStorage.setItem("github_user", "pi.sample.e2e");
        window.localStorage.setItem("clawhub.debug", "1");
        window.localStorage.setItem("agenthub_debug_stream", "1");
        window.sessionStorage.setItem("github_token", "e2e-pi-sample-token");
        window.sessionStorage.setItem("github_user", "pi.sample.e2e");
        window.sessionStorage.setItem("workspace_id", workspaceId);
    }, { workspaceId: PI_SAMPLE_WORKSPACE_ID });
}

async function setComposerText(page: Page, text: string) {
    const composer = page.locator("#composer-task-text");
    await composer.click();
    await page.keyboard.press("Control+A");
    await page.keyboard.type(text);
    await expect(composer).toContainText(text);
}

export async function setupPiMissionPromptHarness(
    page: Page,
    sessionId = PI_SAMPLE_SESSION_ID,
    samplePrompt = PI_SAMPLE_PROMPT,
): Promise<PiMissionPromptHarness> {
    const approvalDecisions: any[] = [];
    const sentMessages: any[] = [];
    const createBodies: any[] = [];
    const events = piMissionFixtureEvents(sessionId);
    let runCount = 0;
    let created = false;
    let streamStartedAt = 0;

    const markStreamStarted = () => {
        if (!streamStartedAt) streamStartedAt = Date.now();
    };
    const visibleEvents = () => {
        if (runCount <= 0) return [];
        markStreamStarted();
        const elapsed = Date.now() - streamStartedAt;
        if (elapsed < 3_000) return events.slice(0, 4);
        if (elapsed < 6_000) return events.slice(0, 7);
        return events;
    };

    await seedPiSampleAuth(page);
    await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        const pathName = url.pathname;

        if (method === "GET" && pathName.endsWith("/api/orchestrate/compose-models")) {
            await route.fulfill({
                status: 200,
                json: {
                    default: "gpt-4.1-enterprise",
                    models: [{
                        id: "gpt-4.1-enterprise",
                        name: "GPT-4.1 Enterprise",
                        publisher: "GitHub Copilot",
                        tier: 1,
                        recommended: true,
                        top_pick: true,
                        reason: "Best default for Pi extension missions.",
                        latency: "fast",
                    }],
                },
            });
            return;
        }

        if (method === "POST" && pathName.endsWith("/api/workspaces/preload")) {
            await route.fulfill({ status: 200, json: { ok: true } });
            return;
        }

        if (method === "GET" && pathName.endsWith("/api/workspaces")) {
            await route.fulfill({
                status: 200,
                json: {
                    workspaces: [{
                        id: PI_SAMPLE_WORKSPACE_ID,
                        name: PI_SAMPLE_WORKSPACE_NAME,
                        git_connected: true,
                        git_provider: "GitHub",
                        git_branch: "main",
                        git_repo_name: "pi-sample-workspace",
                    }],
                    cached_at: "2026-05-01T09:00:00.000Z",
                    source: "e2e",
                },
            });
            return;
        }

        if (method === "GET" && /\/api\/workspaces\/[^/]+\/items$/.test(pathName)) {
            await route.fulfill({
                status: 200,
                json: {
                    items: [
                        { id: "workspace-inventory-report", name: "Workspace Inventory", type: "Report", owner: "BI Team", webUrl: "https://fabric.example/items/workspace-inventory-report" },
                        { id: "workspace-inventory-model", name: "Workspace Inventory Model", type: "SemanticModel", owner: "BI Team", webUrl: "https://fabric.example/items/workspace-inventory-model" },
                    ],
                    captured_at: "2026-05-01T09:00:01.000Z",
                },
            });
            return;
        }

        if (method === "POST" && pathName.endsWith("/api/github/suggest-branch-names")) {
            await route.fulfill({ status: 200, json: { branch_name: "feature/pi-sample", workspace_name: "Pi Sample" } });
            return;
        }

        if (method === "GET" && pathName.endsWith("/api/catalogs/architectures")) {
            await route.fulfill({ status: 200, json: [{ id: "dynamic", name: "Dynamic mission" }] });
            return;
        }

        if (method === "GET" && pathName.endsWith("/api/agents")) {
            await route.fulfill({ status: 200, json: [] });
            return;
        }

        if (method === "GET" && pathName.endsWith("/api/sessions/summary")) {
            await route.fulfill({ status: 200, json: { total: created ? 1 : 0, active_total: created ? 1 : 0, history_total: 0, running: runCount > 0 ? 1 : 0, waiting: 0, failed: 0, completed: 0, cancelled: 0, other_active: 0, by_status: {} } });
            return;
        }

        if (method === "GET" && pathName.endsWith("/api/sessions")) {
            await route.fulfill({ status: 200, json: created ? [makePiSampleSession(sessionId, samplePrompt, runCount > 0 ? "running" : "planned")] : [] });
            return;
        }

        if (method === "POST" && pathName.endsWith("/api/sessions")) {
            const body = await request.postDataJSON();
            createBodies.push(body);
            created = true;
            await route.fulfill({ status: 200, json: makePiSampleSession(sessionId, body.task_description || samplePrompt, "planned") });
            return;
        }

        if (method === "POST" && pathName.endsWith(`/api/sessions/${sessionId}/run`)) {
            runCount += 1;
            await route.fulfill({ status: 200, json: { ok: true, runtime: "pi" } });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${sessionId}/events`)) {
            markStreamStarted();
            await route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                headers: { "Cache-Control": "no-cache" },
                body: sseBody(visibleEvents()),
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${sessionId}/events.json`)) {
            const stagedEvents = visibleEvents();
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                json: { sessionId, source: "persisted", liveExecution: true, sessionStatus: "running", persistedTotal: stagedEvents.length, count: stagedEvents.length, events: stagedEvents },
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${sessionId}`)) {
            await route.fulfill({ status: 200, json: makePiSampleSession(sessionId, samplePrompt, runCount > 0 ? "running" : "planned") });
            return;
        }

        if (method === "POST" && pathName.endsWith(`/api/sessions/${sessionId}/approvals/approval-write-report`)) {
            const body = JSON.parse(request.postData() || "{}");
            approvalDecisions.push(body);
            await route.fulfill({ status: 200, json: { status: "recorded", ...body } });
            return;
        }

        if (method === "POST" && pathName.endsWith(`/api/sessions/${sessionId}/message`)) {
            const body = JSON.parse(request.postData() || "{}");
            sentMessages.push(body);
            await route.fulfill({
                status: 200,
                json: { status: "queued", steeringId: `pi-sample-response-${sentMessages.length}`, targetCount: 1, messagePreview: body.message },
            });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });

    return {
        sessionId,
        samplePrompt,
        createBodies,
        approvalDecisions,
        sentMessages,
        runAttempts: () => runCount,
        setup: async () => {
            await page.goto(`/agent-hub/session/${sessionId}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
            await disableMissionAnimations(page);
            await expect(page.getByRole("log", { name: "Mission stream" })).toBeVisible({ timeout: 30_000 });
        },
        startFromPrompt: async () => {
            await page.goto(`/agent-hub/orchestrator?agenthubE2E=1&ws=${PI_SAMPLE_WORKSPACE_ID}`, { waitUntil: "domcontentloaded" });
            await disableMissionAnimations(page);
            await expect(page.locator("#composer-task-text")).toBeVisible({ timeout: 30_000 });
            await expect(page.getByText(PI_SAMPLE_WORKSPACE_NAME, { exact: true })).toBeVisible({ timeout: 30_000 });
            await setComposerText(page, samplePrompt);
            const startButton = page.getByRole("button", { name: /^Start mission$/i });
            await expect(startButton).toBeEnabled({ timeout: 10_000 });
            await startButton.click({ force: true });
            await expect.poll(() => createBodies.length, { timeout: 30_000 }).toBe(1);
            await expect.poll(() => runCount, { timeout: 30_000 }).toBe(1);
            await expect(page.getByRole("log", { name: "Mission stream" })).toBeVisible({ timeout: 30_000 });
        },
        capture: (testInfo, label) => attachPiMissionEvidence(page, testInfo, label),
    };
}

export async function readPiMissionEvidence(page: Page): Promise<PiMissionEvidence> {
    return page.evaluate(() => {
        const normalizedText = (value: string | null | undefined) => (value || "").replace(/\s+/g, " ").trim();
        const text = (selector: string) => normalizedText(document.querySelector(selector)?.textContent).slice(0, 8000);
        const isVisible = (selector: string) => {
            const node = document.querySelector<HTMLElement>(selector);
            return !!node && !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
        };
        const liveLogRows = Array.from(document.querySelectorAll<HTMLElement>('[data-pi-live-log-row="true"]'));
        const liveLogSeqs = liveLogRows
            .map((node) => Number(node.dataset.piLogSeq || node.getAttribute('data-pi-log-seq') || 0))
            .filter((seq) => Number.isFinite(seq));
        const liveLogRoot = document.querySelector<HTMLElement>('[data-pi-live-log="true"]');
        const root = document.querySelector('.pi-mission-surface');
        const subagentsPanel = document.querySelector<HTMLElement>('[data-pi-subagents-observability="true"]');
        const subagentRows = Array.from(document.querySelectorAll<HTMLElement>('[data-pi-subagents-status-row="true"], [data-pi-subagents-control-row="true"], [data-pi-subagents-result-row="true"], [data-pi-subagents-async-row="true"]'));
        const subagentSeqs = subagentRows
            .map((node) => Number(node.dataset.piSeq || node.getAttribute('data-pi-seq') || 0))
            .filter((seq) => Number.isFinite(seq));
        return {
            surfaceText: text('[aria-label="Mission stream"]'),
            nativeMessageListText: text('message-list'),
            nativeMessageListVisible: isVisible('message-list'),
            liveLogText: text('[data-pi-live-log="true"]'),
            liveLogRowCount: liveLogRows.length,
            liveLogMaxSeq: liveLogSeqs.length ? Math.max(...liveLogSeqs) : 0,
            cliTerminalText: text('[aria-label="Embedded mission terminal"]'),
            cliStartupText: text('[aria-label="Mission startup header"]'),
            cliTranscriptText: text('[aria-label="Mission transcript"]'),
            cliFooterText: text('[aria-label="Mission footer"]'),
            cliLineCount: document.querySelectorAll('[data-pi-cli-line="true"]').length,
            cliToolLineCount: document.querySelectorAll('[data-pi-kind="tool-card"]').length,
            cliToolResultCount: document.querySelectorAll('[data-pi-kind="tool-result"]').length,
            cliAssistantDeltaCount: document.querySelectorAll('[data-pi-kind="assistant-turn"][data-pi-cli-event-type="pi.turn.delta"]').length,
            cliEditorVisible: !!document.querySelector('[data-pi-cli-editor="true"]'),
            turnCount: document.querySelectorAll('[data-pi-kind="assistant-turn"]').length,
            toolCardCount: document.querySelectorAll('[data-pi-kind="tool-card"]').length,
            approvalCardCount: document.querySelectorAll('[data-pi-kind="approval-card"]').length,
            clarificationCardCount: document.querySelectorAll('[data-pi-kind="clarification-card"]').length,
            artifactCardCount: document.querySelectorAll('[data-pi-kind="artifact-card"]').length,
            markerCount: document.querySelectorAll('[data-pi-kind="retry-marker"], [data-pi-kind="compaction-marker"], [data-pi-kind="ui-marker"]').length,
            extensionChipCount: document.querySelectorAll('.pi-extension-chip').length,
            visibleExtensionChipCount: Array.from(document.querySelectorAll<HTMLElement>('.pi-extension-chip')).filter((node) => !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length)).length,
            piWebComponentCount: document.querySelectorAll('pi-chat-panel[data-pi-web-component="pi-chat-panel"], agent-interface[data-pi-runtime="@mariozechner/pi-web-ui"]').length,
            piRuntimePackage: root?.getAttribute('data-pi-runtime-package') || null,
            piArchitectureLayers: root?.getAttribute('data-pi-architecture-layers') || null,
            piApplicationLayer: root?.getAttribute('data-pi-application-layer') || null,
            piCoreLayer: root?.getAttribute('data-pi-core-layer') || null,
            piFoundationLayer: root?.getAttribute('data-pi-foundation-layer') || null,
            piCliPackage: root?.getAttribute('data-pi-cli-package') || null,
            piTuiPackage: root?.getAttribute('data-pi-tui-package') || null,
            piAiPackage: root?.getAttribute('data-pi-ai-package') || null,
            piMcpAdapterPackage: root?.getAttribute('data-pi-mcp-adapter-package') || null,
            piMcpAccessMode: root?.getAttribute('data-pi-mcp-access-mode') || null,
            piContextModePackage: root?.getAttribute('data-pi-context-mode-package') || null,
            piContextModeStatus: root?.getAttribute('data-pi-context-mode-status') || null,
            piBabysitterPackage: root?.getAttribute('data-pi-babysitter-package') || null,
            piBabysitterStatus: root?.getAttribute('data-pi-babysitter-status') || null,
            piLogCompactionPackage: root?.getAttribute('data-pi-log-compaction-package') || null,
            piLogCompactionStatus: root?.getAttribute('data-pi-log-compaction-status') || null,
            piAgenticEngineeringPackage: root?.getAttribute('data-pi-agentic-engineering-package') || null,
            piAgenticEngineeringStatus: root?.getAttribute('data-pi-agentic-engineering-status') || null,
            piRpiProtocol: root?.getAttribute('data-pi-rpi-protocol') || null,
            piQrspiProtocol: root?.getAttribute('data-pi-qrspi-protocol') || null,
            piQrspiPhaseModel: root?.getAttribute('data-pi-qrspi-phase-model') || null,
            piQrspiQuestionPolicy: root?.getAttribute('data-pi-qrspi-question-policy') || null,
            piQrspiResearchPolicy: root?.getAttribute('data-pi-qrspi-research-policy') || null,
            piQrspiInstructionBudget: root?.getAttribute('data-pi-qrspi-instruction-budget') || null,
            piQrspiVerticalSlicePolicy: root?.getAttribute('data-pi-qrspi-vertical-slice-policy') || null,
            piQrspiBacktrackPolicy: root?.getAttribute('data-pi-qrspi-backtrack-policy') || null,
            piQrspiReviewPolicy: root?.getAttribute('data-pi-qrspi-review-policy') || null,
            piContextPackSchema: root?.getAttribute('data-pi-context-pack-schema') || null,
            piContextModeFacade: root?.getAttribute('data-pi-context-mode-facade') || null,
            piContextWindowPolicy: root?.getAttribute('data-pi-context-window-policy') || null,
            piSubagentWorkModel: root?.getAttribute('data-pi-subagent-work-model') || null,
            piLogExecutionStreamingUi: liveLogRoot?.getAttribute('data-pi-log-execution-streaming-ui') || null,
            piLogVisualLanguage: liveLogRoot?.getAttribute('data-pi-log-visual-language') || null,
            piLogCompactionChipVisible: isVisible('[data-pi-log-compaction-chip="true"]'),
            piLogCompactionPolicyText: text('[data-pi-log-compaction-policy="true"]'),
            piLiveLogMetricsText: text('[aria-label="Mission activity metrics"]'),
            piLiveLogInlineTagCount: document.querySelectorAll('[data-pi-log-inline-tags="true"]').length,
            piLiveLogCollapsedCount: document.querySelectorAll('[data-pi-live-log-rollup="true"]').length,
            piLiveLogHiddenDetailCount: document.querySelectorAll('[data-pi-live-log-detail-row="true"]').length,
            piLiveLogHiddenDetailAttribute: Number(liveLogRoot?.getAttribute('data-pi-live-log-hidden-detail-count') || 0),
            piOrchestrationRuntime: root?.getAttribute('data-pi-orchestration-runtime') || null,
            piOrchestrationPackage: root?.getAttribute('data-pi-orchestration-package') || null,
            piOrchestrationHarness: root?.getAttribute('data-pi-orchestration-harness') || null,
            piBackendToolCount: root?.getAttribute('data-pi-backend-tool-count') || null,
            piToolRegistry: root?.getAttribute('data-pi-tool-registry') || null,
            piExtensionSurface: root?.getAttribute('data-pi-extension-surface') || null,
            piExecutionStream: root?.getAttribute('data-pi-execution-stream') || null,
            piStreamInterface: root?.getAttribute('data-pi-stream-interface') || null,
            piExtensionPackages: root?.getAttribute('data-pi-extension-packages') || null,
            piSubagentRuntime: root?.getAttribute('data-pi-subagent-runtime') || null,
            piSubagentPackage: root?.getAttribute('data-pi-subagent-package') || null,
            piSubagentStream: root?.getAttribute('data-pi-subagent-stream') || null,
            piObservabilityStream: root?.getAttribute('data-pi-observability-stream') || null,
            subagentObservabilityText: text('[data-pi-subagents-observability="true"]'),
            subagentStatusCount: Number(subagentsPanel?.getAttribute('data-pi-subagents-status-count') || document.querySelectorAll('[data-pi-subagents-status-row="true"]').length),
            subagentControlCount: Number(subagentsPanel?.getAttribute('data-pi-subagents-control-count') || document.querySelectorAll('[data-pi-subagents-control-row="true"]').length),
            subagentResultCount: Number(subagentsPanel?.getAttribute('data-pi-subagents-result-count') || document.querySelectorAll('[data-pi-subagents-result-row="true"]').length),
            subagentAsyncCount: document.querySelectorAll('[data-pi-subagents-async-row="true"]').length,
            subagentMaxSeq: subagentSeqs.length ? Math.max(...subagentSeqs) : 0,
            proofOpen: !!document.querySelector('.pi-proof-drawer[open]'),
            secondaryEventsOpen: !!document.querySelector('.pi-secondary-events[open]'),
            rawTraceOpen: !!document.querySelector('.pi-trace-drawer[open]'),
            legacyRowCount: document.querySelectorAll('.mc3-transcript-row').length,
            legacyLogStreamCount: document.querySelectorAll('.canvas-log-stream').length,
        };
    });
}

export async function attachPiMissionEvidence(page: Page, testInfo: TestInfo, label: string): Promise<PiMissionEvidence> {
    const evidence = await readPiMissionEvidence(page);
    await testInfo.attach(`${label}.json`, {
        body: JSON.stringify(evidence, null, 2),
        contentType: "application/json",
    });
    await testInfo.attach(`${label}.png`, {
        body: await page.screenshot({ fullPage: true, animations: "disabled" }),
        contentType: "image/png",
    });
    return evidence;
}

export async function attachPiLiveLogScreenshot(page: Page, testInfo: TestInfo, label: string): Promise<void> {
    const liveLog = page.locator(".pi-runtime-host__live-log-shell").first();
    await expect(liveLog).toBeVisible({ timeout: 30_000 });
    const body = await liveLog.screenshot({ animations: "disabled" });
    const evidenceDir = path.join(process.cwd(), "test-results", "pi-log-execution-stream");
    await fs.mkdir(evidenceDir, { recursive: true });
    await fs.writeFile(path.join(evidenceDir, `${label}.png`), body);
    await testInfo.attach(`${label}.png`, {
        body,
        contentType: "image/png",
    });
}