import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { mkdirSync } from "fs";
import { resolve } from "path";

const GIT_WORKSPACE_ID = "e2e-new-session-git-workspace";
const NON_GIT_WORKSPACE_ID = "e2e-new-session-local-workspace";
const GIT_WORKSPACE_NAME = "E2E Analytics Workspace";
const NON_GIT_WORKSPACE_NAME = "E2E Local Workspace";
const CONTEXT_WORKSPACE_ID = "e2e-new-session-context-workspace";
const SCREENSHOT_DIR = resolve(__dirname, "../../docs/screenshots/new-session-flow");
const START_MISSION_RE = /^Start mission$/i;

type MockOptions = {
    initialWorkspaceId?: string;
    failFirstCreate?: boolean;
};

type MockState = {
    createBodies: any[];
    createAttempts: number;
    runAttempts: number;
    failFirstCreate: boolean;
};

const WORKSPACES = [
    {
        id: GIT_WORKSPACE_ID,
        name: GIT_WORKSPACE_NAME,
        git_connected: true,
        git_provider: "GitHub",
        git_branch: "main",
        git_repo_name: "e2e-analytics",
    },
    {
        id: NON_GIT_WORKSPACE_ID,
        name: NON_GIT_WORKSPACE_NAME,
        git_connected: false,
        git_provider: null,
        git_branch: null,
        git_repo_name: null,
    },
    {
        id: CONTEXT_WORKSPACE_ID,
        name: "E2E Governance Workspace",
        git_connected: true,
        git_provider: "GitHub",
        git_branch: "main",
        git_repo_name: "e2e-governance",
    },
];

const WORKSPACE_ITEMS: Record<string, any[]> = {
    [GIT_WORKSPACE_ID]: [
        { id: "sales-lakehouse", name: "Curated Sales Lakehouse", type: "Lakehouse", owner: "Platform" },
        { id: "sales-model", name: "Sales Executive Model", type: "SemanticModel", owner: "BI" },
    ],
    [CONTEXT_WORKSPACE_ID]: [
        { id: "controls-warehouse", name: "Controls Warehouse", type: "Warehouse", owner: "Finance" },
    ],
    [NON_GIT_WORKSPACE_ID]: [],
};

test.describe.configure({ mode: "serial" });
test.use({ viewport: { width: 1366, height: 900 } });

test.describe("New Session flow polish and validation", () => {
    test("accepts long prompts, preserves keyboard focus, and keeps contrast accessible", async ({ page }, testInfo) => {
        await openNewSession(page, { initialWorkspaceId: GIT_WORKSPACE_ID });
        await snapshot(page, testInfo, "iteration-1-step1-empty.png");

        const promptBox = page.locator("#composer-task-text");
        const startButton = page.getByRole("button", { name: START_MISSION_RE });
        await expect(startButton).toBeDisabled();

        await setComposerText(page, "x".repeat(8_001));
        await expect(promptBox).not.toHaveAttribute("aria-invalid", "true");
        await expect(page.locator("#composer-task-meta")).toContainText("8,001 characters");
        await expect(page.locator("#composer-task-error")).toHaveCount(0);
        await expect(startButton).toBeEnabled();

        await setComposerText(page, "Create a governed sales KPI readiness plan.\nInclude data quality checks and report handoff notes.");
        await expect(promptBox).not.toHaveAttribute("aria-invalid", "true");
        await expect(page.locator("#composer-task-meta")).toContainText("97 characters");
        await expect(startButton).toBeEnabled();

        await page.keyboard.press("Tab");
        await expect(page.locator(":focus")).toBeVisible();
        await assertMinimumContrast(page, ".composer-submit-btn", 4.5);
        await assertMinimumContrast(page, ".composer-label", 4.5);
        await snapshot(page, testInfo, "iteration-2-step1-valid-prompt.png");
    });

    test("handles attachment edge cases without losing duplicate filenames", async ({ page }, testInfo) => {
        await openNewSession(page, { initialWorkspaceId: GIT_WORKSPACE_ID });

        const input = page.locator("#composer-file-input");
        await input.setInputFiles([
            { name: "brief.md", mimeType: "text/markdown", buffer: Buffer.from("# Brief\nReview sales KPIs.") },
            { name: "brief.md", mimeType: "text/markdown", buffer: Buffer.from("# Brief\nSecond copy with same name.") },
            { name: "inventory.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ item: "sales-lakehouse" })) },
        ]);

        await expect(page.locator(".ctx-pill", { hasText: "brief.md" })).toHaveCount(2);
        await expect(page.locator(".ctx-pill", { hasText: "inventory.json" })).toHaveCount(1);
        await page.getByLabel("Remove brief.md").first().click();
        await expect(page.locator(".ctx-pill", { hasText: "brief.md" })).toHaveCount(1);

        await input.setInputFiles({
            name: "installer.exe",
            mimeType: "application/octet-stream",
            buffer: Buffer.from([0, 1, 2, 3]),
        });
        await expect(page.locator(".composer-upload-error")).toContainText("not a supported attachment type");

        await input.setInputFiles({
            name: "huge.md",
            mimeType: "text/markdown",
            buffer: Buffer.alloc(10 * 1024 * 1024 + 1, "a"),
        });
        await expect(page.locator(".composer-upload-error")).toContainText("max is 10 MB per file");
        await snapshot(page, testInfo, "iteration-2-attachment-edge-cases.png");
    });

    test("preserves multiline prompt, context, toggles, branch metadata, and attachments when starting", async ({ page }, testInfo) => {
        const state = await openNewSession(page, { initialWorkspaceId: GIT_WORKSPACE_ID });
        await setComposerText(page, "Create a revenue quality readiness pack.\nUse the curated lakehouse and include executive notes.");

        await page.getByRole("button", { name: /^Add workspace$/i }).click();
        await page.getByRole("menuitem", { name: "E2E Governance Workspace" }).click();
        await page.locator("#composer-file-input").setInputFiles({
            name: "readiness-notes.md",
            mimeType: "text/markdown",
            buffer: Buffer.from("# Notes\nNeed variance checks."),
        });
        await page.getByRole("button", { name: /Require approvals/i }).click();
        await page.getByRole("button", { name: /^Branch out/i }).click();
        await page.getByLabel("Destination workspace name").fill("E2E Analytics Workspace - Revenue Quality");
        await page.getByLabel("Git branch name").fill("feature/revenue-quality-e2e");

        await page.getByRole("button", { name: START_MISSION_RE }).click();
        await expect.poll(() => state.runAttempts, { timeout: 20_000 }).toBe(1);
        await expect(page.locator(".mc-canvas-card", { hasText: "Team composition" })).toHaveCount(0);
        await snapshot(page, testInfo, "iteration-3-direct-start.png");

        expect(state.createBodies).toHaveLength(1);
        const body = state.createBodies[0];
        expect(body.task_description).toBe("Create a revenue quality readiness pack.\nUse the curated lakehouse and include executive notes.");
        expect(body.workspace_id).toBe(GIT_WORKSPACE_ID);
        expect(body.attachments).toEqual([
            expect.objectContaining({ name: "readiness-notes.md", kind: "text", mime: "text/markdown" }),
        ]);
        expect(body.context).toMatchObject({
            workspace_name: GIT_WORKSPACE_NAME,
            branch_out: true,
            branch_name: "feature/revenue-quality-e2e",
            destination_workspace_name: "E2E Analytics Workspace - Revenue Quality",
            require_approvals: true,
        });
        expect(body.context.context_items).toEqual(expect.arrayContaining([
            expect.objectContaining({ id: CONTEXT_WORKSPACE_ID, name: "E2E Governance Workspace", type: "workspace" }),
        ]));
    });

    test("recovers cleanly from a failed start request and supports retry", async ({ page }) => {
        const state = await openNewSession(page, { initialWorkspaceId: GIT_WORKSPACE_ID, failFirstCreate: true });
        await setComposerText(page, "Create a retryable plan for sales readiness.");

        await page.getByRole("button", { name: START_MISSION_RE }).click();
        await expect(page.locator(".compose-error")).toContainText("Planner temporarily unavailable", { timeout: 20_000 });
        await expect(page.getByRole("button", { name: START_MISSION_RE })).toBeEnabled();

        await page.getByRole("button", { name: START_MISSION_RE }).click();
        await expect.poll(() => state.runAttempts, { timeout: 20_000 }).toBe(1);
        await expect(page.locator(".mc-canvas-card", { hasText: "Team composition" })).toHaveCount(0);
        expect(state.createAttempts).toBe(2);
    });

    test("disables branch-out planning for non-git source workspaces", async ({ page }, testInfo) => {
        await openNewSession(page, { initialWorkspaceId: NON_GIT_WORKSPACE_ID });
        await setComposerText(page, "Create a branch plan from a workspace without git integration.");
        await page.getByRole("button", { name: /^Branch out/i }).click();

        await expect(page.locator(".branchtree-info--warn")).toContainText("isn't git-connected");
        await expect(page.getByRole("button", { name: START_MISSION_RE })).toBeDisabled();
        await snapshot(page, testInfo, "iteration-3-non-git-guardrail.png");
    });
});

async function openNewSession(page: Page, options: MockOptions = {}): Promise<MockState> {
    const workspaceId = options.initialWorkspaceId || GIT_WORKSPACE_ID;
    const state: MockState = {
        createBodies: [],
        createAttempts: 0,
        runAttempts: 0,
        failFirstCreate: !!options.failFirstCreate,
    };
    await seedAuth(page, workspaceId);
    await mockAgentHubApis(page, state);
    await page.goto(`/agent-hub/orchestrator?agenthubE2E=1&ws=${workspaceId}`, { waitUntil: "domcontentloaded" });
    await disableAnimations(page);
    await expect(page.locator("#composer-task-text")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(WORKSPACES.find((w) => w.id === workspaceId)?.name || GIT_WORKSPACE_NAME, { exact: true })).toBeVisible({ timeout: 30_000 });
    return state;
}

async function seedAuth(page: Page, workspaceId: string) {
    await page.addInitScript(({ ws }) => {
        window.localStorage.clear();
        window.sessionStorage.clear();
        window.localStorage.setItem("github_token", "e2e-token");
        window.localStorage.setItem("github_user", "e2e.user");
        window.sessionStorage.setItem("github_token", "e2e-token");
        window.sessionStorage.setItem("github_user", "e2e.user");
        window.sessionStorage.setItem("workspace_id", ws);
    }, { ws: workspaceId });
}

async function mockAgentHubApis(page: Page, state: MockState) {
    await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname;
        const method = request.method();

        if (method === "GET" && path.endsWith("/api/orchestrate/compose-models")) {
            await route.fulfill({
                status: 200,
                json: {
                    default: "gpt-4.1-enterprise",
                    models: [
                        {
                            id: "gpt-4.1-enterprise",
                            name: "GPT-4.1 Enterprise",
                            publisher: "GitHub Copilot",
                            tier: 1,
                            recommended: true,
                            top_pick: true,
                            reason: "Best default for planning.",
                            latency: "fast",
                        },
                    ],
                },
            });
            return;
        }

        if (method === "POST" && path.endsWith("/api/workspaces/preload")) {
            await route.fulfill({ status: 200, json: { ok: true } });
            return;
        }

        if (method === "GET" && path.endsWith("/api/workspaces")) {
            await route.fulfill({
                status: 200,
                json: {
                    workspaces: WORKSPACES,
                    cached_at: "2026-04-25T12:00:00.000Z",
                    source: "e2e",
                },
            });
            return;
        }

        const workspaceItemsMatch = path.match(/\/api\/workspaces\/([^/]+)\/items$/);
        if (method === "GET" && workspaceItemsMatch) {
            const workspaceId = decodeURIComponent(workspaceItemsMatch[1]);
            await route.fulfill({
                status: 200,
                json: {
                    items: WORKSPACE_ITEMS[workspaceId] || [],
                    captured_at: "2026-04-25T12:01:00.000Z",
                },
            });
            return;
        }

        if (method === "POST" && path.endsWith("/api/github/suggest-branch-names")) {
            await route.fulfill({
                status: 200,
                json: {
                    branch_name: "feature/revenue-quality-e2e",
                    workspace_name: "Revenue Quality",
                },
            });
            return;
        }

        if (method === "GET" && path.endsWith("/api/catalogs/architectures")) {
            await route.fulfill({ status: 200, json: [] });
            return;
        }

        if (method === "GET" && path.endsWith("/api/agents")) {
            await route.fulfill({ status: 200, json: [] });
            return;
        }

        if (method === "GET" && path.endsWith("/api/sessions")) {
            await route.fulfill({ status: 200, json: [] });
            return;
        }

        if (method === "POST" && path.endsWith("/api/sessions")) {
            state.createAttempts += 1;
            if (state.failFirstCreate && state.createAttempts === 1) {
                await route.fulfill({ status: 503, body: "Planner temporarily unavailable" });
                return;
            }
            const body = await request.postDataJSON();
            state.createBodies.push(body);
            await route.fulfill({ status: 200, json: makeSession(body) });
            return;
        }

        if (method === "POST" && /\/api\/sessions\/[^/]+\/run$/.test(path)) {
            state.runAttempts += 1;
            await route.fulfill({ status: 200, json: { ok: true } });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });
}

function makeSession(body: any) {
    return {
        id: "new-session-flow-e2e",
        session_id: "new-session-flow-e2e",
        task_description: body.task_description,
        workspace_id: body.workspace_id,
        status: "planned",
        created_at: "2026-04-25T12:02:00.000Z",
        context: body.context || {},
        agents: [],
        composition: {
            sessionId: "new-session-flow-e2e",
            architecture: "dynamic",
            task: body.task_description,
            slots: [],
            handoffs: [],
            rationale: "Dynamic mission starts immediately and delegates on demand.",
        },
        plan: makePlan(),
    };
}

function makePlan() {
    const nodes = [
        { id: "lead", agent: "FabricAdmin", role: "Readiness lead", skills: ["Planning", "Governance"] },
        { id: "data", agent: "FabricDataEngineer", role: "Validate lakehouse inputs", skills: ["Lakehouse", "Quality"] },
        { id: "report", agent: "Modeler", role: "Prepare executive handoff", skills: ["Semantic model", "Reporting"] },
    ];
    return {
        jobId: "new-session-flow-e2e",
        title: "Dynamic mission",
        summary: "Start a governed readiness mission immediately for E2E validation.",
        assumptions: ["The selected workspace is available."],
        prerequisites: ["Workspace access"],
        steps: nodes.map((node, index) => ({
            id: `step-${index + 1}`,
            order: index + 1,
            title: node.role,
            action: index === 0 ? "coordinate" : "inspect",
            target: { itemType: "Workspace", displayName: GIT_WORKSPACE_NAME, workspaceId: GIT_WORKSPACE_ID },
            inputs: [],
            dependsOn: index === 0 ? [] : [`step-${index}`],
            rationale: "Deterministic E2E coverage.",
            risk: "low",
            reversible: true,
        })),
        workspaceItems: WORKSPACE_ITEMS[GIT_WORKSPACE_ID],
        noAction: [],
        conflicts: [],
        clarificationsNeeded: [],
        footer: { agentCount: nodes.length, stepCount: nodes.length, approvalPoints: 1, executionBlocked: false },
        team: {
            pattern: "solo",
            nodes,
            edges: [
                { from: "lead", to: "data", kind: "delegate" },
                { from: "lead", to: "report", kind: "delegate" },
                { from: "data", to: "report", kind: "report" },
            ],
        },
    };
}

async function setComposerText(page: Page, text: string) {
    await page.locator("#composer-task-text").evaluate((el, value) => {
        el.textContent = value as string;
        el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value as string }));
    }, text);
}

async function disableAnimations(page: Page) {
    await page.addStyleTag({
        content: `
            *, *::before, *::after {
                animation-duration: 0.001ms !important;
                animation-delay: 0ms !important;
                transition-duration: 0.001ms !important;
                scroll-behavior: auto !important;
            }
        `,
    });
}

async function snapshot(page: Page, testInfo: TestInfo, name: string) {
    mkdirSync(SCREENSHOT_DIR, { recursive: true });
    const path = resolve(SCREENSHOT_DIR, name);
    await page.screenshot({ path, fullPage: true });
    await testInfo.attach(name, { path, contentType: "image/png" });
}

async function assertMinimumContrast(page: Page, selector: string, minRatio: number) {
    const ratio = await page.locator(selector).first().evaluate((el) => {
        function parseColor(value: string): [number, number, number, number] {
            const match = value.match(/rgba?\(([^)]+)\)/i);
            if (!match) return [0, 0, 0, 1];
            const parts = match[1].split(",").map((part) => Number(part.trim()));
            return [parts[0] || 0, parts[1] || 0, parts[2] || 0, parts[3] ?? 1];
        }
        function blend(fg: [number, number, number, number], bg: [number, number, number, number]): [number, number, number, number] {
            const alpha = fg[3] + bg[3] * (1 - fg[3]);
            if (alpha === 0) return [0, 0, 0, 0];
            return [
                (fg[0] * fg[3] + bg[0] * bg[3] * (1 - fg[3])) / alpha,
                (fg[1] * fg[3] + bg[1] * bg[3] * (1 - fg[3])) / alpha,
                (fg[2] * fg[3] + bg[2] * bg[3] * (1 - fg[3])) / alpha,
                alpha,
            ];
        }
        function luminance(rgb: [number, number, number, number]) {
            const channels = rgb.slice(0, 3).map((channel) => {
                const value = channel / 255;
                return value <= 0.03928 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
            });
            return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
        }
        const style = getComputedStyle(el);
        let bg: [number, number, number, number] = parseColor(style.backgroundColor);
        let parent = el.parentElement;
        while (bg[3] < 1 && parent) {
            bg = blend(bg, parseColor(getComputedStyle(parent).backgroundColor));
            parent = parent.parentElement;
        }
        if (bg[3] < 1) bg = blend(bg, [255, 255, 255, 1]);
        const fg = parseColor(style.color);
        const fgLum = luminance(fg);
        const bgLum = luminance(bg);
        const lighter = Math.max(fgLum, bgLum);
        const darker = Math.min(fgLum, bgLum);
        return (lighter + 0.05) / (darker + 0.05);
    });
    expect(ratio, `${selector} contrast ratio`).toBeGreaterThanOrEqual(minRatio);
}
