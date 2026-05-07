import { test, expect, type Page, type TestInfo } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const SESSION_ID = "mission-control-reference-visual";
const SCREENSHOT_DIR = path.resolve(__dirname, "../../docs/screenshots/mission-control-reference");

test.use({ viewport: { width: 2048, height: 1200 } });

test.describe("Mission Control reference visual", () => {
    test("matches the full-bleed mission execution reference style", async ({ page }, testInfo) => {
        await seedAuth(page);
        await mockMissionApis(page);
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        const executionSurface = page.getByRole("region", { name: "Mission execution" });
        const missionLogs = page.getByRole("region", { name: "Mission logs" });
        await expect(executionSurface).toBeVisible();
        await expect(page.locator(".mc3-terminal-window-dots")).toHaveCount(0);
        await expect(executionSurface).not.toContainText("AgentHub-Code");
        await expect(page.getByText(/Sales Operations · Normalize regional sales data/i)).toBeVisible();
        await expect(page.getByLabel("Active agent execution")).toContainText("FabricDataEngineer");
        await expect(page.getByLabel("Active agent execution")).toContainText("SalesReporter");
        const schemaRow = missionLogs.locator(".mc3-transcript-row", { hasText: "Completed substep with 3 activity updates" }).first();
        await expect(schemaRow).toBeVisible();
        await schemaRow.getByRole("button", { name: /Show details/ }).click({ force: true });
        await expect(schemaRow.getByText(/currency column type conflict/i)).toBeVisible();
        const reporterRow = missionLogs.locator(".mc3-transcript-row", { hasText: "Completed substep with 2 activity updates" }).first();
        await expect(reporterRow).toBeVisible();
        await reporterRow.getByRole("button", { name: /Show details/ }).click({ force: true });
        await expect(reporterRow.getByText(/Refreshing Sales Weekly semantic model/i)).toBeVisible();
        await expect(page.getByLabel("Mission intelligence")).toContainText(/Latest update|Attention|Needs approval/i);
        await expect(page.getByRole("tablist", { name: "Log category" })).toHaveCount(0);
        await screenshot(page, executionSurface, testInfo, "mission-execution-surface-reference.png");
        await screenshot(page, page.locator(".mc3"), testInfo, "mission-control-reference.png");

        await page.setViewportSize({ width: 1789, height: 768 });
        await expect(executionSurface).toBeVisible();
        await expect(page.getByRole("log", { name: "Mission log stream" })).toBeVisible();
        await screenshot(page, page.locator(".mc3"), testInfo, "mission-control-fabric-shell-reference.png");

        await page.setViewportSize({ width: 1366, height: 720 });
        await expect(page.getByRole("log", { name: "Mission log stream" })).toBeVisible();
        await screenshot(page, page.locator(".mc3"), testInfo, "mission-control-resized-reference.png");

        const metrics = await page.locator(".mc3").evaluate((el) => {
            const first = el.querySelector<HTMLElement>(".mc3-transcript-row") || el.querySelector<HTMLElement>(".mc3-exec-row");
            const terminalSurface = el.querySelector<HTMLElement>(".mc3-terminal-shell") || el.querySelector<HTMLElement>(".mc3-log");
            const intelligence = el.querySelector<HTMLElement>(".mc3-intel");
            const terminalStyle = terminalSurface ? getComputedStyle(terminalSurface) : null;
            return {
                background: getComputedStyle(el as HTMLElement).backgroundColor,
                firstRadius: first ? getComputedStyle(first).borderRadius : "",
                terminalBg: terminalStyle?.backgroundColor ?? "",
                terminalRadius: terminalStyle?.borderRadius ?? "",
                terminalShadow: terminalStyle?.boxShadow ?? "",
                intelligenceBg: intelligence ? getComputedStyle(intelligence).backgroundColor : "",
            };
        });
        expect(metrics.firstRadius).toBeTruthy();
        expect(metrics.terminalRadius).toBe("8px");
        expect(metrics.terminalShadow).not.toBe("none");
        expect(metrics.intelligenceBg).toBeTruthy();
    });

    test("stops polling when a persisted session has no active event stream", async ({ page }) => {
        await seedAuth(page);
        const requestCounts = await mockOrphanedSessionApis(page);
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByText(/No live event stream is available/i).last()).toBeVisible({ timeout: 10_000 });
        await page.waitForTimeout(6_500);

        expect(requestCounts.events, "SSE events endpoint should not reconnect forever after 404").toBeLessThanOrEqual(1);
        expect(requestCounts.session, "full-session recovery reads should stop after stream 404 is classified").toBeLessThanOrEqual(3);
    });

    test("keeps snapshot progress visible while an empty running stream reconnects", async ({ page }) => {
        await seedAuth(page);
        const requestCounts = await mockEmptyReplaySessionApis(page);
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.locator(".mc3-agent-lanes__status", { hasText: /Live event stream is attaching/i })).toBeVisible({ timeout: 10_000 });
        await expect(page.locator(".mc3-exec-row--live")).toHaveCount(2);
        await page.waitForTimeout(2_500);

        expect(requestCounts.events, "empty non-terminal streams should keep retrying while snapshot progress is visible").toBeGreaterThanOrEqual(1);
        expect(requestCounts.eventsJson, "event ledger catch-up should remain bounded during the short retry window").toBeLessThanOrEqual(8);
        expect(requestCounts.session, "full-session recovery reads should remain bounded during the short retry window").toBeLessThanOrEqual(8);
    });

    test("catches up live backend logs without frontend category filtering", async ({ page }, testInfo) => {
        test.setTimeout(90_000);
        await seedAuth(page);
        const liveEvents: any[] = [];
        await mockLiveCatchupApis(page, liveEvents);
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        const executionSurface = page.getByRole("region", { name: "Mission execution" });
        await expect(executionSurface).toBeVisible({ timeout: 30_000 });
        const liveLog = page.getByRole("log", { name: "Mission log stream" });
        await expect(liveLog).toBeVisible({ timeout: 10_000 });

        liveEvents.push(catchupEvent(2, "mission_seeded", "high_level", {
            taskCount: 3,
        }));
        await expect(liveLog.getByText(/Generalist created the mission plan: 3 tasks queued/i).last()).toBeVisible({ timeout: 8_000 });
        await screenshot(page, executionSurface, testInfo, "live-log-streaming-01-high.png");

        liveEvents.push(catchupEvent(3, "generalist_context_pack", "detailed", {
            runId: "run-fde",
            taskId: "task-inventory",
            agentId: "FabricDataEngineer",
            agentName: "FabricDataEngineer",
            taskTitle: "Build workspace inventory tables",
            objectivePreview: "Read accessible Fabric items, normalize metadata, and persist inventory rows for reporting.",
            toolScopeCount: 4,
            upstreamResultCount: 1,
            acceptanceCriteriaCount: 3,
            contextDigest: "ctx-fde-001",
        }));
        await expect(liveLog.getByText(/Generalist delegated structured context to FabricDataEngineer/i).last()).toBeVisible({ timeout: 8_000 });
        await screenshot(page, executionSurface, testInfo, "live-log-streaming-02-detailed.png");

        liveEvents.push(catchupEvent(4, "log_line", "diagnostic", {
            agentId: "run-fde",
            agentName: "FabricDataEngineer",
            level: "info",
            message: "Read workspace inventory returned 42 Fabric items across 6 item types; continuing semantic model validation.",
        }));
        await expect(liveLog.getByText(/42 Fabric items across 6 item types/i).last()).toBeVisible({ timeout: 8_000 });

        // Verify backend tool_progress lines (the rich step-by-step progress
        // emitted from inside long-running MCP tools) reach the UI as
        // diagnostic log entries with elapsed-time context.
        liveEvents.push(catchupEvent(5, "tool_progress", "diagnostic", {
            agentId: "run-fde",
            agentName: "FabricDataEngineer",
            toolName: "fabric_create_workspace_inventory_solution",
            step: "lakehouse_table_validation",
            status: "started",
            elapsedMs: 147466,
        }));
        liveEvents.push(catchupEvent(6, "tool_progress", "diagnostic", {
            agentId: "run-fde",
            agentName: "FabricDataEngineer",
            toolName: "fabric_create_workspace_inventory_solution",
            step: "report_render_validation",
            status: "failed",
            elapsedMs: 240015,
            error: "Power BI ExportTo accepted the request but did not return an export id.",
        }));
        await expect(liveLog.getByText(/lakehouse table validation started · 147s elapsed/i).last()).toBeVisible({ timeout: 8_000 });
        await expect(liveLog.getByText(/report render validation failed · 240s elapsed/i).last()).toBeVisible({ timeout: 8_000 });
        await screenshot(page, executionSurface, testInfo, "live-log-streaming-03-diagnostic.png");

        await expect(liveLog.getByText(/Generalist created the mission plan/i).last()).toBeVisible();
        await expect(liveLog.getByText(/delegated structured context/i).last()).toBeVisible();
        await expect(liveLog.getByText(/42 Fabric items/i).last()).toBeVisible();
        await expect(page.getByRole("tablist", { name: "Log category" })).toHaveCount(0);
        await screenshot(page, executionSurface, testInfo, "live-log-streaming-04-all-visible.png");
    });
});

async function seedAuth(page: Page) {
    await page.addInitScript(() => {
        window.localStorage.clear();
        window.sessionStorage.clear();
        window.localStorage.setItem("github_token", "e2e-reference-token");
        window.localStorage.setItem("github_user", "reference.e2e");
        window.sessionStorage.setItem("github_token", "e2e-reference-token");
        window.sessionStorage.setItem("github_user", "reference.e2e");
        window.sessionStorage.setItem("workspace_id", "workspace-reference");
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

async function mockOrphanedSessionApis(page: Page) {
    const counts = { events: 0, session: 0 };
    await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        const pathName = url.pathname;

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}/events`)) {
            counts.events += 1;
            await route.fulfill({ status: 404, contentType: "application/json", json: { detail: "No active execution for this session" } });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}`)) {
            counts.session += 1;
            await route.fulfill({ status: 200, json: makeSessionRecord("running") });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });
    return counts;
}

async function mockEmptyReplaySessionApis(page: Page) {
    const counts = { events: 0, eventsJson: 0, session: 0 };
    await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        const pathName = url.pathname;

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}/events.json`)) {
            counts.eventsJson += 1;
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                json: {
                    sessionId: SESSION_ID,
                    source: "persisted",
                    liveExecution: false,
                    sessionStatus: "running",
                    persistedTotal: 0,
                    count: 0,
                    events: [],
                },
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}/events`)) {
            counts.events += 1;
            await route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                headers: { "Cache-Control": "no-cache" },
                body: "",
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}`)) {
            counts.session += 1;
            await route.fulfill({ status: 200, json: makeSessionRecord("running") });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });
    return counts;
}

async function mockLiveCatchupApis(page: Page, liveEvents: any[]) {
    await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        const pathName = url.pathname;

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}/events.json`)) {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                json: {
                    sessionId: SESSION_ID,
                    source: "persisted",
                    count: liveEvents.length,
                    events: liveEvents,
                },
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}/events`)) {
            await route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                headers: { "Cache-Control": "no-cache" },
                body: `data: ${JSON.stringify({
                    type: "run_overview",
                    seq: 1,
                    sessionId: SESSION_ID,
                    ts: "2026-04-26T09:00:01.000Z",
                    job: { id: SESSION_ID, status: "running", startedAt: "2026-04-26T09:00:01.000Z", completedAt: null },
                    composition: missionComposition(),
                    activeAgentId: null,
                    artifacts: [],
                    changes: [],
                    slotProgress: [],
                })}\n\n`,
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}`)) {
            await route.fulfill({ status: 200, json: makeSessionRecord("running") });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });
}

function catchupEvent(seq: number, type: string, logCategory: string, extra: Record<string, unknown>) {
    return {
        type,
        seq,
        sessionId: SESSION_ID,
        ts: `2026-04-26T09:00:0${seq}.000Z`,
        logCategory,
        eventId: `${SESSION_ID}:${seq}`,
        payloadDigest: `digest-${seq}`,
        ...extra,
    };
}

function makeSessionRecord(status: "running" | "completed" = "completed") {
    const isCompleted = status === "completed";
    return {
        id: SESSION_ID,
        session_id: SESSION_ID,
        task_description: "Normalize regional sales data, certify the Gold dataset, and refresh the weekly report.",
        workspace_id: "workspace-reference",
        status,
        created_at: "2026-04-26T09:00:00.000Z",
        started_at: "2026-04-26T09:00:01.000Z",
        completed_at: isCompleted ? "2026-04-26T09:05:00.000Z" : null,
        context: { workspace_name: "Sales Operations" },
        agents: slots().map((slot) => ({
            agent_id: slot.agentId,
            role: slot.role,
            status: isCompleted || slot.status === "done" ? "completed" : "running",
            current_step: slot.role,
            session_id: slot.id,
        })),
        composition: missionComposition(),
    };
}

function slots() {
    return [
        { id: "system", agentId: "System", role: "delegated plan", status: "done" },
        { id: "fde-discovery", agentId: "FabricDataEngineer", role: "source discovery", status: "done" },
        { id: "fde-schema", agentId: "FabricDataEngineer", role: "schema alignment", status: "running" },
        { id: "admin", agentId: "FabricAdmin", role: "certified Gold dataset", status: "done" },
        { id: "reporter", agentId: "SalesReporter", role: "semantic model refresh", status: "running" },
    ];
}

function missionComposition() {
    return {
        sessionId: SESSION_ID,
        architecture: "dynamic",
        task: "Normalize regional sales data, certify Gold, refresh report.",
        slots: slots().map((slot) => ({
            id: slot.id,
            agentId: slot.agentId,
            role: slot.role,
            skills: [],
            status: slot.status,
        })),
        handoffs: [
            { from: "system", to: "fde-discovery", kind: "delegate" },
            { from: "fde-discovery", to: "fde-schema", kind: "report" },
            { from: "fde-schema", to: "admin", kind: "report" },
            { from: "admin", to: "reporter", kind: "report" },
        ],
    };
}

function missionEventStream() {
    const ts = "2026-04-26T09:00:03.000Z";
    const events = [
        {
            type: "run_overview", seq: 1, sessionId: SESSION_ID, ts,
            job: { id: SESSION_ID, status: "running", startedAt: "2026-04-26T09:00:01.000Z", completedAt: null },
            composition: missionComposition(),
            activeAgentId: "fde-schema",
            artifacts: [],
            slotProgress: slots().map((slot) => ({
                slotId: slot.id,
                agentId: slot.id,
                agentName: slot.agentId,
                role: slot.role,
                status: slot.status,
            })),
        },
        {
            type: "log_line", seq: 2, sessionId: SESSION_ID, ts,
            agentId: "system", agentName: "System", level: "info",
            message: "Split task into 3 workstreams. FDE -> ingest & normalize. Admin -> reconcile & certify. Reporter -> refresh & publish.",
        },
        {
            type: "log_line", seq: 3, sessionId: SESSION_ID, ts,
            agentId: "fde-discovery", agentName: "FabricDataEngineer", level: "info",
            message: "Scanning OneLake://corporate-sales-raw. Identified 4 missing partitions for 2024-Q3.",
        },
        {
            type: "log_line", seq: 4, sessionId: SESSION_ID, ts,
            agentId: "fde-schema", agentName: "FabricDataEngineer", level: "info",
            message: "Detected currency column type conflict between NA (String) and EU (Enum). Casting to ISO-4217 codes.",
        },
        {
            type: "tool_call_started", seq: 5, sessionId: SESSION_ID, ts,
            agentId: "fde-schema", agentName: "FabricDataEngineer", callId: "schema-align", toolName: "spark_authoring",
            argsPreview: {
                terminalLabel: "spark-authoring • schema_align.py",
                terminalLines: [
                    "> Reading raw partition ... 12,413,044 rows",
                    "> Reading eu partition ... 3,027,802 rows",
                    "> Casting currency -> ISO-4217 enum ...",
                    "> Writing sales_clean ...",
                ],
            },
        },
        {
            type: "tool_call_ended", seq: 6, sessionId: SESSION_ID, ts,
            agentId: "fde-schema", callId: "schema-align", toolName: "spark_authoring", durationMs: 32000, status: "ok",
        },
        {
            type: "approval_required", seq: 7, sessionId: SESSION_ID, ts,
            approvalId: "certify-gold", slotId: "admin", agentId: "FabricAdmin",
            summary: "Row counts reconcile at 99.994 % (delta 743 rows / 0.006 %) — within tolerance. Certifying Gold_Sales_DW.sales_clean will stamp the dataset as production-ready and visible to all dashboard consumers.",
            blastRadius: "row-level", reversible: true,
            toolCallPreview: {
                name: "sqldw.set_certification(workspace, table, certified=true)",
                args: {
                    "> source rows (NA+EU) .....": "15,440,846",
                    "> sales_clean rows ........": "15,440,103",
                    "> delta ...................": "743 (0.006%)",
                    "> tolerance ...............": "≤ 0.05% ✓",
                },
            },
            recoveryActions: ["approve", "request_alternative", "edit_input", "decline"],
        },
        {
            type: "change_recorded", seq: 8, sessionId: SESSION_ID, ts,
            recordId: "dependency-map", kind: "created", status: "applied",
            targetName: "dependency_map.json", targetType: "Evidence artifact", targetScope: "file",
            summary: "Producing the dependency evidence artifact for the sales refresh.",
            toolName: "artifact_writer", agentId: "fde-discovery", agentName: "FabricDataEngineer",
        },
        {
            type: "change_recorded", seq: 9, sessionId: SESSION_ID, ts,
            recordId: "gold-certification", kind: "updated", status: "applied",
            targetName: "Gold_Sales_DW certification", targetType: "Semantic model", targetScope: "item",
            summary: "Certification path prepared after reconciliation passed within tolerance.",
            toolName: "sqldw.set_certification", agentId: "admin", agentName: "FabricAdmin",
        },
        {
            type: "change_recorded", seq: 10, sessionId: SESSION_ID, ts,
            recordId: "legacy-access", kind: "deleted", status: "applied",
            targetName: "Legacy SalesReader access", targetType: "Access grant", targetScope: "access",
            summary: "Legacy access detected and marked for removal review.",
            toolName: "fabric.access_review", agentId: "admin", agentName: "FabricAdmin",
        },
        {
            type: "change_recorded", seq: 11, sessionId: SESSION_ID, ts,
            recordId: "refresh-window", kind: "important_action", status: "applied",
            targetName: "Schedule semantic model refresh", targetType: "Operational action", targetScope: "action",
            summary: "Weekly report refresh is queued after the newly certified Gold dataset is visible.",
            toolName: "powerbi_authoring", agentId: "reporter", agentName: "SalesReporter",
        },
        {
            type: "log_line", seq: 12, sessionId: SESSION_ID, ts,
            agentId: "admin", agentName: "FabricAdmin", level: "info",
            message: "HUMAN-APPROVED\nSet certified=true on Gold_Sales_DW.sales_clean. Handed off to SalesReporter.",
        },
        {
            type: "log_line", seq: 13, sessionId: SESSION_ID, ts,
            agentId: "reporter", agentName: "SalesReporter", level: "info",
            message: "Refreshing Sales Weekly semantic model against the newly-certified Gold dataset.",
        },
        {
            type: "tool_call_started", seq: 14, sessionId: SESSION_ID, ts,
            agentId: "reporter", agentName: "SalesReporter", callId: "refresh-model", toolName: "powerbi_authoring",
            argsPreview: {
                terminalLabel: "powerbi-authoring • refresh",
                terminalLines: [
                    "> triggering refresh id=aif2_c7",
                    "> tables: sales_clean, dim_region, dim_product",
                    "> progress: partition 2 of 3",
                ],
            },
        },
        {
            type: "run_overview", seq: 15, sessionId: SESSION_ID, ts: "2026-04-26T09:05:00.000Z",
            job: { id: SESSION_ID, status: "completed", startedAt: "2026-04-26T09:00:01.000Z", completedAt: "2026-04-26T09:05:00.000Z" },
            totalDuration: "5m 00s",
            composition: missionComposition(),
            activeAgentId: null,
            artifacts: [],
            slotProgress: slots().map((slot) => ({
                slotId: slot.id,
                agentId: slot.id,
                agentName: slot.agentId,
                role: slot.role,
                status: "done",
            })),
        },
    ];
    return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
}

async function disableAnimations(page: Page) {
    await page.addStyleTag({
        content: `
            *, *::before, *::after {
                animation: none !important;
                transition: none !important;
                scroll-behavior: auto !important;
            }
            .mc3-log__tail {
                display: none !important;
            }
        `,
    });
}

async function screenshot(page: Page, locator: ReturnType<Page["locator"]>, testInfo: TestInfo, name: string) {
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
    const outputPath = testInfo.outputPath(name);
    const docsPath = path.join(SCREENSHOT_DIR, name);
    await page.mouse.move(8, 8);
    const box = await locator.boundingBox();
    expect(box, `${name} target should have a bounding box`).toBeTruthy();
    await locator.screenshot({ path: outputPath, animations: "disabled", timeout: 15_000 });
    fs.copyFileSync(outputPath, docsPath);
    await testInfo.attach(name, { path: outputPath, contentType: "image/png" });
    console.log(`[visual] ${docsPath}`);
}
