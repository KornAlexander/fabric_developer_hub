/**
 * Unit tests for the mission-control reducer. Pure function coverage
 * over every event variant; no React, no SSE.
 */

import { describe, it, expect } from "vitest";
import { missionReducer, initialMissionState } from "../../src/components/AgentHub/mission/missionReducer";
import type { MissionEvent } from "../../src/components/AgentHub/mission/events";

function mk<T extends MissionEvent["type"]>(
    seq: number, type: T, extra: object = {},
): MissionEvent {
    return {
        type,
        seq,
        sessionId: "sess-1",
        ts: new Date(2024, 0, 1, 0, 0, seq).toISOString(),
        ...extra,
    } as MissionEvent;
}

describe("missionReducer", () => {
    it("starts from an empty state", () => {
        const s = initialMissionState();
        expect(s.jobStatus).toBe("planned");
        expect(s.lastSeq).toBe(0);
        expect(s.logs).toEqual([]);
    });

    it("dedupes events with an already-seen seq", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "log_line", { level: "info", message: "a" }));
        expect(s.logs).toHaveLength(1);
        // Replay of the same seq — must be a no-op.
        s = missionReducer(s, mk(1, "log_line", { level: "info", message: "a" }));
        expect(s.logs).toHaveLength(1);
    });

    it("advances lastSeq monotonically", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "log_line", { level: "info", message: "a" }));
        s = missionReducer(s, mk(3, "log_line", { level: "info", message: "c" }));
        s = missionReducer(s, mk(2, "log_line", { level: "info", message: "b" })); // out-of-order: dropped (≤ lastSeq)
        expect(s.logs.map(l => l.message)).toEqual(["a", "c"]);
        expect(s.lastSeq).toBe(3);
    });

    it("ignores heartbeats without affecting lastSeq", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "log_line", { level: "info", message: "a" }));
        s = missionReducer(s, { type: "heartbeat" } as MissionEvent);
        expect(s.lastSeq).toBe(1);
        expect(s.logs).toHaveLength(1);
    });

    it("tracks activeAgentId from slot_progress and agent_status", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "slot_progress", {
            slotId: "s1", agentId: "s1", status: "running", activeAgentId: "s1",
        }));
        expect(s.activeAgentId).toBe("s1");
        s = missionReducer(s, mk(2, "agent_status", {
            agentId: "s2", status: "running", currentStep: "working",
        }));
        expect(s.activeAgentId).toBe("s2");
    });

    it("records artifacts preserving insertion order", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "artifact_added", {
            artifactId: "a1", agentId: "s1", kind: "Lakehouse",
            name: "Bronze", state: "written",
        }));
        s = missionReducer(s, mk(2, "artifact_added", {
            artifactId: "a2", agentId: "s1", kind: "Warehouse",
            name: "Silver", state: "draft",
        }));
        expect(s.artifactOrder).toEqual(["a1", "a2"]);
        expect(s.artifacts["a2"].name).toBe("Silver");
    });

    it("updates artifacts without reordering them", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "artifact_added", {
            artifactId: "a1", agentId: "s1", kind: "Lakehouse",
            name: "Bronze", state: "draft",
        }));
        s = missionReducer(s, mk(2, "artifact_updated", {
            artifactId: "a1", state: "written", webUrl: "https://x",
        }));
        expect(s.artifacts["a1"].state).toBe("written");
        expect(s.artifactOrder).toEqual(["a1"]);
    });

    it("records pending approvals and marks them resolved", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "approval_required", {
            approvalId: "ap-1", slotId: "s1", agentId: "s1",
            summary: "Destructive tool call",
            blastRadius: "workspace", reversible: false,
            toolCallPreview: { name: "fabric_delete_item", args: { item_id: "x" } },
            recoveryActions: ["approve", "decline"],
        }));
        expect(s.approvals["ap-1"].summary).toBe("Destructive tool call");
        expect(s.approvals["ap-1"].resolved).toBeFalsy();
        s = missionReducer(s, mk(2, "approval.resolved", {
            approvalId: "ap-1", action: "decline",
        }));
        expect(s.approvals["ap-1"].resolved).toBe(true);
        expect(s.approvals["ap-1"].resolvedAction).toBe("decline");
    });

    it("captures terminal state on job_complete/job_cancelled", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "job_cancelled", {
            jobId: "j1", status: "cancelled", totalDuration: "3s",
        }));
        expect(s.jobStatus).toBe("cancelled");
        expect(s.terminalType).toBe("job_cancelled");
        expect(s.totalDuration).toBe("3s");
    });

    it("merges run_overview into state", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "run_overview", {
            job: { id: "j1", status: "running", startedAt: null, completedAt: null },
            composition: null,
            activeAgentId: "s1",
            artifacts: [{ artifactId: "a1", agentId: "s1", kind: "X", name: "x", state: "written" }],
            changes: [{
                recordId: "c1",
                kind: "created",
                status: "applied",
                targetName: "Bronze",
                targetType: "Lakehouse",
                targetScope: "item",
                summary: "Created Lakehouse Bronze.",
                toolName: "fabric_create_item",
                agentId: "s1",
                agentName: "Fabric Data Engineer",
                ts: "2024-01-01T00:00:00.000Z",
            }],
            slotProgress: [{ slotId: "s1", agentId: "s1", status: "running" }],
        }));
        expect(s.jobStatus).toBe("running");
        expect(s.activeAgentId).toBe("s1");
        expect(s.artifactOrder).toEqual(["a1"]);
        expect(s.changeOrder).toEqual(["c1"]);
        expect(s.changes["c1"].targetName).toBe("Bronze");
        expect(s.slotProgress["s1"].status).toBe("running");
    });

    it("records live change overview events without reordering duplicates", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "change_recorded", {
            recordId: "c1",
            kind: "updated",
            status: "applied",
            targetName: "Files/model.json",
            targetType: "File",
            targetScope: "file",
            summary: "Updated File Files/model.json.",
            toolName: "fabric_write_file",
            agentId: "s1",
            agentName: "Fabric Data Engineer",
        }));
        s = missionReducer(s, mk(2, "run_overview", {
            job: { id: "j1", status: "running", startedAt: null, completedAt: null },
            composition: null,
            activeAgentId: "s1",
            artifacts: [],
            changes: [{
                recordId: "c1",
                kind: "updated",
                status: "applied",
                targetName: "Files/model.json",
                targetType: "File",
                targetScope: "file",
                summary: "Updated File Files/model.json.",
                toolName: "fabric_write_file",
                agentId: "s1",
                agentName: "Fabric Data Engineer",
                ts: "2024-01-01T00:00:02.000Z",
            }],
            slotProgress: [],
        }));

        expect(s.changeOrder).toEqual(["c1"]);
        expect(s.changes["c1"].kind).toBe("updated");
    });

    it("logs dynamic orchestration events", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "mission_seeded", { taskCount: 2 }));
        s = missionReducer(s, mk(2, "task_created", {
            task: { id: "discover", title: "Discover workspace", objective: "Inspect items" },
        }));
        s = missionReducer(s, mk(3, "orchestrator_decision", {
            decision: {
                id: "d1",
                type: "spawn_subagent",
                rationale: "Workspace discovery can start immediately.",
            },
        }));
        s = missionReducer(s, mk(4, "subagent_spawned", {
            run: { id: "run-1", taskId: "discover", agentId: "fabric-admin", agentSessionId: "agent-1" },
            task: { id: "discover", title: "Discover workspace" },
        }));
        s = missionReducer(s, mk(5, "subagent_result", {
            runId: "run-1",
            taskId: "discover",
            result: { id: "result-1", status: "success", summary: "Workspace inventory collected." },
        }));

        expect(s.activeAgentId).toBe("agent-1");
        expect(s.slotProgress["agent-1"].status).toBe("running");
        expect(s.logs.map((l) => l.message)).toEqual([
            "Mission seeded with 2 tasks",
            "Task queued: Discover workspace",
            "Orchestrator: Workspace discovery can start immediately.",
            "Subagent started: Discover workspace",
            "Task result: Workspace inventory collected.",
        ]);
    });

    it("stores explicit public log categories", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "mission_seeded", { taskCount: 1, logCategory: "high_level" }));
        s = missionReducer(s, mk(2, "log_line", {
            level: "info", message: "Working through details", logCategory: "detailed",
        }));
        s = missionReducer(s, mk(3, "tool_call_started", {
            agentId: "s1", callId: "c1", toolName: "fabric_list_items", logCategory: "diagnostic",
        }));

        expect(s.logs.map((log) => log.logCategory)).toEqual(["high_level", "detailed", "diagnostic"]);
    });

    it("drops trace events before they become visible logs", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "log_line", {
            level: "info", message: "Internal trace detail", logCategory: "trace",
        }));

        expect(s.lastSeq).toBe(1);
        expect(s.logs).toEqual([]);
    });

    it("reconstructs generalist delegation, monitoring, intervention, and verification decisions", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "generalist_check_in", {
            logCategory: "high_level",
            readyTaskCount: 2,
            runningSubagentCount: 0,
            completedTaskCount: 0,
            readyTaskIds: ["build", "verify"],
        }));
        s = missionReducer(s, mk(2, "generalist_context_pack", {
            logCategory: "high_level",
            runId: "run-build",
            taskId: "build",
            agentId: "builder",
            agentName: "Builder",
            taskTitle: "Build report",
            contextDigest: "ctx-123",
            toolScopeCount: 3,
        }));
        s = missionReducer(s, mk(3, "generalist_direct_work", {
            logCategory: "high_level",
            runId: "run-generalist",
            taskId: "triage",
            agentId: "generalist",
            taskTitle: "Review verification feedback",
            reason: "Generalist chose to handle routing directly.",
            toolScopeCount: 4,
        }));
        s = missionReducer(s, mk(4, "generalist_state_decision", {
            logCategory: "high_level",
            runId: "run-build",
            taskId: "build",
            agentId: "builder",
            resultStatus: "success",
            taskStatus: "completed",
            summary: "Report artifact created and ready for verification.",
            followupTaskCount: 1,
        }));
        s = missionReducer(s, mk(5, "generalist_steering", {
            logCategory: "high_level",
            runId: "run-verify",
            taskId: "verify",
            agentId: "fabric-verifier",
            agentName: "Fabric Verifier",
            reason: "Verifier needs the repaired report id from the builder result.",
        }));
        s = missionReducer(s, mk(6, "subagent_abandoned", {
            logCategory: "high_level",
            runId: "run-stuck",
            taskId: "repair",
            replacementTaskId: "repair-retry-1",
            reason: "Repeated tool loop continued after steering.",
        }));
        s = missionReducer(s, mk(7, "task_failed", {
            logCategory: "high_level",
            taskId: "verify",
            reason: "verification feedback loop exceeded no-progress limit",
            message: "Fabric verification is not converging after repeated repair attempts.",
        }));

        expect(s.logs.map((log) => log.message)).toEqual([
            "Generalist checkpoint: 2 ready, 0 running, 0 complete",
            "Delegated structured context to Builder for Build report",
            "Generalist handled directly: Review verification feedback - Generalist chose to handle routing directly.",
            "Generalist integrated feedback: Report artifact created and ready for verification.",
            "Generalist steered Fabric Verifier: Verifier needs the repaired report id from the builder result.",
            "Subagent reassigned to repair-retry-1: Repeated tool loop continued after steering.",
            "Task failed: Fabric verification is not converging after repeated repair attempts.",
        ]);
        expect(s.logs.every((log) => log.logCategory === "high_level")).toBe(true);
        expect(s.logs.map((log) => log.kind)).toEqual(["decision", "decision", "decision", "decision", "decision", "decision", "error"]);
    });

    it("bounds the log list", () => {
        let s = initialMissionState();
        for (let i = 1; i <= 2200; i++) {
            s = missionReducer(s, mk(i, "log_line", { level: "info", message: `m${i}` }));
        }
        expect(s.logs.length).toBeLessThanOrEqual(2000);
        // The latest message must still be present.
        expect(s.logs[s.logs.length - 1].message).toBe("m2200");
    });

    it("logs tool_call_started / tool_call_ended with expected kinds", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "tool_call_started", {
            agentId: "s1", callId: "c1", toolName: "fabric_list_items", argsPreview: {},
        }));
        s = missionReducer(s, mk(2, "tool_call_ended", {
            agentId: "s1", callId: "c1", toolName: "fabric_list_items",
            durationMs: 120, status: "ok",
        }));
        expect(s.logs[0].kind).toBe("tool_start");
        expect(s.logs[1].kind).toBe("tool_end");
        expect(s.logs[1].durationMs).toBe(120);
    });

    it("presents tool activity in enterprise-readable language", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "tool_call_started", {
            agentId: "s1",
            agentName: "Fabric Data Engineer",
            callId: "c1",
            toolName: "fabric_create_item",
            argsPreview: {
                item_type: "Notebook",
                display_name: "Quarterly Inventory Review",
            },
        }));
        s = missionReducer(s, mk(2, "tool_call_ended", {
            agentId: "s1",
            callId: "c1",
            toolName: "fabric_create_item",
            durationMs: 1480,
            status: "ok",
        }));

        expect(s.logs[0].message).toBe("Creating Notebook “Quarterly Inventory Review”");
        expect(s.logs[1].message).toBe("Completed: create fabric item · 1.5 s");
        expect(s.logs.map((l) => l.message).join("\n")).not.toMatch(/fabric_create_item|→|←/);
    });

    it("presents backend status lines without internal trace tokens", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "agent_status", {
            agentId: "s1",
            agentName: "Fabric Data Engineer",
            status: "running",
            currentStep: "Calling fabric_create_item...",
        }));
        s = missionReducer(s, mk(2, "log_line", {
            level: "info",
            message: "workflow state: WorkflowRunState.IDLE",
        }));
        s = missionReducer(s, mk(3, "agent_error", {
            agentId: "s1",
            error: "Major issue detected while calling fabric_create_item: TOOL_ERROR → undefined",
        }));
        s = missionReducer(s, mk(4, "action", {
            agentId: "s1",
            action: {
                id: "a1",
                action_type: "fabric_list_items",
                entity_type: "Metadata Discovery",
                entity_name: "Fabric Items",
            },
        }));

        expect(s.logs[0].message).toBe("Creating Fabric item");
        expect(s.logs[1].message).toBe("Workflow idle");
        expect(s.logs[2].message).toBe("Issue while running create fabric item: Tool issue to details unavailable");
        expect(s.logs[3].message).toBe("Read workspace inventory: Fabric Items");
        expect(s.changeOrder).toEqual([]);
        expect(s.logs.map((l) => l.message).join("\n")).not.toMatch(/fabric_create_item|fabric_list_items|TOOL_ERROR|undefined|→|←/);
    });
});
