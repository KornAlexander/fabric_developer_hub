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
            slotProgress: [{ slotId: "s1", agentId: "s1", status: "running" }],
        }));
        expect(s.jobStatus).toBe("running");
        expect(s.activeAgentId).toBe("s1");
        expect(s.artifactOrder).toEqual(["a1"]);
        expect(s.slotProgress["s1"].status).toBe("running");
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
});
