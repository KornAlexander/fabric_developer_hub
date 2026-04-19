/**
 * Unit tests for `src/utils/correlation.ts`.
 *
 * Locks the guarantees the AgentHubApi header injection relies on:
 * - `mintRequestId()` returns distinct values.
 * - `currentRequestId()` is undefined outside any scope.
 * - `withRequestId` binds for the duration of the async fn, including
 *   across `await` hops, and restores the previous value on exit even
 *   when the fn throws.
 */
import { describe, expect, it } from "vitest";
import {
    currentRequestId,
    mintRequestId,
    withRequestId,
} from "../../src/utils/correlation";

describe("correlation", () => {
    it("mintRequestId yields distinct values", () => {
        const a = mintRequestId();
        const b = mintRequestId();
        expect(a).not.toEqual(b);
        expect(a.length).toBeGreaterThan(8);
    });

    it("currentRequestId is undefined outside any scope", () => {
        expect(currentRequestId()).toBeUndefined();
    });

    it("withRequestId binds and restores", async () => {
        expect(currentRequestId()).toBeUndefined();
        await withRequestId("req-1", async () => {
            expect(currentRequestId()).toBe("req-1");
        });
        expect(currentRequestId()).toBeUndefined();
    });

    it("withRequestId survives await hops inside fn", async () => {
        await withRequestId("req-await", async () => {
            await Promise.resolve();
            await new Promise((r) => setTimeout(r, 0));
            expect(currentRequestId()).toBe("req-await");
        });
    });

    it("withRequestId restores the previous binding even if fn throws", async () => {
        await withRequestId("outer", async () => {
            await expect(
                withRequestId("inner", async () => {
                    throw new Error("boom");
                }),
            ).rejects.toThrow("boom");
            // Inner scope unwound; outer binding restored.
            expect(currentRequestId()).toBe("outer");
        });
        expect(currentRequestId()).toBeUndefined();
    });

    it("nested withRequestId restores the outer id on exit", async () => {
        await withRequestId("outer", async () => {
            expect(currentRequestId()).toBe("outer");
            await withRequestId("inner", async () => {
                expect(currentRequestId()).toBe("inner");
            });
            expect(currentRequestId()).toBe("outer");
        });
    });
});
