/**
 * navPreferences — user-configurable click behaviour for the side-nav.
 *
 * Why
 * ---
 * VS Code lets users tune how clicks on navigation surfaces (files,
 * views, tabs) interact with the editor area. We mirror that here so a
 * single click on "New Session", "Sessions", or "Agents and Skills"
 * can be routed to whichever editor surface the user prefers:
 *
 *   - ``smart`` (default): activate the existing tab if one matches;
 *     otherwise open a new tab. For "New Session" this always opens a
 *     fresh draft because drafts are marked *duplicable*.
 *   - ``new-tab``: always append a new tab to the active group (fresh
 *     draft id for duplicable kinds, reuse existing id otherwise but
 *     still move/activate).
 *   - ``replace``: replace the active tab's content in place. The URL
 *     updates, the tab slot stays.
 *   - ``new-group``: open a new editor group to the right and place
 *     the tab there. Keeps the previous tab intact.
 *
 * The preferences live in ``localStorage`` (per-browser, per-user)
 * under a single key so they survive page reloads and propagate
 * across tabs via the ``storage`` event.
 */

export type NavBehaviour = "smart" | "new-tab" | "replace" | "new-group";

/** Keys we track explicit per-item preferences for. Extra items fall
 *  back to ``defaults`` by kind. */
export type NavItemId =
    | "newsession"
    | "sessions"
    | "agents"
    | "pbifixer"
    | "settings";

export interface NavPreferences {
    /** Fallback used when an item has no explicit entry. */
    default: NavBehaviour;
    perItem: Partial<Record<NavItemId, NavBehaviour>>;
}

export const DEFAULT_NAV_PREFERENCES: NavPreferences = {
    default: "smart",
    perItem: {
        // Each New Session click should produce a fresh draft — matches
        // VS Code opening a new untitled file on "New File".
        newsession: "new-tab",
    },
};

const STORAGE_KEY = "agentHub.navPreferences.v1";

export function loadNavPreferences(): NavPreferences {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return DEFAULT_NAV_PREFERENCES;
        const parsed = JSON.parse(raw) as Partial<NavPreferences>;
        return {
            default: parsed.default ?? DEFAULT_NAV_PREFERENCES.default,
            perItem: { ...DEFAULT_NAV_PREFERENCES.perItem, ...(parsed.perItem ?? {}) },
        };
    } catch {
        return DEFAULT_NAV_PREFERENCES;
    }
}

export function saveNavPreferences(prefs: NavPreferences): void {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch { /* quota / disabled storage — silently ignore */ }
}

/** Resolve the behaviour for a specific item given current prefs. */
export function resolveBehaviour(prefs: NavPreferences, item: NavItemId): NavBehaviour {
    return prefs.perItem[item] ?? prefs.default;
}

/** Human label for use in dropdowns / context menus. */
export const BEHAVIOUR_LABEL: Record<NavBehaviour, string> = {
    "smart":      "Smart (activate existing, else new tab)",
    "new-tab":    "Open in new tab",
    "replace":    "Replace current tab",
    "new-group":  "Open in new editor group",
};

/** Short label used in context-menu entries ("open as" style). */
export const BEHAVIOUR_SHORT_LABEL: Record<NavBehaviour, string> = {
    "smart":      "Open",
    "new-tab":    "Open in new tab",
    "replace":    "Replace current tab",
    "new-group":  "Open in new group",
};

export const NAV_ITEM_LABEL: Record<NavItemId, string> = {
    newsession: "New Session",
    sessions:   "Sessions",
    agents:     "Agents and Skills",
    pbifixer:   "Power BI Fixer",
    settings:   "Settings",
};

/** React hook wrapper — subscribes to storage events so preferences
 *  stay in sync across multiple open tabs of the app. */
import { useEffect, useState, useCallback } from "react";
export function useNavPreferences() {
    const [prefs, setPrefs] = useState<NavPreferences>(() => loadNavPreferences());

    useEffect(() => {
        const onStorage = (e: StorageEvent) => {
            if (e.key === STORAGE_KEY) setPrefs(loadNavPreferences());
        };
        window.addEventListener("storage", onStorage);
        return () => window.removeEventListener("storage", onStorage);
    }, []);

    const update = useCallback((next: NavPreferences) => {
        setPrefs(next);
        saveNavPreferences(next);
    }, []);

    return { prefs, setPrefs: update } as const;
}
