import { test, expect, type Page, type TestInfo } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

import {
    disableMissionAnimations,
    makeSessionRecord,
    missionProgressStageEvents,
    runOverviewEvent,
    seedMissionAuth,
} from "./utils/missionEvidence";

const SESSION_ID = "mission-control-chat-execution-sample";
const SAMPLE_PROMPT = "Create an end to end solution (ingestion, transformation, semantic modelling and a report) which shows all Fabric items I have access to in a nice appropriate visualization. Work in folder tmp_wukasz3.";
const WORKSPACE_ID = "fabric-clawhub-workspace";
const WORKSPACE_NAME = "Fabric ClawHub";

async function attachExecutionScreenshot(page: Page, testInfo: TestInfo, label: string) {
    const body = await page.screenshot({ fullPage: false, animations: "disabled" });
    const evidenceDir = path.join(process.cwd(), "test-results", "mission-control-chat-execution");
    await fs.mkdir(evidenceDir, { recursive: true });
    await fs.writeFile(path.join(evidenceDir, `${label}.png`), body);
    await testInfo.attach(`${label}.png`, { body, contentType: "image/png" });
}

async function setupChatExecutionHarness(page: Page) {
    const liveEvents: any[] = [];
    const sentMessages: any[] = [];
    await seedMissionAuth(page);
    await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        const pathName = url.pathname;
        const terminal = liveEvents.some((event) => ["job_complete", "job_failed", "job_cancelled"].includes(String(event?.type || "")));
        const sessionStatus = terminal ? "completed" : "running";

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}/events`)) {
            await route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                headers: { "Cache-Control": "no-cache" },
                body: `data: ${JSON.stringify(runOverviewEvent(SESSION_ID, 1))}\n\n`,
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}/events.json`)) {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                json: {
                    sessionId: SESSION_ID,
                    source: "persisted",
                    liveExecution: !terminal,
                    sessionStatus,
                    persistedTotal: liveEvents.length,
                    count: liveEvents.length,
                    events: liveEvents,
                },
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${SESSION_ID}`)) {
            await route.fulfill({
                status: 200,
                json: {
                    ...makeSessionRecord(SESSION_ID, sessionStatus as "running" | "completed"),
                    task_description: SAMPLE_PROMPT,
                    workspace_id: WORKSPACE_ID,
                    runtime: "dynamic",
                    context: {
                        workspace_name: WORKSPACE_NAME,
                        runtime: "pi",
                        orchestration_runtime: "pi",
                        subagent_runtime: "pi-subagents",
                        context_items: [
                            { id: WORKSPACE_ID, name: WORKSPACE_NAME, type: "workspace" },
                            { id: "fabricitems-lakehouse", name: "FabricItems_Lakehouse", type: "lakehouse", workspaceId: WORKSPACE_ID },
                            { id: "pipeline-1", name: "Pipeline_1", type: "pipeline", workspaceId: WORKSPACE_ID },
                        ],
                        prompt_attachments: [
                            { id: "prompt-overview", name: "task_prompt_overview.png", kind: "image", size: 142_336 },
                            { id: "workshop-pdf", name: "Fabric workshop (1).pdf", kind: "pdf", size: 1_884_160 },
                        ],
                        pi_orchestration: {
                            runtime: "pi",
                            subagent_runtime: "pi-subagents",
                            runtime_package_name: "@mariozechner/pi-agent-core",
                            frontend_runtime_package_name: "@mariozechner/pi-web-ui",
                            execution_surface_extension: "@fabric-clawhub/pi-mission-ui",
                            stream_transport: "agenthub-sse-to-pi-extension",
                        },
                    },
                },
            });
            return;
        }

        if (method === "POST" && pathName.endsWith(`/api/sessions/${SESSION_ID}/message`)) {
            const body = JSON.parse(request.postData() || "{}");
            sentMessages.push(body);
            await route.fulfill({ status: 200, json: { status: "queued", steeringId: `chat-${sentMessages.length}`, targetCount: 1 } });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });
    return {
        liveEvents,
        sentMessages,
        pushEvents: (...events: any[]) => liveEvents.push(...events),
    };
}

async function readChatUxMetrics(page: Page) {
    return page.locator(".mc3").evaluate((root) => {
        const normalizedText = (value: string | null | undefined) => (value || "").replace(/\s+/g, " ").trim();
        const rectFor = (selector: string) => {
            const node = root.querySelector<HTMLElement>(selector);
            if (!node) return null;
            const rect = node.getBoundingClientRect();
            return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
        };
        const styleFor = (selector: string, pseudo?: string) => {
            const node = root.querySelector<HTMLElement>(selector);
            if (!node) return null;
            const style = window.getComputedStyle(node, pseudo);
            return {
                display: style.display,
                content: style.content,
                overflowY: style.overflowY,
                backgroundImage: style.backgroundImage,
                borderImageSource: style.borderImageSource,
                boxShadow: style.boxShadow,
            };
        };
        const overlapArea = (a: ReturnType<typeof rectFor>, b: ReturnType<typeof rectFor>) => {
            if (!a || !b) return 0;
            const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
            const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
            return Math.round(x * y);
        };
        const topbar = rectFor(".mc3-chat-topbar");
        const chatScroll = root.querySelector<HTMLElement>(".mc3-chat-scroll");
        const chatRect = rectFor(".mc3-chat-scroll");
        const composer = rectFor(".mc3-steering");
        const summary = rectFor(".mc3-summary-rail");
        const shellBefore = styleFor(".mc3-chat-shell", "::before");
        const topbarText = normalizedText(root.querySelector(".mc3-chat-topbar")?.textContent);
        const promptText = normalizedText(root.querySelector('[data-mission-prompt-message="true"]')?.textContent);
        const overflowNodes = Array.from(root.querySelectorAll<HTMLElement>([
            ".mc3-chat-topbar__title strong",
            ".mc3-chat-bubble",
            ".mc3-summary-section",
            ".mc3-steering__btn",
            ".ctx-pill-name",
        ].join(","))).filter((node) => node.scrollWidth > node.clientWidth + 2 || node.scrollHeight > node.clientHeight + 3);
        const rowRects = Array.from(root.querySelectorAll<HTMLElement>(".mc3-chat-scroll > .mc3-chat-message, .mc3-chat-scroll > .mc3-agent-lanes"))
            .map((node) => {
                const rect = node.getBoundingClientRect();
                return { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, height: rect.height, text: normalizedText(node.textContent).slice(0, 80) };
            });
        const rowOverlaps = rowRects.slice(1).map((rect, index) => {
            const previous = rowRects[index];
            const x = Math.max(0, Math.min(previous.right, rect.right) - Math.max(previous.left, rect.left));
            const y = Math.max(0, Math.min(previous.bottom, rect.bottom) - Math.max(previous.top, rect.top));
            return { index, pixels: Math.round(x * y), vertical: Math.round(y), previous: previous.text, current: rect.text };
        }).filter((item) => item.pixels > 1);
        return {
            topbarText,
            promptText,
            promptInTopbar: topbarText.includes("Create an end to end solution"),
            promptMessageVisible: !!root.querySelector('[data-mission-prompt-message="true"]'),
            contextChipText: normalizedText(root.querySelector(".mc3-context-pills")?.textContent),
            chatOverflowY: window.getComputedStyle(chatScroll!).overflowY,
            chatClientHeight: chatScroll?.clientHeight ?? 0,
            chatScrollHeight: chatScroll?.scrollHeight ?? 0,
            rowCount: root.querySelectorAll(".mc3-transcript-row").length,
            summaryVisible: !!root.querySelector(".mc3-summary-rail"),
            composerVisible: !!root.querySelector(".mc3-steering"),
            frameBeforeDisplay: shellBefore?.display ?? null,
            frameBeforeContent: shellBefore?.content ?? null,
            shellStyle: styleFor(".mc3-chat-shell"),
            overlaps: {
                topbarChat: overlapArea(topbar, chatRect),
                chatComposer: overlapArea(chatRect, composer),
                chatSummary: overlapArea(chatRect, summary),
                composerSummary: overlapArea(composer, summary),
            },
            rowOverlaps,
            overflows: overflowNodes.map((node) => `${node.className}: ${normalizedText(node.textContent).slice(0, 120)}`).slice(0, 20),
        };
    });
}

test.use({ viewport: { width: 1440, height: 960 } });

test.describe("Mission Control chat execution experience", () => {
    test("streams a sample prompt through the chat layout with screenshots during execution", async ({ page }, testInfo) => {
        test.setTimeout(120_000);
        const harness = await setupChatExecutionHarness(page);
        const tape = missionProgressStageEvents(SESSION_ID);

        await page.goto(`/agent-hub/session/${SESSION_ID}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableMissionAnimations(page);

        const execution = page.getByRole("region", { name: "Mission execution" });
        const log = page.getByRole("log", { name: "Mission log stream" });
        await expect(execution).toBeVisible({ timeout: 30_000 });
        await expect(log).toBeVisible({ timeout: 10_000 });
        await expect(page.locator('[data-mission-prompt-message="true"]')).toContainText(SAMPLE_PROMPT, { timeout: 10_000 });
        await expect(page.locator(".mc3-chat-topbar")).not.toContainText(SAMPLE_PROMPT);
        await expect(page.locator(".mc3-context-pills")).toContainText(WORKSPACE_NAME);
        await expect(page.locator(".mc3-context-pills")).toContainText("FabricItems_Lakehouse");
        await expect(page.locator(".mc3-context-pills")).toContainText("Pipeline_1");
        await expect(page.locator(".mc3-context-pills")).toContainText("task_prompt_overview.png");
        await expect(page.locator(".mc3-context-pills")).toContainText("Fabric workshop (1).pdf");
        await expect(page.getByLabel("Mission intelligence")).toBeVisible();
        await expect(page.getByLabel("Mission steering")).toBeVisible();
        await attachExecutionScreenshot(page, testInfo, "01-initial-prompt-context");

        harness.pushEvents(...tape.plan, ...tape.llm, ...tape.specialist, ...tape.toolProgress);
        await expect(log).toContainText("I will inspect the existing report definition", { timeout: 10_000 });
        await expect(log).toContainText("workspace scan started", { timeout: 10_000 });
        await expect(page.getByLabel("Active agent execution")).toHaveCount(0);
        await attachExecutionScreenshot(page, testInfo, "02-during-tool-progress");

        harness.pushEvents(...tape.rollup, ...tape.approval);
        await expect(log).toContainText("Prepared workspace inventory repair evidence", { timeout: 10_000 });
        await expect(page.getByLabel("Mission intelligence")).toContainText("Needs approval", { timeout: 10_000 });
        await attachExecutionScreenshot(page, testInfo, "03-approval-state");

        await page.getByLabel("Steer mission").fill("Keep the output evidence visible in the right pane.");
        await page.getByRole("button", { name: "Send" }).click({ force: true });
        await expect.poll(() => harness.sentMessages.length, { timeout: 10_000 }).toBe(1);
        expect(harness.sentMessages[0]).toMatchObject({ mode: "queue", message: "Keep the output evidence visible in the right pane." });

        harness.pushEvents(...tape.steering, ...tape.verification, ...tape.terminal);
        await expect(log).toContainText("Verifier REJECTED", { timeout: 10_000 });
        await expect(page.getByLabel("Mission intelligence")).toContainText("Workspace Inventory report definition", { timeout: 10_000 });
        await attachExecutionScreenshot(page, testInfo, "04-settled-with-summary-output");

        const metrics = await readChatUxMetrics(page);
        const evidenceDir = path.join(process.cwd(), "test-results", "mission-control-chat-execution");
        await fs.mkdir(evidenceDir, { recursive: true });
        await fs.writeFile(path.join(evidenceDir, "chat-execution-ux-metrics.json"), JSON.stringify(metrics, null, 2));
        await testInfo.attach("chat-execution-ux-metrics.json", {
            body: JSON.stringify(metrics, null, 2),
            contentType: "application/json",
        });
        expect(metrics.promptMessageVisible).toBe(true);
        expect(metrics.promptInTopbar).toBe(false);
        expect(metrics.contextChipText).toContain("FabricItems_Lakehouse");
        expect(metrics.contextChipText).toContain("Fabric workshop (1).pdf");
        expect(metrics.chatOverflowY).not.toBe("hidden");
        expect(metrics.chatClientHeight).toBeGreaterThan(420);
        expect(metrics.chatScrollHeight).toBeGreaterThan(metrics.chatClientHeight);
        expect(metrics.rowCount).toBeGreaterThan(5);
        expect(metrics.summaryVisible).toBe(true);
        expect(metrics.composerVisible).toBe(true);
        expect(metrics.frameBeforeDisplay).toBe("none");
        expect(metrics.overlaps.topbarChat).toBe(0);
        expect(metrics.overlaps.chatComposer).toBe(0);
        expect(metrics.overlaps.composerSummary).toBe(0);
        expect(metrics.rowOverlaps).toEqual([]);
        expect(metrics.overflows).toEqual([]);
    });
});
