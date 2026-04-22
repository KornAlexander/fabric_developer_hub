/**
 * Regression test: ``getWorkspaces`` dedupes concurrent callers.
 *
 * Motivation: opening multiple "New Session" editor tabs used to fire
 * one ``/api/workspaces`` request per tab, so later tabs showed
 * "Loading…" long after earlier ones had settled. The client now
 * shares a single in-flight promise and a short-lived result cache
 * across all callers. This test locks that behaviour in.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getWorkspaces, invalidateWorkspacesCache } from "../../src/controller/AgentHubApi";

const realFetch = globalThis.fetch;

describe("getWorkspaces dedupe", () => {
    beforeEach(() => {
        invalidateWorkspacesCache();
    });
    afterEach(() => {
        globalThis.fetch = realFetch;
        invalidateWorkspacesCache();
        vi.restoreAllMocks();
    });

    it("makes only one network call for concurrent callers", async () => {
        let hits = 0;
        globalThis.fetch = vi.fn(async () => {
            hits += 1;
            // Simulate latency so the second caller enters before the
            // first resolves — this is the exact editor-tab race.
            await new Promise((r) => setTimeout(r, 10));
            return new Response(
                JSON.stringify({ workspaces: [{ id: "a", name: "A" }], cached_at: null, source: "cache" }),
                { status: 200, headers: { "Content-Type": "application/json" } },
            );
        }) as typeof fetch;
        const [r1, r2, r3] = await Promise.all([
            getWorkspaces({}),
            getWorkspaces({}),
            getWorkspaces({}),
        ]);
        expect(hits).toBe(1);
        expect(r1.workspaces).toEqual(r2.workspaces);
        expect(r2.workspaces).toEqual(r3.workspaces);
    });

    it("serves a second call from the short-lived cache", async () => {
        let hits = 0;
        globalThis.fetch = vi.fn(async () => {
            hits += 1;
            return new Response(
                JSON.stringify({ workspaces: [{ id: "b", name: "B" }], cached_at: null, source: "cache" }),
                { status: 200, headers: { "Content-Type": "application/json" } },
            );
        }) as typeof fetch;
        await getWorkspaces({});
        await getWorkspaces({});
        expect(hits).toBe(1);
    });

    it("bypasses the cache when refresh=true", async () => {
        let hits = 0;
        globalThis.fetch = vi.fn(async () => {
            hits += 1;
            return new Response(
                JSON.stringify({ workspaces: [], cached_at: null, source: "refreshed" }),
                { status: 200, headers: { "Content-Type": "application/json" } },
            );
        }) as typeof fetch;
        await getWorkspaces({});
        await getWorkspaces({}, true);
        expect(hits).toBe(2);
    });

    it("invalidateWorkspacesCache forces the next call to refetch", async () => {
        let hits = 0;
        globalThis.fetch = vi.fn(async () => {
            hits += 1;
            return new Response(
                JSON.stringify({ workspaces: [], cached_at: null, source: "cache" }),
                { status: 200, headers: { "Content-Type": "application/json" } },
            );
        }) as typeof fetch;
        await getWorkspaces({});
        invalidateWorkspacesCache();
        await getWorkspaces({});
        expect(hits).toBe(2);
    });
});
