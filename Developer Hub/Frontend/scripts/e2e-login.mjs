#!/usr/bin/env node
/**
 * Open Chromium with the shared WSL profile so the developer can log
 * into sites once. Those cookies/localStorage are then reused by the
 * Playwright suite when PLAYWRIGHT_USER_DATA_DIR points at the same
 * directory.
 *
 * Usage:   npm run login:e2e
 *          (optionally) USER_DATA_DIR=/custom/path npm run login:e2e
 */
import { chromium } from "playwright";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";

const userDataDir =
    process.env.USER_DATA_DIR ||
    process.env.PLAYWRIGHT_USER_DATA_DIR ||
    path.join(os.homedir(), ".config", "chromium-wsl");

fs.mkdirSync(userDataDir, { recursive: true });

const lock = path.join(userDataDir, "SingletonLock");
if (fs.existsSync(lock)) {
    console.error(
        `\nProfile already in use: ${userDataDir}\n` +
            `Close the other Chromium window first (check with: pgrep -a -f chrome-linux/chrome).\n`,
    );
    process.exit(1);
}

console.log(`Opening Chromium with profile: ${userDataDir}`);
console.log("Log in to whatever you need, then simply close the window.\n");

const ctx = await chromium.launchPersistentContext(userDataDir, {
    headless: false,
    args: ["--no-sandbox"],
});

// Ensure at least one page is visible.
if (ctx.pages().length === 0) await ctx.newPage();

await new Promise((resolve) => ctx.on("close", () => resolve()));
console.log("Browser closed. Credentials are saved in the profile directory.");
