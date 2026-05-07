import { test, expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import { resolveGitHubCopilotToken } from "./utils/githubCopilotToken";
import { judgeMissionDesignEvidence } from "./utils/llmJudge";

const SESSION_ID = "mission-control-redesign-e2e";
const SCREENSHOT_DIR = path.resolve(__dirname, "../../docs/screenshots/mission-control-redesign");
const WRITE_SCREENSHOTS = process.env.AGENTHUB_WRITE_SCREENSHOTS === "1";

test.use({ viewport: { width: 1440, height: 960 } });

test.describe("Mission Control redesign", () => {
    test("keeps the terminal transcript reachable on short screens", async ({ page }) => {
        await page.setViewportSize({ width: 1440, height: 768 });
        await seedAuth(page);
        await mockMissionApis(page);
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByLabel("Mission execution")).toBeVisible();
        await expect(page.locator(".mc3-chat-main")).toBeVisible();
        await expect(page.locator(".right-rail")).toHaveCount(0);
        await expect(page.locator(".mission-pulse")).toHaveCount(0);
        await expect(page.locator(".canvas-log-stream")).toBeVisible();
        await expect(page.getByLabel("Mission intelligence")).toBeVisible();
        await expect(page.getByLabel("Mission steering")).toBeVisible();
        await expect(page.getByRole("log", { name: "Mission log stream" })).toContainText("Created workspace inventory proof", { timeout: 30_000 });
        await expect(page.locator(".mc3")).not.toContainText(/\breceipts?\b/i);

        const beforeScroll = await readMainScrollMetrics(page);
        expect(beforeScroll.logHeight).toBeGreaterThanOrEqual(280);
        expect(beforeScroll.logVisibleHeight).toBeGreaterThan(240);
        expect(beforeScroll.transcriptRowVisibleHeight).toBeGreaterThan(40);
        expect(beforeScroll.logOverflowY).not.toBe("hidden");

        await page.locator(".mc3").evaluate((root) => {
            const candidates = [
                root.closest<HTMLElement>(".editor-group__body"),
                document.querySelector<HTMLElement>(".agenthub-main"),
                document.scrollingElement as HTMLElement | null,
                root as HTMLElement,
            ].filter(Boolean) as HTMLElement[];
            const scrollContainer = candidates.find((node) => {
                const style = window.getComputedStyle(node);
                return style.overflowY !== "hidden" && node.scrollHeight > node.clientHeight + 10;
            }) || candidates[0];
            if (scrollContainer) scrollContainer.scrollTop = scrollContainer.scrollHeight;
        });
        const afterScroll = await readMainScrollMetrics(page);
        if (beforeScroll.scrollContainerScrollHeight > beforeScroll.scrollContainerClientHeight + 10) {
            expect(afterScroll.scrollContainerScrollTop).toBeGreaterThan(0);
        } else {
            expect(beforeScroll.scrollContainerScrollHeight).toBeLessThanOrEqual(beforeScroll.scrollContainerClientHeight + 10);
        }
        expect(afterScroll.steeringVisibleHeight).toBeGreaterThan(80);
    });

    test("replaces the dashboard with a modern Fabric-framed mission execution surface", async ({ page, request }, testInfo) => {
        test.setTimeout(180_000);
        await seedAuth(page);
        await mockMissionApis(page);
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByLabel("Mission execution")).toContainText("Mission control");
        await expect(page.getByLabel("Mission status")).toContainText("UX Review Workspace");
        await expect(page.locator(".mc3-terminal-window-dots")).toHaveCount(0);
        await expect(page.getByLabel("Mission execution")).not.toContainText("AgentHub-Code");
        await expect(page.getByLabel("Mission logs")).toBeVisible();
        await expect(page.getByLabel("Mission intelligence")).toBeVisible();
        await expect(page.getByLabel("Mission execution")).not.toContainText(/\breceipts?\b/i);
        await expect(page.getByLabel("Mission steering")).toBeVisible();
        await expect(page.locator(".right-rail")).toHaveCount(0);
        await expect(page.locator(".mission-pulse")).toHaveCount(0);
        await expect(page.getByRole("log", { name: "Mission log stream" })).toContainText("Created workspace inventory proof");
        await expect(page.getByLabel("Mission intelligence")).toContainText("OneLake MCP tools require approval");
        await expect(page.locator(".mc3-transcript-row", { hasText: "Created workspace inventory proof" })).not.toContainText(/\breceipts?\b/i);
        await expect(page.locator(".mc3-transcript-row", { hasText: "Steering queued" })).toContainText("Prioritize artifact links");
        await expect(page.locator(".mc3-transcript-row", { hasText: "Interrupt deferred" })).toContainText("after current write finishes");
        await expect(page.locator(".mc3-transcript-row", { hasText: "mcp server approval required" })).toContainText("OneLake MCP tools require approval");
        await expect(page.getByLabel("Mission intelligence")).toContainText("Changes");
        await expect(page.getByLabel("Mission intelligence")).toContainText("5");
        await expect(page.locator(".mc3-intel-change", { hasText: "Bronze_Operational_Inventory" })).toBeVisible();
        await expect(page.locator(".mc3-intel-change", { hasText: "Inventory synthesis notebook" })).toBeVisible();
        await expect(page.locator(".mc3-intel-change", { hasText: "Files/tmp/old-inventory.csv" })).toBeVisible();
        await expect(page.locator(".mc3-intel-change", { hasText: "Inventory reconciliation pipeline" })).toBeVisible();
        await expect(page.locator(".mc3-intel-change")).toHaveCount(5);
        await expect(page.locator(".mc3-intel")).not.toContainText(/queried/i);
        await expect(page.getByText("Planned output")).toHaveCount(0);
        await expect(page.getByText("Draft · not yet written")).toHaveCount(0);
        await expect(page.getByText("Artifacts so far")).toHaveCount(0);
        await expect(page.getByText("Latest artifacts")).toHaveCount(0);

        const standardMetrics = await readMissionMetrics(page);
        expect(standardMetrics.logScrollHeight).toBeGreaterThanOrEqual(420);
        expect(standardMetrics.logWidth).toBeGreaterThanOrEqual(760);
        expect(standardMetrics.intelligenceWidth).toBeGreaterThanOrEqual(280);
        const fillMetrics = await readSpaceUsageMetrics(page);
        expect(fillMetrics.horizontalGap).toBeLessThanOrEqual(2);
        expect(fillMetrics.mc3Width).toBeGreaterThanOrEqual(fillMetrics.bodyWidth - 2);
        expect(fillMetrics.logShareOfShell).toBeGreaterThan(0.52);
        await assertNoCriticalTextOverflow(page.locator(".mc3"));
        await assertCalmMissionStyling(page);

        await expect(page.getByRole("tablist", { name: "Log category" })).toHaveCount(0);
        const rollupRow = page.locator(".mc3-transcript-row", { hasText: "Created workspace inventory proof" }).first();
        await rollupRow.getByRole("button", { name: /Show details \(\+3\)/ }).click({ force: true });
        await expect(rollupRow.locator(".mc3-transcript-child__marker").first()).toHaveText("⎿");
        await expect(rollupRow.locator(".mc3-transcript-child__text", { hasText: "Create Fabric item" }).first()).toBeVisible();
        await rollupRow.getByRole("button", { name: /Hide details/ }).click({ force: true });

        await expect(page.locator(".mc3-transcript-row", { hasText: "Diagnostic issue detected" })).toContainText("Missing relationship cardinality");
        await expect(page.locator(".mc3-transcript-row", { hasText: "mcp server approval required" })).toContainText("OneLake MCP tools require approval");
        await expect(page.locator(".mc3")).not.toContainText(/TOOL_ERROR|fabric_create_item|undefined|=>|SECRET_INTERNAL_TRACE_DO_NOT_RENDER/);

        await expect(page.locator(".mc3-transcript-row", { hasText: "Created workspace inventory proof" })).toBeVisible();
        await screenshot(page.locator(".mc3-intel"), testInfo, "run-intelligence.png");
        await screenshotMain(page, testInfo, "desktop-standard.png");
        const designEvidence = await collectMissionDesignEvidence(page, testInfo, "desktop-standard");
        await judgeMissionDesign(request, testInfo, designEvidence);

        await page.setViewportSize({ width: 390, height: 900 });
        await page.evaluate(() => window.dispatchEvent(new Event("resize")));
        await expect(page.locator(".mc3-chat-main")).toBeVisible();
        const mobileMetrics = await readMissionMetrics(page);
        expect(mobileMetrics.logScrollHeight).toBeGreaterThanOrEqual(430);
        await assertNoCriticalTextOverflow(page.locator(".mc3"));
        await screenshotMain(page, testInfo, "mobile-standard.png");
    });

    test("streams parallel agent execution with current detail and collapsed history", async ({ page }) => {
        await seedAuth(page);
        await mockMissionApis(page, { status: "running" });
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        const liveRows = page.locator(".mc3-exec-row--live");
        await expect(liveRows).toHaveCount(3);
        await expect(page.getByLabel("Active agent execution")).toHaveCount(0);
        await expect(page.getByLabel("Mission intelligence")).toContainText("FabricDataEngineer");
        const liveRow = liveRows.first();
        await expect(liveRow).toBeVisible();
        await expect(liveRow).toContainText("FabricDataEngineer");
        await expect(liveRow.locator(".mc3-exec-current")).toBeVisible();
        await expect(liveRow.locator(".mc3-agent-progress-line")).toBeVisible();
        await expect(liveRow.locator(".mc3-exec-spinner")).toBeVisible();
        await expect(liveRow.getByLabel("Current task details")).toBeVisible();
        await expect(liveRow.locator(".mc3-exec-activity.is-current")).toContainText(/Workspace inventory checkpoint 13/);
        await expect(liveRow.locator(".mc3-exec-activity")).toHaveCount(6);
        await expect(liveRows.nth(1).locator(".mc3-exec-activity.is-current")).toContainText(/Read workspace inventory|query workspace items/);
        await expect(liveRows.nth(1).locator(".mc3-exec-activity")).toHaveCount(2);
        await expect(liveRows.nth(2)).toContainText("Modeler");
        await expect(liveRows.nth(2).locator(".mc3-exec-activity.is-current")).toContainText(/Read item definition|definition scan/i);

        const collapsedHistory = page
            .locator(".mc3-transcript-row")
            .filter({ hasText: "Workspace inventory checkpoint 7:" })
            .first();
        await expect(collapsedHistory).toBeVisible();
        await expect(collapsedHistory.locator(".mc3-transcript-child__text")).toHaveCount(0);
        await collapsedHistory.getByRole("button", { name: /Show details \(\+7\)/ }).click({ force: true });
        await expect(collapsedHistory.locator(".mc3-transcript-child__text", { hasText: /Workspace inventory checkpoint 1:/ })).toBeVisible();
    });

    test("shows live agent progress when the public stream has no log rows", async ({ page }, testInfo) => {
        test.setTimeout(90_000);
        await seedAuth(page);
        await mockMissionApis(page, { status: "running", eventMode: "empty" });
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByText("Preparing the first execution step.")).toHaveCount(0);

        const liveRows = page.locator(".mc3-exec-row--live");
        await expect(liveRows).toHaveCount(2, { timeout: 30_000 });
        const liveRow = liveRows.first();
        await expect(liveRow).toBeVisible({ timeout: 30_000 });
        await expect(liveRow.locator(".mc3-exec-current")).toContainText("Inventory synthesis");
        await expect(liveRow.locator(".mc3-agent-progress-line")).toContainText("Reading workspace inventory");
        await expect(liveRow).toContainText("FabricDataEngineer");
        await expect(liveRow).toContainText(/Read workspace inventory|Reading workspace inventory/);
        await expect(liveRow.getByLabel("Current task details")).toContainText("Reading workspace inventory");
        await expect(liveRows.nth(1)).toContainText("Modeler");
        await expect(liveRows.nth(1).locator(".mc3-exec-current")).toContainText("Report recommendation");
        await expect(liveRows.nth(1).locator(".mc3-agent-progress-line")).toContainText("Reading item details");
        await expect(page.getByLabel("Active agent execution")).toHaveCount(0);
        await expect(page.getByLabel("Mission intelligence")).toContainText(/FabricDataEngineer|Modeler/);

        await screenshotMain(page, testInfo, "empty-stream-live-progress.png");
    });

    test("shows a useful waiting state instead of a frozen initializing row", async ({ page }) => {
        await seedAuth(page);
        await mockMissionApis(page, { status: "running", eventMode: "empty", noAgents: true });
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByText("Initializing...")).toHaveCount(0);
        await expect(page.getByText("Initializing…")).toHaveCount(0);
        await expect(page.locator(".mc3-exec-row--live")).toHaveCount(0);
        await expect(page.locator(".mc3-exec-empty")).toBeVisible({ timeout: 30_000 });
        await expect(page.locator(".mc3-exec-empty")).toContainText("No agent telemetry is attached yet");
        await expect(page.locator(".mc3-exec-empty")).toContainText("Awaiting first row");
        const emptyMetrics = await page.locator(".canvas-log-stream").evaluate((log) => {
            const empty = log.querySelector<HTMLElement>(".mc3-exec-empty");
            const logRect = (log as HTMLElement).getBoundingClientRect();
            const emptyRect = empty?.getBoundingClientRect();
            return {
                emptyHeight: emptyRect?.height ?? 0,
                logHeight: logRect.height,
            };
        });
        expect(emptyMetrics.emptyHeight).toBeGreaterThan(100);
        await expect(page.getByLabel("Active agent execution")).toHaveCount(0);
        await expect(page.getByLabel("Mission execution")).toContainText("quiet");
    });

    test("shows immediate startup telemetry and a clear backend failure state", async ({ page }) => {
        await seedAuth(page);
        await mockMissionApis(page, { status: "failed", eventMode: "startupFailed" });
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });

        const mission = page.getByLabel("Mission execution");
        await expect(mission).toBeVisible({ timeout: 30_000 });
        await expect(page.getByRole("log", { name: "Mission log stream" })).toContainText("Mission accepted. Preparing the isolated tool runtime", { timeout: 8_000 });
        await expect(page.getByRole("alert", { name: "Mission failure summary" })).toContainText("Mission failed", { timeout: 8_000 });
        await expect(page.getByRole("alert", { name: "Mission failure summary" })).toContainText(/backend|runtime/i);
        await expect(page.getByLabel("Active agent execution")).toHaveCount(0);
        await expect(mission).toContainText("failed");
        await expect(mission).toContainText("error");
        await expect(mission).not.toContainText(/\bdone\b/);
        await expect(mission).not.toContainText(/\bcomplete\b/);
        await expect(mission).not.toContainText("Mission stopped before public events arrived");
        await expect(mission).not.toContainText("Diagnostics pending");
        const fillMetrics = await readSpaceUsageMetrics(page);
        expect(fillMetrics.horizontalGap).toBeLessThanOrEqual(2);
        expect(fillMetrics.mc3Width).toBeGreaterThanOrEqual(fillMetrics.bodyWidth - 2);
    });

    test("sends queued and interrupt steering directives to the backend", async ({ page }) => {
        await seedAuth(page);
        const mock = await mockMissionApis(page, { status: "running" });
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.getByLabel("Mission steering")).toBeVisible({ timeout: 30_000 });
        await page.getByLabel("Target").selectOption("engineer");

        await page.getByLabel("Steer mission").fill("Prioritize the warehouse reconciliation summary.");
    await page.getByRole("button", { name: "Send" }).click({ force: true });
        await expect(page.getByRole("status")).toContainText("Directive accepted");

        await page.getByLabel("Steer mission").fill("Pause after the current write and verify artifacts.");
    await page.getByRole("button", { name: "Interrupt" }).click({ force: true });
        await expect(page.getByRole("status")).toContainText("Interrupt accepted");

        expect(mock.sentMessages).toHaveLength(2);
        expect(mock.sentMessages[0]).toMatchObject({
            message: "Prioritize the warehouse reconciliation summary.",
            target_agent_id: "engineer",
            mode: "queue",
        });
        expect(mock.sentMessages[1]).toMatchObject({
            message: "Pause after the current write and verify artifacts.",
            target_agent_id: "engineer",
            mode: "interrupt",
        });
    });
});

async function seedAuth(page: Page) {
    await page.addInitScript(() => {
        window.localStorage.clear();
        window.sessionStorage.clear();
        window.localStorage.setItem("github_token", "e2e-mission-token");
        window.localStorage.setItem("github_user", "mission.e2e");
        window.sessionStorage.setItem("github_token", "e2e-mission-token");
        window.sessionStorage.setItem("github_user", "mission.e2e");
        window.sessionStorage.setItem("workspace_id", "workspace-redesign");
    });
}

type MockMissionOptions = { status?: "running" | "completed" | "failed"; eventMode?: "standard" | "empty" | "startupFailed"; noAgents?: boolean };

async function mockMissionApis(page: Page, options: MockMissionOptions = {}) {
    const sentMessages: any[] = [];
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
                body: options.eventMode === "empty" ? "" : missionEventStream(options),
            });
            return;
        }

        if (method === "POST" && pathName.endsWith(`/api/sessions/${SESSION_ID}/message`)) {
            const body = JSON.parse(request.postData() || "{}");
            sentMessages.push(body);
            await route.fulfill({
                status: 200,
                json: {
                    status: "queued",
                    steeringId: `steer-${sentMessages.length}`,
                    targetMode: body.target_agent_id ? "agent" : "broadcast",
                    mode: body.mode || "queue",
                    targetAgentSessionIds: body.target_agent_id ? [body.target_agent_id] : ["admin", "engineer"],
                    targetCount: body.target_agent_id ? 1 : 2,
                    messagePreview: String(body.message || "").slice(0, 120),
                },
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}`)) {
            await route.fulfill({ status: 200, json: makeSessionRecord(options) });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });
    return { sentMessages };
}

function makeSessionRecord(options: MockMissionOptions = {}) {
    const status = options.status || "completed";
    const runningCurrentStep: Record<string, string> = {
        engineer: "Calling fabric_list_items...",
        modeler: "Calling fabric_get_item_definition...",
    };
    return {
        id: SESSION_ID,
        session_id: SESSION_ID,
        task_description: "Run a read-only operational inspection and produce a workspace item inventory summary.",
        workspace_id: "workspace-redesign",
        status,
        created_at: "2026-04-25T12:00:00.000Z",
        started_at: "2026-04-25T12:01:00.000Z",
        completed_at: status === "completed" || status === "failed" ? "2026-04-25T12:02:12.000Z" : null,
        context: { workspace_name: "UX Review Workspace" },
        agents: options.noAgents ? [] : missionSlots().map((slot) => ({
            agent_id: slot.agentId,
            role: slot.role,
            status: status === "completed" ? "completed" : status === "failed" ? "failed" : slot.id === "admin" ? "completed" : "running",
            current_step: status === "completed"
                ? "Execution complete"
                : status === "failed"
                    ? "Tool runtime failed before work started"
                : runningCurrentStep[slot.id] || "Completed",
            session_id: slot.id,
        })),
        composition: missionComposition(),
    };
}

function missionSlots() {
    return [
        { id: "admin", agentId: "FabricAdmin", role: "Workspace inspection" },
        { id: "engineer", agentId: "FabricDataEngineer", role: "Inventory synthesis" },
        { id: "modeler", agentId: "Modeler", role: "Report recommendation" },
    ];
}

function missionComposition() {
    return {
        sessionId: SESSION_ID,
        architecture: "supervisor",
        task: "Inspect the workspace and summarize item inventory.",
        slots: missionSlots().map((slot) => ({ ...slot, skills: [], status: "done" })),
        handoffs: [
            { from: "admin", to: "engineer", kind: "report" },
            { from: "engineer", to: "modeler", kind: "report" },
        ],
    };
}

function missionEventStream(options: MockMissionOptions = {}) {
    const ts = "2026-04-25T12:01:12.000Z";
    const completed = (options.status || "completed") === "completed";
    const failed = options.status === "failed";
    let seq = 1;
    const nextSeq = () => seq++;
    if (options.eventMode === "startupFailed" || failed) {
        const reason = "Mission failed while preparing the isolated tool runtime. This is an AgentHub runtime/startup error, not a problem with your prompt. Details: mission MCP runtime exited with code 137";
        const events = [
            {
                type: "composition_ready",
                seq: nextSeq(),
                sessionId: SESSION_ID,
                ts,
                composition: missionComposition(),
            },
            {
                type: "run_overview",
                seq: nextSeq(),
                sessionId: SESSION_ID,
                ts,
                job: { id: SESSION_ID, status: "running", startedAt: "2026-04-25T12:01:00.000Z", completedAt: null },
                composition: missionComposition(),
                activeAgentId: "generalist",
                artifacts: [],
                changes: [],
                slotProgress: [{ slotId: "generalist", agentId: "generalist", agentName: "Generalist", role: "Mission controller", status: "running", currentStep: "Preparing isolated tool runtime" }],
            },
            {
                type: "log_line",
                seq: nextSeq(),
                sessionId: SESSION_ID,
                ts,
                logCategory: "detailed",
                agentId: "generalist",
                agentName: "Generalist",
                level: "info",
                message: "Mission accepted. Preparing the isolated tool runtime and attaching live events.",
            },
            {
                type: "slot_progress",
                seq: nextSeq(),
                sessionId: SESSION_ID,
                ts,
                logCategory: "high_level",
                slotId: "generalist",
                agentId: "generalist",
                agentName: "Generalist",
                role: "Mission controller",
                status: "failed",
                currentStep: "Tool runtime failed before work started",
                reason: "runtime_start_failed",
            },
            {
                type: "agent_error",
                seq: nextSeq(),
                sessionId: SESSION_ID,
                ts,
                logCategory: "high_level",
                agentId: "generalist",
                agentName: "Generalist",
                error: reason,
            },
            { type: "job_failed", seq: nextSeq(), sessionId: SESSION_ID, ts, jobId: SESSION_ID, status: "failed", totalDuration: "8s", reason },
        ];
        return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
    }
    const events: any[] = [
        {
            type: "run_overview",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            job: { id: SESSION_ID, status: "running", startedAt: "2026-04-25T12:01:00.000Z", completedAt: null },
            composition: missionComposition(),
            activeAgentId: "admin",
            artifacts: [],
            changes: [],
            slotProgress: missionSlots().map((slot, index) => ({
                slotId: slot.id,
                agentId: slot.id,
                agentName: slot.agentId,
                role: slot.role,
                status: index === 0 ? "running" : "queued",
            })),
        },
        ...Array.from({ length: 18 }, (_, index) => ({
            type: "log_line",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: index < 3 ? "high_level" : "detailed",
            agentId: index < 7 ? "admin" : index < 13 ? "engineer" : "modeler",
            agentName: index < 7 ? "FabricAdmin" : index < 13 ? "FabricDataEngineer" : "Modeler",
            level: "info",
            message: `Workspace inventory checkpoint ${index + 1}: reviewed Fabric item metadata and captured next action evidence.`,
        })),
        {
            type: "tool_call_started",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "detailed",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            callId: "call-create-proof",
            toolName: "fabric_create_item",
            toolKind: "write",
            operationKind: "create",
            argsPreview: { itemType: "Lakehouse", displayName: "Bronze_Operational_Inventory" },
        },
        {
            type: "tool_progress",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "detailed",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            toolName: "fabric_create_item",
            toolKind: "write",
            operationKind: "create",
            callId: "call-create-proof",
            step: "metadata_validation",
            status: "started",
            elapsedMs: 2100,
        },
        {
            type: "tool_call_ended",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "detailed",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            callId: "call-create-proof",
            toolName: "fabric_create_item",
            toolKind: "write",
            operationKind: "create",
            durationMs: 3200,
            status: "ok",
        },
        {
            type: "activity_rollup",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            scope: "tool_batch",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            callId: "call-create-proof",
            toolName: "fabric_create_item",
            toolKind: "write",
            operationKind: "create",
            summary: "Created workspace inventory proof and captured the generated item link.",
            coveredSeqStart: seq - 4,
            coveredSeqEnd: seq - 2,
            detailCount: 3,
            status: "completed",
            durationMs: 3400,
            counts: { toolCalls: 1, outputChars: 640 },
        },
        {
            type: "user_message_queued",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            steeringId: "steering-e2e-queue",
            targetAgentSessionId: "engineer",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            targetMode: "agent",
            mode: "queue",
            messagePreview: "Prioritize artifact links before the final summary.",
        },
        {
            type: "user_message_delivered",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            steeringId: "steering-e2e-queue",
            targetAgentSessionId: "engineer",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            targetMode: "agent",
            mode: "queue",
            deliveredAtRound: 2,
            messagePreview: "Prioritize artifact links before the final summary.",
        },
        {
            type: "turn_interrupt_requested",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            steeringId: "steering-e2e-interrupt",
            targetAgentSessionId: "engineer",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            targetMode: "agent",
            mode: "interrupt",
            messagePreview: "Stop after the current write and show the proof.",
        },
        {
            type: "turn_interrupt_deferred",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            steeringId: "steering-e2e-interrupt",
            targetAgentSessionId: "engineer",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            targetMode: "agent",
            mode: "interrupt",
            messagePreview: "Stop after the current write and show the proof.",
            reason: "after current write finishes",
        },
        {
            type: "diagnostic_baseline_captured",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "diagnostic",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            callId: "call-create-proof",
            toolName: "fabric_create_item",
            toolKind: "write",
            operationKind: "create",
            baselineCount: 2,
            summary: "Captured report definition diagnostics before writing the proof item.",
        },
        {
            type: "diagnostic_new_issues",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "diagnostic",
            agentId: "modeler",
            agentName: "Modeler",
            callId: "call-create-proof",
            toolName: "fabric_create_item",
            toolKind: "write",
            operationKind: "validation",
            newIssueCount: 1,
            summary: "Post-write validation found a new semantic model issue.",
            issues: [{ severity: "error", code: "relationship", message: "Missing relationship cardinality." }],
        },
        {
            type: "mcp_server_approval_required",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "high_level",
            serverId: "onelake",
            toolsPreview: ["read", "write", "list"],
            risk: "workspace write access",
            summary: "OneLake MCP tools require approval before write operations continue.",
        },
        {
            type: "log_line",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            logCategory: "trace",
            level: "info",
            message: "SECRET_INTERNAL_TRACE_DO_NOT_RENDER",
            tags: ["trace"],
        },
        ...missionChanges(ts).map((change) => ({
            type: "change_recorded",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            ...change,
        })),
        {
            type: "artifact_added",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            artifactId: "lakehouse-target",
            agentId: "engineer",
            kind: "Lakehouse",
            name: "Lakehouse in workspace 8bdca8af",
            state: "draft",
        },
        {
            type: "artifact_added",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            artifactId: "warehouse-target",
            agentId: "modeler",
            kind: "Warehouse",
            name: "Warehouse in workspace 8bdca8af",
            state: "draft",
        },
    ];

    if (!completed) {
        events.push({
            type: "run_overview",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            job: { id: SESSION_ID, status: "running", startedAt: "2026-04-25T12:01:00.000Z", completedAt: null },
            composition: missionComposition(),
            activeAgentId: "engineer",
            artifacts: [
                { artifactId: "lakehouse-target", agentId: "engineer", kind: "Lakehouse", name: "Lakehouse in workspace 8bdca8af", state: "draft" },
                { artifactId: "warehouse-target", agentId: "modeler", kind: "Warehouse", name: "Warehouse in workspace 8bdca8af", state: "draft" },
            ],
            changes: missionChanges(ts),
            slotProgress: missionSlots().map((slot) => ({
                slotId: slot.id,
                agentId: slot.id,
                agentName: slot.agentId,
                role: slot.role,
                status: slot.id === "engineer" || slot.id === "modeler" ? "running" : "done",
                currentStep: slot.id === "engineer" ? "Calling fabric_list_items..." : slot.id === "modeler" ? "Calling fabric_get_item_definition..." : "Completed",
            })),
        });
        events.push(
            {
                type: "tool_call_started",
                seq: nextSeq(),
                sessionId: SESSION_ID,
                ts,
                logCategory: "detailed",
                agentId: "engineer",
                agentName: "FabricDataEngineer",
                callId: "call-live-list-items",
                toolName: "fabric_list_items",
                toolKind: "read",
                operationKind: "read",
                argsPreview: { workspaceId: "workspace-redesign" },
            },
            {
                type: "tool_progress",
                seq: nextSeq(),
                sessionId: SESSION_ID,
                ts,
                logCategory: "detailed",
                agentId: "engineer",
                agentName: "FabricDataEngineer",
                toolName: "fabric_list_items",
                toolKind: "read",
                operationKind: "read",
                callId: "call-live-list-items",
                step: "query_workspace_items",
                status: "started",
                elapsedMs: 4200,
            },
            {
                type: "tool_call_started",
                seq: nextSeq(),
                sessionId: SESSION_ID,
                ts,
                logCategory: "detailed",
                agentId: "modeler",
                agentName: "Modeler",
                callId: "call-live-model-definition",
                toolName: "fabric_get_item_definition",
                toolKind: "read",
                operationKind: "read",
                argsPreview: { itemId: "semantic-model-target" },
            },
            {
                type: "tool_progress",
                seq: nextSeq(),
                sessionId: SESSION_ID,
                ts,
                logCategory: "detailed",
                agentId: "modeler",
                agentName: "Modeler",
                toolName: "fabric_get_item_definition",
                toolKind: "read",
                operationKind: "read",
                callId: "call-live-model-definition",
                step: "definition_scan",
                status: "started",
                elapsedMs: 3600,
            },
        );
    } else {
        events.push({
            type: "run_overview",
            seq: nextSeq(),
            sessionId: SESSION_ID,
            ts,
            job: { id: SESSION_ID, status: "completed", startedAt: "2026-04-25T12:01:00.000Z", completedAt: "2026-04-25T12:02:12.000Z" },
            composition: missionComposition(),
            activeAgentId: null,
            artifacts: [
                { artifactId: "lakehouse-target", agentId: "engineer", kind: "Lakehouse", name: "Lakehouse in workspace 8bdca8af", state: "draft" },
                { artifactId: "warehouse-target", agentId: "modeler", kind: "Warehouse", name: "Warehouse in workspace 8bdca8af", state: "draft" },
            ],
            changes: missionChanges(ts),
            slotProgress: missionSlots().map((slot) => ({
                slotId: slot.id,
                agentId: slot.id,
                agentName: slot.agentId,
                role: slot.role,
                status: "done",
            })),
        });
        events.push({ type: "job_complete", seq: nextSeq(), sessionId: SESSION_ID, ts, jobId: SESSION_ID, status: "completed", totalDuration: "00:01:12" });
    }

    return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
}

function missionChanges(ts: string) {
    return [
        {
            recordId: "change-created-lakehouse",
            kind: "created",
            status: "applied",
            targetName: "Bronze_Operational_Inventory",
            targetType: "Lakehouse",
            targetScope: "item",
            summary: "Created Lakehouse Bronze_Operational_Inventory.",
            toolName: "fabric_create_item",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            targetId: "lakehouse-target",
            ts,
        },
        {
            recordId: "change-updated-notebook",
            kind: "updated",
            status: "applied",
            targetName: "Inventory synthesis notebook",
            targetType: "Notebook",
            targetScope: "item",
            summary: "Updated Notebook Inventory synthesis notebook.",
            toolName: "fabric_write_file",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            ts,
        },
        {
            recordId: "change-updated-setting",
            kind: "updated",
            status: "applied",
            targetName: "Workspace retention policy",
            targetType: "Set Endorsement",
            targetScope: "settings",
            summary: "Updated Set Endorsement Workspace retention policy.",
            toolName: "sl_set_endorsement",
            agentId: "admin",
            agentName: "FabricAdmin",
            ts,
        },
        {
            recordId: "change-deleted-staging",
            kind: "deleted",
            status: "applied",
            targetName: "Files/tmp/old-inventory.csv",
            targetType: "File",
            targetScope: "file",
            summary: "Deleted File Files/tmp/old-inventory.csv.",
            toolName: "fabric_delete_file",
            agentId: "engineer",
            agentName: "FabricDataEngineer",
            ts,
        },
        {
            recordId: "change-ran-pipeline",
            kind: "important_action",
            status: "applied",
            targetName: "Inventory reconciliation pipeline",
            targetType: "Run Item Job",
            targetScope: "execution",
            summary: "Applied Run Item Job to Inventory reconciliation pipeline.",
            toolName: "sl_run_item_job",
            agentId: "modeler",
            agentName: "Modeler",
            ts,
        },
    ];
}

async function disableAnimations(page: Page) {
    await page.addStyleTag({
        content: `
            *, *::before, *::after {
                animation-duration: 0s !important;
                animation-delay: 0s !important;
                transition-duration: 0s !important;
                scroll-behavior: auto !important;
            }
        `,
    });
}

async function collectMissionDesignEvidence(page: Page, testInfo: TestInfo, label: string) {
    const evidence = await page.locator(".mc3").evaluate((root, evidenceLabel) => {
        const normalizedText = (value: string | null | undefined) => (value || "").replace(/\s+/g, " ").trim();
        const className = (node: Element) => typeof (node as HTMLElement).className === "string"
            ? (node as HTMLElement).className
            : String((node as HTMLElement).getAttribute("class") || "");
        const visible = (node: HTMLElement) => {
            const rect = node.getBoundingClientRect();
            const style = window.getComputedStyle(node);
            return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
        };
        const readRect = (selector: string) => {
            const node = root.querySelector<HTMLElement>(selector);
            if (!node) return null;
            const rect = node.getBoundingClientRect();
            return {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
            };
        };
        const styleSummary = (selector: string, pseudo?: string) => {
            const node = root.querySelector<HTMLElement>(selector);
            if (!node) return null;
            const style = window.getComputedStyle(node, pseudo);
            return {
                background: style.background,
                backgroundColor: style.backgroundColor,
                backgroundImage: style.backgroundImage,
                borderColor: style.borderColor,
                borderRadius: style.borderRadius,
                boxShadow: style.boxShadow,
                color: style.color,
            };
        };
        const textContent = normalizedText(root.textContent).slice(0, 9000);
        const transcriptSummaries = Array.from(root.querySelectorAll<HTMLElement>(".mc3-transcript-row"))
            .map((node) => normalizedText(node.textContent).slice(0, 180))
            .filter(Boolean);
        const allElements = Array.from(root.querySelectorAll<HTMLElement>("*"));
        const legacyClassNameMatches = allElements
            .map(className)
            .filter((value) => /receipt/i.test(value))
            .slice(0, 24);
        const legacyTermMatches = textContent.match(/\breceipts?\b/ig) || [];
        const criticalSelectors = [
            ".mc3-execution-header",
            ".canvas-log-stream",
            ".mc3-intel",
            ".mc3-steering",
        ];
        const criticalRects = criticalSelectors.map((selector) => {
            const node = root.querySelector<HTMLElement>(selector);
            if (!node || !visible(node)) return null;
            const rect = node.getBoundingClientRect();
            return { selector, left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
        }).filter(Boolean) as Array<{ selector: string; left: number; top: number; right: number; bottom: number; width: number; height: number }>;
        const overlaps: string[] = [];
        for (let i = 0; i < criticalRects.length; i += 1) {
            for (let j = i + 1; j < criticalRects.length; j += 1) {
                const a = criticalRects[i];
                const b = criticalRects[j];
                const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
                const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
                if (x * y > 8) overlaps.push(`${a.selector} overlaps ${b.selector} by ${Math.round(x * y)}px`);
            }
        }
        const overflowSelectors = [
            ".mc3-execution-header__copy strong",
            ".mc3-execution-status",
            ".mc3-terminal-connection",
            ".mc3-agent-lane p",
            ".mc3-exec-headline",
            ".mc3-exec-current",
            ".mc3-agent-progress-line",
            ".mc3-summary-rail__header strong",
            ".mc3-summary-status",
            ".mc3-intel-signal strong",
            ".mc3-intel-change strong",
            ".mc3-intel-change small",
            ".mc3-steering__btn",
        ].join(",");
        const overflows = Array.from(root.querySelectorAll<HTMLElement>(overflowSelectors))
            .filter(visible)
            .filter((node) => node.scrollWidth > node.clientWidth + 2 || node.scrollHeight > node.clientHeight + 2)
            .map((node) => `${className(node) || node.tagName}: ${normalizedText(node.textContent).slice(0, 120)}`)
            .slice(0, 40);
        const shellStyle = styleSummary(".mc3-terminal-shell");
        const shellFrame = styleSummary(".mc3-terminal-shell", "::before");
        const frameEvidenceText = JSON.stringify({ shellStyle, shellFrame });
        const fabricColorRegex = /(0, 120, 212|0, 188, 242|92, 45, 145|227, 0, 140|255, 185, 0|#0078d4|#00bcf2|#5c2d91|#e3008c|#ffb900)/i;
        const shell = root.querySelector<HTMLElement>(".mc3-terminal-shell");
        const intelligence = root.querySelector<HTMLElement>(".mc3-intel");
        const steering = root.querySelector<HTMLElement>(".mc3-steering");
        const shimmerFramePresent = !!shell && /gradient/i.test(shellFrame?.backgroundImage || shellStyle?.backgroundImage || "");
        const fabricColorMotivationPresent = fabricColorRegex.test(frameEvidenceText);
        return {
            label: evidenceLabel,
            viewport: { width: window.innerWidth, height: window.innerHeight },
            visibleCopySample: {
                header: normalizedText(root.querySelector('[aria-label="Mission status"]')?.textContent).slice(0, 360),
                latestTranscriptRows: transcriptSummaries.slice(-5),
                intelligence: normalizedText(root.querySelector('[aria-label="Mission intelligence"]')?.textContent).slice(0, 900),
                composer: normalizedText(root.querySelector('[aria-label="Mission steering"]')?.textContent).slice(0, 420),
            },
            contentDensity: {
                transcriptRows: transcriptSummaries.length,
                collapsedDetailButtons: root.querySelectorAll(".mc3-transcript-row__expand").length,
                liveRows: root.querySelectorAll(".mc3-exec-row--live").length,
                intelligenceSignals: root.querySelectorAll(".mc3-intel-signal").length,
                outputChangeRows: root.querySelectorAll(".mc3-intel-change").length,
                maxVisibleOutputChangeRows: 5,
            },
            designContract: {
                productDirection: "Modern agentic execution workbench, not a landing page and not a legacy dashboard.",
                modernCues: [
                    "masked animated Fabric gradient frame around the execution shell",
                    "glass-like run intelligence panel with Fabric blue, cyan, purple, magenta, and amber accents",
                    "live transcript, intelligence, and steering composer arranged as a focused workbench",
                    "progressive disclosure keeps detailed activity behind Show details controls",
                ],
                fabricShimmerFrame: shimmerFramePresent,
                fabricPaletteMotivated: fabricColorMotivationPresent,
                paletteTokens: ["#0078d4", "#00bcf2", "#5c2d91", "#e3008c", "#ffb900"],
                intelligencePanelIntent: intelligence?.dataset.designIntent || null,
                intelligencePanelPalette: intelligence?.dataset.designPalette || null,
                steeringComposerIntent: steering?.dataset.designIntent || null,
                noLegacyTermsOrClasses: legacyTermMatches.length === 0 && legacyClassNameMatches.length === 0,
                noDetectedOverflows: overflows.length === 0,
                noStructuralOverlaps: overlaps.length === 0,
                functionalElementsPresent: {
                    deduplicatedExecution: !root.querySelector('[aria-label="Active agent execution"]'),
                    liveTranscript: !!root.querySelector('[aria-label="Mission log stream"]'),
                    missionIntelligence: !!root.querySelector('[aria-label="Mission intelligence"]'),
                    outputChanges: root.querySelectorAll(".mc3-intel-change").length,
                    steeringComposer: !!root.querySelector('[aria-label="Mission steering"]'),
                },
            },
            structure: {
                missionExecution: !!root.querySelector('[aria-label="Mission execution"]'),
                missionLogs: !!root.querySelector('[aria-label="Mission logs"]'),
                activeAgentExecution: !!root.querySelector('[aria-label="Active agent execution"]'),
                missionIntelligence: !!root.querySelector('[aria-label="Mission intelligence"]'),
                missionSteering: !!root.querySelector('[aria-label="Mission steering"]'),
                outputChangeRows: root.querySelectorAll(".mc3-intel-change").length,
                transcriptRows: root.querySelectorAll(".mc3-transcript-row").length,
                liveRows: root.querySelectorAll(".mc3-exec-row--live").length,
                shimmerFramePresent,
                fabricColorMotivationPresent,
            },
            layout: {
                shell: readRect(".mc3-terminal-shell"),
                header: readRect(".mc3-execution-header"),
                logs: readRect(".canvas-log-stream"),
                intelligence: readRect(".mc3-intel"),
                steering: readRect(".mc3-steering"),
                overlaps,
                overflows,
            },
            visualStyle: {
                root: styleSummary(".mc3"),
                shell: shellStyle,
                shellFrame,
                logs: styleSummary(".canvas-log-stream"),
                intelligence: styleSummary(".mc3-intel"),
                composer: styleSummary(".mc3-steering"),
            },
            labels: {
                header: normalizedText(root.querySelector('[aria-label="Mission status"]')?.textContent).slice(0, 500),
                lanes: normalizedText(root.querySelector('[aria-label="Active agent execution"]')?.textContent).slice(0, 900),
                intelligence: normalizedText(root.querySelector('[aria-label="Mission intelligence"]')?.textContent).slice(0, 1200),
                composer: normalizedText(root.querySelector('[aria-label="Mission steering"]')?.textContent).slice(0, 700),
            },
            forbidden: {
                legacyTermMatches,
                legacyClassNameMatches,
                rawInternalTextVisible: /TOOL_ERROR|fabric_create_item|undefined|=>|SECRET_INTERNAL_TRACE_DO_NOT_RENDER/.test(textContent),
            },
        };
    }, label);

    await testInfo.attach(`design-evidence-${label}.json`, {
        body: JSON.stringify(evidence, null, 2),
        contentType: "application/json",
    });
    return evidence;
}

async function judgeMissionDesign(request: APIRequestContext, testInfo: TestInfo, evidence: any) {
    expect(evidence.structure.shimmerFramePresent, JSON.stringify(evidence.structure, null, 2)).toBe(true);
    expect(evidence.structure.fabricColorMotivationPresent, JSON.stringify(evidence.visualStyle, null, 2)).toBe(true);
    expect(evidence.structure.missionExecution).toBe(true);
    expect(evidence.structure.missionLogs).toBe(true);
    expect(evidence.structure.activeAgentExecution).toBe(false);
    expect(evidence.structure.missionIntelligence).toBe(true);
    expect(evidence.structure.missionSteering).toBe(true);
    expect(evidence.structure.outputChangeRows).toBeGreaterThanOrEqual(4);
    expect(evidence.forbidden.legacyTermMatches, JSON.stringify(evidence.forbidden, null, 2)).toEqual([]);
    expect(evidence.forbidden.legacyClassNameMatches, JSON.stringify(evidence.forbidden, null, 2)).toEqual([]);
    expect(evidence.forbidden.rawInternalTextVisible).toBe(false);
    expect(evidence.layout.overlaps, JSON.stringify(evidence.layout, null, 2)).toEqual([]);
    expect(evidence.layout.overflows, JSON.stringify(evidence.layout, null, 2)).toEqual([]);

    if (process.env.AGENTHUB_E2E_DESIGN_JUDGE === "0") {
        testInfo.annotations.push({ type: "design-judge-disabled", description: "AGENTHUB_E2E_DESIGN_JUDGE=0" });
        return;
    }

    const backendUrl = process.env.WORKLOAD_BE_URL || "http://127.0.0.1:5000";
    const githubToken = await resolveGitHubCopilotToken(request, backendUrl);
    testInfo.annotations.push({ type: "design-judge-token-source", description: githubToken.source });
    const verdict = await judgeMissionDesignEvidence(request, evidence, {
        backendUrl,
        githubToken: githubToken.token,
        model: process.env.AGENTHUB_E2E_DESIGN_JUDGE_MODEL || process.env.AGENTHUB_E2E_LLM_JUDGE_MODEL || "gpt-4o-mini",
    });
    await testInfo.attach("designer-judge-verdict.json", {
        body: JSON.stringify(verdict, null, 2),
        contentType: "application/json",
    });
    expect(verdict.blockingIssues, JSON.stringify(verdict, null, 2)).toEqual([]);
    expect(verdict.pass, JSON.stringify(verdict, null, 2)).toBe(true);
    expect(verdict.scores.modernity, JSON.stringify(verdict, null, 2)).toBeGreaterThanOrEqual(8);
    expect(verdict.scores.clarity, JSON.stringify(verdict, null, 2)).toBeGreaterThanOrEqual(8);
    expect(verdict.scores.fabricColorMotivation, JSON.stringify(verdict, null, 2)).toBeGreaterThanOrEqual(8);
    expect(verdict.scores.noOverlapPolish, JSON.stringify(verdict, null, 2)).toBeGreaterThanOrEqual(8);
    expect(verdict.scores.agenticWorkflowFit, JSON.stringify(verdict, null, 2)).toBeGreaterThanOrEqual(8);
}

async function readMissionMetrics(page: Page) {
    return page.locator(".mc3").evaluate((root) => {
        const log = root.querySelector<HTMLElement>(".canvas-log-stream") || root.querySelector<HTMLElement>(".mc3-chat-main") || root.querySelector<HTMLElement>(".mc3-dmc-canvas");
        const logScroll = root.querySelector<HTMLElement>(".canvas-log-stream");
        const intelligence = root.querySelector<HTMLElement>(".mc3-intel");
        const logRect = log?.getBoundingClientRect();
        const scrollRect = logScroll?.getBoundingClientRect();
        const intelligenceRect = intelligence?.getBoundingClientRect();
        return {
            logWidth: logRect?.width ?? 0,
            logHeight: logRect?.height ?? 0,
            logScrollHeight: scrollRect?.height ?? 0,
            intelligenceWidth: intelligenceRect?.width ?? 0,
        };
    });
}

async function readSpaceUsageMetrics(page: Page) {
    return page.locator(".mc3").evaluate((root) => {
        const element = root as HTMLElement;
        const body = element.closest<HTMLElement>(".editor-group__body") || element.parentElement || element;
        const shell = element.querySelector<HTMLElement>(".mc3-terminal-shell");
        const log = element.querySelector<HTMLElement>(".canvas-log-stream") || element.querySelector<HTMLElement>(".mc3-chat-main") || element.querySelector<HTMLElement>(".mc3-dmc-canvas");
        const mc3Rect = element.getBoundingClientRect();
        const bodyRect = body.getBoundingClientRect();
        const shellRect = shell?.getBoundingClientRect();
        const logRect = log?.getBoundingClientRect();
        return {
            mc3Width: mc3Rect.width,
            bodyWidth: bodyRect.width,
            horizontalGap: Math.abs(mc3Rect.left - bodyRect.left) + Math.abs(mc3Rect.right - bodyRect.right),
            logShareOfShell: shellRect && logRect ? logRect.height / shellRect.height : 0,
        };
    });
}

async function assertCalmMissionStyling(page: Page) {
    const styles = await page.locator(".mc3").evaluate((root) => {
        const read = (selector: string) => {
            const element = root.querySelector<HTMLElement>(selector);
            if (!element) return null;
            const computed = window.getComputedStyle(element);
            return {
                animationName: computed.animationName,
                fontFamily: computed.fontFamily,
                borderRadius: computed.borderRadius,
            };
        };
        return {
            current: read(".mc3-exec-current"),
            meta: read(".mc3-transcript-row__meta span"),
            connection: read(".mc3-terminal-connection"),
            row: read(".mc3-transcript-row"),
        };
    });
    const checked = Object.values(styles).filter(Boolean) as Array<{ animationName: string; fontFamily: string; borderRadius: string }>;
    expect(checked.length).toBeGreaterThan(0);
    for (const style of checked) {
        expect(style.animationName).toBe("none");
        expect(style.fontFamily.toLowerCase()).toContain("segoe ui");
    }
}

async function readMainScrollMetrics(page: Page) {
    return page.locator(".agenthub-main").evaluate((main) => {
        const visibleHeight = (rect?: DOMRect) => rect
            ? Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0))
            : 0;
        const root = main.querySelector<HTMLElement>(".mc3");
        const compose = main.querySelector<HTMLElement>(".compose-page");
        const candidates = [
            root?.closest<HTMLElement>(".editor-group__body"),
            main,
            document.scrollingElement as HTMLElement | null,
            root,
        ].filter(Boolean) as HTMLElement[];
        const scrollContainer = candidates.find((node) => {
            const style = window.getComputedStyle(node);
            return style.overflowY !== "hidden" && node.scrollHeight > node.clientHeight + 10;
        }) || candidates[0];
        const shell = main.querySelector<HTMLElement>(".mc3-terminal-shell");
        const log = main.querySelector<HTMLElement>(".canvas-log-stream") || main.querySelector<HTMLElement>(".mc3-chat-main") || main.querySelector<HTMLElement>(".mc3-dmc-canvas");
        const transcriptRows = Array.from(main.querySelectorAll<HTMLElement>(".mc3-transcript-row"));
        const steering = main.querySelector<HTMLElement>(".mc3-steering");
        const scrollStyle = window.getComputedStyle(scrollContainer);
        const logStyle = log ? window.getComputedStyle(log) : null;
        const composeStyle = compose ? window.getComputedStyle(compose) : null;
        const rootStyle = root ? window.getComputedStyle(root) : null;
        const shellRect = shell?.getBoundingClientRect();
        const logRect = log?.getBoundingClientRect();
        const transcriptRowVisibleHeight = transcriptRows.reduce((max, row) => Math.max(max, visibleHeight(row.getBoundingClientRect())), 0);
        const steeringRect = steering?.getBoundingClientRect();
        return {
            scrollContainerClass: scrollContainer.className,
            scrollContainerClientHeight: scrollContainer.clientHeight,
            scrollContainerScrollHeight: scrollContainer.scrollHeight,
            scrollContainerScrollTop: scrollContainer.scrollTop,
            scrollContainerOverflowY: scrollStyle.overflowY,
            logOverflowY: logStyle?.overflowY ?? "",
            composeOverflowY: composeStyle?.overflowY ?? "",
            mc3OverflowY: rootStyle?.overflowY ?? "",
            viewportHeight: window.innerHeight,
            shellBottom: shellRect?.bottom ?? 0,
            logBottom: logRect?.bottom ?? 0,
            logHeight: logRect?.height ?? 0,
            logVisibleHeight: visibleHeight(logRect),
            transcriptRowVisibleHeight,
            steeringVisibleHeight: visibleHeight(steeringRect),
        };
    });
}

async function screenshotMain(page: Page, testInfo: TestInfo, name: string) {
    if (!WRITE_SCREENSHOTS) return;
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
    await capturePageSnapshot(page, testInfo, name);
}

async function screenshot(locator: ReturnType<Page["locator"]>, testInfo: TestInfo, name: string) {
    if (!WRITE_SCREENSHOTS) return;
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
    await pageFlush(locator);
    await captureLocatorSnapshot(locator, testInfo, name);
}

async function captureLocatorSnapshot(locator: ReturnType<Page["locator"]>, testInfo: TestInfo, name: string) {
    const docsPath = path.join(SCREENSHOT_DIR, name);
    try {
        await locator.screenshot({ path: docsPath, timeout: 3_000, animations: "disabled" });
        console.log(`[diag] Screenshot: ${docsPath}`);
        await testInfo.attach(name, { path: docsPath, contentType: "image/png" });
    } catch (error) {
        console.log(`[diag] Screenshot skipped for ${name}: ${error instanceof Error ? error.message : String(error)}`);
        await testInfo.attach(`${name}.skipped.txt`, {
            body: `Screenshot skipped: ${error instanceof Error ? error.message : String(error)}`,
            contentType: "text/plain",
        });
    }
}

async function capturePageSnapshot(page: Page, testInfo: TestInfo, name: string) {
    const docsPath = path.join(SCREENSHOT_DIR, name);
    try {
        await page.screenshot({ path: docsPath, timeout: 30_000, animations: "disabled", fullPage: false });
        console.log(`[diag] Screenshot: ${docsPath}`);
        await testInfo.attach(name, { path: docsPath, contentType: "image/png" });
    } catch (error) {
        console.log(`[diag] Screenshot skipped for ${name}: ${error instanceof Error ? error.message : String(error)}`);
        await testInfo.attach(`${name}.skipped.txt`, {
            body: `Screenshot skipped: ${error instanceof Error ? error.message : String(error)}`,
            contentType: "text/plain",
        });
    }
}

async function rasterizeLocator(locator: ReturnType<Page["locator"]>): Promise<string> {
    return locator.evaluate(async (node) => {
        const element = node as HTMLElement;
        const rect = element.getBoundingClientRect();
        const width = Math.max(1, Math.ceil(rect.width));
        const height = Math.max(1, Math.ceil(rect.height));
        const clone = element.cloneNode(true) as HTMLElement;
        clone.style.width = `${width}px`;
        clone.style.height = `${height}px`;
        clone.style.margin = "0";

        const css = Array.from(document.styleSheets)
            .map((sheet) => {
                try {
                    return Array.from(sheet.cssRules).map((rule) => rule.cssText).join("\n");
                } catch {
                    return "";
                }
            })
            .join("\n");
        const bodyStyles = window.getComputedStyle(document.body);
        const root = document.createElement("div");
        root.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
        root.style.width = `${width}px`;
        root.style.height = `${height}px`;
        root.style.overflow = "hidden";
        root.style.background = bodyStyles.backgroundColor || "#ffffff";
        root.appendChild(clone);

        const markup = new XMLSerializer().serializeToString(root);
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><foreignObject width="100%" height="100%"><html xmlns="http://www.w3.org/1999/xhtml"><head><style>${css}</style></head><body style="margin:0;background:${bodyStyles.backgroundColor || "#ffffff"};">${markup}</body></html></foreignObject></svg>`;
        const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
        try {
            const image = new Image();
            await new Promise<void>((resolve, reject) => {
                const timer = window.setTimeout(() => reject(new Error("SVG rasterization timed out")), 2_000);
                image.onload = () => {
                    window.clearTimeout(timer);
                    resolve();
                };
                image.onerror = () => {
                    window.clearTimeout(timer);
                    reject(new Error("SVG rasterization failed"));
                };
                image.src = url;
            });
            const canvas = document.createElement("canvas");
            canvas.width = width;
            canvas.height = height;
            const context = canvas.getContext("2d");
            if (!context) throw new Error("Canvas 2D context unavailable");
            context.drawImage(image, 0, 0);
            return canvas.toDataURL("image/png").split(",")[1] || "";
        } finally {
            URL.revokeObjectURL(url);
        }
    });
}

async function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
    let timer: NodeJS.Timeout | null = null;
    try {
        return await Promise.race([
            promise,
            new Promise<T>((_, reject) => {
                timer = setTimeout(() => reject(new Error(message)), ms);
            }),
        ]);
    } finally {
        if (timer) clearTimeout(timer);
    }
}

async function pageFlush(locator: ReturnType<Page["locator"]>) {
    await locator.waitFor({ state: "visible" });
}

async function assertNoCriticalTextOverflow(root: ReturnType<Page["locator"]>) {
    const overflows = await root.evaluate((el) => {
        const selectors = [
            "button:not(.fui-Tab)",
            ".mc3-log__bar-title",
            ".mc3-log__bar-meta",
            ".mc3-artifact__name",
            ".mc3-artifact__state",
            ".mc3-change-card__agent",
            ".mc3-change-card__status",
            ".mc3-rail__section-title",
            ".mc3-steering__btn",
            ".mc3-transcript-row__kind",
            ".mc3-execution-header__copy strong",
            ".mc3-summary-rail__header strong",
            ".mc3-summary-status",
            ".mc3-intel-change strong",
            ".mc3-intel-change__meta span",
        ].join(",");
        return Array.from(el.querySelectorAll<HTMLElement>(selectors))
            .filter((node) => {
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
            })
            .filter((node) => node.scrollWidth > node.clientWidth + 2 || node.scrollHeight > node.clientHeight + 2)
            .map((node) => `${node.className || node.tagName}: ${(node.textContent || "").trim().slice(0, 80)}`);
    });
    expect(overflows, `critical text should fit: ${overflows.join("; ")}`).toEqual([]);
}