/**
 * Tiny in-memory cache for data that a page needs on mount.
 *
 * The sidebar-nav handler in `AgentHubLayout` starts fetching the target
 * page's data the moment the user clicks, stashes the result here, and
 * only triggers the route change once the fetch finishes (or 1 s elapses,
 * whichever comes first). The target page then reads from this cache on
 * mount and skips its own "loading" state — so fast loads feel instant
 * and flicker-free.
 *
 * On slow loads (>1 s) the route changes anyway; the page falls back to
 * its normal skeleton + "still loading" experience.
 */

export type PreloadKey = "sessions" | "agents";

interface Entry {
    value: unknown;
    t: number;
}

const DATA: Map<PreloadKey, Entry> = new Map();
const PENDING: Map<PreloadKey, Promise<unknown>> = new Map();

/** Default staleness window — anything older is treated as absent. */
const DEFAULT_MAX_AGE_MS = 30_000;

export function setPreloaded<T>(key: PreloadKey, value: T): void {
    DATA.set(key, { value, t: Date.now() });
}

/**
 * Peek at the cached value without removing it. Returns `undefined` if
 * the entry is missing or older than `maxAgeMs`.
 */
export function readPreloaded<T>(
    key: PreloadKey,
    maxAgeMs: number = DEFAULT_MAX_AGE_MS,
): T | undefined {
    const entry = DATA.get(key);
    if (!entry) return undefined;
    if (Date.now() - entry.t > maxAgeMs) {
        DATA.delete(key);
        return undefined;
    }
    return entry.value as T;
}

export function setPending<T>(key: PreloadKey, promise: Promise<T>): void {
    PENDING.set(key, promise);
    const clear = () => {
        if (PENDING.get(key) === (promise as unknown as Promise<unknown>)) {
            PENDING.delete(key);
        }
    };
    promise.then(clear, clear);
}

export function getPending<T>(key: PreloadKey): Promise<T> | undefined {
    return PENDING.get(key) as Promise<T> | undefined;
}

/** Clear everything — useful for sign-out. */
export function clearPreloadCache(): void {
    DATA.clear();
    PENDING.clear();
}
