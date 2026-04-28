import { test, expect, type Page } from "@playwright/test";

const SESSION_ID = "orchestrator-internal-e2e";

test.describe("internal orchestrator visibility", () => {
    test("does not render orchestrator as a visible agent in Mission Control", async ({ page }) => {
        await seedAuth(page);
        await mockMissionApis(page);

        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByRole("heading", { name: "Plan a Fabric solution." })).toBeVisible();
        await expect(page.getByText(/Orchestrator/i)).toHaveCount(0);
        await expect(page.getByText("Generalist", { exact: true })).toHaveCount(0);
        await expect(page.getByText("Generalist checkpoint: 2 ready, 0 running, 0 complete")).toBeVisible();
        await expect(page.getByText("Delegated structured context to Builder for Build report")).toBeVisible();
        await expect(page.getByText("Generalist handled directly: Review verification feedback - Generalist chose to handle routing directly.")).toBeVisible();
        await expect(page.getByText("Started 2 tasks in parallel")).toBeVisible();
        await expect(page.getByText("Generalist integrated feedback: Report artifact created and ready for verification.")).toBeVisible();
        await expect(page.getByText("Generalist steered Fabric Verifier: Verifier needs the repaired report id from the builder result.")).toBeVisible();
        await expect(page.getByText("Subagent reassigned to repair-retry-1: Repeated tool loop continued after steering.")).toBeVisible();
        await expect(page.getByText("Task result: Verifier accepted the repaired report with screenshot evidence.")).toBeVisible();
        await expect(page.getByText("Mission complete")).toBeVisible();
        const logCategoryTabs = page.getByRole("tablist", { name: "Log category" });
        const diagnosticsTab = logCategoryTabs.getByRole("tab", { name: /Diagnostics \(1\)/ });
        await diagnosticsTab.evaluate((element) => (element as HTMLElement).click());
        await expect(page.getByText("Generalist inspected subagent tool_loop (create fabric item)")).toBeVisible();
        const detailedTab = logCategoryTabs.getByRole("tab", { name: /Detailed \(2\)/ });
        await detailedTab.evaluate((element) => (element as HTMLElement).click());
        await expect(page.getByText(/system recovery decision/i)).toBeVisible();
    });
});

async function seedAuth(page: Page) {
    await page.addInitScript(() => {
        window.localStorage.clear();
        window.sessionStorage.clear();
        window.localStorage.setItem("github_token", "e2e-token");
        window.localStorage.setItem("github_user", "agenthub.e2e");
        window.sessionStorage.setItem("github_token", "e2e-token");
        window.sessionStorage.setItem("github_user", "agenthub.e2e");
        window.sessionStorage.setItem("workspace_id", "workspace-orchestrator-internal");
    });
}

async function mockMissionApis(page: Page) {
    await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        const pathName = url.pathname;

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}/events`)) {
            await route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                headers: { "Cache-Control": "no-cache" },
                body: missionEventStream(),
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}`)) {
            await route.fulfill({ status: 200, json: makeSessionRecord() });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });
}

function missionComposition() {
    return {
        sessionId: SESSION_ID,
        architecture: "dynamic",
        task: "Plan a Fabric solution.",
        slots: [
            { id: "orchestrator", agentId: "orchestrator", role: "Coordinate internally", skills: [], status: "done" },
            { id: "architect", agentId: "Architect", role: "Plan the solution", skills: [], status: "done" },
        ],
        handoffs: [
            { from: "orchestrator", to: "architect", kind: "delegate" },
            { from: "architect", to: "orchestrator", kind: "report" },
        ],
    };
}

function makeSessionRecord() {
    return {
        id: SESSION_ID,
        session_id: SESSION_ID,
        task_description: "Plan a Fabric solution.",
        workspace_id: "workspace-orchestrator-internal",
        status: "completed",
        created_at: "2026-04-25T12:00:00.000Z",
        started_at: "2026-04-25T12:01:00.000Z",
        completed_at: "2026-04-25T12:02:00.000Z",
        context: { workspace_name: "Internal Orchestration Workspace" },
        agents: [
            { agent_id: "orchestrator", role: "Coordinate internally", status: "completed", session_id: "orchestrator" },
            { agent_id: "Architect", role: "Plan the solution", status: "completed", session_id: "architect" },
        ],
        composition: missionComposition(),
    };
}

function missionEventStream() {
    const ts = "2026-04-25T12:01:12.000Z";
    const events = [
        {
            type: "run_overview",
            seq: 1,
            sessionId: SESSION_ID,
            ts,
            job: { id: SESSION_ID, status: "completed", startedAt: "2026-04-25T12:01:00.000Z", completedAt: "2026-04-25T12:02:00.000Z" },
            composition: missionComposition(),
            activeAgentId: null,
            artifacts: [],
            slotProgress: [
                { slotId: "orchestrator", agentId: "orchestrator", agentName: "Orchestrator", role: "Coordinate internally", status: "done" },
                { slotId: "architect", agentId: "architect", agentName: "Architect", role: "Plan the solution", status: "done" },
            ],
        },
        {
            type: "generalist_check_in",
            seq: 2,
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            readyTaskCount: 2,
            runningSubagentCount: 0,
            completedTaskCount: 0,
            readyTaskIds: ["build", "verify"],
        },
        {
            type: "generalist_context_pack",
            seq: 3,
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            runId: "run-build",
            taskId: "build",
            agentId: "builder",
            agentName: "Builder",
            taskTitle: "Build report",
            contextDigest: "ctx-build",
            toolScopeCount: 3,
        },
        {
            type: "generalist_context_pack",
            seq: 4,
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            runId: "run-verify",
            taskId: "verify",
            agentId: "fabric-verifier",
            agentName: "Fabric Verifier",
            taskTitle: "Verify report",
            contextDigest: "ctx-verify",
            toolScopeCount: 2,
        },
        {
            type: "parallel_group_spawned",
            seq: 5,
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            runIds: ["run-build", "run-verify"],
        },
        {
            type: "generalist_direct_work",
            seq: 6,
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            runId: "run-generalist",
            taskId: "triage",
            agentId: "generalist",
            taskTitle: "Review verification feedback",
            reason: "Generalist chose to handle routing directly.",
            toolScopeCount: 4,
            contextDigest: "ctx-generalist",
        },
        {
            type: "subagent_inspected",
            seq: 7,
            sessionId: SESSION_ID,
            ts,
            logCategory: "diagnostic",
            runId: "run-build",
            taskId: "build",
            matchingSignalCount: 1,
            signal: { kind: "tool_loop", toolName: "fabric_create_item", argHash: "abc123" },
        },
        {
            type: "generalist_state_decision",
            seq: 8,
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            runId: "run-build",
            taskId: "build",
            agentId: "builder",
            resultStatus: "success",
            taskStatus: "completed",
            summary: "Report artifact created and ready for verification.",
            followupTaskCount: 1,
        },
        {
            type: "generalist_steering",
            seq: 9,
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            runId: "run-verify",
            taskId: "verify",
            agentId: "fabric-verifier",
            agentName: "Fabric Verifier",
            reason: "Verifier needs the repaired report id from the builder result.",
        },
        {
            type: "subagent_abandoned",
            seq: 10,
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            runId: "run-stuck",
            taskId: "repair",
            replacementTaskId: "repair-retry-1",
            reason: "Repeated tool loop continued after steering.",
        },
        {
            type: "subagent_result",
            seq: 11,
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            runId: "run-verify",
            taskId: "verify",
            result: {
                id: "result-verify",
                status: "success",
                summary: "Verifier accepted the repaired report with screenshot evidence.",
            },
        },
        {
            type: "mission_completed",
            seq: 12,
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
        },
        {
            type: "log_line",
            seq: 13,
            sessionId: SESSION_ID,
            ts,
            agentId: "orchestrator",
            agentName: "Orchestrator",
            level: "info",
            message: "Orchestrator recovery decision: observe existing work after failure.",
        },
        {
            type: "job_complete",
            seq: 14,
            sessionId: SESSION_ID,
            ts,
            jobId: SESSION_ID,
            status: "completed",
            totalDuration: "00:01:00",
        },
    ];
    return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
}