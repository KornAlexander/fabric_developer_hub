/**
 * Client-side request correlation IDs.
 *
 * Mints a short UUID per logical user action so the frontend can tag every
 * outbound `fetch()` with `X-Request-ID`. Combined with the backend's
 * `RequestIdLogFilter`, this lets an operator triaging a report from the
 * Fabric portal trace "user clicked Run in session xyz" to the exact
 * log lines produced by the backend controllers and orchestrator.
 *
 * Scoping model:
 *   - `mintRequestId()` — creates a fresh ID (use per user-initiated
 *     action: submit prompt, approve plan, refresh data…).
 *   - `withRequestId(id, fn)` — binds `id` for the duration of `fn`.
 *     Nested calls use the innermost binding.
 *   - `currentRequestId()` — returns the ID bound by the closest
 *     enclosing `withRequestId`, or `undefined` outside any scope.
 *
 * The scope is stored in a module-level mutable variable because the
 * browser's `fetch()` event model is single-threaded: every `await` in
 * a call chain resumes on the same microtask queue. There is no
 * AsyncLocalStorage in the browser, but there is also no true concurrency
 * between tasks — overlapping user actions each mint their own ID and
 * pass it explicitly via the `opts` bag, so the globals are only used
 * for transparent propagation within a single logical flow.
 */

let _currentId: string | undefined;

/** Generate a new request ID. Uses `crypto.randomUUID()` if available and
 *  falls back to a cheap Math.random-based id for older runtimes. */
export function mintRequestId(): string {
    try {
        if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
            return crypto.randomUUID();
        }
    } catch {
        /* fall through to Math.random fallback */
    }
    // Fallback: 16 random hex chars — enough for log correlation.
    let s = "";
    for (let i = 0; i < 16; i++) {
        s += Math.floor(Math.random() * 16).toString(16);
    }
    return `fe-${s}`;
}

export function currentRequestId(): string | undefined {
    return _currentId;
}

/** Run `fn` with `id` bound as the current request ID. Restores the
 *  previous binding on exit even if `fn` throws or the promise rejects. */
export async function withRequestId<T>(id: string, fn: () => Promise<T>): Promise<T> {
    const prev = _currentId;
    _currentId = id;
    try {
        return await fn();
    } finally {
        _currentId = prev;
    }
}
