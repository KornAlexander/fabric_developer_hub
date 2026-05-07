import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

import { PI_SAMPLE_PROMPT, attachPiLiveLogScreenshot, setupPiMissionPromptHarness } from "./utils/piMissionEvidence";

test.use({ viewport: { width: 1440, height: 960 } });

async function latestPiLiveLogSeq(page: import("@playwright/test").Page): Promise<number> {
    return page.locator('[data-pi-live-log-row="true"]').evaluateAll((rows) => {
        const seqs = rows
            .map((row) => Number((row as HTMLElement).dataset.piLogSeq || row.getAttribute("data-pi-log-seq") || 0))
            .filter((seq) => Number.isFinite(seq));
        return seqs.length ? Math.max(...seqs) : 0;
    }).catch(() => 0);
}

async function latestPiSubagentSeq(page: import("@playwright/test").Page): Promise<number> {
    return page.locator('[data-pi-subagents-status-row="true"], [data-pi-subagents-control-row="true"], [data-pi-subagents-result-row="true"], [data-pi-subagents-async-row="true"]').evaluateAll((rows) => {
        const seqs = rows
            .map((row) => Number((row as HTMLElement).dataset.piSeq || row.getAttribute("data-pi-seq") || 0))
            .filter((seq) => Number.isFinite(seq));
        return seqs.length ? Math.max(...seqs) : 0;
    }).catch(() => 0);
}

async function attachFullPageScreenshot(page: import("@playwright/test").Page, testInfo: import("@playwright/test").TestInfo, label: string) {
    const body = await page.screenshot({ fullPage: true, animations: "disabled" });
    const evidenceDir = path.join(process.cwd(), "test-results", "pi-subagents-observability");
    await fs.mkdir(evidenceDir, { recursive: true });
    await fs.writeFile(path.join(evidenceDir, `${label}.png`), body);
    await testInfo.attach(`${label}.png`, {
        body,
        contentType: "image/png",
    });
}

test.describe("Mission Control Pi sample prompt", () => {
    test("starts from a sample prompt and opens the full Pi Web UI mission session", async ({ page }, testInfo) => {
        test.setTimeout(120_000);
        const harness = await setupPiMissionPromptHarness(page);

        await harness.startFromPrompt();

        expect(harness.createBodies).toHaveLength(1);
        expect(harness.createBodies[0]).toMatchObject({
            task_description: PI_SAMPLE_PROMPT,
            workspace_id: "pi-sample-workspace",
            context: {
                workspace_name: "AgentHub Pi Sample Workspace",
                runtime: "pi",
                orchestration_runtime: "pi",
                subagent_runtime: "pi-subagents",
                subagent_package: "npm:pi-subagents@0.21.3",
                subagent_observability: "pi-subagents-native-events",
                execution_stream_interface: "pi-extension",
                pi_orchestration: {
                    runtime: "pi",
                    subagent_runtime: "pi-subagents",
                    subagent_package_name: "pi-subagents",
                    subagent_runtime_mode: "foreground-status-control-results",
                    runtime_package_name: "@mariozechner/pi-agent-core",
                    mcp_adapter_package_name: "pi-mcp-adapter",
                    mcp_access_mode: "pi-mcp-adapter-proxy-via-agenthub-policy",
                    mcp_direct_tools_default: false,
                    context_mode_package_name: "context-mode",
                    process_governor_package_name: "@a5c-ai/babysitter-pi",
                    process_governor: "babysitter-pi",
                    log_compaction_extension: "@fabric-clawhub/pi-log-compactor",
                    agentic_engineering_extension: "@fabric-clawhub/pi-agentic-engineering",
                    rpi_protocol: "research-plan-implement-context-gates",
                    context_pack_schema: "ContextPackV2",
                    subagent_work_model: "context-window-fork",
                    context_mode_facade: "agenthub-governed-context-mode",
                    frontend_runtime_package_name: "@mariozechner/pi-web-ui",
                    execution_surface_extension: "@fabric-clawhub/pi-mission-ui",
                    stream_transport: "agenthub-sse-to-pi-extension",
                },
            },
        });
        expect(harness.createBodies[0].context.pi_orchestration.extensions.map((extension: any) => extension.source)).toEqual(expect.arrayContaining([
            "npm:@mariozechner/pi-web-ui@0.71.1",
            "npm:@mariozechner/pi-agent-core@0.71.1",
            "npm:@mariozechner/pi-coding-agent@0.71.1",
            "npm:@mariozechner/pi-tui@0.71.1",
            "npm:pi-ask-user@0.8.0",
            "npm:pi-subagents@0.21.3",
            "npm:pi-mcp-adapter@2.5.2",
            "npm:context-mode@1.0.103",
            "npm:@a5c-ai/babysitter-pi@0.1.3",
            ".pi/extensions/fabric-clawhub-mission-ui.ts",
            ".pi/extensions/fabric-clawhub-log-compactor.ts",
            ".pi/extensions/fabric-clawhub-agentic-engineering.ts",
        ]));
        expect(harness.runAttempts()).toBe(1);

        const piPage = page.locator(".mc3-pi-web-ui-page");
        const piStream = page.getByRole("log", { name: "Mission stream" });
        await expect(piPage).toHaveAttribute("data-mission-summary", /Use Pi extensions to inspect the Workspace Inventory report/);
        await expect(piStream).toHaveAttribute("data-runtime", "pi");
        await expect(piStream).toHaveAttribute("data-pi-runtime-package", "@mariozechner/pi-web-ui");
        await expect(piStream).toHaveAttribute("data-pi-architecture-layers", "application->core->foundation");
        await expect(piStream).toHaveAttribute("data-pi-core-layer", "@mariozechner/pi-agent-core");
        await expect(piStream).toHaveAttribute("data-pi-foundation-layer", /@mariozechner\/pi-ai/);
        await expect(piStream).toHaveAttribute("data-pi-backend-tool-count", /[1-9]\d*/);
        await expect(piStream).toHaveAttribute("data-pi-stream-interface", "pi-web-ui-agent-interface");
        await expect(piStream).toHaveAttribute("data-pi-subagent-runtime", "pi-subagents");
        await expect(piStream).toHaveAttribute("data-pi-subagent-package", /pi-subagents/);
        await expect(piStream).toHaveAttribute("data-pi-context-mode-status", "active");
        await expect(piStream).toHaveAttribute("data-pi-babysitter-status", "active");
        await expect(piStream).toHaveAttribute("data-pi-log-compaction-status", "active");
        await expect(piStream).toHaveAttribute("data-pi-agentic-engineering-package", "@fabric-clawhub/pi-agentic-engineering");
        await expect(piStream).toHaveAttribute("data-pi-rpi-protocol", "research-plan-implement-context-gates");
        await expect(piStream).toHaveAttribute("data-pi-context-pack-schema", "ContextPackV2");
        await expect(piStream).toHaveAttribute("data-pi-context-mode-facade", "agenthub-governed-context-mode");
        await expect(piStream).toHaveAttribute("data-pi-subagent-work-model", "context-window-fork");
        await expect(piStream).toHaveAttribute("data-pi-observability-stream", "pi-subagents-native-events");
        await expect(piStream).toHaveAttribute("data-design-intent", "native-pi-web-ui-session");

        await expect(page.locator('pi-chat-panel[data-pi-web-component="pi-chat-panel"]')).toHaveCount(1);
        await expect(page.locator('agent-interface[data-pi-runtime="@mariozechner/pi-web-ui"]')).toHaveCount(1);
        await expect(page.locator("message-editor textarea")).toBeVisible();

        const subagentsPanel = page.locator('[data-pi-subagents-observability="true"]');
        await expect(subagentsPanel).toBeVisible({ timeout: 30_000 });
        await expect(page.locator('[data-pi-subagents-status-row="true"]').first()).toBeVisible({ timeout: 30_000 });
        await expect(subagentsPanel).toContainText(/Agent work summary|MissionPlanner|delegated agents status stream/i, { timeout: 30_000 });
        const firstSubagentSeq = await latestPiSubagentSeq(page);
        expect(firstSubagentSeq, "Pi subagent observability must expose sequenced native status rows").toBeGreaterThan(0);
        await attachFullPageScreenshot(page, testInfo, "pi-sample-prompt-subagents-first-visible");

        const piLiveLog = page.getByLabel("Mission activity log");
        await expect(piLiveLog).toBeVisible({ timeout: 30_000 });
        await expect(piLiveLog).toHaveAttribute("data-pi-log-execution-streaming-ui", "agenthub-code-inspired");
        await expect(piLiveLog.locator('[data-pi-log-compaction-chip="true"]')).toContainText("Auto-summary");
        await expect(piLiveLog.locator('[data-pi-log-compaction-policy="true"]')).toContainText("Newest rows stay open");
        await expect(page.locator('[data-pi-live-log-row="true"]').first()).toBeVisible({ timeout: 30_000 });
        await expect(piLiveLog).toContainText(/I inspected the workspace first|Inspecting workspace context/i, { timeout: 30_000 });
        const firstVisibleSeq = await latestPiLiveLogSeq(page);
        expect(firstVisibleSeq, "The Pi live-log strip should expose a sequenced rendered log row before the growth screenshot").toBeGreaterThan(0);
        await attachFullPageScreenshot(page, testInfo, "pi-sample-prompt-live-log-first-visible");

        await expect.poll(() => latestPiLiveLogSeq(page), {
            timeout: 12_000,
            intervals: [500, 750, 1000],
            message: "Pi live-log rows should advance as staged mission events arrive",
        }).toBeGreaterThan(firstVisibleSeq);
        await expect.poll(() => latestPiSubagentSeq(page), {
            timeout: 12_000,
            intervals: [500, 750, 1000],
            message: "Pi subagent status/control/result rows should advance as staged mission events arrive",
        }).toBeGreaterThan(firstSubagentSeq);
        await expect(page.locator('[data-pi-subagents-control-row="true"]').first()).toBeVisible({ timeout: 10_000 });
        await expect(subagentsPanel).toHaveAttribute("data-pi-subagents-result-count", /[1-9]\d*/, { timeout: 10_000 });
        await expect(subagentsPanel).toContainText(/needs review|paused behind AgentHub approval/i, { timeout: 10_000 });
        await attachFullPageScreenshot(page, testInfo, "pi-sample-prompt-subagents-streaming");
        await expect(piLiveLog).toContainText(/Report definition loaded|Read the current report definition|Workspace Inventory verifier evidence/i, { timeout: 10_000 });
        await attachFullPageScreenshot(page, testInfo, "pi-sample-prompt-live-log-streaming");
        await expect.poll(async () => page.locator('[data-pi-live-log-rollup="true"]').count(), {
            timeout: 10_000,
            intervals: [500, 750, 1000],
            message: "Pi log compactor should collapse older live-log rows into rollups",
        }).toBeGreaterThan(0);
        await expect(piLiveLog.locator('[aria-label="Mission activity metrics"]')).toContainText(/current|summaries|hidden/);
        await expect(piLiveLog).toHaveAttribute("data-pi-log-visual-language", "agenthub-code-tree");
        await expect(piLiveLog.locator('[data-pi-log-inline-tags="true"]').first()).toBeVisible();
        await attachPiLiveLogScreenshot(page, testInfo, "pi-sample-prompt-log-execution-stream");

        await expect(page.locator("message-list")).toContainText("I inspected the workspace first");
        await expect(page.locator("message-list")).toContainText("fabric_get_item_definition");
        await expect(page.locator("message-list")).toContainText("fabric_update_item_definition");

        await expect(page.getByRole("region", { name: "Mission execution" })).toHaveCount(0);
        await expect(page.getByLabel("Embedded mission terminal")).toHaveCount(0);
        await expect(page.getByLabel("Mission reply editor")).toHaveCount(0);
        await expect(page.getByText("Run intelligence")).toHaveCount(0);
        await expect(page.getByText("Steer mission")).toHaveCount(0);
        await expect(page.locator(".right-rail")).toHaveCount(0);
        await expect(page.locator(".canvas-log-stream")).toHaveCount(0);
        await expect(page.locator(".mc3-transcript-row")).toHaveCount(0);
        await expect(piStream).not.toContainText(/FABRIC_API_TOKEN|POWERBI_API_TOKEN|Bearer\s+|SECRET_INTERNAL_TRACE_DO_NOT_RENDER|schemaVersion/i);

        const editor = page.locator("message-editor").last();
        const editorInput = editor.locator("textarea");
        await editorInput.fill("Keep the verifier lane running after approval.");
        const sendButton = editor.locator("button:not(:disabled)").last();
        await expect(sendButton).toBeVisible({ timeout: 10_000 });
        await sendButton.click();
        await expect.poll(() => harness.sentMessages.length, { timeout: 10_000 }).toBe(1);
        await expect(page.locator("message-list")).toContainText("Queued in AgentHub", { timeout: 10_000 });
        expect(harness.sentMessages).toHaveLength(1);
        expect(harness.sentMessages[0]).toMatchObject({ mode: "queue", message: "Keep the verifier lane running after approval." });

        const evidence = await harness.capture(testInfo, "pi-sample-prompt-web-ui");
        expect(evidence.piRuntimePackage).toBe("@mariozechner/pi-web-ui");
        expect(evidence.piArchitectureLayers).toBe("application->core->foundation");
        expect(Number(evidence.piBackendToolCount)).toBeGreaterThan(0);
        expect(evidence.piStreamInterface).toBe("pi-web-ui-agent-interface");
        expect(evidence.piSubagentRuntime).toBe("pi-subagents");
        expect(evidence.piSubagentPackage).toContain("pi-subagents");
        expect(evidence.piContextModeStatus).toBe("active");
        expect(evidence.piBabysitterStatus).toBe("active");
        expect(evidence.piLogCompactionStatus).toBe("active");
        expect(evidence.piAgenticEngineeringPackage).toBe("@fabric-clawhub/pi-agentic-engineering");
        expect(evidence.piRpiProtocol).toBe("research-plan-implement-context-gates");
        expect(evidence.piContextPackSchema).toBe("ContextPackV2");
        expect(evidence.piContextModeFacade).toBe("agenthub-governed-context-mode");
        expect(evidence.piSubagentWorkModel).toBe("context-window-fork");
        expect(evidence.piLogExecutionStreamingUi).toBe("agenthub-code-inspired");
        expect(evidence.piLogVisualLanguage).toBe("agenthub-code-tree");
        expect(evidence.piLogCompactionChipVisible).toBe(true);
        expect(evidence.piLogCompactionPolicyText).toContain("Newest rows stay open");
        expect(evidence.piLiveLogMetricsText).toMatch(/current|summaries|hidden/);
        expect(evidence.piLiveLogInlineTagCount).toBeGreaterThan(0);
        expect(evidence.piLiveLogCollapsedCount).toBeGreaterThan(0);
        expect(evidence.piLiveLogHiddenDetailCount).toBeGreaterThan(0);
        expect(evidence.piLiveLogHiddenDetailAttribute).toBeGreaterThan(0);
        expect(evidence.piSubagentStream).toBe("status-control-result");
        expect(evidence.piObservabilityStream).toBe("pi-subagents-native-events");
        expect(evidence.piExtensionPackages).toContain("npm:@mariozechner/pi-web-ui@0.71.1");
        expect(evidence.piExtensionPackages).toContain("npm:context-mode@1.0.103");
        expect(evidence.piExtensionPackages).toContain("npm:@a5c-ai/babysitter-pi@0.1.3");
        expect(evidence.piExtensionPackages).toContain(".pi/extensions/fabric-clawhub-log-compactor.ts");
        expect(evidence.piExtensionPackages).toContain(".pi/extensions/fabric-clawhub-agentic-engineering.ts");
        expect(evidence.liveLogRowCount).toBeGreaterThan(0);
        expect(evidence.liveLogMaxSeq).toBeGreaterThan(firstVisibleSeq);
        expect(evidence.subagentStatusCount).toBeGreaterThan(0);
        expect(evidence.subagentControlCount).toBeGreaterThan(0);
        expect(evidence.subagentResultCount).toBeGreaterThan(0);
        expect(evidence.subagentMaxSeq).toBeGreaterThan(firstSubagentSeq);
        expect(evidence.subagentObservabilityText).toMatch(/Agent work summary|MissionPlanner|Planner subagent produced/i);
        expect(evidence.nativeMessageListVisible).toBe(true);
        expect(evidence.legacyRowCount).toBe(0);
        expect(evidence.legacyLogStreamCount).toBe(0);
    });
});
