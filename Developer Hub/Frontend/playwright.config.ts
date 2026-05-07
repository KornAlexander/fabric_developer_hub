import { defineConfig, devices } from "@playwright/test";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";

/**
 * Playwright smoke + regression suite for Plan.
 *
 * The OrchestratorPage is hosted inside a Fabric workload iframe in
 * production. For the smoke test we serve a stand-alone harness via the
 * existing webpack-dev-server (``npm start``) and drive it directly — the
 * point of this suite is to guard against bundle regressions (e.g.
 * accidentally re-introducing reactflow), not to test the full Fabric
 * embedding which would need a live Fabric portal.
 *
 * Playwright browsers themselves are not installed here (that step
 * requires internet + ``npx playwright install`` and several hundred MB
 * of downloads). CI will install them before running the suite.
 */

// Auto-discover the shared Chromium profile if the developer has run
// ``npm run login:e2e`` previously. This lets the VS Code Playwright
// sidebar pick up credentials without requiring the user to export
// PLAYWRIGHT_USER_DATA_DIR in every shell. CLI usage is unchanged —
// explicit env vars still win.
const DEFAULT_PROFILE = path.join(os.homedir(), ".config", "chromium-wsl");
if (!process.env.PLAYWRIGHT_USER_DATA_DIR && fs.existsSync(DEFAULT_PROFILE)) {
    process.env.PLAYWRIGHT_USER_DATA_DIR = DEFAULT_PROFILE;
}

export default defineConfig({
    testDir: "./e2e",
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 1 : 0,
    workers: process.env.CI || process.env.PLAYWRIGHT_USER_DATA_DIR ? 1 : undefined,
    reporter: [["list"]],
    use: {
        baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:60006",
        trace: "retain-on-failure",
        // WSL2 has no user namespaces; Chromium needs --no-sandbox there.
        launchOptions: { args: ["--no-sandbox"] },
    },
    projects: [
        {
            name: "chromium",
            use: { ...devices["Desktop Chrome"] },
        },
    ],
});
