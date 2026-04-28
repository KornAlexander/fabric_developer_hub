import { test as base, expect, chromium, type BrowserContext } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * Shared Playwright fixtures.
 *
 * Set ``PLAYWRIGHT_USER_DATA_DIR`` to a Chromium user-data directory to
 * reuse a real browser profile (cookies, logged-in sessions, etc.) for
 * the test run. If unset, tests use Playwright's default ephemeral
 * context — existing behaviour.
 *
 * Workflow for authenticated tests:
 *   1.  ``npm run login:e2e``   # opens the shared profile; log in; close
 *   2.  ``PLAYWRIGHT_USER_DATA_DIR=$HOME/.config/chromium-wsl npm run test:e2e``
 *
 * IMPORTANT: only one Chromium process can use a user-data-dir at a
 * time. Close the login window before starting the test run.
 */

const userDataDir = process.env.PLAYWRIGHT_USER_DATA_DIR;

export const test = base.extend<{ context: BrowserContext }>({
    context: async ({ browser, browserName, headless }, use) => {
        if (!userDataDir || browserName !== "chromium") {
            // Default path: let Playwright build a fresh context per test.
            const ctx = await browser.newContext();
            await use(ctx);
            await ctx.close();
            return;
        }

        fs.mkdirSync(userDataDir, { recursive: true });
        const lock = path.join(userDataDir, "SingletonLock");
        if (fs.existsSync(lock)) {
            throw new Error(
                `Chromium profile ${userDataDir} is in use (SingletonLock present). ` +
                    `Close the browser window opened via "npm run login:e2e" before running tests.`,
            );
        }

        // Honor Playwright's resolved ``headless`` (driven by --headed CLI
        // flag, VS Code "Show browser" toggle, or ``use.headless`` in the
        // config). ``PLAYWRIGHT_HEADFUL=1`` is an additional escape hatch
        // for shells that can't pass ``--headed``.
        const runHeadless = process.env.PLAYWRIGHT_HEADFUL ? false : headless;
        const ctx = await chromium.launchPersistentContext(userDataDir, {
            headless: runHeadless,
            args: [
                "--no-sandbox",
                // Chromium's Private Network Access (PNA) blocks loopback
                // fetches from app.powerbi.com with "Permission was denied
                // for this request to access the `loopback` address space",
                // even when the loopback dev-server sends
                // Access-Control-Allow-Private-Network: true.  Disabling
                // just these features is targeted enough NOT to break
                // OAuth/CSRF protections that --disable-web-security would.
                // Also disable site isolation so Playwright can inspect the
                // cross-origin (app.powerbi.com → 127.0.0.1) workload
                // iframe — without this the orchestrator frame appears in
                // page.frames() but all getByRole queries time out.
                "--disable-features=BlockInsecurePrivateNetworkRequests,PrivateNetworkAccessRespectPreflightResults,PrivateNetworkAccessSendPreflights,LocalNetworkAccessChecks,PrivateNetworkAccessPermissionPrompt,IsolateOrigins,site-per-process,SitePerProcess,ProcessPerSiteUpToMainFrameThreshold",
                "--disable-site-isolation-trials",
            ],
        });
        await use(ctx);
        await ctx.close();
    },
});

export { expect };
