import { chromium, type APIRequestContext, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export interface ResolvedGitHubToken {
    token: string;
    source: string;
}

interface CachedGitHubToken {
    token?: string;
    githubUser?: string;
    createdAt?: string;
    source?: string;
}

interface DeviceFlowResponse {
    user_code?: string;
    verification_uri?: string;
    verification_uri_complete?: string;
    device_code?: string;
    interval?: number;
    expires_in?: number;
    status?: string;
    access_token?: string;
    github_user?: string;
    error?: string;
}

type TokenValidationResult = { ok: true } | { ok: false; message: string };

const DEFAULT_CACHE_PATH = path.join(os.homedir(), ".config", "agenthub", "e2e-copilot-token.json");
const GITHUB_TOKEN_PATTERN = /(?:gh[opsu]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)/g;
const DEV_HUB_ORIGIN_PATTERN = /https?:\/\/(?:localhost|127\.0\.0\.1):60006/;

export async function resolveGitHubCopilotToken(
    request: APIRequestContext,
    backendUrl: string,
): Promise<ResolvedGitHubToken> {
    const normalizedBackendUrl = backendUrl.replace(/\/$/, "");
    const diagnostics: string[] = [];

    const envToken = process.env.AGENTHUB_GITHUB_TOKEN || process.env.GITHUB_TOKEN || "";
    if (envToken) {
        const validation = await validateToken(request, normalizedBackendUrl, envToken, "environment token");
        if (validation.ok) return { token: envToken, source: process.env.AGENTHUB_GITHUB_TOKEN ? "AGENTHUB_GITHUB_TOKEN" : "GITHUB_TOKEN" };
        if (validation.ok === false) diagnostics.push(validation.message);
    }

    const cached = readCachedToken();
    if (cached.token) {
        const validation = await validateToken(request, normalizedBackendUrl, cached.token, "cached Copilot app token");
        if (validation.ok) return { token: cached.token, source: tokenCachePath() };
        if (validation.ok === false) diagnostics.push(validation.message);
        clearCachedToken();
    }

    for (const browserToken of readDeveloperHubBrowserTokens()) {
        const validation = await validateToken(request, normalizedBackendUrl, browserToken.token, browserToken.source);
        if (validation.ok) {
            writeCachedToken({ token: browserToken.token, source: browserToken.source, createdAt: new Date().toISOString() });
            return { token: browserToken.token, source: browserToken.source };
        }
        if (validation.ok === false) diagnostics.push(validation.message);
    }

    const profileToken = await resolveViaPersistentProfileDeviceFlow(request, normalizedBackendUrl);
    if ("token" in profileToken) return profileToken;
    if (profileToken.source) diagnostics.push(profileToken.source);

    const ghToken = readGitHubCliToken();
    if (ghToken) {
        const validation = await validateToken(request, normalizedBackendUrl, ghToken, "GitHub CLI token");
        if (validation.ok) return { token: ghToken, source: "gh auth token" };
        if (validation.ok === false) diagnostics.push(validation.message);
    }

    throw new Error([
        "No GitHub token available that the Developer Hub backend can exchange for Copilot.",
        ...diagnostics,
        `Run \`npm run token:e2e\` once to store a Copilot GitHub App token at ${tokenCachePath()}, sign in through the Developer Hub app in a persistent Chromium profile, or set AGENTHUB_GITHUB_TOKEN to a backend-compatible token.`,
        "If you pasted a token into chat or logs, revoke it and generate a fresh one before caching it.",
    ].join("\n"));
}

async function resolveViaPersistentProfileDeviceFlow(
    request: APIRequestContext,
    backendUrl: string,
): Promise<ResolvedGitHubToken | { source: string }> {
    if (process.env.AGENTHUB_E2E_DISABLE_PROFILE_DEVICE_FLOW === "1") return { source: "persistent profile device flow disabled by AGENTHUB_E2E_DISABLE_PROFILE_DEVICE_FLOW" };

    const profilePath = persistentProfilePath();
    if (!fs.existsSync(profilePath)) return { source: `persistent Chromium profile not found: ${profilePath}` };
    if (fs.existsSync(path.join(profilePath, "SingletonLock"))) {
        return { source: `persistent Chromium profile is already in use: ${profilePath}` };
    }

    const deviceResponse = await request.post(`${backendUrl}/api/github/device-code`, {
        data: {},
        timeout: 30_000,
    });
    if (!deviceResponse.ok()) {
        return { source: `persistent profile device flow could not start (${deviceResponse.status()}): ${redact(await deviceResponse.text())}` };
    }

    const device = await deviceResponse.json() as DeviceFlowResponse;
    if (!device.device_code || !device.user_code || !device.verification_uri) {
        return { source: "persistent profile device flow returned an incomplete device-code response" };
    }

    const auth = await authorizeDeviceFlowWithPersistentProfile(device, profilePath);
    if (auth.ok === false) return { source: auth.message };

    const poll = await pollDeviceFlowToken(request, backendUrl, device);
    if ("message" in poll) return { source: poll.message };

    const validation = await validateToken(request, backendUrl, poll.token, "persistent profile Copilot app token");
    if (validation.ok === false) return { source: validation.message };

    const source = `GitHub device flow via persistent Chromium profile (${profilePath})`;
    writeCachedToken({ token: poll.token, githubUser: poll.githubUser, source, createdAt: new Date().toISOString() });
    return { token: poll.token, source };
}

async function authorizeDeviceFlowWithPersistentProfile(
    device: DeviceFlowResponse,
    profilePath: string,
): Promise<{ ok: true } | { ok: false; message: string }> {
    let context: Awaited<ReturnType<typeof chromium.launchPersistentContext>> | null = null;
    try {
        context = await chromium.launchPersistentContext(profilePath, {
            headless: process.env.PLAYWRIGHT_HEADFUL ? false : true,
            args: ["--no-sandbox"],
        });
        const page = await context.newPage();
        const userCode = String(device.user_code || "").toUpperCase();
        const verificationUri = String(device.verification_uri || "https://github.com/login/device");
        const verificationUrl = device.verification_uri_complete
            || `${verificationUri}${verificationUri.includes("?") ? "&" : "?"}user_code=${encodeURIComponent(userCode)}`;
        await page.goto(verificationUrl, { waitUntil: "domcontentloaded", timeout: 45_000 });

        for (let attempt = 0; attempt < 30; attempt += 1) {
            const currentUrl = page.url();
            if (/\/login\/device\/success/.test(currentUrl)) return { ok: true };
            if (/\/login\/device\/failure/.test(currentUrl)) {
                return { ok: false, message: `persistent profile device flow reached GitHub failure page: ${currentUrl}` };
            }

            if (/\/login\/device\/select_account/.test(currentUrl)) {
                const selected = await clickFirstEnabled(page, [/^continue$/i, /continue as/i, /use this account/i]);
                if (!selected.ok) await page.waitForTimeout(700);
                continue;
            }

            if (/\/login\/device\/confirmation/.test(currentUrl)) {
                const authorized = await clickFirstEnabled(page, [/^authorize$/i, /authorize github copilot|authorize/i, /allow|approve|confirm/i]);
                if (authorized.ok) return { ok: true };
                return { ok: false, message: `persistent profile device flow blocked on GitHub consent: ${await summarizeGitHubAuthPage(page)}` };
            }

            if (/\/login\/device\??/.test(currentUrl) && await enterDeviceCode(page, userCode)) {
                await clickFirstEnabled(page, [/^continue$/i, /continue|next|verify|submit/i]);
                await page.waitForTimeout(1000);
                continue;
            }

            await clickFirstEnabled(page, [/^continue$/i, /authorize|allow|approve|confirm/i]);
            await page.waitForTimeout(1000);
        }
        return { ok: false, message: `persistent profile device flow did not reach authorization: ${page.url()}` };
    } catch (error) {
        return { ok: false, message: `persistent profile device flow could not use Chromium profile: ${redact(error instanceof Error ? error.message : String(error))}` };
    } finally {
        await context?.close().catch(() => undefined);
    }
}

async function enterDeviceCode(page: Page, userCode: string): Promise<boolean> {
    const code = userCode.replace(/-/g, "");
    const inputs = await page.getByRole("textbox").all().catch(() => []);
    if (inputs.length >= 8) {
        for (let index = 0; index < 8; index += 1) {
            await inputs[index].fill(code[index] || "");
        }
        return true;
    }

    const singleInput = page.locator("input[name='user_code'], input#user-code, input[name='otp'], input[placeholder*='XXXX-XXXX' i]").first();
    if (await singleInput.isVisible({ timeout: 500 }).catch(() => false)) {
        await singleInput.fill(code);
        return true;
    }
    return false;
}

async function clickFirstEnabled(page: Page, patterns: RegExp[]): Promise<{ ok: true } | { ok: false; reason: string }> {
    for (const pattern of patterns) {
        const button = page.getByRole("button", { name: pattern }).first();
        if (await button.isVisible({ timeout: 700 }).catch(() => false)) {
            await button.scrollIntoViewIfNeeded().catch(() => undefined);
            if (!(await button.isEnabled({ timeout: 1_500 }).catch(() => false))) return { ok: false, reason: `disabled:${pattern}` };
            await button.click();
            return { ok: true };
        }

        const link = page.getByRole("link", { name: pattern }).first();
        if (await link.isVisible({ timeout: 500 }).catch(() => false)) {
            await link.click();
            return { ok: true };
        }
    }
    return { ok: false, reason: "not-found" };
}

async function summarizeGitHubAuthPage(page: Page): Promise<string> {
    const summary = await page.evaluate(() => ({
        url: location.href,
        title: document.title,
        alerts: Array.from(document.querySelectorAll('[role="alert"], .flash, .flash-error, .flash-warn, .error, .warning'))
            .map((element) => element.textContent?.replace(/\s+/g, " ").trim())
            .filter(Boolean)
            .slice(0, 5),
        buttons: Array.from(document.querySelectorAll("button"))
            .map((button) => ({ text: button.textContent?.replace(/\s+/g, " ").trim(), disabled: button.hasAttribute("disabled") }))
            .slice(0, 8),
    }));
    return redact(JSON.stringify(summary).slice(0, 1_200));
}

async function pollDeviceFlowToken(
    request: APIRequestContext,
    backendUrl: string,
    device: DeviceFlowResponse,
): Promise<{ token: string; githubUser?: string } | { message: string }> {
    const started = Date.now();
    const timeoutMs = Math.min(Math.max(Number(device.expires_in || 900) * 1000, 60_000), 75_000);
    const intervalMs = Math.max(Number(device.interval || 5) * 1000, 3_000);

    while (Date.now() - started < timeoutMs) {
        const response = await request.post(`${backendUrl}/api/github/poll-token`, {
            data: { device_code: device.device_code },
            timeout: 30_000,
        });
        if (!response.ok()) return { message: `persistent profile device flow poll failed (${response.status()}): ${redact(await response.text())}` };
        const body = await response.json() as DeviceFlowResponse;
        if (body.status === "pending") {
            await delay(intervalMs);
            continue;
        }
        if (body.status === "complete" && body.access_token) return { token: body.access_token, githubUser: body.github_user };
        return { message: `persistent profile device flow ended without token: ${redact(body.error || body.status || "unknown")}` };
    }

    return { message: "persistent profile device flow authorization timed out" };
}

async function validateToken(
    request: APIRequestContext,
    backendUrl: string,
    token: string,
    label: string,
): Promise<TokenValidationResult> {
    try {
        const response = await request.get(`${backendUrl}/api/github/models`, {
            headers: { Authorization: `Bearer ${token}` },
            timeout: 30_000,
        });
        if (response.ok()) return { ok: true };
        return { ok: false, message: `${label} rejected by backend (${response.status()}): ${redact(await response.text())}` };
    } catch (error) {
        return { ok: false, message: `${label} could not be validated: ${redact(error instanceof Error ? error.message : String(error))}` };
    }
}

function readCachedToken(): CachedGitHubToken {
    const filePath = tokenCachePath();
    try {
        const raw = fs.readFileSync(filePath, "utf8");
        const parsed = JSON.parse(raw) as CachedGitHubToken;
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
        return {};
    }
}

function clearCachedToken(): void {
    try {
        fs.rmSync(tokenCachePath(), { force: true });
    } catch {
        // Best effort only; validation failure is already reported to the test.
    }
}

function writeCachedToken(value: CachedGitHubToken): void {
    const filePath = tokenCachePath();
    fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
    fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    fs.chmodSync(filePath, 0o600);
}

function readDeveloperHubBrowserTokens(): ResolvedGitHubToken[] {
    if (process.env.AGENTHUB_E2E_DISABLE_BROWSER_TOKEN === "1") return [];
    const configHome = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
    const roots = uniqueStrings([
        process.env.PLAYWRIGHT_USER_DATA_DIR || "",
        path.join(configHome, "chromium-wsl"),
    ]);
    const tokens = new Map<string, string>();
    for (const root of roots) {
        for (const dir of localStorageDirs(root)) {
            for (const filePath of localStorageFiles(dir)) {
                collectDeveloperHubTokens(filePath, root, tokens);
            }
        }
    }
    return Array.from(tokens, ([token, source]) => ({ token, source }));
}

function localStorageDirs(root: string): string[] {
    if (!root) return [];
    const dirs = new Set<string>();
    const addIfExists = (dir: string) => {
        try {
            if (fs.statSync(dir).isDirectory()) dirs.add(dir);
        } catch {
            // Ignore missing or inaccessible browser profiles.
        }
    };
    addIfExists(path.join(root, "Local Storage", "leveldb"));
    addIfExists(path.join(root, "Default", "Local Storage", "leveldb"));
    try {
        for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
            if (entry.isDirectory()) addIfExists(path.join(root, entry.name, "Local Storage", "leveldb"));
        }
    } catch {
        // Ignore missing or inaccessible browser profiles.
    }
    return Array.from(dirs);
}

function localStorageFiles(dir: string): string[] {
    try {
        return fs.readdirSync(dir)
            .filter((name) => /\.(?:ldb|log)$/.test(name))
            .map((name) => path.join(dir, name));
    } catch {
        return [];
    }
}

function collectDeveloperHubTokens(filePath: string, root: string, tokens: Map<string, string>): void {
    let text = "";
    try {
        text = fs.readFileSync(filePath).toString("latin1");
    } catch {
        return;
    }
    if (!text.includes("github_token") && !DEV_HUB_ORIGIN_PATTERN.test(text)) return;

    for (const match of text.matchAll(GITHUB_TOKEN_PATTERN)) {
        const token = match[0];
        const index = match.index || 0;
        const nearby = text.slice(Math.max(0, index - 800), Math.min(text.length, index + token.length + 800));
        if (nearby.includes("github_token") || DEV_HUB_ORIGIN_PATTERN.test(nearby)) {
            tokens.set(token, `Developer Hub browser localStorage (${root})`);
        }
    }
}

function readGitHubCliToken(): string {
    if (process.env.AGENTHUB_E2E_DISABLE_GH_TOKEN === "1") return "";
    try {
        return execFileSync("gh", ["auth", "token"], {
            encoding: "utf8",
            stdio: ["ignore", "pipe", "ignore"],
            timeout: 10_000,
        }).trim();
    } catch {
        return "";
    }
}

function tokenCachePath(): string {
    return process.env.AGENTHUB_E2E_COPILOT_TOKEN_CACHE || DEFAULT_CACHE_PATH;
}

function persistentProfilePath(): string {
    const configHome = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
    return process.env.PLAYWRIGHT_USER_DATA_DIR || path.join(configHome, "chromium-wsl");
}

function uniqueStrings(values: string[]): string[] {
    return Array.from(new Set(values.filter(Boolean)));
}

function delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function redact(value: string): string {
    return value
        .replace(/Bearer\s+[A-Za-z0-9._\-]+/g, "Bearer [REDACTED]")
        .replace(/gh[opsu]_[A-Za-z0-9_]+/g, "[REDACTED_GITHUB_TOKEN]")
        .replace(/github_pat_[A-Za-z0-9_]+/g, "[REDACTED_GITHUB_TOKEN]");
}
