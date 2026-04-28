/**
 * Open an external URL in a new browser tab from inside the Fabric
 * workload iframe.
 *
 * The iframe is sandboxed without ``allow-popups``, so direct routes
 * (``window.open``, ``<a target="_blank">``, ``showSaveFilePicker``…)
 * are silently blocked by the browser. The only reliable path is the
 * workload SDK's ``navigation.openBrowserTab`` — it proxies the call
 * out to the Fabric host portal (a regular first-party page) which
 * performs the navigation on the iframe's behalf.
 *
 * See [docs/fabric-iframe-open-tab.md](../../../../docs/fabric-iframe-open-tab.md)
 * for the full rationale and gotchas.
 *
 * This helper:
 *   1. Calls ``workloadClient.navigation.openBrowserTab`` first.
 *   2. If the SDK throws OR returns ``{ success: false }`` (Fabric's
 *      URL allowlist, usually), strips the ``experience`` query param
 *      and retries — that's the single most common allowlist trigger.
 *   3. Falls back to a plain ``window.open`` for local-dev /
 *      Storybook / Playwright runs where ``workloadClient`` is stubbed.
 *   4. Invokes the optional ``onFallback`` callback so the caller can
 *      surface a "copy this URL" banner if every automatic path fails.
 *
 * IMPORTANT: call this from within the user-gesture handler (the
 * ``onClick``) — Chromium requires an active transient activation to
 * relay the request through the host portal. If you ``await``
 * arbitrary network work first you may lose the activation window.
 */
import type { WorkloadClientAPI } from "@ms-fabric/workload-client";

/** Result of ``openExternalTab`` — ``"sdk"`` / ``"window"`` indicate a
 *  successful open via that channel; ``"fallback"`` means every
 *  channel failed and ``onFallback`` was called (if provided). */
export type OpenTabOutcome = "sdk" | "window" | "fallback";

export interface OpenExternalTabOptions {
    /** Called when every automatic path failed. Typically shows a
     *  dialog/banner containing the URL so the user can paste it into
     *  a fresh tab manually. The URL has already been copied to the
     *  clipboard via ``navigator.clipboard`` when possible. */
    onFallback?: (url: string) => void;
    /** Skip the clipboard copy. Default ``false`` (we always try to
     *  copy so Ctrl-V in a new tab is one keystroke). */
    skipClipboard?: boolean;
}

async function tryOpenViaSdk(
    workloadClient: WorkloadClientAPI | undefined,
    url: string,
): Promise<boolean> {
    const sdk = workloadClient?.navigation?.openBrowserTab;
    if (typeof sdk !== "function") return false;
    try {
        const res = await sdk.call(workloadClient!.navigation, {
            url,
            // Older SDK builds throw when ``queryParams`` is missing.
            queryParams: {},
        });
        // SDK shapes: some builds return ``undefined`` on success and
        // throw on failure; newer ones return ``{ success: boolean }``.
        // Treat ``undefined`` as success so we don't over-reject.
        if (res === undefined) return true;
        return !!(res as { success?: boolean }).success;
    } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[openExternalTab] openBrowserTab threw:", err);
        return false;
    }
}

export async function openExternalTab(
    workloadClient: WorkloadClientAPI | undefined,
    rawUrl: string,
    opts: OpenExternalTabOptions = {},
): Promise<OpenTabOutcome> {
    if (!opts.skipClipboard) {
        // Best-effort — clipboard access may be blocked in the sandbox.
        try { await navigator.clipboard.writeText(rawUrl); } catch { /* ignore */ }
    }

    // Attempt 1: direct URL through the SDK.
    if (await tryOpenViaSdk(workloadClient, rawUrl)) return "sdk";

    // Attempt 2: drop the ``experience`` query param, which Fabric's
    // host allowlist frequently rejects.
    try {
        const u = new URL(rawUrl);
        if (u.searchParams.has("experience")) {
            u.searchParams.delete("experience");
            const stripped = u.toString();
            if (stripped !== rawUrl && await tryOpenViaSdk(workloadClient, stripped)) {
                return "sdk";
            }
        }
    } catch { /* relative URLs or unparseable — skip */ }

    // Attempt 3: ``window.open`` — usually blocked inside the sandbox
    // but works in local dev / Storybook / Playwright where the iframe
    // is absent or relaxed.
    try {
        const w = window.open(rawUrl, "_blank", "noopener,noreferrer");
        if (w) return "window";
    } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[openExternalTab] window.open threw:", err);
    }

    // Everything failed — hand off to the caller so they can render a
    // banner / dialog with the URL.
    opts.onFallback?.(rawUrl);
    return "fallback";
}

/** Click handler factory for ``<a target="_blank">`` links. Preserves
 *  Ctrl/Cmd/Shift/middle-click (the browser's native "open in new tab"
 *  bypass still works even in sandboxed iframes), and on plain left
 *  click routes through ``openExternalTab``.
 *
 * Usage:
 * ```tsx
 * <a href={url} target="_blank" rel="noopener noreferrer"
 *    onClick={externalLinkOnClick(workloadClient, url)}>
 *   Open
 * </a>
 * ```
 */
export function externalLinkOnClick(
    workloadClient: WorkloadClientAPI | undefined,
    url: string,
    opts: OpenExternalTabOptions = {},
) {
    return (e: React.MouseEvent<HTMLAnchorElement>) => {
        // Let the browser handle modified clicks (Ctrl / ⌘ / Shift /
        // middle-click) — those bypass the popup blocker and give
        // power users control over which window/tab the link opens in.
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        void openExternalTab(workloadClient, url, opts);
    };
}
