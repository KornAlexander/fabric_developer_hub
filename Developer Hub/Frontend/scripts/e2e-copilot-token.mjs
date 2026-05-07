#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const backendUrl = (process.env.WORKLOAD_BE_URL || "http://127.0.0.1:5000").replace(/\/$/, "");
const cachePath = process.env.AGENTHUB_E2E_COPILOT_TOKEN_CACHE || path.join(os.homedir(), ".config", "agenthub", "e2e-copilot-token.json");
const githubTokenPattern = /(?:gh[opsu]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)/g;
const devHubOriginPattern = /https?:\/\/(?:localhost|127\.0\.0\.1):60006/;

if (process.argv.includes("--clear")) {
    fs.rmSync(cachePath, { force: true });
    console.log(`Cleared ${cachePath}`);
    process.exit(0);
}

const existing = readCache();
if (existing.token && await validate(existing.token)) {
    console.log(`Cached Copilot GitHub App token is valid: ${cachePath}`);
    process.exit(0);
}

const browserToken = await readValidDeveloperHubBrowserToken();
if (browserToken) {
    writeCache({ token: browserToken.token, source: browserToken.source, createdAt: new Date().toISOString() });
    console.log(`Cached existing Developer Hub browser token: ${cachePath}`);
    process.exit(0);
}

console.log(`Requesting a Copilot GitHub App token from ${backendUrl}`);
const device = await postJson(`${backendUrl}/api/github/device-code`, {});
const verificationUrl = device.verification_uri_complete || device.verification_uri;
console.log(`Open: ${verificationUrl}`);
console.log(`Code: ${device.user_code}`);
console.log("Waiting for GitHub authorization. The token will be stored locally with 0600 permissions and will not be printed.");

const started = Date.now();
const timeoutMs = Math.max(Number(device.expires_in || 900) * 1000, 60_000);
const intervalMs = Math.max(Number(device.interval || 5) * 1000, 5_000);

while (Date.now() - started < timeoutMs) {
    await delay(intervalMs);
    const poll = await postJson(`${backendUrl}/api/github/poll-token`, { device_code: device.device_code });
    if (poll.status === "pending") continue;
    if (poll.status !== "complete" || !poll.access_token) {
        throw new Error(`GitHub authorization failed: ${poll.error || poll.status || "unknown error"}`);
    }
    if (!await validate(poll.access_token)) {
        throw new Error("GitHub returned a token, but the backend could not exchange it for Copilot.");
    }
    writeCache({ token: poll.access_token, githubUser: poll.github_user || null, createdAt: new Date().toISOString() });
    console.log(`Cached Copilot GitHub App token for ${poll.github_user || "GitHub user"}: ${cachePath}`);
    process.exit(0);
}

throw new Error("Timed out waiting for GitHub device authorization.");

async function validate(token) {
    try {
        const response = await fetch(`${backendUrl}/api/github/models`, {
            headers: { Authorization: `Bearer ${token}` },
        });
        if (response.ok) return true;
        const body = await response.text();
        console.error(`Token validation failed (${response.status}): ${redact(body)}`);
        return false;
    } catch (error) {
        console.error(`Token validation failed: ${redact(error instanceof Error ? error.message : String(error))}`);
        return false;
    }
}

async function postJson(url, body) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!response.ok) {
        throw new Error(`${url} failed (${response.status}): ${redact(await response.text())}`);
    }
    return response.json();
}

function readCache() {
    try {
        return JSON.parse(fs.readFileSync(cachePath, "utf8"));
    } catch {
        return {};
    }
}

function writeCache(value) {
    fs.mkdirSync(path.dirname(cachePath), { recursive: true, mode: 0o700 });
    fs.writeFileSync(cachePath, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    fs.chmodSync(cachePath, 0o600);
}

async function readValidDeveloperHubBrowserToken() {
    if (process.env.AGENTHUB_E2E_DISABLE_BROWSER_TOKEN === "1") return null;
    for (const candidate of readDeveloperHubBrowserTokens()) {
        if (await validate(candidate.token)) return candidate;
    }
    return null;
}

function readDeveloperHubBrowserTokens() {
    const configHome = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
    const roots = uniqueStrings([
        process.env.PLAYWRIGHT_USER_DATA_DIR || "",
        path.join(configHome, "chromium-wsl"),
    ]);
    const tokens = new Map();
    for (const root of roots) {
        for (const dir of localStorageDirs(root)) {
            for (const filePath of localStorageFiles(dir)) collectDeveloperHubTokens(filePath, root, tokens);
        }
    }
    return Array.from(tokens, ([token, source]) => ({ token, source }));
}

function localStorageDirs(root) {
    if (!root) return [];
    const dirs = new Set();
    const addIfExists = (dir) => {
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

function localStorageFiles(dir) {
    try {
        return fs.readdirSync(dir)
            .filter((name) => /\.(?:ldb|log)$/.test(name))
            .map((name) => path.join(dir, name));
    } catch {
        return [];
    }
}

function collectDeveloperHubTokens(filePath, root, tokens) {
    let text = "";
    try {
        text = fs.readFileSync(filePath).toString("latin1");
    } catch {
        return;
    }
    if (!text.includes("github_token") && !devHubOriginPattern.test(text)) return;
    for (const match of text.matchAll(githubTokenPattern)) {
        const token = match[0];
        const index = match.index || 0;
        const nearby = text.slice(Math.max(0, index - 800), Math.min(text.length, index + token.length + 800));
        if (nearby.includes("github_token") || devHubOriginPattern.test(nearby)) tokens.set(token, `Developer Hub browser localStorage (${root})`);
    }
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function uniqueStrings(values) {
    return Array.from(new Set(values.filter(Boolean)));
}

function redact(value) {
    return value
        .replace(/Bearer\s+[A-Za-z0-9._\-]+/g, "Bearer [REDACTED]")
        .replace(/gh[opsu]_[A-Za-z0-9_]+/g, "[REDACTED_GITHUB_TOKEN]")
        .replace(/github_pat_[A-Za-z0-9_]+/g, "[REDACTED_GITHUB_TOKEN]");
}
