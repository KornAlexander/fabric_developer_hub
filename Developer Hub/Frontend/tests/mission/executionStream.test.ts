import { describe, expect, it } from "vitest";

import { buildExecutionTranscriptRows } from "../../src/components/AgentHub/mission/executionStream";
import { initialMissionState, type LogEntry } from "../../src/components/AgentHub/mission/missionReducer";

function log(seq: number, overrides: Partial<LogEntry>): LogEntry {
    return {
        seq,
        ts: `2026-04-30T12:00:${String(seq).padStart(2, "0")}.000Z`,
        level: "info",
        message: `event ${seq}`,
        logCategory: "detailed",
        kind: "log",
        ...overrides,
    };
}

describe("execution stream presentation", () => {
    it("collapses completed rollup ranges into a receipt row with expandable children", () => {
        const logs = [
            log(1, { kind: "tool_start", message: "Create Fabric item", toolName: "fabric_create_item" }),
            log(2, { kind: "log", message: "metadata validation started", toolName: "fabric_create_item" }),
            log(3, { kind: "tool_end", message: "Finished Create Fabric item", toolName: "fabric_create_item" }),
            log(4, {
                kind: "rollup",
                logCategory: "high_level",
                message: "Created workspace inventory proof.",
                coveredSeqStart: 1,
                coveredSeqEnd: 3,
                detailCount: 3,
                rollupStatus: "completed",
                durationMs: 1200,
                counts: { toolCalls: 1 },
            }),
        ];

        const rows = buildExecutionTranscriptRows(logs, initialMissionState({ jobStatus: "completed", terminalType: "job_complete" }));

        expect(rows).toHaveLength(1);
        expect(rows[0]).toMatchObject({ isReceipt: true, state: "done", hiddenCount: 3 });
        expect(rows[0].children.map((child) => child.seq)).toEqual([1, 2, 3]);
        expect(rows[0].headline).toContain("Created workspace inventory proof");
    });

    it("derives a live animated row from recent active-agent detail events", () => {
        const logs = [
            log(1, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "tool_start", toolName: "fabric_list_items", message: "Start item list" }),
            log(2, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "log", toolName: "fabric_list_items", message: "Fabric List Items · query workspace items started" }),
            log(3, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "log", toolName: "fabric_list_items", message: "Fabric List Items · reading item metadata" }),
            log(4, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "log", logCategory: "diagnostic", toolName: "fabric_list_items", message: "Fabric List Items · diagnostic progress 1" }),
            log(5, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "log", logCategory: "diagnostic", toolName: "fabric_list_items", message: "Fabric List Items · diagnostic progress 2" }),
            log(6, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "log", logCategory: "diagnostic", toolName: "fabric_list_items", message: "Fabric List Items · diagnostic progress 3" }),
            log(7, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "log", logCategory: "diagnostic", toolName: "fabric_list_items", message: "Fabric List Items · diagnostic progress 4" }),
        ];
        const state = initialMissionState({ jobStatus: "running", activeAgentId: "engineer" });

        const rows = buildExecutionTranscriptRows([], state, logs);

        expect(rows[0]).toMatchObject({ isLive: true, state: "running" });
        expect(rows[0].progress.semanticClass).toBe("search-read");
        expect(rows[0].activities).toHaveLength(7);
        expect(rows[0].activities.at(-1)).toMatchObject({ current: true });
        expect(rows[0].hiddenCount).toBe(0);
        expect(rows[0].progress.mode).toBe("tool-use");
    });

    it("keeps only the open substep live and collapses completed prior detail", () => {
        const logs = [
            log(1, { agentId: "engineer", agentName: "FabricDataEngineer", message: "Workspace inventory checkpoint 1" }),
            log(2, { agentId: "engineer", agentName: "FabricDataEngineer", message: "Workspace inventory checkpoint 2" }),
            log(3, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "tool_start", callId: "open-call", toolName: "fabric_list_items", message: "Start item list" }),
            log(4, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "log", callId: "open-call", toolName: "fabric_list_items", message: "query workspace items started" }),
            log(5, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "log", callId: "open-call", toolName: "fabric_list_items", message: "reading item metadata" }),
        ];
        const state = initialMissionState({ jobStatus: "running", activeAgentId: "engineer" });

        const rows = buildExecutionTranscriptRows(logs, state, logs);

        const live = rows.find((row) => row.isLive);
        expect(live).toMatchObject({ isLive: true, state: "running" });
        expect(live?.activities.map((activity) => activity.seq)).toEqual([3, 4, 5]);
        const completed = rows.find((row) => !row.isLive && row.children.some((child) => child.seq === 1));
        expect(completed).toBeTruthy();
        expect(completed?.hiddenCount).toBe(2);
        expect(completed?.children.map((child) => child.seq)).toEqual([1, 2]);
        expect(rows.at(-1)).toBe(live);
    });

    it("derives a live row from running slot progress when runtime logs are empty", () => {
        const state = initialMissionState({
            jobStatus: "running",
            activeAgentId: "engineer",
            slotProgress: {
                engineer: {
                    slotId: "engineer",
                    agentId: "engineer",
                    agentName: "FabricDataEngineer",
                    role: "Inventory synthesis",
                    status: "running",
                    currentStep: "Calling fabric_list_items...",
                },
            },
        });

        const rows = buildExecutionTranscriptRows([], state, []);

        expect(rows[0]).toMatchObject({ isLive: true, state: "running" });
        expect(rows[0].entry.agentName).toBe("FabricDataEngineer");
        expect(rows[0].headline).toContain("Reading workspace inventory");
        expect(rows[0].progress).toMatchObject({ mode: "tool-use", semanticClass: "search-read", statusText: "Reading workspace inventory" });
        expect(rows[0].progress.spinnerMessage).toBe("Inventory synthesis…");
        expect(rows[0].activities.at(-1)).toMatchObject({ current: true, text: "Reading workspace inventory" });
    });

    it("does not reuse the same live row key when a log fuzzy-matches multiple active slots", () => {
        const logs = [
            log(18, {
                agentId: "fabric-data-engineer-run",
                agentName: "FabricDataEngineer",
                kind: "log",
                message: "Working on FabricDataEngineer",
            }),
        ];
        const state = initialMissionState({
            jobStatus: "running",
            activeAgentId: "fabric-data-engineer-run",
            slotProgress: {
                engineer: {
                    slotId: "engineer",
                    agentId: "fabric-data-engineer-run",
                    agentName: "FabricDataEngineer",
                    role: "FabricDataEngineer",
                    status: "running",
                    currentStep: "Working",
                },
                verifier: {
                    slotId: "verifier",
                    agentId: "fabric-verifier-run",
                    agentName: "FabricVerifier",
                    role: "Verify FabricDataEngineer output",
                    status: "waiting",
                    currentStep: "Waiting",
                },
            },
            lastSeq: 18,
        });

        const rows = buildExecutionTranscriptRows(logs, state, logs);
        const keys = rows.map((row) => row.key);

        expect(keys).toHaveLength(new Set(keys).size);
        expect(keys.filter((key) => key === "live-18")).toHaveLength(1);
    });

    it("uses the latest meaningful activity instead of generic collapsed substep copy", () => {
        const logs = [
            log(1, { agentId: "engineer", agentName: "FabricDataEngineer", message: "Workspace inventory checkpoint 1" }),
            log(2, { agentId: "engineer", agentName: "FabricDataEngineer", message: "Created notebook for ingestion with 4 source entities" }),
            log(3, { agentId: "engineer", agentName: "FabricDataEngineer", message: "Pipeline ABC ran successfully in 42 s" }),
        ];
        const rows = buildExecutionTranscriptRows(logs, initialMissionState({ jobStatus: "completed", terminalType: "job_complete" }), logs);

        expect(rows[0].headline).toBe("Pipeline ABC ran successfully in 42 s");
        expect(rows[0].headline).not.toMatch(/Completed substep|activity updates/i);
    });

    it("keeps debug digests and hidden counts out of visible metadata", () => {
        const logs = [
            log(1, { agentId: "engineer", agentName: "FabricDataEngineer", message: "Read workspace metadata" }),
            log(2, { agentId: "engineer", agentName: "FabricDataEngineer", message: "Prepared workspace inventory proof", payloadDigest: "digest-internal-1234", durationMs: 2600, counts: { toolCalls: 1 } }),
        ];

        const rows = buildExecutionTranscriptRows(logs, initialMissionState({ jobStatus: "completed", terminalType: "job_complete" }), logs);

        expect(rows[0].hiddenCount).toBe(2);
        expect(rows[0].meta.join(" ")).toBe("2.6 s");
        expect(rows[0].meta.join(" ")).not.toMatch(/hidden|digest|tool call/i);
    });

    it("preserves explicit LLM spinner phases on live rows", () => {
        const state = initialMissionState({ jobStatus: "running", activeAgentId: "planner" });
        const requesting = buildExecutionTranscriptRows([], state, [
            log(1, {
                agentId: "planner",
                agentName: "MissionPlanner",
                kind: "phase",
                message: "Requesting model response for Plan inventory repair",
                progressMode: "requesting",
                progressSemanticClass: "preparing",
                payloadSummary: { taskDescription: "Plan inventory repair" },
            }),
        ]);
        const thinking = buildExecutionTranscriptRows([], state, [
            log(1, {
                agentId: "planner",
                agentName: "MissionPlanner",
                kind: "decision",
                message: "Thinking: choosing a safe next read",
                progressMode: "thinking",
                progressSemanticClass: "thinking",
            }),
        ]);
        const responding = buildExecutionTranscriptRows([], state, [
            log(1, {
                agentId: "planner",
                agentName: "MissionPlanner",
                kind: "log",
                message: "Streaming assistant response: I will inspect first.",
                progressMode: "responding",
                progressSemanticClass: "thinking",
            }),
        ]);

        expect(requesting[0].progress.mode).toBe("requesting");
        expect(requesting[0].progress.spinnerMessage).toBe("Plan inventory repair…");
        expect(thinking[0].progress.mode).toBe("thinking");
        expect(thinking[0].progress.semanticClass).toBe("thinking");
        expect(responding[0].progress.mode).toBe("responding");
    });

    it("keeps assistant text streams as one growing live transcript row", () => {
        const state = initialMissionState({ jobStatus: "running", activeAgentId: "planner" });
        const rows = buildExecutionTranscriptRows([], state, [
            log(1, {
                agentId: "planner",
                agentName: "MissionPlanner",
                message: "I will inspect the existing dataset before changing it.",
                streamId: "assistant:req-1",
                streamKind: "assistant",
                streamStatus: "streaming",
                streamText: "I will inspect the existing dataset before changing it.",
                progressMode: "responding",
                counts: { tokenCount: 12 },
            }),
        ]);

        expect(rows).toHaveLength(1);
        expect(rows[0]).toMatchObject({ isLive: true, isTextStream: true, streamKind: "assistant" });
        expect(rows[0].streamText).toBe("I will inspect the existing dataset before changing it.");
        expect(rows[0].progress.tokenCount).toBe(12);
        expect(rows[0].progress.mode).toBe("responding");
    });

    it("settles finalized assistant text without burying the response in a collapsed summary", () => {
        const rows = buildExecutionTranscriptRows([
            log(1, {
                agentId: "planner",
                agentName: "MissionPlanner",
                kind: "decision",
                message: "The repair should reuse the existing lakehouse.",
                streamId: "assistant:req-1",
                streamKind: "assistant",
                streamStatus: "finalized",
                streamText: "The repair should reuse the existing lakehouse.",
                progressMode: "responding",
            }),
        ], initialMissionState({ jobStatus: "running", activeAgentId: "planner" }));

        expect(rows).toHaveLength(1);
        expect(rows[0]).toMatchObject({ isLive: false, isTextStream: true, state: "done" });
        expect(rows[0].headline).toBe("Assistant response");
        expect(rows[0].streamText).toBe("The repair should reuse the existing lakehouse.");
        expect(rows[0].hiddenCount).toBe(0);
    });

    it("labels active tool activities with familiar action badges", () => {
        const logs = [
            log(1, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "tool_start", callId: "c1", toolName: "fabric_create_item", message: "Creating Notebook “Inventory”" }),
            log(2, { agentId: "engineer", agentName: "FabricDataEngineer", kind: "log", callId: "c1", toolName: "fabric_list_items", message: "Reading workspace inventory" }),
        ];
        const state = initialMissionState({ jobStatus: "running", activeAgentId: "engineer" });

        const rows = buildExecutionTranscriptRows([], state, logs);

        expect(rows[0].activities.map((activity) => activity.badge)).toEqual(["Create", "Read"]);
        expect(rows[0].activities.map((activity) => activity.category)).toEqual(["write", "read"]);
    });

    it("does not invent an initializing row when there are no logs or active slots", () => {
        const state = initialMissionState({ jobStatus: "running" });

        const rows = buildExecutionTranscriptRows([], state, []);

        expect(rows).toEqual([]);
    });
});
