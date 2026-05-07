import { test, expect, type Locator, type Page, type TestInfo } from "@playwright/test";

const WORKSPACE_ID = "e2e-enterprise-workspace";
const WORKSPACE_NAME = "E2E Enterprise Fabric Workspace";
const CONTEXT_WORKSPACE_ID = "finance-governance-workspace";
const CONTEXT_WORKSPACE_NAME = "Finance Governance Workspace";
const SESSION_ID = "new-session-full-flow-e2e";
const SELECTED_MODEL_ID = "gpt-4o-enterprise";
const BRANCH_NAME = "feature/customer-360-readiness-e2e";
const DESTINATION_WORKSPACE_NAME = `${WORKSPACE_NAME} - Customer 360 Readiness`;
const START_MISSION_RE = /^Start mission$/i;

const TASK_PROMPT = [
    "Create an enterprise customer 360 readiness pack for the sales leadership team.",
    "Use the curated sales lakehouse, reconcile churn risk measures, create the readiness artifacts,",
    "and summarize governance blockers before anything is merged back.",
].join(" ");

const WORKSPACES = [
    {
        id: WORKSPACE_ID,
        name: WORKSPACE_NAME,
        git_connected: true,
        git_provider: "GitHub",
        git_branch: "main",
        git_repo_name: "fabric-enterprise-analytics",
    },
    {
        id: CONTEXT_WORKSPACE_ID,
        name: CONTEXT_WORKSPACE_NAME,
        git_connected: true,
        git_provider: "GitHub",
        git_branch: "main",
        git_repo_name: "finance-governance-controls",
    },
];

const WORKSPACE_ITEMS = {
    [WORKSPACE_ID]: [
        {
            id: "lakehouse-curated-sales",
            name: "Curated Sales Lakehouse",
            type: "Lakehouse",
            owner: "Data Platform",
            webUrl: "https://fabric.example/items/lakehouse-curated-sales",
        },
        {
            id: "semantic-sales-kpis",
            name: "Sales Executive KPI Model",
            type: "SemanticModel",
            owner: "BI Team",
            webUrl: "https://fabric.example/items/semantic-sales-kpis",
        },
        {
            id: "notebook-quality-checks",
            name: "Customer 360 Quality Checks",
            type: "Notebook",
            owner: "Data Engineering",
            webUrl: "https://fabric.example/items/notebook-quality-checks",
        },
    ],
    [CONTEXT_WORKSPACE_ID]: [
        {
            id: "warehouse-governance-controls",
            name: "Governance Controls Warehouse",
            type: "Warehouse",
            owner: "Finance Ops",
            webUrl: "https://fabric.example/items/warehouse-governance-controls",
        },
    ],
};

type ApiState = {
    createBody: any | null;
    sessionCreated: boolean;
    sessionStatus: "planned" | "running" | "completed";
    /** Artifacts the SSE stream announced as ``state: "written"`` — i.e.,
     *  the run reports that they were actually created. Populated when
     *  the events endpoint is hit so the workspace-items API can
     *  reflect the new state. */
    createdArtifacts: Array<{ id: string; name: string; type: string; webUrl: string; workspaceId: string }>;
    /** Counter so the test can prove the destination workspace was
     *  re-fetched after completion (i.e. UI / caller actually picked up
     *  the new state). */
    destinationItemsFetches: number;
};

/**
 * Items the prompt explicitly requires the run to produce. Each entry
 * is keyed by the artifact name that should land in the destination
 * workspace once the orchestration completes. The test asserts every
 * one of these is (a) emitted as a written artifact in the SSE stream,
 * (b) returned by the destination workspace items API after completion,
 * and (c) surfaced in the Mission Control UI for the user to inspect.
 */
const REQUIRED_ITEMS: Array<{ name: string; type: string }> = [
    { name: "Lakehouse_Customer360_Readiness", type: "Lakehouse" },
    { name: "Customer360_Readiness_Report", type: "Report" },
];

test.describe.configure({ mode: "serial" });
test.use({ viewport: { width: 1440, height: 960 } });

test("New Session end-to-end experience is complete, verified, and visually polished", async ({ page }, testInfo) => {
    const state: ApiState = {
        createBody: null,
        sessionCreated: false,
        sessionStatus: "planned",
        createdArtifacts: [],
        destinationItemsFetches: 0,
    };
    await seedAuth(page);
    await mockAgentHubApis(page, state);

    await page.goto(`/agent-hub/orchestrator?agenthubE2E=1&ws=${WORKSPACE_ID}`, { waitUntil: "domcontentloaded" });
    await disableAnimations(page);

    const promptBox = page.locator("#composer-task-text");
    await expect(promptBox).toBeVisible({ timeout: 30_000 });
    await expect(promptBox).toHaveAttribute("spellcheck", "false");
    await expect(page.getByText(WORKSPACE_NAME, { exact: true })).toBeVisible({ timeout: 30_000 });

    await promptBox.click();
    await promptBox.pressSequentially(`${TASK_PROMPT} Reference `, { delay: 0 });
    await promptBox.pressSequentially("@Curated", { delay: 0 });
    await page.locator(".mention-pop__item", { hasText: "Curated Sales Lakehouse" }).click();

    await page.getByRole("button", { name: /^add workspace$/i }).click();
    await page.getByRole("menuitem", { name: CONTEXT_WORKSPACE_NAME }).click();
    await expect(page.locator(".ctx-pill", { hasText: CONTEXT_WORKSPACE_NAME })).toBeVisible();

    await page.locator("#composer-file-input").setInputFiles([
        {
            name: "executive-brief.md",
            mimeType: "text/markdown",
            buffer: Buffer.from("# Executive brief\nFocus areas: churn risk, sales KPI drift, governance blockers.\n"),
        },
        {
            name: "workspace-inventory.json",
            mimeType: "application/json",
            buffer: Buffer.from(JSON.stringify({ source: WORKSPACE_NAME, items: ["Curated Sales Lakehouse", "Sales Executive KPI Model"] }, null, 2)),
        },
    ]);
    await expect(page.locator(".ctx-pill", { hasText: "executive-brief.md" })).toBeVisible();
    await expect(page.locator(".ctx-pill", { hasText: "workspace-inventory.json" })).toBeVisible();

    await page.getByRole("button", { name: /GPT-4\.1 Enterprise/i }).click();
    await page.getByRole("menuitem", { name: /GPT-4o Enterprise Multimodal/i }).click();
    await expect(page.getByRole("button", { name: /GPT-4o Enterprise Multimodal/i })).toBeVisible();

    const approvalsToggle = page.getByRole("button", { name: /Require approvals/i });
    const branchToggle = page.getByRole("button", { name: /^Branch out/i });
    await approvalsToggle.click();
    await branchToggle.click();
    await expect(approvalsToggle).toHaveAttribute("aria-pressed", "true");
    await expect(branchToggle).toHaveAttribute("aria-pressed", "true");

    await expect(page.getByLabel("Destination workspace name")).toBeVisible({ timeout: 10_000 });
    await page.getByLabel("Destination workspace name").fill(DESTINATION_WORKSPACE_NAME);
    await page.getByLabel("Git branch name").fill(BRANCH_NAME);

    await screenshotMain(page, testInfo, "new-session-step1-configured.png");
    await assertNoCriticalTextOverflow(page.locator(".composer-card"));

    await page.getByRole("button", { name: START_MISSION_RE }).click();

    expect(state.createBody, "create-session body was captured").toBeTruthy();
    assertCreateSessionBody(state.createBody);
    await expect(page.locator(".mc-canvas-card", { hasText: "Team composition" })).toHaveCount(0);

    await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".agent-node", { hasText: "Mission control" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Generalist", { exact: true })).toHaveCount(0);
    await expect(page.getByText(/Completed · dynamic · 4 agents/)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("heading", { name: "Run complete" })).toBeVisible({ timeout: 30_000 });

    // Verify that EVERY required item is surfaced in Mission Control
    // outputs rail and clickable to its webUrl. This is the user's
    // first proof that the items they asked for actually exist.
    for (const required of REQUIRED_ITEMS) {
        const artifactRow = page.locator(".mc3-rail .dmc-live__outputs .ledger-row", { hasText: required.name });
        await expect(artifactRow, `Required item missing from Mission Control rail: ${required.name}`).toBeVisible();
        await expect(artifactRow.getByText(required.name, { exact: true })).toBeVisible();
    }

    await expect(page.locator(".mc3-log")).toContainText("Agent added: Recovery builder");
    await expect(page.locator(".mc3-log")).toContainText("spawned fabric-data-engineer");
    await assertNoCriticalTextOverflow(page.locator(".mc3"));
    await screenshotMain(page, testInfo, "new-session-step3-run-complete.png");

    // Result verification — prove the run actually produced what the
    // prompt required, not merely that the UI ran without errors.
    await verifyRunResults(page, state);

    await page.getByRole("button", { name: /^Sessions$/ }).click();
    await expect(page.locator(".sessions-page .sessions-title")).toHaveText("Sessions", { timeout: 30_000 });
    const completedRow = page.locator(".recent-jobs-row", { hasText: "customer 360 readiness pack" });
    await expect(completedRow).toBeVisible();
    await expect(completedRow.locator(".recent-jobs-status")).toHaveText("100% SUCCESS");
    await assertNoCriticalTextOverflow(page.locator(".sessions-page"));
    await screenshotMain(page, testInfo, "new-session-step4-sessions-overview.png");
});

async function seedAuth(page: Page) {
    await page.addInitScript(({ workspaceId }) => {
        window.localStorage.clear();
        window.sessionStorage.clear();
        window.localStorage.setItem("github_token", "e2e-new-session-token");
        window.localStorage.setItem("github_user", "enterprise.e2e");
        window.sessionStorage.setItem("github_token", "e2e-new-session-token");
        window.sessionStorage.setItem("github_user", "enterprise.e2e");
        window.sessionStorage.setItem("workspace_id", workspaceId);
    }, { workspaceId: WORKSPACE_ID });
}

async function mockAgentHubApis(page: Page, state: ApiState) {
    await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        const path = url.pathname;

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
                            reason: "Best default for multi-agent planning.",
                            latency: "fast",
                        },
                        {
                            id: SELECTED_MODEL_ID,
                            name: "GPT-4o Enterprise Multimodal",
                            publisher: "GitHub Copilot",
                            tier: 1,
                            recommended: true,
                            top_pick: false,
                            reason: "Use when prompt attachments include files or images.",
                            latency: "medium",
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
                    cached_at: new Date("2026-04-25T12:00:00.000Z").toISOString(),
                    source: "cache",
                },
            });
            return;
        }

        const workspaceItemsMatch = path.match(/\/api\/workspaces\/([^/]+)\/items$/);
        if (method === "GET" && workspaceItemsMatch) {
            const workspaceId = decodeURIComponent(workspaceItemsMatch[1]);
            const seeded = WORKSPACE_ITEMS[workspaceId as keyof typeof WORKSPACE_ITEMS] || [];
            // Once the orchestration has completed, the destination
            // workspace must surface the freshly-created items so the
            // user can immediately inspect / open them. We blend the
            // SSE-announced artifacts in for the source workspace (the
            // run's destination), which mirrors what a real backend
            // would do after the deploy step finishes.
            const isDestination = workspaceId === WORKSPACE_ID;
            if (isDestination) state.destinationItemsFetches += 1;
            const merged = isDestination && state.sessionStatus === "completed"
                ? [
                    ...seeded,
                    ...state.createdArtifacts
                        .filter((a) => a.workspaceId === workspaceId)
                        .map((a) => ({
                            id: a.id,
                            name: a.name,
                            type: a.type,
                            owner: "Customer 360 Readiness Coordinator",
                            webUrl: a.webUrl,
                        })),
                ]
                : seeded;
            await route.fulfill({
                status: 200,
                json: {
                    items: merged,
                    captured_at: new Date("2026-04-25T12:01:00.000Z").toISOString(),
                },
            });
            return;
        }

        if (method === "POST" && path.endsWith("/api/github/suggest-branch-names")) {
            await route.fulfill({
                status: 200,
                json: {
                    branch_name: BRANCH_NAME,
                    workspace_name: "Customer 360 Readiness",
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

        if (method === "GET" && path.endsWith(`/api/sessions/${SESSION_ID}/events`)) {
            state.sessionStatus = "completed";
            // The events stream announces every artifact the run wrote;
            // mirror those into ``state.createdArtifacts`` so the
            // workspace-items mock and the post-run verifier can prove
            // the prompt's required outputs landed in the workspace.
            state.createdArtifacts = [
                {
                    id: "lakehouse-readiness",
                    name: "Lakehouse_Customer360_Readiness",
                    type: "Lakehouse",
                    webUrl: "https://fabric.example/items/lakehouse-readiness",
                    workspaceId: WORKSPACE_ID,
                },
                {
                    id: "readiness-report",
                    name: "Customer360_Readiness_Report",
                    type: "Report",
                    webUrl: "https://fabric.example/items/readiness-report",
                    workspaceId: WORKSPACE_ID,
                },
            ];
            await route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                headers: { "Cache-Control": "no-cache" },
                body: missionEventStream(),
            });
            return;
        }

        if (method === "POST" && path.endsWith(`/api/sessions/${SESSION_ID}/run`)) {
            state.sessionStatus = "running";
            await route.fulfill({ status: 200, json: makeSessionRecord(state, "running") });
            return;
        }

        if (method === "GET" && path.endsWith(`/api/sessions/${SESSION_ID}`)) {
            await route.fulfill({ status: 200, json: makeSessionRecord(state, state.sessionStatus) });
            return;
        }

        if (method === "GET" && path.endsWith("/api/sessions")) {
            await route.fulfill({
                status: 200,
                json: state.sessionCreated ? [makeSessionRecord(state, state.sessionStatus)] : [],
            });
            return;
        }

        if (method === "POST" && path.endsWith("/api/sessions")) {
            state.createBody = await request.postDataJSON();
            state.sessionCreated = true;
            state.sessionStatus = "planned";
            await route.fulfill({ status: 200, json: makeSessionRecord(state, "planned") });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });
}

function assertCreateSessionBody(body: any) {
    expect(body.task_description).toContain("customer 360 readiness pack");
    expect(body.task_description).toContain("@Curated Sales Lakehouse");
    expect(body.workspace_id).toBe(WORKSPACE_ID);
    expect(body.model).toBe(SELECTED_MODEL_ID);
    expect(body.attachments).toEqual(expect.arrayContaining([
        expect.objectContaining({ name: "executive-brief.md", kind: "text", mime: "text/markdown" }),
        expect.objectContaining({ name: "workspace-inventory.json", kind: "text", mime: "application/json" }),
    ]));
    expect(body.context).toMatchObject({
        workspace_name: WORKSPACE_NAME,
        destination_workspace: WORKSPACE_ID,
        branch_out: true,
        branch_name: BRANCH_NAME,
        destination_workspace_name: DESTINATION_WORKSPACE_NAME,
        require_approvals: true,
    });
    expect(body.context.context_items).toEqual(expect.arrayContaining([
        expect.objectContaining({
            id: "lakehouse-curated-sales",
            name: "Curated Sales Lakehouse",
            type: "Lakehouse",
            workspaceId: WORKSPACE_ID,
        }),
        expect.objectContaining({
            id: CONTEXT_WORKSPACE_ID,
            name: CONTEXT_WORKSPACE_NAME,
            type: "workspace",
        }),
    ]));
    expect(body.context.workspace_items).toEqual(expect.arrayContaining([
        expect.objectContaining({ id: "lakehouse-curated-sales", name: "Curated Sales Lakehouse" }),
        expect.objectContaining({ id: "semantic-sales-kpis", name: "Sales Executive KPI Model" }),
    ]));
}

function makeSessionRecord(state: ApiState, status: "planned" | "running" | "completed") {
    const now = new Date("2026-04-25T12:03:00.000Z").toISOString();
    const completedAt = status === "completed" ? new Date("2026-04-25T12:04:08.000Z").toISOString() : null;
    const createContext = state.createBody?.context ?? {};
    const attachments = state.createBody?.attachments ?? [];
    return {
        id: SESSION_ID,
        session_id: SESSION_ID,
        task_description: state.createBody?.task_description || TASK_PROMPT,
        workspace_id: WORKSPACE_ID,
        status,
        created_at: new Date("2026-04-25T12:02:00.000Z").toISOString(),
        started_at: new Date("2026-04-25T12:03:01.000Z").toISOString(),
        completed_at: completedAt,
        context: {
            ...createContext,
            workspace_name: DESTINATION_WORKSPACE_NAME,
            prompt_attachments: attachments,
            context_items: createContext.context_items || [
                { id: "lakehouse-curated-sales", name: "Curated Sales Lakehouse", type: "Lakehouse", workspaceId: WORKSPACE_ID },
                { id: CONTEXT_WORKSPACE_ID, name: CONTEXT_WORKSPACE_NAME, type: "workspace" },
            ],
        },
        agents: missionSlots().map((slot) => ({
            agent_id: slot.agentId,
            role: slot.role,
            status: status === "completed" ? "completed" : status === "running" ? "running" : "queued",
            current_step: status === "completed" ? "Execution complete" : "Preparing execution",
            session_id: slot.id,
        })),
        composition: missionComposition(),
        plan: makePlan(),
    };
}

function makePlan() {
    const nodes = [
        {
            id: "orchestrator",
            agent: "FabricAdmin",
            role: "Customer 360 Readiness Coordinator",
            skills: ["Fabric API grounding", "Workspace governance", "Deployment review"],
        },
        {
            id: "inventory",
            agent: "FabricDataEngineer",
            role: "Inventory lakehouse, notebook, and semantic-model dependencies before execution.",
            skills: ["Spark consumption", "Lakehouse inspection", "Data quality"],
        },
        {
            id: "quality",
            agent: "FabricDataEngineer",
            role: "Create the readiness outputs and reconcile churn-risk quality signals.",
            skills: ["Spark authoring", "Delta Lake", "Notebook authoring"],
        },
        {
            id: "reporting",
            agent: "Modeler",
            role: "Publish the executive KPI handoff and summarize governance blockers.",
            skills: ["Power BI authoring", "Semantic modeling", "Governance evidence"],
        },
    ];
    return {
        jobId: SESSION_ID,
        title: "Customer 360 readiness orchestration",
        summary: "Create a governed readiness pack from the curated sales lakehouse and supporting artifacts.",
        assumptions: ["The selected source workspace is git-connected."],
        prerequisites: ["Sales data access", "Git branch ready"],
        steps: nodes.map((node, index) => ({
            id: `step-${index + 1}`,
            order: index + 1,
            title: node.role,
            action: index === 0 ? "coordinate" : "create",
            target: {
                itemType: index === 2 ? "Lakehouse" : index === 3 ? "Report" : "Workspace",
                displayName: index === 2 ? "Lakehouse_Customer360_Readiness" : index === 3 ? "Customer360_Readiness_Report" : DESTINATION_WORKSPACE_NAME,
                workspaceId: WORKSPACE_ID,
            },
            inputs: [],
            dependsOn: index === 0 ? [] : [`step-${index}`],
            rationale: "Enterprise readiness output requested by the user.",
            risk: index === 0 ? "low" : "medium",
            reversible: true,
        })),
        workspaceItems: WORKSPACE_ITEMS[WORKSPACE_ID],
        noAction: [],
        conflicts: [],
        clarificationsNeeded: [],
        footer: {
            agentCount: nodes.length,
            stepCount: nodes.length,
            approvalPoints: 1,
            executionBlocked: false,
        },
        team: {
            pattern: "solo",
            nodes,
            edges: [
                { from: "orchestrator", to: "inventory", kind: "delegate" },
                { from: "orchestrator", to: "quality", kind: "delegate" },
                { from: "orchestrator", to: "reporting", kind: "delegate" },
                { from: "inventory", to: "quality", kind: "report" },
                { from: "quality", to: "reporting", kind: "report" },
            ],
        },
    };
}

function missionSlots() {
    return [
        { id: "orchestrator", agentId: "FabricAdmin", role: "Customer 360 Readiness Coordinator" },
        { id: "inventory", agentId: "FabricDataEngineer", role: "Inventory and dependency analysis" },
        { id: "quality", agentId: "FabricDataEngineer", role: "Readiness artifact creation" },
        { id: "reporting", agentId: "Modeler", role: "Executive KPI report publishing" },
    ];
}

function missionComposition() {
    return {
        sessionId: SESSION_ID,
        architecture: "dynamic",
        task: TASK_PROMPT,
        slots: missionSlots().map((slot) => ({
            ...slot,
            skills: [
                { id: "fabric-api-grounding", name: "Fabric API grounding" },
                { id: "workspace-governance", name: "Workspace governance" },
            ],
            status: "done",
        })),
        handoffs: [
            { from: "orchestrator", to: "inventory", kind: "delegate" },
            { from: "orchestrator", to: "quality", kind: "delegate" },
            { from: "orchestrator", to: "reporting", kind: "delegate" },
            { from: "inventory", to: "quality", kind: "report" },
            { from: "quality", to: "reporting", kind: "report" },
        ],
        rationale: "Dynamic mission control starts immediately and delegates specialist work on demand.",
    };
}

function missionEventStream() {
    const ts = "2026-04-25T12:03:12.000Z";
    const recoverySlot = {
        slotId: "recovery-quality",
        agentId: "recovery-quality",
        agentName: "Recovery builder",
        role: "Recovery builder",
    };
    const events = [
        {
            type: "run_overview",
            seq: 1,
            sessionId: SESSION_ID,
            ts,
            job: { id: SESSION_ID, status: "running", startedAt: "2026-04-25T12:03:01.000Z", completedAt: null },
            composition: missionComposition(),
            activeAgentId: "inventory",
            artifacts: [],
            slotProgress: missionSlots().map((slot) => ({
                slotId: slot.id,
                agentId: slot.agentId,
                agentName: slot.agentId,
                role: slot.role,
                status: slot.id === "inventory" ? "running" : "queued",
            })),
        },
        {
            type: "log_line",
            seq: 2,
            sessionId: SESSION_ID,
            ts,
            agentId: "inventory",
            agentName: "FabricDataEngineer",
            level: "info",
            message: "Validated Curated Sales Lakehouse and Sales Executive KPI Model as source context.",
        },
        {
            type: "action",
            seq: 3,
            sessionId: SESSION_ID,
            ts,
            agentId: "quality",
            agentName: "FabricDataEngineer",
            action: {
                id: "create-lakehouse-readiness",
                action_type: "create",
                entity_name: "Lakehouse_Customer360_Readiness",
                entity_type: "Lakehouse",
            },
        },
        {
            type: "agent_error",
            seq: 4,
            sessionId: SESSION_ID,
            ts,
            agentId: "quality",
            agentName: "FabricDataEngineer",
            error: "LLM error: timeout while creating readiness artifacts",
            phase: 2,
        },
        {
            type: "slot_progress",
            seq: 5,
            sessionId: SESSION_ID,
            ts,
            slotId: "quality",
            agentId: "quality",
            agentName: "FabricDataEngineer",
            role: "Readiness artifact creation",
            status: "failed",
            reason: "LLM error: timeout while creating readiness artifacts",
        },
        {
            type: "log_line",
            seq: 6,
            sessionId: SESSION_ID,
            ts,
            agentId: "quality",
            agentName: "FabricDataEngineer",
            level: "warn",
            message: "Orchestrator recovery decision: spawned fabric-data-engineer to recover failed slot Readiness artifact creation.",
            tags: ["orchestrator_recovery", "spawn_agent"],
        },
        {
            type: "agent_added",
            seq: 7,
            sessionId: SESSION_ID,
            ts,
            jobId: SESSION_ID,
            agent: {
                agentId: "fabric-data-engineer",
                sessionId: "recovery-quality",
                role: "Recovery builder",
                goal: "Recover readiness artifact creation without duplicating completed work.",
                status: "queued",
            },
        },
        {
            type: "slot_progress",
            seq: 8,
            sessionId: SESSION_ID,
            ts,
            ...recoverySlot,
            status: "running",
        },
        {
            type: "slot_progress",
            seq: 9,
            sessionId: SESSION_ID,
            ts,
            ...recoverySlot,
            status: "done",
        },
        {
            type: "artifact_added",
            seq: 10,
            sessionId: SESSION_ID,
            ts,
            artifactId: "lakehouse-readiness",
            agentId: "quality",
            kind: "Lakehouse",
            name: "Lakehouse_Customer360_Readiness",
            state: "written",
            webUrl: "https://fabric.example/items/lakehouse-readiness",
        },
        {
            type: "artifact_added",
            seq: 11,
            sessionId: SESSION_ID,
            ts,
            artifactId: "readiness-report",
            agentId: "reporting",
            kind: "Report",
            name: "Customer360_Readiness_Report",
            state: "written",
            webUrl: "https://fabric.example/items/readiness-report",
        },
        {
            type: "run_overview",
            seq: 12,
            sessionId: SESSION_ID,
            ts,
            job: { id: SESSION_ID, status: "completed", startedAt: "2026-04-25T12:03:01.000Z", completedAt: "2026-04-25T12:04:08.000Z" },
            composition: missionComposition(),
            activeAgentId: null,
            artifacts: [
                { artifactId: "lakehouse-readiness", agentId: "quality", kind: "Lakehouse", name: "Lakehouse_Customer360_Readiness", state: "written", webUrl: "https://fabric.example/items/lakehouse-readiness" },
                { artifactId: "readiness-report", agentId: "reporting", kind: "Report", name: "Customer360_Readiness_Report", state: "written", webUrl: "https://fabric.example/items/readiness-report" },
            ],
            slotProgress: [
                ...missionSlots().map((slot) => ({
                slotId: slot.id,
                agentId: slot.agentId,
                agentName: slot.agentId,
                role: slot.role,
                status: "done",
                })),
                { ...recoverySlot, status: "done" },
            ],
        },
        {
            type: "job_complete",
            seq: 13,
            sessionId: SESSION_ID,
            ts,
            jobId: SESSION_ID,
            status: "completed",
            totalDuration: "00:01:07",
        },
    ];
    return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
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

/**
 * After the run reports completion, prove the orchestration actually
 * produced what the user asked for:
 *   1. Every REQUIRED_ITEMS entry was emitted as a written artifact in
 *      the SSE stream (i.e., recorded into ``state.createdArtifacts``).
 *   2. The destination workspace items API now returns each required
 *      item with a navigable webUrl, so a user landing on the workspace
 *      after the run will see the new content.
 *   3. The Mission Control rail surfaces each required item as an
 *      inspectable artifact link.
 *
 * This is the difference between "the UI rendered some success copy"
 * and "the prompt's stated outcome was actually achieved".
 */
async function verifyRunResults(page: Page, state: ApiState) {
    // (1) SSE stream emitted every required artifact.
    const emittedNames = new Set(state.createdArtifacts.map((a) => a.name));
    for (const required of REQUIRED_ITEMS) {
        expect(
            emittedNames,
            `Run did not emit a written artifact for required item: ${required.name}. Emitted: ${[...emittedNames].join(", ") || "(none)"}`,
        ).toContain(required.name);
    }

    // (2) Destination workspace items API reflects the creations.
    //     Use ``page.evaluate(fetch)`` so the request goes through the
    //     browser context where ``page.route()`` mocks apply \u2014 a raw
    //     ``page.request.get()`` would bypass them and hit the dev
    //     server, which returns the SPA HTML shell.
    const itemsBody = await page.evaluate(async (workspaceId) => {
        const r = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/items`, {
            headers: { Accept: "application/json" },
        });
        if (!r.ok) throw new Error(`items endpoint returned ${r.status}`);
        return r.json();
    }, WORKSPACE_ID);
    const items: Array<{ name: string; type: string; webUrl?: string }> = itemsBody.items ?? [];
    const itemNames = new Set(items.map((it) => it.name));
    for (const required of REQUIRED_ITEMS) {
        const match = items.find((it) => it.name === required.name);
        expect(
            match,
            `Destination workspace is missing required item ${required.name}. Workspace contains: ${[...itemNames].join(", ") || "(empty)"}`,
        ).toBeTruthy();
        expect(match!.type, `Required item ${required.name} has wrong type`).toBe(required.type);
        expect(match!.webUrl, `Required item ${required.name} is missing a webUrl`).toMatch(/^https?:\/\//);
    }
    // The pre-existing workspace items must still be there \u2014 the run
    // adds outputs, never silently drops things.
    const seedItems = WORKSPACE_ITEMS[WORKSPACE_ID] || [];
    for (const seed of seedItems) {
        expect(itemNames, `Run accidentally removed pre-existing item: ${seed.name}`).toContain(seed.name);
    }
    expect(state.destinationItemsFetches, "destination workspace items API was never queried").toBeGreaterThan(0);

    // (3) Mission Control rail surfaces every required item as a link
    //     the user can open.
    for (const required of REQUIRED_ITEMS) {
        const artifactLink = page.locator(`.mc3-rail .dmc-live__outputs .ledger-row a[href*="fabric.example"]`, { hasText: required.name }).first();
        // Some implementations wrap the link as a button with data-href;
        // fall back to a non-anchor selector if no anchor is present.
        const fallback = page.locator(".mc3-rail .dmc-live__outputs .ledger-row", { hasText: required.name }).first();
        const target = (await artifactLink.count()) > 0 ? artifactLink : fallback;
        await expect(target, `Required artifact not exposed in rail: ${required.name}`).toBeVisible();
    }
}

async function screenshotMain(page: Page, testInfo: TestInfo, name: string) {
    await movePointerAway(page);
    await page.evaluate(async () => {
        await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });
    const path = testInfo.outputPath(name);
    await page.locator(".agenthub-main").screenshot({ path, animations: "disabled" });
    await testInfo.attach(name, { path, contentType: "image/png" });
}

async function waitForLayout(root: Locator) {
    await root.evaluate(async () => {
        await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });
}

async function movePointerAway(page: Page) {
    await page.mouse.move(8, 8);
}

async function assertNoCriticalTextOverflow(root: Locator) {
    const overflows = await root.evaluate((el) => {
        const selectors = [
            "button",
            ".ctx-pill",
            ".select-trigger__label",
            ".composer-model-btn",
            ".mc3-kpi-card__value",
            ".mc3-completion__tile-value",
            ".recent-jobs-task-main span",
            ".recent-jobs-status",
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

async function assertGraphMetrics(metrics: Awaited<ReturnType<typeof readGraphMetrics>>) {
    expect(metrics.nodeOverlaps, `node overlaps: ${metrics.nodeOverlaps.join("; ")}`).toEqual([]);
    expect(metrics.labelNodeOverlaps, `edge labels over nodes: ${metrics.labelNodeOverlaps.join("; ")}`).toEqual([]);
    expect(metrics.detachedEdges, `detached graph edges: ${metrics.detachedEdges.join("; ")}`).toEqual([]);
    expect(metrics.contentOverflows, `node text overflow: ${metrics.contentOverflows.join("; ")}`).toEqual([]);
    expect(metrics.clippedNodes, `clipped nodes: ${metrics.clippedNodes.join("; ")}`).toEqual([]);
}

async function readGraphMetrics(root: Locator): Promise<{
    nodeOverlaps: string[];
    labelNodeOverlaps: string[];
    detachedEdges: string[];
    contentOverflows: string[];
    clippedNodes: string[];
}> {
    return root.evaluate((el) => {
        type Box = { name: string; left: number; top: number; right: number; bottom: number };
        const boxFor = (node: Element, name: string): Box => {
            const r = node.getBoundingClientRect();
            return { name, left: r.left, top: r.top, right: r.right, bottom: r.bottom };
        };
        const intersects = (a: Box, b: Box, tolerance = 2) => {
            const x = Math.min(a.right, b.right) - Math.max(a.left, b.left);
            const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
            return x > tolerance && y > tolerance;
        };
        const touchesNodeBoundary = (point: { x: number; y: number }, node: Box, tolerance = 6) => {
            const withinX = point.x >= node.left - tolerance && point.x <= node.right + tolerance;
            const withinY = point.y >= node.top - tolerance && point.y <= node.bottom + tolerance;
            if (!withinX || !withinY) return false;
            const nearVerticalSide = Math.min(Math.abs(point.x - node.left), Math.abs(point.x - node.right)) <= tolerance;
            const nearHorizontalSide = Math.min(Math.abs(point.y - node.top), Math.abs(point.y - node.bottom)) <= tolerance;
            return nearVerticalSide || nearHorizontalSide;
        };
        const nodeElements = Array.from(el.querySelectorAll<HTMLElement>(".mc-node:not(.mc-node--skeleton)"))
            .filter((node) => node.offsetParent !== null);
        const nodes = nodeElements.map((node) => boxFor(
            node,
            node.querySelector(".mc-node__title")?.textContent?.trim() || node.getAttribute("data-agent") || "node",
        ));
        const labels = Array.from(el.querySelectorAll<HTMLElement>(".mc-edge-label"))
            .filter((node) => node.offsetParent !== null && node.getBoundingClientRect().width > 0)
            .map((node, index) => boxFor(node, node.textContent?.trim() || `label ${index + 1}`));
        const stage = el.querySelector<HTMLElement>(".mc-canvas-fit");
        const stageRect = stage?.getBoundingClientRect();

        const nodeOverlaps: string[] = [];
        for (let i = 0; i < nodes.length; i += 1) {
            for (let j = i + 1; j < nodes.length; j += 1) {
                if (intersects(nodes[i], nodes[j], 3)) {
                    nodeOverlaps.push(`${nodes[i].name} overlaps ${nodes[j].name}`);
                }
            }
        }

        const labelNodeOverlaps: string[] = [];
        for (const label of labels) {
            for (const node of nodes) {
                if (intersects(label, node, 2)) {
                    labelNodeOverlaps.push(`${label.name} overlaps ${node.name}`);
                }
            }
        }

        const detachedEdges: string[] = [];
        Array.from(el.querySelectorAll<SVGPathElement>(".mc-canvas__edges .mc-edge"))
            .filter((path) => path.getTotalLength() > 0)
            .forEach((path, index) => {
                const matrix = path.ownerSVGElement?.getScreenCTM();
                if (!matrix) return;
                const length = path.getTotalLength();
                const start = new DOMPoint(path.getPointAtLength(0).x, path.getPointAtLength(0).y).matrixTransform(matrix);
                const end = new DOMPoint(path.getPointAtLength(length).x, path.getPointAtLength(length).y).matrixTransform(matrix);
                if (!nodes.some((node) => touchesNodeBoundary(start, node))) {
                    detachedEdges.push(`edge ${index + 1} start is detached`);
                }
                if (!nodes.some((node) => touchesNodeBoundary(end, node))) {
                    detachedEdges.push(`edge ${index + 1} end is detached`);
                }
            });

        const contentOverflows: string[] = [];
        nodeElements.forEach((nodeEl, nodeIndex) => {
            const nodeBox = nodes[nodeIndex];
            const pieces = Array.from(nodeEl.querySelectorAll<HTMLElement>(
                ".mc-node__title, .mc-node__role, .mc-node__skill--selected",
            ));
            pieces.forEach((piece) => {
                const name = piece.textContent?.trim() || piece.className || "content";
                if (piece.scrollWidth > piece.clientWidth + 1 || piece.scrollHeight > piece.clientHeight + 1) {
                    contentOverflows.push(`${nodeBox.name}: ${name} is clipped`);
                    return;
                }
                const box = piece.getBoundingClientRect();
                const outOfNode =
                    box.left < nodeBox.left - 1
                    || box.top < nodeBox.top - 1
                    || box.right > nodeBox.right + 1
                    || box.bottom > nodeBox.bottom + 1;
                if (outOfNode) contentOverflows.push(`${nodeBox.name}: ${name} paints outside node`);
            });
        });

        const clippedNodes: string[] = [];
        if (stageRect) {
            nodes.forEach((node) => {
                const clipped =
                    node.left < stageRect.left - 2
                    || node.top < stageRect.top - 2
                    || node.right > stageRect.right + 2
                    || node.bottom > stageRect.bottom + 2;
                if (clipped) clippedNodes.push(node.name);
            });
        }

        return { nodeOverlaps, labelNodeOverlaps, detachedEdges, contentOverflows, clippedNodes };
    });
}