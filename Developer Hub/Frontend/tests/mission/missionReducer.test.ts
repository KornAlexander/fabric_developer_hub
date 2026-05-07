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

    it("settles active slots when a run reaches terminal completion", () => {
        let s = initialMissionState({
            jobStatus: "running",
            activeAgentId: "planner",
            slotProgress: {
                planner: {
                    slotId: "planner",
                    agentId: "planner",
                    agentName: "MissionPlanner",
                    role: "Mission planning",
                    status: "running",
                    currentStep: "Streaming assistant response",
                },
                verifier: {
                    slotId: "verifier",
                    agentId: "verifier",
                    agentName: "FabricVerifier",
                    role: "Evidence verification",
                    status: "queued",
                },
            },
            agentStatus: { planner: "running", verifier: "queued" },
        });

        s = missionReducer(s, mk(1, "job_complete", {
            jobId: "j1", status: "completed", totalDuration: "8m 00s",
        }));

        expect(s.activeAgentId).toBeNull();
        expect(s.slotProgress.planner.status).toBe("done");
        expect(s.slotProgress.planner.currentStep).toBe("Run completed");
        expect(s.slotProgress.verifier.status).toBe("done");
        expect(s.agentStatus.planner).toBe("completed");
        expect(s.agentStatus.verifier).toBe("completed");
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
            "Generalist created the mission plan: 2 tasks queued for delegation, execution, and verification",
            "Generalist queued task: Discover workspace — Inspect items",
            "Orchestrator: Workspace discovery can start immediately.",
            "Specialist started: fabric-admin handling Discover workspace",
            "Specialist result for discover: success — Workspace inventory collected.",
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
            "Generalist checkpoint: 2 ready for assignment, 0 specialists running, 0 complete, 0 blocked, 0 failed",
            "Generalist delegated structured context to Builder for Build report (3 allowed tools · context ctx-123)",
            "Generalist handled directly instead of delegating: Review verification feedback. Reason: Generalist chose to handle routing directly.",
            "Generalist reviewed specialist feedback for build: Report artifact created and ready for verification. (1 follow-ups)",
            "Generalist intervened and steered Fabric Verifier: Verifier needs the repaired report id from the builder result.",
            "Generalist reassigned repair to repair-retry-1: Repeated tool loop continued after steering.",
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

    it("records activity rollups as high-level receipt rows with covered detail ranges", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "activity_rollup", {
            logCategory: "high_level",
            scope: "tool_batch",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            callId: "call-1",
            toolName: "fabric_create_workspace_inventory_solution",
            toolKind: "write",
            operationKind: "create",
            summary: "Created the lakehouse, semantic model, and report shell.",
            coveredSeqStart: 10,
            coveredSeqEnd: 28,
            detailCount: 19,
            status: "completed",
            durationMs: 2200,
            counts: { toolCalls: 4, outputChars: 1280 },
        }));

        expect(s.logs).toHaveLength(1);
        expect(s.logs[0].kind).toBe("rollup");
        expect(s.logs[0].logCategory).toBe("high_level");
        expect(s.logs[0].coveredSeqStart).toBe(10);
        expect(s.logs[0].coveredSeqEnd).toBe(28);
        expect(s.logs[0].detailCount).toBe(19);
        expect(s.logs[0].toolKind).toBe("write");
        expect(s.logs[0].operationKind).toBe("create");
        expect(s.logs[0].message).toBe("Created the lakehouse, semantic model, and report shell. (4 tool calls · 1280 chars) (+19 detail events)");
    });

    it("records user steering queue, delivery, and conservative interrupt semantics", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "user_message_queued", {
            steeringId: "st-1",
            targetAgentSessionId: "agent-1",
            agentName: "FabricDataEngineer",
            targetMode: "agent",
            mode: "queue",
            messagePreview: "Use the existing lakehouse instead of creating a new one.",
        }));
        s = missionReducer(s, mk(2, "turn_interrupt_requested", {
            steeringId: "st-2",
            targetAgentSessionId: "agent-1",
            targetMode: "agent",
            mode: "interrupt",
            messagePreview: "Stop creating new artifacts and inspect current output.",
        }));
        s = missionReducer(s, mk(3, "turn_interrupt_deferred", {
            steeringId: "st-2",
            targetAgentSessionId: "agent-1",
            targetMode: "agent",
            mode: "interrupt",
            messagePreview: "Stop creating new artifacts and inspect current output.",
            reason: "Tool call is at a non-interruptible write boundary.",
        }));
        s = missionReducer(s, mk(4, "user_message_delivered", {
            steeringId: "st-1",
            agentId: "agent-1",
            agentName: "FabricDataEngineer",
            targetMode: "agent",
            mode: "queue",
            messagePreview: "Use the existing lakehouse instead of creating a new one.",
            deliveredAtRound: 3,
        }));

        expect(s.logs.map((log) => log.kind)).toEqual(["steering", "steering", "steering", "steering"]);
        expect(s.logs.map((log) => log.level)).toEqual(["warn", "warn", "warn", "info"]);
        expect(s.logs[0].message).toBe("Steering queued for FabricDataEngineer: Use the existing lakehouse instead of creating a new one.");
        expect(s.logs[1].message).toBe("Interrupt requested: Stop creating new artifacts and inspect current output.");
        expect(s.logs[2].message).toContain("Interrupt deferred: Stop creating new artifacts");
        expect(s.logs[2].message).toContain("non-interruptible write boundary");
        expect(s.logs[3].message).toBe("Steering delivered for FabricDataEngineer · round 3: Use the existing lakehouse instead of creating a new one.");
        expect(s.logs.every((log) => log.logCategory === "high_level")).toBe(true);
    });

    it("records diagnostic baselines, new issues, and runtime trust updates", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "diagnostic_baseline_captured", {
            logCategory: "diagnostic",
            agentId: "engineer",
            toolName: "fabric_write_file",
            baselineCount: 2,
            summary: "Captured report definition diagnostics before write.",
        }));
        s = missionReducer(s, mk(2, "diagnostic_new_issues", {
            logCategory: "diagnostic",
            agentId: "engineer",
            toolName: "fabric_write_file",
            newIssueCount: 1,
            summary: "Post-write validation found new schema issue.",
            issues: [{ severity: "error", code: "SchemaViolation", message: "Missing visual container position." }],
        }));
        s = missionReducer(s, mk(3, "mcp_server_approval_required", {
            logCategory: "high_level",
            serverId: "workspace-powerbi-tools",
            source: "workspace",
            toolsPreview: ["read_model", "publish_model"],
            risk: "Workspace MCP tools can modify Fabric items.",
        }));

        expect(s.logs.map((log) => log.kind)).toEqual(["diagnostic", "diagnostic", "diagnostic"]);
        expect(s.logs[0].message).toBe("Diagnostic baseline captured after write item file (2): Captured report definition diagnostics before write.");
        expect(s.logs[1].level).toBe("error");
        expect(s.logs[1].message).toContain("Diagnostic issue detected after write item file (1)");
        expect(s.logs[1].message).toContain("Missing visual container position");
        expect(s.logs[2].logCategory).toBe("high_level");
        expect(s.logs[2].message).toBe("mcp server approval required · server workspace-powerbi-tools: Workspace MCP tools can modify Fabric items. (read_model, publish_model)");
    });

    it("renders backend tool_progress steps as live diagnostic log lines", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "tool_progress", {
            agentId: "06402b86", agentName: "FabricDataEngineer",
            toolName: "fabric_create_workspace_inventory_solution",
            step: "lakehouse_table_validation",
            status: "started",
            elapsedMs: 147466,
            logCategory: "diagnostic",
        }));
        s = missionReducer(s, mk(2, "tool_progress", {
            agentId: "06402b86", agentName: "FabricDataEngineer",
            toolName: "fabric_create_workspace_inventory_solution",
            step: "report_render_validation",
            status: "failed",
            elapsedMs: 240015,
            error: "Power BI ExportTo accepted the request but did not return an export id.",
            logCategory: "diagnostic",
        }));
        expect(s.logs).toHaveLength(2);
        expect(s.logs[0].message).toContain("Create Workspace Inventory Solution");
        expect(s.logs[0].message).toContain("lakehouse table validation started");
        expect(s.logs[0].message).toContain("147s elapsed");
        expect(s.logs[0].logCategory).toBe("diagnostic");
        expect(s.logs[0].level).toBe("info");
        expect(s.logs[1].level).toBe("error");
        expect(s.logs[1].message).toContain("report render validation failed");
        expect(s.logs[1].message).toContain("240s elapsed");
        expect(s.logs[1].message).toContain("Power BI ExportTo");
    });

    it("records first-class LLM stream phases as live progress entries", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "llm_request_started", {
            agentId: "planner",
            agentName: "MissionPlanner",
            requestId: "req-1",
            model: "gpt-4o-mini",
            taskTitle: "Plan the inventory repair",
            logCategory: "high_level",
        }));
        s = missionReducer(s, mk(2, "thinking_started", {
            agentId: "planner",
            agentName: "MissionPlanner",
            requestId: "req-1",
            summary: "Choosing the safest next read before writing files.",
            logCategory: "high_level",
        }));
        s = missionReducer(s, mk(3, "assistant_text_delta", {
            agentId: "planner",
            agentName: "MissionPlanner",
            requestId: "req-1",
            delta: "I will inspect ",
            tokenCount: 4,
            logCategory: "detailed",
        }));
        s = missionReducer(s, mk(4, "assistant_text_delta", {
            agentId: "planner",
            agentName: "MissionPlanner",
            requestId: "req-1",
            delta: "the existing dataset before changing it.",
            tokenCount: 12,
            logCategory: "detailed",
        }));
        s = missionReducer(s, mk(5, "assistant_text_finalized", {
            agentId: "planner",
            agentName: "MissionPlanner",
            requestId: "req-1",
            text: "I will inspect the existing dataset before changing it.",
            tokenCount: 12,
            logCategory: "detailed",
        }));

        expect(s.activeAgentId).toBe("planner");
        expect(s.agentStatus.planner).toBe("running");
        expect(s.slotProgress.planner.currentStep).toBe("Assistant response ready");
        expect(s.logs.map((log) => log.progressMode)).toEqual(["requesting", "thinking", "responding"]);
        expect(s.logs[0].message).toBe("Preparing Plan the inventory repair");
        expect(s.logs[1].streamKind).toBe("thinking");
        expect(s.logs[2].message).toBe("I will inspect the existing dataset before changing it.");
        expect(s.logs[2].streamKind).toBe("assistant");
        expect(s.logs[2].streamStatus).toBe("finalized");
        expect(s.logs[2].streamText).toBe("I will inspect the existing dataset before changing it.");
        expect(s.logs[2].counts?.tokenCount).toBe(12);
    });

    it("renders verifier_verdict step-by-step pass/fail breakdown", () => {
        let s = initialMissionState();
        s = missionReducer(s, mk(1, "verifier_verdict", {
            verdictId: "verdict-abc",
            verifierRunId: "run-v",
            verifierTaskId: "task-v",
            verifierAgentId: "fabric-verifier",
            targetTaskId: "task-build",
            passed: false,
            verifierClaimedSuccess: false,
            structuralFailures: ["STEP_FAILED:SEMANTIC_MODEL_QUERYABLE"],
            requiresUserBrowserRender: true,
            deliverables: [],
            evidence: {
                browserVerifiedUrls: ["https://app.powerbi.com/groups/w/reports/r"],
                screenshotPaths: ["/tmp/r.png"],
                visualsRendered: false,
                loadingStuckObserved: false,
                errorsObserved: ["semantic model not queryable"],
                expectedTextMatched: false,
            },
            stepResults: [
                { step: "Data ingestion", status: "passed", evidence: "2 Delta tables present" },
                { step: "Notebook transformation", status: "passed" },
                {
                    step: "Semantic model queryable",
                    status: "failed",
                    reason: "DAX returned Invalid object name 'dbo.X_FabricItems'",
                },
                { step: "Report renders", status: "not_applicable", detail: "blocked by upstream" },
            ],
            criteria: [],
            decisionRationale: "Verifier rejected: semantic model not queryable.",
            summary: "Decomposed goal; semantic model query failed.",
        }));

        const message = s.logs[s.logs.length - 1].message;
        expect(message).toContain("Verifier REJECTED");
        expect(message).toContain("STEP_FAILED:SEMANTIC_MODEL_QUERYABLE");
        expect(message).toContain("✓ Data ingestion (passed)");
        expect(message).toContain("✓ Notebook transformation (passed)");
        expect(message).toContain("✗ Semantic model queryable (failed)");
        expect(message).toContain("Invalid object name");
        expect(message).toContain("· Report renders (not_applicable)");
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
