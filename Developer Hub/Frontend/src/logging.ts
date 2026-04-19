/**
 * Console silencer for the Fabric workload bundle.
 *
 * Why this exists: Fabric loads our workload inside the shared Power BI
 * portal page, so every `console.log` / `console.info` / `console.debug`
 * we emit ends up in the *user's* devtools — mixed in with Fabric's own
 * telemetry. That includes, historically, our env banner, theme-change
 * chatter, item-CRUD payloads, and the attachment-download diagnostics.
 * None of that belongs in a production console, and some of it (payload
 * strings, item IDs) is arguably PII-adjacent.
 *
 * Policy:
 *   - `console.error` and `console.warn` always pass through. We want
 *     unexpected failures to be visible to anyone triaging an issue.
 *   - `console.log` / `console.info` / `console.debug` / `console.trace`
 *     are no-ops by default.
 *   - Developers can re-enable the full stream for a single session by
 *     either:
 *       1. Appending `?debug=1` to the URL, or
 *       2. Running `localStorage.setItem("clawhub.debug","1")` in the
 *          devtools console (and refreshing).
 *     Opting out again: `localStorage.removeItem("clawhub.debug")`.
 */

function isDebugEnabled(): boolean {
    try {
        if (typeof window === "undefined") return false;
        const qs = new URLSearchParams(window.location.search);
        if (qs.get("debug") === "1") return true;
        if (window.localStorage?.getItem("clawhub.debug") === "1") return true;
    } catch {
        // localStorage / URLSearchParams may throw in restricted contexts.
    }
    return false;
}

export function installConsoleSilencer(): void {
    if (isDebugEnabled()) return;
    const noop = () => {};
    // Keep references so anyone who really needs the original can reach
    // them via `(console as any).__raw`.
    (console as any).__raw = {
        log: console.log.bind(console),
        info: console.info.bind(console),
        debug: console.debug.bind(console),
        trace: console.trace.bind(console),
    };
    console.log = noop;
    console.info = noop;
    console.debug = noop;
    console.trace = noop;
}

// Install at module-load time. ES-module `import` statements run before
// any top-level code in the importing file, so we can't rely on our
// entry points calling `installConsoleSilencer()` from their body —
// that would run *after* peer imports like `./i18n` have already
// printed their banner. Putting the call here guarantees we're the
// very first module body to execute (provided consumers import
// `./logging` before anything chatty).
installConsoleSilencer();
