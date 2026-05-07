import { test } from "./fixtures";
import * as path from "node:path";
import * as fs from "node:fs";

/**
 * Fabric-portal screenshot loop for Mission Control redesign.
 *
 * MSFT Fabric portal is the source of truth (the workload renders inside
 * an iframe at app.powerbi.com). This spec opens the portal in the
 * already-authenticated chromium profile, navigates directly to a
 * recent completed session, waits for the workload iframe to mount,
 * and screenshots Mission Control as it appears INSIDE the portal.
 *
 * Run with:
 *   PLAYWRIGHT_USER_DATA_DIR=$HOME/.config/chromium-wsl \
 *     npx playwright test e2e/mission-control-portal-shot.spec.ts \
 *     --project=chromium --reporter=list
 *
 * Override the session id with FABRIC_SESSION_ID=<uuid> if needed.
 */

const WORKSPACE_ID = process.env.FABRIC_WORKSPACE_ID || "8bdca8af-1db1-4fd8-9564-0c98b4dbdffc";
const TENANT_ID = process.env.FABRIC_TENANT_ID || "bfccc183-b152-43b7-babd-7feaa07557d1";
const SESSION_ID = process.env.FABRIC_SESSION_ID || "1f4299ee-1588-4d4b-83ca-61c820cb5ae5";

test.use({
    viewport: { width: 1600, height: 900 },
});

test("Fabric portal Mission Control screenshot", async ({ page }) => {
    test.setTimeout(240_000);
    test.skip(
        !process.env.PLAYWRIGHT_USER_DATA_DIR,
        "Set PLAYWRIGHT_USER_DATA_DIR to a logged-in Chromium profile (npm run login:e2e).",
    );

    const portalUrl = `https://app.powerbi.com/workloads/Org.FabricClawHub/agent-hub/session/${SESSION_ID}`
        + `?ws=${WORKSPACE_ID}&ctid=${TENANT_ID}&experience=fabric-developer`;

    page.on("console", (m) => {
        if (m.type() === "error") {
            console.log(`[browser:error] ${m.text().slice(0, 240)}`);
        }
    });

    const outDir = path.resolve(__dirname, "../test-results");
    fs.mkdirSync(outDir, { recursive: true });

    async function shot(label: string) {
        const ts = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
        const shotPath = path.join(outDir, `mc-portal-${label}-${ts}.png`);
        const cdp = await page.context().newCDPSession(page);
        try {
            const result = await cdp.send("Page.captureScreenshot", { format: "png" });
            fs.writeFileSync(shotPath, Buffer.from(result.data, "base64"));
            console.log(`[diag] ${label} screenshot saved: ${shotPath} (${fs.statSync(shotPath).size} bytes)`);
        } catch (err) {
            console.log(`[diag] ${label} CDP screenshot error: ${(err as Error).message}`);
        } finally {
            await cdp.detach().catch(() => undefined);
        }
    }

    console.log(`[diag] navigating to ${portalUrl}`);
    await page.goto(portalUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });

    // Take a screenshot every 15s for 90s so we always get something
    // even if the iframe takes a while to render.
    for (let i = 0; i < 6; i++) {
        await page.waitForTimeout(15_000);
        await shot(`t${(i + 1) * 15}s`);
    }
});
