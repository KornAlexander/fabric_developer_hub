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
        await page.getByRole("button", { name: START_MISSION_RE }).click();

        await expect.poll(() => state.runAttempts, { timeout: 30_000 }).toBe(1);
        expect(state.sessions[0]).toMatchObject({ architecture: "dynamic", slotIds: ["generalist"] });
        await expect(page.locator(".mc-canvas-card", { hasText: "Team composition" })).toHaveCount(0);
        await expect(page.getByText(/solo|reflection|mixed|supervisor|sequential|hierarchical|network/i)).toHaveCount(0);
    });

    test("uses the same dynamic path for read-only workspace inspection", async ({ page }) => {
        const state = await openDynamicSessionPage(page);
        await setComposerText(page, "List the current workspace items and return a concise read-only inventory summary.");
        await page.getByRole("button", { name: START_MISSION_RE }).click();

        await expect.poll(() => state.runAttempts, { timeout: 30_000 }).toBe(1);
        expect(state.sessions[0]).toMatchObject({ architecture: "dynamic", slotIds: ["generalist"] });
        await expect(page.locator(".mc-canvas-card", { hasText: "Team composition" })).toHaveCount(0);
    });
});

async function openDynamicSessionPage(page: Page): Promise<MockState> {
    const state: MockState = { sessions: [], runAttempts: 0 };
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

        if (method === "GET" && path.endsWith("/api/sessions")) {
            await route.fulfill({ status: 200, json: state.sessions.map((s) => makeSession(s.id, s.prompt)) });
            return;
        }

        const sessionMatch = path.match(/\/api\/sessions\/([^/]+)$/);
        if (method === "GET" && sessionMatch) {
            const session = state.sessions.find((s) => s.id === sessionMatch[1]);
            await route.fulfill({ status: 200, json: session ? makeSession(session.id, session.prompt) : {} });
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

function makeSession(id: string, prompt: string) {
    return {
        id,
        session_id: id,
        task_description: prompt,
        workspace_id: WORKSPACE_ID,
        status: "planned",
        created_at: "2026-04-25T12:02:00.000Z",
        context: { workspace_name: WORKSPACE_NAME },
        agents: [],
        composition: {
            sessionId: id,
            architecture: "dynamic",
            task: prompt,
            slots: [{
                id: "generalist",
                agentId: "generalist",
                role: "Generalist mission controller",
                skills: [],
                status: "planned",
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

async function setComposerText(page: Page, text: string) {
    await page.locator("#composer-task-text").evaluate((el, value) => {
        el.textContent = value;
        el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    }, text);
}
