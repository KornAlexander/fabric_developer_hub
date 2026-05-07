import { test, expect } from "@playwright/test";

import { attachPiLiveLogScreenshot, setupPiMissionHarness } from "./utils/piMissionEvidence";

test.use({ viewport: { width: 1440, height: 960 } });

test.describe("Mission Control Pi execution surface", () => {
    test("uses Pi Web UI as the full mission page and queues input through AgentHub", async ({ page }, testInfo) => {
        test.setTimeout(120_000);
        const harness = await setupPiMissionHarness(page);
        await harness.setup();

        const piPage = page.locator(".mc3-pi-web-ui-page");
        const piStream = page.getByRole("log", { name: "Mission stream" });
        const runtime = page.locator(".pi-runtime-host--full");
        const editor = page.locator("message-editor").last();
        const editorInput = editor.locator("textarea");

        await expect(piPage).toBeVisible();
        await expect(piStream).toBeVisible();
        await expect(piStream).toHaveAttribute("data-runtime", "pi");
        await expect(piStream).toHaveAttribute("data-pi-runtime-package", "@mariozechner/pi-web-ui");
        await expect(piStream).toHaveAttribute("data-pi-architecture-layers", "application->core->foundation");
        await expect(piStream).toHaveAttribute("data-pi-application-layer", /@mariozechner\/pi-coding-agent/);
        await expect(piStream).toHaveAttribute("data-pi-core-layer", "@mariozechner/pi-agent-core");
        await expect(piStream).toHaveAttribute("data-pi-foundation-layer", /@mariozechner\/pi-ai/);
        await expect(piStream).toHaveAttribute("data-pi-context-mode-status", "active");
        await expect(piStream).toHaveAttribute("data-pi-babysitter-status", "active");
        await expect(piStream).toHaveAttribute("data-pi-log-compaction-status", "active");
        await expect(piStream).toHaveAttribute("data-pi-agentic-engineering-package", "@fabric-clawhub/pi-agentic-engineering");
        await expect(piStream).toHaveAttribute("data-pi-agentic-engineering-status", "active");
        await expect(piStream).toHaveAttribute("data-pi-rpi-protocol", "research-plan-implement-context-gates");
        await expect(piStream).toHaveAttribute("data-pi-qrspi-protocol", "question-research-design-structure-plan-implement-verify-review");
        await expect(piStream).toHaveAttribute("data-pi-qrspi-phase-model", /question->research->design->structure->plan->worktree->implement->verify->review/);
        await expect(piStream).toHaveAttribute("data-pi-qrspi-question-policy", "question-first-neutral");
        await expect(piStream).toHaveAttribute("data-pi-qrspi-research-policy", "blind-factual-before-design");
        await expect(piStream).toHaveAttribute("data-pi-qrspi-instruction-budget", "instructions-not-only-tokens");
        await expect(piStream).toHaveAttribute("data-pi-qrspi-vertical-slice-policy", "thin-end-to-end-slice-before-horizontal-layers");
        await expect(piStream).toHaveAttribute("data-pi-qrspi-backtrack-policy", "phase-backtracking-enabled");
        await expect(piStream).toHaveAttribute("data-pi-qrspi-review-policy", "fresh-context-code-review-required");
        await expect(piStream).toHaveAttribute("data-pi-context-pack-schema", "ContextPackV2");
        await expect(piStream).toHaveAttribute("data-pi-context-mode-facade", "agenthub-governed-context-mode");
        await expect(piStream).toHaveAttribute("data-pi-context-window-policy", /context_pack=primary-work-unit/);
        await expect(piStream).toHaveAttribute("data-pi-subagent-work-model", "context-window-fork");
        await expect(piStream).toHaveAttribute("data-pi-backend-tool-count", /[1-9]\d*/);
        await expect(piStream).toHaveAttribute("data-pi-tool-registry", "agenthub-tool-runtime");
        await expect(piStream).toHaveAttribute("data-pi-stream-interface", "pi-web-ui-agent-interface");
        await expect(piStream).toHaveAttribute("data-design-intent", "native-pi-web-ui-session");

        await expect(runtime).toHaveAttribute("data-pi-runtime", "@mariozechner/pi-web-ui");
        await expect(runtime).toHaveAttribute("data-pi-agentic-engineering-extension", "@fabric-clawhub/pi-agentic-engineering");
        await expect(runtime).toHaveAttribute("data-pi-qrspi-protocol", "question-research-design-structure-plan-implement-verify-review");
        await expect(runtime).toHaveAttribute("data-pi-qrspi-instruction-budget", "instructions-not-only-tokens");
        await expect(runtime).toHaveAttribute("data-pi-qrspi-backtrack-policy", "phase-backtracking-enabled");
        await expect(runtime).toHaveAttribute("data-pi-context-pack-schema", "ContextPackV2");
        await expect(page.locator('pi-chat-panel[data-pi-web-component="pi-chat-panel"]')).toHaveCount(1);
        await expect(page.locator('agent-interface[data-pi-runtime="@mariozechner/pi-web-ui"]')).toHaveCount(1);
        await expect(editor).toBeVisible();
        await expect(editorInput).toBeVisible();

        await expect(page.getByRole("region", { name: "Mission execution" })).toHaveCount(0);
        await expect(page.getByLabel("Embedded mission terminal")).toHaveCount(0);
        await expect(page.getByLabel("Mission reply editor")).toHaveCount(0);
        await expect(page.getByText("Run intelligence")).toHaveCount(0);
        await expect(page.getByText("Steer mission")).toHaveCount(0);
        await expect(page.getByText("Latest update")).toHaveCount(0);
        await expect(page.locator(".mission-grid")).toHaveCount(0);
        await expect(page.locator(".right-rail")).toHaveCount(0);
        await expect(page.locator(".canvas-log-stream")).toHaveCount(0);
        await expect(page.locator(".mc3-transcript-row")).toHaveCount(0);

        await expect(page.locator("message-list")).toContainText("I inspected the workspace first");
        await expect(page.locator("message-list")).toContainText("fabric_get_item_definition");
        await expect(page.locator("message-list")).toContainText("fabric_update_item_definition");
        await expect(piStream).not.toContainText(/FABRIC_API_TOKEN|POWERBI_API_TOKEN|Bearer\s+|SECRET_INTERNAL_TRACE_DO_NOT_RENDER|schemaVersion/i);
        const piLiveLog = page.getByLabel("Mission activity log");
        await expect(piLiveLog).toHaveAttribute("data-pi-log-execution-streaming-ui", "agenthub-code-inspired");
        await expect(piLiveLog).toHaveAttribute("data-pi-log-visual-language", "agenthub-code-tree");
        await expect(piLiveLog.locator('[data-pi-log-compaction-chip="true"]')).toContainText("Auto-summary");
        await expect(piLiveLog.locator('[data-pi-log-compaction-policy="true"]')).toContainText("Newest rows stay open");
        await expect(piLiveLog.locator('[aria-label="Mission activity metrics"]')).toContainText(/current|summaries|hidden/);
        await expect(piLiveLog.locator('[data-pi-log-inline-tags="true"]').first()).toBeVisible();
        await expect.poll(async () => page.locator('[data-pi-live-log-rollup="true"]').count(), {
            timeout: 10_000,
            intervals: [500, 750, 1000],
            message: "Pi log compactor should collapse older live-log rows into rollups",
        }).toBeGreaterThan(0);
        await attachPiLiveLogScreenshot(page, testInfo, "pi-log-execution-stream-desktop");

        await editorInput.fill("Continue with verifier evidence after approval.");
        const sendButton = editor.locator("button:not(:disabled)").last();
        await expect(sendButton).toBeVisible({ timeout: 10_000 });
        await sendButton.click();
        await expect.poll(() => harness.sentMessages.length, { timeout: 10_000 }).toBe(1);
        await expect(page.locator("message-list")).toContainText("Queued in AgentHub", { timeout: 10_000 });
        expect(harness.sentMessages).toHaveLength(1);
        expect(harness.sentMessages[0]).toMatchObject({ mode: "queue", message: "Continue with verifier evidence after approval." });

        const box = await runtime.boundingBox();
        expect(box?.width).toBeGreaterThan(1000);
        expect(box?.height).toBeGreaterThan(760);

        const evidence = await harness.capture(testInfo, "pi-web-ui-full-page-desktop");
        expect(evidence.piRuntimePackage).toBe("@mariozechner/pi-web-ui");
        expect(evidence.piArchitectureLayers).toBe("application->core->foundation");
        expect(evidence.piCoreLayer).toBe("@mariozechner/pi-agent-core");
        expect(evidence.piFoundationLayer).toContain("@mariozechner/pi-ai");
        expect(evidence.piContextModeStatus).toBe("active");
        expect(evidence.piBabysitterStatus).toBe("active");
        expect(evidence.piLogCompactionStatus).toBe("active");
        expect(evidence.piAgenticEngineeringPackage).toBe("@fabric-clawhub/pi-agentic-engineering");
        expect(evidence.piAgenticEngineeringStatus).toBe("active");
        expect(evidence.piRpiProtocol).toBe("research-plan-implement-context-gates");
        expect(evidence.piQrspiProtocol).toBe("question-research-design-structure-plan-implement-verify-review");
        expect(evidence.piQrspiPhaseModel).toContain("question->research->design->structure->plan");
        expect(evidence.piQrspiQuestionPolicy).toBe("question-first-neutral");
        expect(evidence.piQrspiResearchPolicy).toBe("blind-factual-before-design");
        expect(evidence.piQrspiInstructionBudget).toBe("instructions-not-only-tokens");
        expect(evidence.piQrspiVerticalSlicePolicy).toBe("thin-end-to-end-slice-before-horizontal-layers");
        expect(evidence.piQrspiBacktrackPolicy).toBe("phase-backtracking-enabled");
        expect(evidence.piQrspiReviewPolicy).toBe("fresh-context-code-review-required");
        expect(evidence.piContextPackSchema).toBe("ContextPackV2");
        expect(evidence.piContextModeFacade).toBe("agenthub-governed-context-mode");
        expect(evidence.piContextWindowPolicy).toContain("context_pack=primary-work-unit");
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
        expect(Number(evidence.piBackendToolCount)).toBeGreaterThan(0);
        expect(evidence.piStreamInterface).toBe("pi-web-ui-agent-interface");
        expect(evidence.piExtensionPackages).toContain("npm:@mariozechner/pi-web-ui@0.71.1");
        expect(evidence.piExtensionPackages).toContain("npm:context-mode@1.0.103");
        expect(evidence.piExtensionPackages).toContain("npm:@a5c-ai/babysitter-pi@0.1.3");
        expect(evidence.piExtensionPackages).toContain(".pi/extensions/fabric-clawhub-log-compactor.ts");
        expect(evidence.piExtensionPackages).toContain(".pi/extensions/fabric-clawhub-agentic-engineering.ts");
        expect(evidence.legacyRowCount).toBe(0);
        expect(evidence.legacyLogStreamCount).toBe(0);

        await page.reload({ waitUntil: "domcontentloaded" });
        await expect(page.locator(".mc3-pi-web-ui-page")).toBeVisible({ timeout: 30_000 });
        await expect(page.locator("message-editor textarea")).toBeVisible({ timeout: 30_000 });

        await page.setViewportSize({ width: 390, height: 900 });
        await expect(page.locator(".mc3-pi-web-ui-page")).toBeVisible();
        await expect(page.locator("message-editor textarea")).toBeVisible();
        const overflow = await page.locator(".pi-mission-surface--web-ui").evaluate((root) => {
            const rootRect = (root as HTMLElement).getBoundingClientRect();
            return Array.from(root.querySelectorAll<HTMLElement>("pi-chat-panel, agent-interface, message-editor"))
                .map((node) => node.getBoundingClientRect())
                .filter((rect) => rect.width > 0 && (rect.left < rootRect.left - 1 || rect.right > rootRect.right + 1))
                .length;
        });
        expect(overflow).toBe(0);
        await expect(page.getByLabel("Mission activity log")).toHaveAttribute("data-pi-log-execution-streaming-ui", "agenthub-code-inspired");
        await expect(page.getByLabel("Mission activity log")).toHaveAttribute("data-pi-log-visual-language", "agenthub-code-tree");
        await attachPiLiveLogScreenshot(page, testInfo, "pi-log-execution-stream-mobile");
        await harness.capture(testInfo, "pi-web-ui-full-page-mobile");
    });
});
