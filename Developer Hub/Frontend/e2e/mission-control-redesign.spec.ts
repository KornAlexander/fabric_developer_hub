import { test, expect, type Page, type TestInfo } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const SESSION_ID = "mission-control-redesign-e2e";
const SCREENSHOT_DIR = path.resolve(__dirname, "../../docs/screenshots/mission-control-redesign");

test.use({ viewport: { width: 1440, height: 960 } });

test.describe("Mission Control redesign", () => {
    test("keeps logs and run overview reachable on short screens", async ({ page }) => {
        await page.setViewportSize({ width: 1440, height: 768 });
        await seedAuth(page);
        await mockMissionApis(page);
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.locator(".mc3-log")).toBeVisible();
        await expect(page.locator(".mc3-rail")).toBeVisible();

        const beforeScroll = await readMainScrollMetrics(page);
        expect(beforeScroll.scrollContainerOverflowY).not.toBe("hidden");
        expect(beforeScroll.composeOverflowY).not.toBe("hidden");
        expect(beforeScroll.mc3OverflowY).not.toBe("hidden");
        expect(beforeScroll.scrollContainerScrollHeight).toBeGreaterThan(beforeScroll.scrollContainerClientHeight + 80);

        await page.locator(".mc3").evaluate((root) => {
            const scrollContainer = root.closest<HTMLElement>(".editor-group__body")
                || document.querySelector<HTMLElement>(".agenthub-main");
            if (scrollContainer) scrollContainer.scrollTop = scrollContainer.scrollHeight;
        });
        const afterScroll = await readMainScrollMetrics(page);
        expect(afterScroll.scrollContainerScrollTop).toBeGreaterThan(80);
        expect(afterScroll.logBottom).toBeLessThanOrEqual(afterScroll.viewportHeight + 32);
        expect(afterScroll.railBottom).toBeLessThanOrEqual(afterScroll.viewportHeight + 32);
    });

    test("puts logs first and presents outputs without draft-noise", async ({ page }, testInfo) => {
        await seedAuth(page);
        await mockMissionApis(page);
        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableAnimations(page);

        await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
        await expect(page.locator(".mc3-log__bar-title")).toContainText("Full run log");
        await expect(page.locator(".mc3-rail__section-title", { hasText: "Outputs" })).toBeVisible();
        await expect(page.locator(".mc3-rail__section-title", { hasText: "Change overview" })).toBeVisible();
        await expect(page.locator(".mc3-change-overview__badge")).toHaveText("5 APPLIED");
        await expect(page.locator(".mc3-change-group__label", { hasText: /created/i })).toBeVisible();
        await expect(page.locator(".mc3-change-group__label", { hasText: /updated/i })).toBeVisible();
        await expect(page.locator(".mc3-change-group__label", { hasText: /deleted/i })).toBeVisible();
        await expect(page.locator(".mc3-change-group__label", { hasText: /important actions/i })).toBeVisible();
        await expect(page.locator(".mc3-change-card")).toHaveCount(5);
        await expect(page.locator(".mc3-change-overview")).not.toContainText(/read|queried|workspace inventory/i);
        await expect(page.getByText("Planned output")).toHaveCount(2);
        await expect(page.getByText("Draft · not yet written")).toHaveCount(0);
        await expect(page.getByText("Artifacts so far")).toHaveCount(0);
        await expect(page.getByText("Latest artifacts")).toHaveCount(0);

        const standardMetrics = await readMissionMetrics(page);
        expect(standardMetrics.logScrollHeight).toBeGreaterThanOrEqual(430);
        expect(standardMetrics.logWidth).toBeGreaterThanOrEqual(700);
        expect(standardMetrics.railWidth).toBeLessThanOrEqual(380);
        expect(standardMetrics.logWidth).toBeGreaterThan(standardMetrics.railWidth * 2);
        await assertNoCriticalTextOverflow(page.locator(".mc3"));
        await screenshot(page.locator(".mc3-change-overview"), testInfo, "change-overview.png");
        await screenshotMain(page, testInfo, "desktop-standard.png");

        await page.getByRole("tab", { name: "Expanded log" }).click();
        await expect(page.locator(".mc3")).toHaveClass(/mc3--log-focus/);
        await expect(page.locator(".mc3-rail")).toBeHidden();
        const expandedMetrics = await readMissionMetrics(page);
        expect(expandedMetrics.logWidth).toBeGreaterThan(standardMetrics.logWidth);
        expect(expandedMetrics.logHeight).toBeGreaterThanOrEqual(760);
        await screenshotMain(page, testInfo, "desktop-expanded-log.png");

        await page.getByRole("tab", { name: "Standard" }).click();
        await expect(page.locator(".mc3")).not.toHaveClass(/mc3--log-focus/);
        await expect(page.locator(".mc3-rail")).toBeVisible();

        await page.setViewportSize({ width: 390, height: 900 });
        await page.evaluate(() => window.dispatchEvent(new Event("resize")));
        await expect(page.locator(".mc3-log")).toBeVisible();
        const mobileMetrics = await readMissionMetrics(page);
        expect(mobileMetrics.logScrollHeight).toBeGreaterThanOrEqual(430);
        await assertNoCriticalTextOverflow(page.locator(".mc3"));
        await screenshotMain(page, testInfo, "mobile-standard.png");
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

function makeSessionRecord() {
    return {
        id: SESSION_ID,
        session_id: SESSION_ID,
        task_description: "Run a read-only operational inspection and produce a workspace item inventory summary.",
        workspace_id: "workspace-redesign",
        status: "completed",
        created_at: "2026-04-25T12:00:00.000Z",
        started_at: "2026-04-25T12:01:00.000Z",
        completed_at: "2026-04-25T12:02:12.000Z",
        context: { workspace_name: "UX Review Workspace" },
        agents: missionSlots().map((slot) => ({
            agent_id: slot.agentId,
            role: slot.role,
            status: "completed",
            current_step: "Execution complete",
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

function missionEventStream() {
    const ts = "2026-04-25T12:01:12.000Z";
    const events = [
        {
            type: "run_overview",
            seq: 1,
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
            seq: index + 2,
            sessionId: SESSION_ID,
            ts,
            agentId: index < 7 ? "admin" : index < 13 ? "engineer" : "modeler",
            agentName: index < 7 ? "FabricAdmin" : index < 13 ? "FabricDataEngineer" : "Modeler",
            level: "info",
            message: `Workspace inventory checkpoint ${index + 1}: reviewed Fabric item metadata and captured next action evidence.`,
        })),
        ...missionChanges(ts).map((change, index) => ({
            type: "change_recorded",
            seq: 21 + index,
            sessionId: SESSION_ID,
            ts,
            ...change,
        })),
        {
            type: "artifact_added",
            seq: 26,
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
            seq: 27,
            sessionId: SESSION_ID,
            ts,
            artifactId: "warehouse-target",
            agentId: "modeler",
            kind: "Warehouse",
            name: "Warehouse in workspace 8bdca8af",
            state: "draft",
        },
        {
            type: "run_overview",
            seq: 28,
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
        },
        { type: "job_complete", seq: 29, sessionId: SESSION_ID, ts, jobId: SESSION_ID, status: "completed", totalDuration: "00:01:12" },
    ];
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

async function readMissionMetrics(page: Page) {
    return page.locator(".mc3").evaluate((root) => {
        const log = root.querySelector<HTMLElement>(".mc3-log");
        const logScroll = root.querySelector<HTMLElement>(".mc3-log__scroll");
        const rail = root.querySelector<HTMLElement>(".mc3-rail");
        const logRect = log?.getBoundingClientRect();
        const scrollRect = logScroll?.getBoundingClientRect();
        const railRect = rail?.getBoundingClientRect();
        return {
            logWidth: logRect?.width ?? 0,
            logHeight: logRect?.height ?? 0,
            logScrollHeight: scrollRect?.height ?? 0,
            railWidth: railRect?.width ?? 0,
        };
    });
}

async function readMainScrollMetrics(page: Page) {
    return page.locator(".agenthub-main").evaluate((main) => {
        const root = main.querySelector<HTMLElement>(".mc3");
        const compose = main.querySelector<HTMLElement>(".compose-page");
        const scrollContainer = root?.closest<HTMLElement>(".editor-group__body") || main;
        const log = main.querySelector<HTMLElement>(".mc3-log");
        const rail = main.querySelector<HTMLElement>(".mc3-rail");
        const scrollStyle = window.getComputedStyle(scrollContainer);
        const composeStyle = compose ? window.getComputedStyle(compose) : null;
        const rootStyle = root ? window.getComputedStyle(root) : null;
        const logRect = log?.getBoundingClientRect();
        const railRect = rail?.getBoundingClientRect();
        return {
            scrollContainerClass: scrollContainer.className,
            scrollContainerClientHeight: scrollContainer.clientHeight,
            scrollContainerScrollHeight: scrollContainer.scrollHeight,
            scrollContainerScrollTop: scrollContainer.scrollTop,
            scrollContainerOverflowY: scrollStyle.overflowY,
            composeOverflowY: composeStyle?.overflowY ?? "",
            mc3OverflowY: rootStyle?.overflowY ?? "",
            viewportHeight: window.innerHeight,
            logBottom: logRect?.bottom ?? 0,
            railBottom: railRect?.bottom ?? 0,
        };
    });
}

async function screenshotMain(page: Page, testInfo: TestInfo, name: string) {
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
    await page.mouse.move(8, 8);
    await page.evaluate(async () => {
        await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });
    const outputPath = testInfo.outputPath(name);
    const docsPath = path.join(SCREENSHOT_DIR, name);
    await page.locator(".agenthub-main").screenshot({ path: outputPath, animations: "disabled" });
    fs.copyFileSync(outputPath, docsPath);
    await testInfo.attach(name, { path: outputPath, contentType: "image/png" });
}

async function screenshot(locator: ReturnType<Page["locator"]>, testInfo: TestInfo, name: string) {
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
    await pageFlush(locator);
    const outputPath = testInfo.outputPath(name);
    const docsPath = path.join(SCREENSHOT_DIR, name);
    await locator.screenshot({ path: outputPath, animations: "disabled" });
    fs.copyFileSync(outputPath, docsPath);
    await testInfo.attach(name, { path: outputPath, contentType: "image/png" });
}

async function pageFlush(locator: ReturnType<Page["locator"]>) {
    await locator.evaluate(async () => {
        await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });
}

async function assertNoCriticalTextOverflow(root: ReturnType<Page["locator"]>) {
    const overflows = await root.evaluate((el) => {
        const selectors = [
            "button",
            ".mc3-log__bar-title",
            ".mc3-log__bar-meta",
            ".mc3-artifact__name",
            ".mc3-artifact__state",
            ".mc3-change-card__agent",
            ".mc3-change-card__status",
            ".mc3-rail__section-title",
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