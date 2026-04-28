import { test, expect } from "@playwright/test";
import * as path from "node:path";
import * as fs from "node:fs";

/**
 * Mission Control visual-regression loop.
 *
 * This test bypasses the Fabric portal entirely. It POSTs to the dev
 * fixture endpoint to spin up an in-memory mission with a Generalist +
 * 3 specialists + structured changes, then loads the workload
 * standalone via http://localhost:60006/agent-hub/session/{id} and
 * screenshots the rendered page.
 *
 * The screenshot is the iteration target — diff it against
 * Design/agent-ux/prototypes/04-dynamic-mission-canvas/index.html
 * screens 2/3/4 (the mid-run dynamic-canvas state).
 *
 * Run with:
 *   npx playwright test e2e/mission-control-visual.spec.ts --project=chromium --reporter=list
 *
 * No Fabric login required — the fixture endpoint is gated on the
 * backend's debug mode and runs against ``_DEV_USER_KEY``.
 */

const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:60006";
const BACKEND_URL = process.env.WORKLOAD_BE_URL || "http://127.0.0.1:5000";

test.use({
    viewport: { width: 1600, height: 900 },
});

test("Mission Control renders dynamic-canvas fixture", async ({ page }) => {
    test.setTimeout(120_000);

    page.on("console", (m) => {
        if (m.type() === "error") {
            console.log(`[browser:error] ${m.text().slice(0, 300)}`);
        }
    });
    page.on("pageerror", (e) => {
        console.log(`[browser:pageerror] ${e.message.slice(0, 300)}`);
    });

    // Block external font hosts AND inject system-font fallback so
    // page.screenshot's implicit font-ready wait resolves immediately.
    await page.route("https://fonts.googleapis.com/**", (r) => r.abort());
    await page.route("https://fonts.gstatic.com/**", (r) => r.abort());
    await page.addInitScript(() => {
        const s = document.createElement("style");
        s.textContent = `@font-face{font-family:Inter;src:local('Arial');}@font-face{font-family:'Material Symbols Outlined';src:local('Arial');}`;
        document.documentElement.appendChild(s);
    });

    // 1. Spin up a fixture mission on the backend.
    const fixtureRes = await page.request.post(
        `${BACKEND_URL}/api/_test/make-mission-fixture`,
    );
    expect(fixtureRes.ok(), `fixture endpoint returned ${fixtureRes.status()}`).toBe(true);
    const { session_id: sessionId } = await fixtureRes.json() as { session_id: string };
    expect(sessionId, "fixture endpoint must return session_id").toBeTruthy();
    console.log(`[diag] fixture session_id=${sessionId}`);

    // Pre-seed a fake GitHub token in localStorage so the
    // useGitHubAuth gate (AgentHubLayout.tsx:414) lets us through
    // without running the GitHub device-flow sign-in.
    await page.addInitScript(() => {
        try {
            window.localStorage.setItem("github_token", "e2e-visual-fake-github-token");
            window.localStorage.setItem("github_user", "e2e-visual");
        } catch {}
    });

    // 2. Load the Mission Control page directly (no Fabric portal).
    //    `?agenthubE2E=1` is the existing standalone bootstrap that
    //    skips the Fabric workload-client iframe handshake and uses a
    //    mock client (see Frontend/src/index.ts:58).
    await page.goto(`${FRONTEND_URL}/agent-hub/session/${sessionId}?agenthubE2E=1`, {
        waitUntil: "domcontentloaded",
        timeout: 20_000,
    });

    // 3. Fixed dwell while the SPA bundle evaluates and the SSE stream
    //    catches up. We don't use waitForFunction because we want a
    //    screenshot of WHATEVER renders (even partial) for the visual
    //    diff loop.
    await page.waitForTimeout(8_000);

    // 5. Screenshot for visual diff. Use an absolute path so the file
    //    lands where we expect regardless of Playwright's cwd.
    const outDir = path.resolve(__dirname, "../test-results");
    fs.mkdirSync(outDir, { recursive: true });
    const shotPath = path.join(outDir, `mission-control-visual-${sessionId}.png`);
    // Use a CDP-based screenshot to bypass Playwright's implicit
    // font-ready wait that hangs when external fonts are blocked.
    const cdp = await page.context().newCDPSession(page);
    try {
        const result = await cdp.send("Page.captureScreenshot", {
            format: "png",
            captureBeyondViewport: false,
        });
        fs.writeFileSync(shotPath, Buffer.from(result.data, "base64"));
    } catch (err) {
        console.log(`[diag] CDP screenshot error: ${(err as Error).message}`);
    }
    await cdp.detach().catch(() => undefined);
    console.log(`[diag] screenshot saved to ${shotPath} (exists=${fs.existsSync(shotPath)})`);

    // 6. Diagnostics.
    const bodyText = await page.locator("body").innerText({ timeout: 5_000 }).catch(() => "");
    const rootHtml = await page.locator("#root").innerHTML({ timeout: 5_000 })
        .then((h) => h.slice(0, 1500))
        .catch(() => "(no #root)");
    console.log(`[diag] body text: ${JSON.stringify(bodyText.slice(0, 500))}`);
    console.log(`[diag] #root html: ${JSON.stringify(rootHtml)}`);
});
