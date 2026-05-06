import { expect, type Page, type TestInfo } from "@playwright/test";

export const DEFAULT_PROGRESS_SESSION_ID = "mission-control-progress-contract";

export interface MissionEvidenceRow {
    kind: string | null;
    state: string | null;
    live: boolean;
    attention: boolean;
    text: string;
}

export interface MissionEvidence {
    stage: string;
    execution: string;
    status: string;
    statusState: string | null;
    connection: string;
    connectionState: string | null;
    lanes: string;
    liveRowCount: number;
    spinnerModes: Array<string | null>;
    progressLine: string;
    currentTaskDetails: string;
    logStream: string;
    outcome: string;
    intelligence: string;
    steering: string;
    rows: MissionEvidenceRow[];
}

export interface MissionEvidenceStage {
    label: string;
    evidence: MissionEvidence;
    consoleEvidence: string[];
}

export interface MissionProgressHarness {
    sessionId: string;
    liveEvents: any[];
    consoleEvidence: string[];
    pushEvents: (...events: any[]) => void;
    capture: (label: string, options?: { screenshot?: boolean }) => Promise<MissionEvidenceStage>;
}

export function missionEvent(sessionId: string, seq: number, type: string, extra: Record<string, unknown> = {}) {
    return {
        type,
        seq,
        sessionId,
        ts: `2026-05-01T09:00:${String(seq).padStart(2, "0")}.000Z`,
        eventId: `${sessionId}:${seq}`,
        payloadDigest: `digest-${seq}`,
        ...extra,
    };
}

export function missionComposition(sessionId: string) {
    return {
        sessionId,
        architecture: "dynamic",
        task: "Repair the Fabric workspace inventory report and certify the visible evidence.",
        slots: [
            { id: "planner", agentId: "MissionPlanner", role: "Mission planning", skills: [], status: "running" },
            { id: "run-fde", agentId: "FabricDataEngineer", role: "Workspace inventory repair", skills: [], status: "queued" },
            { id: "run-verifier", agentId: "FabricVerifier", role: "Evidence verification", skills: [], status: "queued" },
        ],
        handoffs: [
            { from: "planner", to: "run-fde", kind: "delegate" },
            { from: "run-fde", to: "run-verifier", kind: "verify" },
        ],
    };
}

export function runOverviewEvent(sessionId: string, seq = 1, status: "running" | "completed" | "failed" | "cancelled" = "running") {
    const completed = status === "completed";
    return missionEvent(sessionId, seq, "run_overview", {
        logCategory: "high_level",
        job: {
            id: sessionId,
            status,
            startedAt: "2026-05-01T09:00:00.000Z",
            completedAt: completed ? "2026-05-01T09:08:00.000Z" : null,
        },
        composition: missionComposition(sessionId),
        activeAgentId: completed ? null : "planner",
        artifacts: [],
        changes: [],
        slotProgress: [
            {
                slotId: "planner",
                agentId: "planner",
                agentName: "MissionPlanner",
                role: "Mission planning",
                status: completed ? "done" : "running",
                currentStep: completed ? "Mission settled" : "Waiting for the first public mission event",
            },
            {
                slotId: "run-fde",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
                role: "Workspace inventory repair",
                status: completed ? "done" : "queued",
            },
            {
                slotId: "run-verifier",
                agentId: "run-verifier",
                agentName: "FabricVerifier",
                role: "Evidence verification",
                status: completed ? "done" : "queued",
            },
        ],
    });
}

export function makeSessionRecord(sessionId: string, status: "running" | "completed" | "failed" | "cancelled" = "running") {
    const completed = status === "completed";
    return {
        id: sessionId,
        session_id: sessionId,
        task_description: "Repair the Fabric workspace inventory report and certify the visible evidence.",
        workspace_id: "workspace-progress-contract",
        status,
        created_at: "2026-05-01T09:00:00.000Z",
        started_at: "2026-05-01T09:00:01.000Z",
        completed_at: completed ? "2026-05-01T09:08:00.000Z" : null,
        context: { workspace_name: "AgentHub Progress Lab" },
        composition: missionComposition(sessionId),
        agents: [
            {
                agent_id: "planner",
                role: "Mission planning",
                status: completed ? "completed" : "running",
                current_step: completed ? "Mission settled" : "Waiting for public mission events",
                session_id: "planner",
            },
            {
                agent_id: "FabricDataEngineer",
                role: "Workspace inventory repair",
                status: completed ? "completed" : "queued",
                current_step: completed ? "Repair complete" : "Queued for specialist work",
                session_id: "run-fde",
            },
            {
                agent_id: "FabricVerifier",
                role: "Evidence verification",
                status: completed ? "completed" : "queued",
                current_step: completed ? "Verification complete" : "Queued for verification",
                session_id: "run-verifier",
            },
        ],
    };
}

export async function seedMissionAuth(page: Page) {
    await page.addInitScript(() => {
        window.localStorage.clear();
        window.sessionStorage.clear();
        window.localStorage.setItem("github_token", "e2e-progress-token");
        window.localStorage.setItem("github_user", "progress.e2e");
        window.localStorage.setItem("clawhub.debug", "1");
        window.localStorage.setItem("agenthub_debug_stream", "1");
        window.sessionStorage.setItem("github_token", "e2e-progress-token");
        window.sessionStorage.setItem("github_user", "progress.e2e");
        window.sessionStorage.setItem("workspace_id", "workspace-progress-contract");
    });
}

export function installMissionConsoleCapture(page: Page): string[] {
    const consoleEvidence: string[] = [];
    page.on("console", (message) => {
        const text = message.text();
        if (text.includes("[mc-stream]") || message.type() === "error" || message.type() === "warning") {
            consoleEvidence.push(redactString(`${message.type()}: ${text}`));
        }
    });
    return consoleEvidence;
}

export async function mockMissionProgressApis(page: Page, sessionId: string, liveEvents: any[]) {
    const counts = { events: 0, eventsJson: 0, session: 0, message: 0 };
    await page.route("**/api/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        const pathName = url.pathname;
        const terminal = liveEvents.some((event) => ["job_complete", "job_failed", "job_cancelled"].includes(String(event?.type || "")));
        const sessionStatus = terminal ? "completed" : "running";

        if (method === "GET" && pathName.endsWith(`/api/sessions/${sessionId}/events.json`)) {
            counts.eventsJson += 1;
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                json: {
                    sessionId,
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

        if (method === "GET" && pathName.endsWith(`/api/sessions/${sessionId}/events`)) {
            counts.events += 1;
            await route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                headers: { "Cache-Control": "no-cache" },
                body: `data: ${JSON.stringify(runOverviewEvent(sessionId, 1))}\n\n`,
            });
            return;
        }

        if (method === "GET" && pathName.endsWith(`/api/sessions/${sessionId}`)) {
            counts.session += 1;
            await route.fulfill({ status: 200, json: makeSessionRecord(sessionId, sessionStatus) });
            return;
        }

        if (method === "POST" && pathName.endsWith(`/api/sessions/${sessionId}/message`)) {
            counts.message += 1;
            await route.fulfill({
                status: 200,
                json: {
                    status: "queued",
                    steeringId: `steer-${counts.message}`,
                    targetMode: "broadcast",
                    mode: "queue",
                    targetCount: 1,
                    messagePreview: "Prioritize artifact links and keep the verifier evidence visible.",
                },
            });
            return;
        }

        await route.fulfill({ status: 200, json: {} });
    });
    return counts;
}

export async function disableMissionAnimations(page: Page) {
    await page.addStyleTag({
        content: `
            *, *::before, *::after {
                animation: none !important;
                transition: none !important;
                scroll-behavior: auto !important;
            }
            .mc3-log__tail { display: none !important; }
        `,
    });
}

export async function readMissionEvidence(page: Page, label: string): Promise<MissionEvidence> {
    const evidence = await page.evaluate((stageLabel) => {
        const normalizedText = (value: string | null | undefined) => (value || "").replace(/\s+/g, " ").trim();
        const text = (selector: string) => normalizedText(document.querySelector(selector)?.textContent).slice(0, 6000);
        const textAll = (selector: string) => Array.from(document.querySelectorAll(selector))
            .map((node) => normalizedText(node.textContent))
            .filter(Boolean)
            .join(" | ")
            .slice(0, 6000);
        const attr = (selector: string, name: string) => document.querySelector(selector)?.getAttribute(name) ?? null;
        const rows = Array.from(document.querySelectorAll(".mc3-transcript-row")).map((node) => ({
            kind: node.getAttribute("data-kind"),
            state: node.getAttribute("data-state"),
            live: node.classList.contains("mc3-exec-row--live"),
            attention: node.classList.contains("mc3-exec-row--attention"),
            text: normalizedText(node.textContent).slice(0, 500),
        }));
        return {
            stage: String(stageLabel),
            execution: text('[aria-label="Mission execution"]'),
            status: text(".mc3-execution-status"),
            statusState: attr(".mc3-execution-status", "data-state"),
            connection: text(".mc3-terminal-connection"),
            connectionState: attr(".mc3-terminal-connection", "data-state"),
            lanes: text('[aria-label="Active agent execution"]'),
            liveRowCount: document.querySelectorAll(".mc3-exec-row--live").length,
            spinnerModes: Array.from(document.querySelectorAll(".mc3-exec-current")).map((node) => node.getAttribute("data-spinner-mode")),
            progressLine: textAll(".mc3-agent-progress-line"),
            currentTaskDetails: textAll('[aria-label="Current task details"]'),
            logStream: text('[aria-label="Mission log stream"]'),
            outcome: text(".mc3-outcome-banner"),
            intelligence: text('[aria-label="Mission intelligence"]'),
            steering: text('[aria-label="Mission steering"]'),
            rows,
        };
    }, label);
    return redactEvidence(evidence) as MissionEvidence;
}

export async function attachMissionEvidence(
    page: Page,
    testInfo: TestInfo,
    label: string,
    consoleEvidence: string[] = [],
    options: { screenshot?: boolean } = {},
): Promise<MissionEvidenceStage> {
    const evidence = await readMissionEvidence(page, label);
    const stage: MissionEvidenceStage = {
        label,
        evidence,
        consoleEvidence: consoleEvidence.slice(-80),
    };
    await testInfo.attach(`${label}.json`, {
        body: JSON.stringify(stage, null, 2),
        contentType: "application/json",
    });
    if (options.screenshot !== false) {
        await testInfo.attach(`${label}.png`, {
            body: await page.screenshot({ fullPage: true, animations: "disabled" }),
            contentType: "image/png",
        });
    }
    return stage;
}

export async function setupMissionProgressHarness(
    page: Page,
    testInfo: TestInfo,
    options: { sessionId?: string } = {},
): Promise<MissionProgressHarness> {
    const sessionId = options.sessionId || DEFAULT_PROGRESS_SESSION_ID;
    const liveEvents: any[] = [];
    await seedMissionAuth(page);
    const consoleEvidence = installMissionConsoleCapture(page);
    await mockMissionProgressApis(page, sessionId, liveEvents);
    const harness: MissionProgressHarness = {
        sessionId,
        liveEvents,
        consoleEvidence,
        pushEvents: (...events: any[]) => {
            liveEvents.push(...events);
        },
        capture: (label: string, captureOptions?: { screenshot?: boolean }) => attachMissionEvidence(page, testInfo, label, consoleEvidence, captureOptions),
    };
    return harness;
}

export async function runMissionProgressEvidenceTape(
    page: Page,
    testInfo: TestInfo,
    options: { sessionId?: string; screenshots?: boolean } = {},
): Promise<{ sessionId: string; stages: MissionEvidenceStage[]; consoleEvidence: string[] }> {
    const harness = await setupMissionProgressHarness(page, testInfo, options);
    const stages: MissionEvidenceStage[] = [];
    const screenshot = options.screenshots !== false;
    await page.goto(`/agent-hub/session/${harness.sessionId}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
    await disableMissionAnimations(page);
    await expect(page.locator(".mc3")).toBeVisible({ timeout: 30_000 });
    stages.push(await harness.capture("accepted", { screenshot }));

    harness.pushEvents(...missionProgressStageEvents(harness.sessionId).plan);
    await expect(page.getByRole("log", { name: "Mission log stream" })).toContainText("Generalist created the mission plan", { timeout: 10_000 });
    stages.push(await harness.capture("plan-visible", { screenshot }));

    harness.pushEvents(...missionProgressStageEvents(harness.sessionId).llm);
    await expect(page.getByRole("log", { name: "Mission log stream" })).toContainText("I will inspect the existing report definition", { timeout: 10_000 });
    await expect(page.locator(".mc3-stream-block--assistant.is-live").first()).toBeVisible({ timeout: 10_000 });
    stages.push(await harness.capture("llm-responding", { screenshot }));

    harness.pushEvents(...missionProgressStageEvents(harness.sessionId).specialist);
    await expect(page.getByLabel("Active agent execution")).toHaveCount(0);
    stages.push(await harness.capture("specialist-running", { screenshot }));

    harness.pushEvents(...missionProgressStageEvents(harness.sessionId).toolProgress);
    await expect(page.getByRole("log", { name: "Mission log stream" })).toContainText("workspace scan started", { timeout: 10_000 });
    stages.push(await harness.capture("tool-progress-running", { screenshot }));

    harness.pushEvents(...missionProgressStageEvents(harness.sessionId).rollup);
    await expect(page.getByRole("log", { name: "Mission log stream" })).toContainText("Prepared workspace inventory repair evidence", { timeout: 10_000 });
    stages.push(await harness.capture("rollup-visible", { screenshot }));

    harness.pushEvents(...missionProgressStageEvents(harness.sessionId).approval);
    await expect(page.getByLabel("Mission intelligence")).toContainText("Needs approval", { timeout: 10_000 });
    stages.push(await harness.capture("approval-required", { screenshot }));

    harness.pushEvents(...missionProgressStageEvents(harness.sessionId).steering);
    await expect(page.getByRole("log", { name: "Mission log stream" })).toContainText("Interrupt deferred", { timeout: 10_000 });
    stages.push(await harness.capture("steering-visible", { screenshot }));

    harness.pushEvents(...missionProgressStageEvents(harness.sessionId).verification);
    await expect(page.getByRole("log", { name: "Mission log stream" })).toContainText("Verifier REJECTED", { timeout: 10_000 });
    stages.push(await harness.capture("verifier-visible", { screenshot }));

    harness.pushEvents(...missionProgressStageEvents(harness.sessionId).terminal);
    await expect(page.locator(".mc3-exec-row--live")).toHaveCount(0, { timeout: 10_000 });
    stages.push(await harness.capture("terminal-complete", { screenshot }));

    return { sessionId: harness.sessionId, stages, consoleEvidence: harness.consoleEvidence };
}

export function missionProgressStageEvents(sessionId: string) {
    return {
        plan: [
            missionEvent(sessionId, 2, "mission_seeded", { logCategory: "high_level", taskCount: 3 }),
            missionEvent(sessionId, 3, "generalist_check_in", {
                logCategory: "high_level",
                readyTaskCount: 1,
                runningSubagentCount: 0,
                completedTaskCount: 0,
                blockedTaskCount: 0,
                failedTaskCount: 0,
            }),
        ],
        llm: [
            missionEvent(sessionId, 4, "llm_request_started", {
                logCategory: "high_level",
                agentId: "planner",
                agentName: "MissionPlanner",
                requestId: "req-plan",
                model: "gpt-4o-mini",
                taskTitle: "Plan inventory repair",
            }),
            missionEvent(sessionId, 5, "thinking_started", {
                logCategory: "high_level",
                agentId: "planner",
                agentName: "MissionPlanner",
                requestId: "req-plan",
                summary: "Choosing the safest read-before-write path.",
            }),
            missionEvent(sessionId, 6, "assistant_text_delta", {
                logCategory: "detailed",
                agentId: "planner",
                agentName: "MissionPlanner",
                requestId: "req-plan",
                delta: "I will inspect the existing report definition before changing files.",
                tokenCount: 22,
            }),
        ],
        specialist: [
            missionEvent(sessionId, 7, "generalist_context_pack", {
                logCategory: "high_level",
                runId: "run-fde",
                taskId: "task-inventory-repair",
                agentId: "FabricDataEngineer",
                agentName: "FabricDataEngineer",
                taskTitle: "Repair workspace inventory report",
                objectivePreview: "Read current PBIR files, apply a small safe correction, and preserve verifier evidence.",
                toolScopeCount: 4,
                upstreamResultCount: 1,
                acceptanceCriteriaCount: 3,
                contextDigest: "ctx-progress-001",
            }),
            missionEvent(sessionId, 8, "subagent_spawned", {
                logCategory: "high_level",
                run: { id: "run-fde", taskId: "task-inventory-repair", agentId: "FabricDataEngineer", agentSessionId: "run-fde", status: "running" },
                task: { id: "task-inventory-repair", title: "Repair workspace inventory report", objective: "Fix the report evidence path without broad rewrites." },
            }),
            missionEvent(sessionId, 9, "slot_progress", {
                logCategory: "high_level",
                slotId: "run-fde",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
                role: "Workspace inventory repair",
                status: "running",
                activeAgentId: "run-fde",
                currentStep: "Reading workspace inventory report definition",
            }),
        ],
        toolProgress: [
            missionEvent(sessionId, 10, "tool_call_started", {
                logCategory: "diagnostic",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
                callId: "inventory-repair",
                toolName: "fabric_get_item_definition",
                toolKind: "read",
                operationKind: "read",
                argsPreview: { item_type: "Report", display_name: "Workspace Inventory" },
            }),
            missionEvent(sessionId, 11, "tool_progress", {
                logCategory: "diagnostic",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
                callId: "inventory-repair",
                toolName: "fabric_get_item_definition",
                toolKind: "read",
                operationKind: "read",
                step: "workspace_scan",
                status: "started",
                elapsedMs: 1100,
            }),
            missionEvent(sessionId, 12, "tool_progress", {
                logCategory: "diagnostic",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
                callId: "inventory-repair",
                toolName: "fabric_get_item_definition",
                toolKind: "read",
                operationKind: "read",
                step: "report_definition_validation",
                status: "running",
                elapsedMs: 2300,
            }),
        ],
        rollup: [
            missionEvent(sessionId, 13, "tool_call_ended", {
                logCategory: "diagnostic",
                agentId: "run-fde",
                callId: "inventory-repair",
                toolName: "fabric_get_item_definition",
                toolKind: "read",
                operationKind: "read",
                durationMs: 2600,
                status: "ok",
            }),
            missionEvent(sessionId, 14, "activity_rollup", {
                logCategory: "high_level",
                scope: "tool_batch",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
                callId: "inventory-repair",
                toolName: "fabric_get_item_definition",
                toolKind: "read",
                operationKind: "read",
                summary: "Prepared workspace inventory repair evidence from the current report definition.",
                coveredSeqStart: 10,
                coveredSeqEnd: 13,
                detailCount: 4,
                status: "completed",
                durationMs: 2600,
                counts: { toolCalls: 1, outputChars: 980 },
            }),
        ],
        approval: [
            missionEvent(sessionId, 15, "approval_required", {
                logCategory: "high_level",
                approvalId: "approve-write-report",
                slotId: "run-fde",
                agentId: "run-fde",
                summary: "Write the repaired report definition back to the workspace inventory report.",
                blastRadius: "report-definition",
                reversible: true,
                toolCallPreview: { name: "fabric_write_file", args: { display_name: "Workspace Inventory", path: "definition/report.json" } },
                recoveryActions: ["approve", "decline", "edit_input"],
            }),
            missionEvent(sessionId, 16, "slot_progress", {
                logCategory: "high_level",
                slotId: "run-fde",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
                role: "Workspace inventory repair",
                status: "approval_required",
                activeAgentId: "run-fde",
                currentStep: "Waiting for approval to write the repaired report definition",
            }),
        ],
        steering: [
            missionEvent(sessionId, 17, "user_message_queued", {
                logCategory: "high_level",
                steeringId: "steer-1",
                targetAgentSessionId: "run-fde",
                agentName: "FabricDataEngineer",
                targetMode: "agent",
                mode: "queue",
                messagePreview: "Keep artifact links visible and do not hide verifier failures.",
            }),
            missionEvent(sessionId, 18, "turn_interrupt_requested", {
                logCategory: "high_level",
                steeringId: "steer-2",
                targetAgentSessionId: "run-fde",
                agentName: "FabricDataEngineer",
                targetMode: "agent",
                mode: "interrupt",
                messagePreview: "Pause before any broad rewrite.",
            }),
            missionEvent(sessionId, 19, "turn_interrupt_deferred", {
                logCategory: "high_level",
                steeringId: "steer-2",
                targetAgentSessionId: "run-fde",
                agentName: "FabricDataEngineer",
                targetMode: "agent",
                mode: "interrupt",
                messagePreview: "Pause before any broad rewrite.",
                reason: "Current write boundary is not interruptible until the approval resolves.",
            }),
            missionEvent(sessionId, 20, "user_message_delivered", {
                logCategory: "high_level",
                steeringId: "steer-1",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
                targetMode: "agent",
                mode: "queue",
                messagePreview: "Keep artifact links visible and do not hide verifier failures.",
                deliveredAtRound: 2,
            }),
        ],
        verification: [
            missionEvent(sessionId, 21, "approval.resolved", {
                logCategory: "high_level",
                approvalId: "approve-write-report",
                action: "approve",
            }),
            missionEvent(sessionId, 22, "slot_progress", {
                logCategory: "high_level",
                slotId: "run-fde",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
                role: "Workspace inventory repair",
                status: "running",
                activeAgentId: "run-fde",
                currentStep: "Writing repaired report definition and collecting proof",
            }),
            missionEvent(sessionId, 23, "tool_progress", {
                logCategory: "diagnostic",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
                callId: "inventory-write",
                toolName: "fabric_write_file",
                toolKind: "write",
                operationKind: "write",
                step: "report_definition_write",
                status: "completed",
                elapsedMs: 3900,
            }),
            missionEvent(sessionId, 24, "change_recorded", {
                logCategory: "high_level",
                recordId: "report-definition-repair",
                kind: "updated",
                status: "applied",
                targetName: "Workspace Inventory report definition",
                targetType: "Report definition",
                targetScope: "file",
                summary: "Updated the report definition and kept verifier evidence attached.",
                toolName: "fabric_write_file",
                agentId: "run-fde",
                agentName: "FabricDataEngineer",
            }),
            missionEvent(sessionId, 25, "artifact_added", {
                logCategory: "high_level",
                artifactId: "repair-proof",
                agentId: "run-fde",
                kind: "proof",
                name: "Workspace inventory repair proof",
                state: "written",
                webUrl: "https://example.test/proof",
            }),
            missionEvent(sessionId, 26, "verifier_verdict", {
                logCategory: "high_level",
                verdictId: "verdict-progress",
                verifierRunId: "run-verifier",
                verifierTaskId: "verify-inventory",
                verifierAgentId: "FabricVerifier",
                targetTaskId: "task-inventory-repair",
                passed: false,
                verifierClaimedSuccess: false,
                structuralFailures: ["STEP_FAILED:REPORT_RENDER"],
                requiresUserBrowserRender: true,
                deliverables: [{ type: "Report", name: "Workspace Inventory", webUrl: "https://example.test/report" }],
                evidence: {
                    browserVerifiedUrls: ["https://example.test/report"],
                    screenshotPaths: ["/tmp/progress-report.png"],
                    visualsRendered: false,
                    loadingStuckObserved: false,
                    errorsObserved: ["Report visual did not render after definition repair."],
                    expectedTextMatched: false,
                },
                stepResults: [
                    { step: "Definition write", status: "passed", evidence: "File update accepted" },
                    { step: "Report renders", status: "failed", reason: "Visual did not render in browser proof" },
                ],
                criteria: ["Report renders", "Evidence remains visible"],
                decisionRationale: "Verifier rejected because browser render proof failed.",
                summary: "Report render proof failed after the repair.",
            }),
        ],
        terminal: [
            missionEvent(sessionId, 27, "mission_completed", { logCategory: "high_level" }),
            missionEvent(sessionId, 28, "job_complete", {
                logCategory: "high_level",
                jobId: sessionId,
                status: "completed",
                totalDuration: "8m 00s",
            }),
        ],
    };
}

export function assertNoRawRuntimeText(evidence: MissionEvidence) {
    const userFacing = JSON.stringify({
        execution: evidence.execution,
        lanes: evidence.lanes,
        progressLine: evidence.progressLine,
        currentTaskDetails: evidence.currentTaskDetails,
        logStream: evidence.logStream,
        intelligence: evidence.intelligence,
        steering: evidence.steering,
        rows: evidence.rows.map((row) => row.text),
    });
    expect(userFacing).not.toMatch(/Bearer\s+[A-Za-z0-9._-]+/i);
    expect(userFacing).not.toMatch(/github_token|fabric_token|e2e-progress-token|e2e-fabric-token/i);
    expect(userFacing).not.toMatch(/TOOL_ERROR|undefined|=>|->|SECRET_INTERNAL_TRACE_DO_NOT_RENDER/i);
    expect(userFacing).not.toMatch(/\{\s*"(?:type|args|toolName)"/i);
}

export function redactEvidence(value: unknown): unknown {
    if (typeof value === "string") return redactString(value);
    if (Array.isArray(value)) return value.map((item) => redactEvidence(item));
    if (value && typeof value === "object") {
        return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, redactEvidence(item)]));
    }
    return value;
}

export function redactString(value: string): string {
    return value
        .replace(/Bearer\s+[A-Za-z0-9._-]+/gi, "Bearer [redacted]")
    .replace(/((?:github|fabric|copilot|authorization)[_-]?token)[=:]\s*[^\s,;]+/gi, "$1=[redacted]")
        .replace(/e2e-(?:progress|fabric)-token/gi, "[redacted-token]")
        .replace(/gh[pousr]_[A-Za-z0-9_]+/g, "[redacted-github-token]");
}