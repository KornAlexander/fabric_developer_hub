import { test, expect } from "@playwright/test";

/**
 * Mission Control regression + smoke.
 *
 * These tests run against the webpack-dev-server (``npm start``) like
 * the plan suite. They do not require a live backend — the surface
 * renders its shell (header, status pill, Pause/Terminate buttons, Run
 * Overview rail, Artifacts rail) before the first SSE event arrives,
 * and ``useMissionStream`` gracefully handles the absence of a
 * backend by retrying with exponential backoff.
 *
 * Primary guarantees:
 *   1. Deep-link at ``/agent-hub/session/:id`` renders Mission Control
 *      (not the legacy SessionDetailPage, which has been retired).
 *   2. The Terminate button is visible and enabled before a run ends.
 *   3. The Pause button is disabled (no-op placeholder).
 *   4. No bundle regressions — ``reactflow`` stays out of the JS.
 *   5. No runtime console errors from the Mission Control shell
 *      itself (network errors from missing backend are ignored).
 */

test.describe("Mission Control", () => {
    test("deep-link renders the mission-control shell", async ({ page }) => {
        const consoleErrors: string[] = [];
        page.on("console", (msg) => {
            if (msg.type() !== "error") return;
            const text = msg.text();
            // Network/connection errors are expected without a backend —
            // the hook reconnects on its own and the reducer seeds
            // state from the (eventual) fallback fetch.
            if (/net::|Failed to fetch|ECONNREFUSED|404|500|EventSource/i.test(text)) return;
            consoleErrors.push(text);
        });

        await page.goto("/#/agent-hub/session/e2e-smoke-id");
        await page.waitForLoadState("networkidle");

        // The Terminate button is the canonical "I'm on the mission
        // control surface" selector — it's unique to that page and
        // always rendered once the shell mounts.
        const terminate = page.getByRole("button", { name: /terminate/i });
        await expect(terminate).toBeVisible({ timeout: 10_000 });
        await expect(terminate).toBeEnabled();

        // Pause is a disabled placeholder per spec §1.
        const pause = page.getByRole("button", { name: /^pause$/i });
        await expect(pause).toBeVisible();
        await expect(pause).toBeDisabled();

        expect(consoleErrors, "unexpected console errors on mission-control shell").toEqual([]);
    });

    test("mission-control bundle does not leak reactflow", async ({ page }) => {
        const scriptUrls: string[] = [];
        page.on("response", (r) => {
            const url = r.url();
            if (url.endsWith(".js") || url.includes("/static/")) scriptUrls.push(url);
        });

        await page.goto("/#/agent-hub/session/e2e-smoke-id");
        await page.waitForLoadState("networkidle");

        for (const url of scriptUrls) {
            const resp = await page.request.get(url).catch((): null => null);
            if (!resp) continue;
            const body = await resp.text().catch(() => "");
            expect(
                body.toLowerCase(),
                `reactflow string leaked into bundle at ${url}`,
            ).not.toContain("reactflow");
        }
    });
});
