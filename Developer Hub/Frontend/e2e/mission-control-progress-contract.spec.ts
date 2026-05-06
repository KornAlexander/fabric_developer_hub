import { test, expect } from "@playwright/test";

import {
    assertNoRawRuntimeText,
    disableMissionAnimations,
    missionProgressStageEvents,
    setupMissionProgressHarness,
    type MissionEvidenceStage,
} from "./utils/missionEvidence";

test.use({ viewport: { width: 1440, height: 960 } });

test.describe("Mission Control progress contract", () => {
    test("renders progressive LLM, tool, approval, steering, verifier, and terminal behavior", async ({ page }, testInfo) => {
        test.setTimeout(120_000);
        const harness = await setupMissionProgressHarness(page, testInfo);
        const tape = missionProgressStageEvents(harness.sessionId);
        const stages: MissionEvidenceStage[] = [];
        const capture = async (label: string) => {
            const stage = await harness.capture(label);
            assertNoRawRuntimeText(stage.evidence);
            stages.push(stage);
            return stage;
        };

        await page.goto(`/agent-hub/session/${harness.sessionId}?agenthubE2E=1`, { waitUntil: "domcontentloaded" });
        await disableMissionAnimations(page);

        const executionSurface = page.getByRole("region", { name: "Mission execution" });
        const liveLog = page.getByRole("log", { name: "Mission log stream" });
        await expect(executionSurface).toBeVisible({ timeout: 30_000 });
        await expect(liveLog).toBeVisible({ timeout: 10_000 });
        await expect(executionSurface).toContainText("Repair the Fabric workspace inventory report", { timeout: 10_000 });
        const accepted = await capture("accepted");
        expect(accepted.evidence.execution.length).toBeGreaterThan(80);
        expect(accepted.evidence.statusState).toMatch(/running|waiting/);

        harness.pushEvents(...tape.plan);
        await expect(liveLog).toContainText("Generalist created the mission plan", { timeout: 10_000 });
        await expect(liveLog).toContainText("Generalist checkpoint", { timeout: 10_000 });
        const plan = await capture("plan-visible");
        expect(plan.evidence.rows.some((row) => /mission plan/i.test(row.text))).toBe(true);

        const [requestStarted, thinkingStarted, textDelta] = tape.llm;
        harness.pushEvents(requestStarted);
        await expect(liveLog).toContainText("Preparing Plan inventory repair", { timeout: 10_000 });
        await expect(liveLog).not.toContainText("gpt-4o-mini");
        await expect(page.locator('.mc3-exec-current[data-spinner-mode="requesting"]').first()).toBeVisible({ timeout: 10_000 });
        const requesting = await capture("llm-requesting");
        expect(requesting.evidence.spinnerModes).toContain("requesting");
        expect(requesting.evidence.progressLine).toMatch(/preparing|plan inventory repair|next public agent event/i);

        harness.pushEvents(thinkingStarted);
        await expect(liveLog).toContainText("Choosing the safest", { timeout: 10_000 });
        await expect(page.locator(".mc3-stream-block--thinking.is-live").first()).toBeVisible({ timeout: 10_000 });
        const thinking = await capture("llm-thinking");
        expect(thinking.evidence.logStream).toMatch(/Thinking|safest read/i);

        harness.pushEvents(textDelta);
        await expect(liveLog).toContainText("I will inspect the existing report definition", { timeout: 10_000 });
        await expect(page.locator(".mc3-stream-block--assistant.is-live").first()).toBeVisible({ timeout: 10_000 });
        const responding = await capture("llm-responding");
        expect(responding.evidence.logStream).toContain("inspect the existing report definition");

        harness.pushEvents(...tape.specialist);
        await expect(page.getByLabel("Active agent execution")).toHaveCount(0);
        await expect(liveLog).toContainText("Specialist started", { timeout: 10_000 });
        await expect(page.getByLabel("Mission intelligence")).toContainText("FabricDataEngineer", { timeout: 10_000 });
        const specialist = await capture("specialist-running");
        expect(specialist.evidence.intelligence).toContain("FabricDataEngineer");
        expect(specialist.evidence.liveRowCount).toBeGreaterThanOrEqual(1);

        const [toolStarted, ...toolTicks] = tape.toolProgress;
        harness.pushEvents(toolStarted);
        await expect(page.locator('.mc3-exec-current[data-spinner-mode="tool-use"]').first()).toBeVisible({ timeout: 10_000 });
        const toolStart = await capture("tool-started");
        harness.pushEvents(...toolTicks);
        await expect(liveLog).toContainText("workspace scan started", { timeout: 10_000 });
        await expect(liveLog).toContainText("report definition validation running", { timeout: 10_000 });
        const toolProgress = await capture("tool-progress-running");
        expect(toolProgress.evidence.spinnerModes).toContain("tool-use");
        expect(toolProgress.evidence.liveRowCount).toBeLessThanOrEqual(4);
        expect(toolProgress.evidence.rows.filter((row) => row.live && /FabricDataEngineer|workspace scan|report definition validation/i.test(row.text)).length).toBeLessThanOrEqual(1);
        expect(toolProgress.evidence.rows.filter((row) => /workspace scan started/i.test(row.text)).length).toBeLessThanOrEqual(1);
        expect(toolProgress.evidence.rows.length).toBeLessThanOrEqual(toolStart.evidence.rows.length + 2);

        harness.pushEvents(...tape.rollup);
        await expect(liveLog).toContainText("Prepared workspace inventory repair evidence", { timeout: 10_000 });
        const rollupRow = page.locator(".mc3-transcript-row", { hasText: "Prepared workspace inventory repair evidence" }).first();
        await expect(rollupRow).toBeVisible();
        await expect(rollupRow.getByRole("button", { name: /Show details \(\+4\)/ })).toBeVisible();
        await rollupRow.getByRole("button", { name: /Show details/ }).click({ force: true });
        await expect(rollupRow.locator(".mc3-transcript-child__text").first()).toBeVisible();
        const rollup = await capture("rollup-visible");
        expect(rollup.evidence.intelligence).toContain("Prepared workspace inventory repair evidence");

        harness.pushEvents(...tape.approval);
        await expect(page.locator(".mc3-execution-status")).toHaveAttribute("data-state", "waiting", { timeout: 10_000 });
        await expect(page.locator(".mc3-terminal-connection")).toHaveAttribute("data-state", "waiting", { timeout: 10_000 });
        await expect(page.getByLabel("Active agent execution")).toHaveCount(0);
        await expect(page.getByLabel("Mission intelligence")).toContainText("Needs approval", { timeout: 10_000 });
        await expect(liveLog).toContainText("Approval required", { timeout: 10_000 });
        const approval = await capture("approval-required");
        expect(approval.evidence.status).toContain("waiting for approval");
        expect(approval.evidence.connectionState).toBe("waiting");

        harness.pushEvents(...tape.steering);
        await expect(liveLog).toContainText("Steering queued", { timeout: 10_000 });
        await expect(liveLog).toContainText("Interrupt requested", { timeout: 10_000 });
        await expect(liveLog).toContainText("Interrupt deferred", { timeout: 10_000 });
        await expect(liveLog).toContainText("Steering delivered", { timeout: 10_000 });
        const steering = await capture("steering-visible");
        expect(steering.evidence.logStream).toContain("Interrupt deferred");

        harness.pushEvents(...tape.verification);
        await expect(page.locator(".mc3-execution-status")).not.toContainText("waiting for approval", { timeout: 10_000 });
        await expect(page.getByLabel("Mission intelligence")).toContainText("Workspace Inventory report definition", { timeout: 10_000 });
        await expect(liveLog).toContainText("Verifier REJECTED", { timeout: 10_000 });
        await expect(liveLog).toContainText("Report renders", { timeout: 10_000 });
        const verifier = await capture("verifier-visible");
        expect(verifier.evidence.rows.some((row) => row.attention && /Verifier REJECTED/i.test(row.text))).toBe(true);

        harness.pushEvents(...tape.terminal);
        await expect(page.locator(".mc3-exec-row--live")).toHaveCount(0, { timeout: 10_000 });
        await expect(page.locator(".mc3-execution-status")).toContainText("verifier rejected", { timeout: 10_000 });
        await expect(page.locator(".mc3-terminal-connection")).toHaveAttribute("data-state", "complete", { timeout: 10_000 });
        await expect(page.getByLabel("Mission intelligence")).toContainText("Workspace Inventory report definition", { timeout: 10_000 });
        await expect(page.locator("#mc3-steering-message")).toBeDisabled();
        const terminal = await capture("terminal-complete");
        expect(terminal.evidence.liveRowCount).toBe(0);
        expect(terminal.evidence.statusState).toBe("failed");
        expect(terminal.evidence.rows.some((row) => row.live)).toBe(false);
        expect(terminal.evidence.status).toContain("verifier rejected");

        await testInfo.attach("mission-progress-evidence-tape.json", {
            body: JSON.stringify(stages, null, 2),
            contentType: "application/json",
        });
        await testInfo.attach("mission-progress-console.json", {
            body: JSON.stringify(harness.consoleEvidence, null, 2),
            contentType: "application/json",
        });

        expect(harness.consoleEvidence.some((line) => line.includes("mission_seeded"))).toBe(true);
        expect(harness.consoleEvidence.some((line) => line.includes("tool_progress"))).toBe(true);
        const unexpectedConsoleErrors = harness.consoleEvidence.filter((line) => /^error:/i.test(line) && !/SSE ERROR/i.test(line));
        expect(unexpectedConsoleErrors).toEqual([]);
    });
});