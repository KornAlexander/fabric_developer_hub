/**
 * Integration test for `AgentHubApi` header injection.
 *
 * Verifies that the module-level `headers()` helper stamps `X-Request-ID`
 * on every outbound call based on the `withRequestId(...)` scope. We test
 * through one exported API function rather than poking at the private
 * helper so a refactor that replaces the helper still exercises the
 * guarantee.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { listSessions } from "../../src/controller/AgentHubApi";
import { mintRequestId, withRequestId } from "../../src/utils/correlation";

const realFetch = globalThis.fetch;

function mockFetchCapture(): { calls: RequestInit[] } {
    const captured: RequestInit[] = [];
    globalThis.fetch = vi.fn(async (_url: unknown, init?: RequestInit) => {
        captured.push(init ?? {});
        return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    return { calls: captured };
}

describe("AgentHubApi X-Request-ID header", () => {
    afterEach(() => {
        globalThis.fetch = realFetch;
        vi.restoreAllMocks();
    });

    it("is set from the current withRequestId scope", async () => {
        const { calls } = mockFetchCapture();
        const id = mintRequestId();
        await withRequestId(id, async () => {
            await listSessions({});
        });
        const sent = calls[0].headers as Record<string, string>;
        expect(sent["X-Request-ID"]).toBe(id);
    });

    it("is absent when no scope is active", async () => {
        const { calls } = mockFetchCapture();
        await listSessions({});
        const sent = (calls[0].headers as Record<string, string>) ?? {};
        expect(sent["X-Request-ID"]).toBeUndefined();
    });

    it("explicit opts.requestId wins over the current scope", async () => {
        const { calls } = mockFetchCapture();
        await withRequestId("from-scope", async () => {
            await listSessions({ requestId: "explicit-override" });
        });
        const sent = calls[0].headers as Record<string, string>;
        expect(sent["X-Request-ID"]).toBe("explicit-override");
    });
});
