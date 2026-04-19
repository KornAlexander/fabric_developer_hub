import { test, expect } from "@playwright/test";

/**
 * Regression + smoke tests for Plan.
 *
 * These tests run against the webpack-dev-server (``npm start``). They
 * do NOT require a live Fabric backend — the OrchestratorPage renders
 * the static composer / discovery shell without hitting any APIs until
 * the user clicks "Generate Plan".
 *
 * Primary guarantees:
 *   1. ``reactflow`` is no longer loaded in the JS bundle — guards
 *      against an accidental revert of the Plan refactor.
 *   2. The composer renders with zero console errors / failed network
 *      requests so we catch runtime regressions early.
 */

test.describe("Plan regression", () => {
    test("does not load reactflow anywhere in the bundle", async ({ page }) => {
        const scriptUrls: string[] = [];
        page.on("response", (r) => {
            const url = r.url();
            if (url.endsWith(".js") || url.includes("/static/")) scriptUrls.push(url);
        });

        const consoleErrors: string[] = [];
        page.on("console", (msg) => {
            if (msg.type() === "error") consoleErrors.push(msg.text());
        });

        await page.goto("/");
        await page.waitForLoadState("networkidle");

        // Pull every JS response text and ensure none contains reactflow.
        for (const url of scriptUrls) {
            const resp = await page.request.get(url).catch((): null => null);
            if (!resp) continue;
            const body = await resp.text().catch(() => "");
            expect(
                body.toLowerCase(),
                `reactflow string leaked into bundle at ${url}`,
            ).not.toContain("reactflow");
        }

        expect(consoleErrors, "unexpected console errors on load").toEqual([]);
    });

    test("composer loads with no failed network requests", async ({ page }) => {
        const failures: string[] = [];
        page.on("response", (r) => {
            if (r.status() >= 500) failures.push(`${r.status()} ${r.url()}`);
        });
        page.on("requestfailed", (r) => failures.push(`failed ${r.url()}`));

        await page.goto("/");
        await page.waitForLoadState("networkidle");

        expect(failures, "non-2xx/5xx requests detected").toEqual([]);
    });
});
