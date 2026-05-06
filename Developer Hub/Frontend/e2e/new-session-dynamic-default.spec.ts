import { test, expect, type Page } from "@playwright/test";

const WORKSPACE_ID = "dynamic-default-workspace";
const WORKSPACE_NAME = "Dynamic Default Workspace";
const START_MISSION_RE = /^Start mission$/i;

type CreatedSession = {
    id: string;
    architecture: string;
    prompt: string;
    slotIds: string[];
};

type MockState = {
    sessions: CreatedSession[];
    runAttempts: number;
    eventStreamAttempts: number;
};

test.describe.configure({ mode: "serial" });
test.use({ viewport: { width: 1366, height: 900 } });

test.describe("New Session dynamic default", () => {
    test("starts a user-facing build as one dynamic mission without Step 2", async ({ page }) => {
        const state = await openDynamicSessionPage(page);
        await setComposerText(
            page,
            "Create an executive sales analytics solution with lakehouse prep, a semantic model, and a polished Power BI report.",
        );
        await page.getByRole("button", { name: START_MISSION_RE }).click({ force: true });

        await expect.poll(() => state.runAttempts, { timeout: 30_000 }).toBe(1);
        expect(state.sessions[0]).toMatchObject({ architecture: "dynamic", slotIds: ["generalist"] });
        await expect(page.locator(".mc-canvas-card", { hasText: "Team composition" })).toHaveCount(0);
        await expect(page.getByText(/solo|reflection|mixed|supervisor|sequential|hierarchical|network/i)).toHaveCount(0);
    });

    test("uses the same dynamic path for read-only workspace inspection", async ({ page }) => {
        const state = await openDynamicSessionPage(page);
        await setComposerText(page, "List the current workspace items and return a concise read-only inventory summary.");
        await page.getByRole("button", { name: START_MISSION_RE }).click({ force: true });

        await expect.poll(() => state.runAttempts, { timeout: 30_000 }).toBe(1);
        expect(state.sessions[0]).toMatchObject({ architecture: "dynamic", slotIds: ["generalist"] });
        await expect(page.locator(".mc-canvas-card", { hasText: "Team composition" })).toHaveCount(0);
    });

    test("keeps the submitted mission page subscribed until live logs attach", async ({ page }) => {
        const state = await openDynamicSessionPage(page);
        await setComposerText(page, "Create an end to end Fabric inventory solution and show live progress while agents work.");
        await page.evaluate(() => { (window as any).__agentHubMissionStartMs = performance.now(); });
        await page.getByRole("button", { name: START_MISSION_RE }).click({ force: true });

        await expect(page.getByLabel("Mission log stream")).toContainText("live progress is attached", { timeout: 1_000 });
        const startupLatencyMs = await page.evaluate(() => performance.now() - (window as any).__agentHubMissionStartMs);
        console.log(`[startup-latency] prompt-to-live-log=${Math.round(startupLatencyMs)}ms`);
        expect(startupLatencyMs).toBeLessThanOrEqual(1_000);

        await expect.poll(() => state.runAttempts, { timeout: 30_000 }).toBe(1);
        await expect(page.getByLabel("Mission execution")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByText("Initializing...")).toHaveCount(0);
        await expect(page.getByText("Initializing…")).toHaveCount(0);

        await expect(page.locator(".mc3-exec-row--live")).toBeVisible({ timeout: 30_000 });
        await expect(page.getByLabel("Mission log stream")).toContainText("Workspace inventory scan is running");
        await expect(page.getByLabel("Mission log stream")).toContainText("live progress is attached");
        await page.setViewportSize({ width: 480, height: 360 });
        await expectLiveLogLayoutVisible(page);
        expect(state.eventStreamAttempts).toBeGreaterThanOrEqual(2);
    });
});

async function expectLiveLogLayoutVisible(page: Page) {
    await page.locator(".mc3-exec-row--live").scrollIntoViewIfNeeded();
    const layout = await page.locator(".mc3-dmc-grid").evaluate((grid) => {
        const stream = document.querySelector(".canvas-log-stream");
        const row = document.querySelector(".mc3-exec-row--live");
        const rowText = row?.textContent ?? "";
        const gridRect = grid.getBoundingClientRect();
        const streamRect = stream?.getBoundingClientRect();
        const rowRect = row?.getBoundingClientRect();
        const visibleRowHeight = rowRect
            ? Math.min(rowRect.bottom, window.innerHeight) - Math.max(rowRect.top, 0)
            : 0;
        const visibleGridHeight = Math.min(gridRect.bottom, window.innerHeight) - Math.max(gridRect.top, 0);

        return {
            gridHeight: gridRect.height,
            streamHeight: streamRect?.height ?? 0,
            rowTop: rowRect?.top ?? Number.POSITIVE_INFINITY,
            rowBottom: rowRect?.bottom ?? Number.NEGATIVE_INFINITY,
            rowText,
            visibleGridHeight,
            visibleRowHeight,
            viewportHeight: window.innerHeight,
        };
    });

    expect(layout.gridHeight).toBeGreaterThan(160);
    expect(layout.streamHeight).toBeGreaterThan(120);
    expect(layout.visibleGridHeight).toBeGreaterThan(80);
    expect(layout.visibleRowHeight).toBeGreaterThan(32);
    expect(layout.rowTop).toBeLessThan(layout.viewportHeight);
    expect(layout.rowBottom).toBeGreaterThan(0);
    expect(layout.rowText).toContain("Workspace inventory scan is running");
}

async function openDynamicSessionPage(page: Page): Promise<MockState> {
    const state: MockState = { sessions: [], runAttempts: 0, eventStreamAttempts: 0 };
    await seedAuth(page);
    await mockAgentHubApis(page, state);
    await page.goto(`/agent-hub/orchestrator?agenthubE2E=1&ws=${WORKSPACE_ID}`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("#composer-task-text")).toBeVisible({ timeout: 30_000 });
    return state;
}

async function seedAuth(page: Page) {
    await page.addInitScript(({ workspaceId }) => {
        window.localStorage.clear();
        window.sessionStorage.clear();
        window.localStorage.setItem("github_token", "dynamic-default-token");
        window.localStorage.setItem("github_user", "dynamic.default.e2e");
        window.sessionStorage.setItem("github_token", "dynamic-default-token");
        window.sessionStorage.setItem("github_user", "dynamic.default.e2e");
        window.sessionStorage.setItem("workspace_id", workspaceId);
    }, { workspaceId: WORKSPACE_ID });
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
                    models: [{
                        id: "gpt-4.1-enterprise",
                        name: "GPT-4.1 Enterprise",
                        publisher: "GitHub Copilot",
                        tier: 1,
                        recommended: true,
                        top_pick: true,
                        reason: "Best default for dynamic missions.",
                        latency: "fast",
                    }],
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
                    workspaces: [{
                        id: WORKSPACE_ID,
                        name: WORKSPACE_NAME,
                        git_connected: true,
                        git_provider: "GitHub",
                        git_branch: "main",
                        git_repo_name: "dynamic-default",
                    }],
                    cached_at: "2026-04-25T12:00:00.000Z",
                    source: "e2e",
                },
            });
            return;
        }

        if (method === "GET" && /\/api\/workspaces\/[^/]+\/items$/.test(path)) {
            await route.fulfill({
                status: 200,
                json: {
                    items: [
                        { id: "orders-lakehouse", name: "Orders Lakehouse", type: "Lakehouse", owner: "Data Engineering", webUrl: "https://fabric.example/items/orders-lakehouse" },
                        { id: "ops-model", name: "Operations KPI Model", type: "SemanticModel", owner: "BI Team", webUrl: "https://fabric.example/items/ops-model" },
                    ],
                    captured_at: "2026-04-25T12:01:00.000Z",
                },
            });
            return;
        }

        if (method === "POST" && path.endsWith("/api/github/suggest-branch-names")) {
            await route.fulfill({ status: 200, json: { branch_name: "feature/dynamic-default", workspace_name: "Dynamic Default" } });
            return;
        }

        if (method === "GET" && path.endsWith("/api/catalogs/architectures")) {
            await route.fulfill({ status: 200, json: [{ id: "dynamic", name: "Dynamic mission" }] });
            return;
        }

        if (method === "GET" && path.endsWith("/api/agents")) {
            await route.fulfill({ status: 200, json: [] });
            return;
        }

        const eventsJsonMatch = path.match(/\/api\/sessions\/([^/]+)\/events\.json$/);
        if (method === "GET" && eventsJsonMatch) {
            await route.fulfill({
                status: 200,
                json: {
                    sessionId: eventsJsonMatch[1],
                    source: "persisted",
                    liveExecution: false,
                    sessionStatus: state.runAttempts > 0 ? "running" : "planned",
                    persistedTotal: 0,
                    count: 0,
                    events: [],
                },
            });
            return;
        }

        const eventsMatch = path.match(/\/api\/sessions\/([^/]+)\/events$/);
        if (method === "GET" && eventsMatch) {
            state.eventStreamAttempts += 1;
            const session = state.sessions.find((s) => s.id === eventsMatch[1]);
            const shouldAttachLive = state.runAttempts > 0 && state.eventStreamAttempts >= 2;
            await route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                headers: { "Cache-Control": "no-cache" },
                body: shouldAttachLive && session ? missionEventStream(session.id, session.prompt) : "",
            });
            return;
        }

        if (method === "GET" && path.endsWith("/api/sessions")) {
            await route.fulfill({ status: 200, json: state.sessions.map((s) => makeSession(s.id, s.prompt, state.runAttempts > 0 ? "running" : "planned")) });
            return;
        }

        const sessionMatch = path.match(/\/api\/sessions\/([^/]+)$/);
        if (method === "GET" && sessionMatch) {
            const session = state.sessions.find((s) => s.id === sessionMatch[1]);
            await route.fulfill({ status: 200, json: session ? makeSession(session.id, session.prompt, state.runAttempts > 0 ? "running" : "planned") : {} });
            return;
        }

        if (method === "POST" && path.endsWith("/api/sessions")) {
            const body = await request.postDataJSON();
            const prompt = String(body.task_description || "");
            const id = `dynamic-default-${state.sessions.length + 1}`;
            const session = makeCreatedSession(id, prompt);
            state.sessions.push(session);
            await route.fulfill({ status: 200, json: makeSession(id, prompt) });
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

function makeCreatedSession(id: string, prompt: string): CreatedSession {
    const session = makeSession(id, prompt);
    return {
        id,
        architecture: session.composition.architecture,
        prompt,
        slotIds: session.composition.slots.map((slot) => slot.id),
    };
}

function makeSession(id: string, prompt: string, status: "planned" | "running" = "planned") {
    return {
        id,
        session_id: id,
        task_description: prompt,
        workspace_id: WORKSPACE_ID,
        status,
        created_at: "2026-04-25T12:02:00.000Z",
        started_at: status === "running" ? "2026-04-25T12:03:00.000Z" : null,
        context: { workspace_name: WORKSPACE_NAME },
        agents: status === "running" ? [{
            agent_id: "generalist",
            role: "Generalist mission controller",
            status: "running",
            session_id: "generalist",
            current_step: "Workspace inventory scan is running",
        }] : [],
        composition: {
            sessionId: id,
            architecture: "dynamic",
            task: prompt,
            slots: [{
                id: "generalist",
                agentId: "generalist",
                role: "Generalist mission controller",
                skills: [],
                status: status === "running" ? "active" : "planned",
            }],
            handoffs: [],
            rationale: "Start immediately with the hidden generalist mission controller.",
            headline: "Dynamic mission",
        },
        plan: {
            jobId: id,
            title: "Dynamic mission",
            summary: "The mission controller starts immediately and delegates work on demand.",
            assumptions: ["Workspace access is available."],
            prerequisites: ["Workspace access"],
            steps: [],
            workspaceItems: [],
            noAction: [],
            conflicts: [],
            clarificationsNeeded: [],
            footer: { agentCount: 1, stepCount: 0, approvalPoints: 0, executionBlocked: false },
            team: { pattern: "solo", nodes: [], edges: [] },
        },
    };
}

function missionEventStream(sessionId: string, prompt: string) {
    const ts = "2026-04-25T12:03:00.000Z";
    const events = [
        {
            type: "run_overview",
            seq: 1,
            sessionId,
            ts,
            job: { id: sessionId, status: "running", startedAt: ts, completedAt: null },
            composition: makeSession(sessionId, prompt, "running").composition,
            activeAgentId: "generalist",
            artifacts: [],
            changes: [],
            slotProgress: [{
                slotId: "generalist",
                agentId: "generalist",
                agentName: "Generalist",
                role: "Generalist mission controller",
                status: "running",
                currentStep: "Workspace inventory scan is running",
            }],
        },
        {
            type: "log_line",
            seq: 2,
            sessionId,
            ts,
            logCategory: "high_level",
            agentId: "generalist",
            agentName: "Generalist",
            level: "info",
            message: "Workspace inventory scan is running and live progress is attached.",
        },
    ];
    return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
}

async function setComposerText(page: Page, text: string) {
    await page.locator("#composer-task-text").evaluate((el, value) => {
        el.textContent = value;
        el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    }, text);
}