import { test, expect, type Page, type TestInfo } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const SESSION_ID = "mission-control-reference-visual";
const SCREENSHOT_DIR = path.resolve(__dirname, "../../docs/screenshots/mission-control-reference");

test.use({ viewport: { width: 2048, height: 1200 } });

test.describe("Mission Control reference visual", () => {
    test("matches the dynamic mission canvas and change-ledger style", async ({ page }, testInfo) => {
        await seedAuth(page);
        await mockMissionApis(page);
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByRole("region", { name: "Agent mission canvas" })).toBeVisible();
        await expect(page.locator(".agent-node").first()).toBeVisible();
        await expect(page.locator(".change-ledger")).toBeVisible();
        await expect(page.locator(".ledger-section", { hasText: /updated/i })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Generalist" })).toBeVisible();

        await activateTab(page, /Detailed/i);
        await expect(page.locator(".log-window", { hasText: /completed in|refreshing|casting/i }).first()).toBeVisible();
        await screenshot(page, page.getByRole("region", { name: "Agent mission canvas" }), testInfo, "live-canvas-reference.png");
        await screenshot(page, page.locator(".change-ledger"), testInfo, "change-ledger-reference.png");
        await screenshot(page, page.locator(".mc3"), testInfo, "mission-control-reference.png");

        await page.getByRole("button", { name: "Show agent log stream" }).click();
        await expect(page.locator(".canvas-log-stream")).toBeVisible();
        await expect(page.locator(".canvas-log-row__agent", { hasText: /Generalist|FabricDataEngineer|FabricAdmin|SalesReporter/ }).first()).toBeVisible();
        await screenshot(page, page.getByRole("region", { name: "Agent mission canvas" }), testInfo, "live-canvas-log-stream-reference.png");
        await page.getByRole("button", { name: "Show agent cards" }).click();
        await expect(page.locator(".agent-node").first()).toBeVisible();

        await expect(page.getByRole("button", { name: "Reset agent layout" })).toBeVisible();
        await expect(page.locator(".agent-card-resize").first()).toBeVisible();
        await assertAgentNodesDoNotOverlap(page);

        await page.setViewportSize({ width: 1789, height: 768 });
        await page.locator(".mc3-dmc-canvas").evaluate((element: HTMLElement) => { element.scrollTop = 0; element.scrollLeft = 0; });
        await expect(page.locator(".mc3-dmc-grid")).toBeVisible();
        await assertAgentNodesDoNotOverlap(page);
        await screenshot(page, page.getByRole("region", { name: "Agent mission canvas" }), testInfo, "live-canvas-fabric-shell-reference.png");
        await screenshot(page, page.locator(".mc3"), testInfo, "mission-control-fabric-shell-reference.png");

        await page.setViewportSize({ width: 1366, height: 720 });
        await page.locator(".mc3-dmc-canvas").evaluate((element: HTMLElement) => { element.scrollTop = 0; element.scrollLeft = 0; });
        await expect(page.locator(".canvas-log-stream")).toBeVisible();
        await expect(page.locator(".canvas-log-stream--auto")).toBeVisible();
        await expect(page.getByRole("button", { name: "Show agent cards" })).toBeDisabled();
        await screenshot(page, page.getByRole("region", { name: "Agent mission canvas" }), testInfo, "live-canvas-resized-log-stream-reference.png");
        await screenshot(page, page.locator(".mc3"), testInfo, "mission-control-resized-reference.png");

        const metrics = await page.locator(".mc3-dmc-live").evaluate((el) => {
            const canvas = el.querySelector<HTMLElement>(".agent-canvas");
            const first = el.querySelector<HTMLElement>(".agent-node") || el.querySelector<HTMLElement>(".canvas-log-row");
            const terminal = el.querySelector<HTMLElement>(".log-window") || el.querySelector<HTMLElement>(".canvas-log-stream");
            const ledger = el.querySelector<HTMLElement>(".right-rail .change-ledger");
            return {
                background: getComputedStyle(canvas || el as HTMLElement).backgroundColor,
                firstRadius: first ? getComputedStyle(first).borderRadius : "",
                terminalBg: terminal ? getComputedStyle(terminal).backgroundColor : "",
                terminalRadius: terminal ? getComputedStyle(terminal).borderRadius : "",
                ledgerBg: ledger ? getComputedStyle(ledger).backgroundColor : "",
            };
        });
        expect(metrics.firstRadius).toBeTruthy();
        expect(metrics.terminalRadius).toBeTruthy();
        expect(metrics.ledgerBg).toBeTruthy();
    });

    test("stops polling when a persisted session has no active event stream", async ({ page }) => {
        await seedAuth(page);
        const requestCounts = await mockOrphanedSessionApis(page);
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByText(/No live event stream is available/i)).toBeVisible({ timeout: 10_000 });
        await page.waitForTimeout(6_500);

        expect(requestCounts.events, "SSE events endpoint should not reconnect forever after 404").toBeLessThanOrEqual(1);
        expect(requestCounts.session, "full-session recovery reads should stop after stream 404 is classified").toBeLessThanOrEqual(3);
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

async function activateTab(page: Page, name: RegExp) {
    const tabList = page.getByRole("tablist", { name: "Log category" });
    const tab = tabList.getByRole("tab", { name });
    await expect(tab).toBeVisible();
    await tab.evaluate((element: HTMLElement) => element.click());
    await expect(tab).toHaveAttribute("aria-selected", "true");
}

async function assertAgentNodesDoNotOverlap(page: Page) {
    const overlaps = await page.locator(".mc3-dmc-canvas .agent-node").evaluateAll((nodes) => {
        const boxes = nodes.map((node) => {
            const rect = (node as HTMLElement).getBoundingClientRect();
            return {
                label: (node as HTMLElement).innerText.split("\n")[0] || (node as HTMLElement).dataset.agentNodeId || "node",
                left: rect.left,
                top: rect.top,
                right: rect.right,
                bottom: rect.bottom,
                area: rect.width * rect.height,
            };
        });
        const found: string[] = [];
        for (let i = 0; i < boxes.length; i += 1) {
            for (let j = i + 1; j < boxes.length; j += 1) {
                const a = boxes[i];
                const b = boxes[j];
                const width = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
                const height = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
                const overlap = width * height;
                const ratio = overlap / Math.max(1, Math.min(a.area, b.area));
                if (ratio > 0.08) found.push(`${a.label} overlaps ${b.label} by ${Math.round(ratio * 100)}%`);
            }
        }
        return found;
    });
    expect(overlaps).toEqual([]);
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
