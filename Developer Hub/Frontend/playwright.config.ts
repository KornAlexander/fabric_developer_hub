import { defineConfig, devices } from "@playwright/test";

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
export default defineConfig({
    testDir: "./e2e",
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 1 : 0,
    workers: process.env.CI ? 1 : undefined,
    reporter: [["list"]],
    use: {
        baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:60006",
        trace: "retain-on-failure",
    },
    projects: [
        {
            name: "chromium",
            use: { ...devices["Desktop Chrome"] },
        },
    ],
});
