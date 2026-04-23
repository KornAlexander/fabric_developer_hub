/**
 * EditorTabs — VS Code-style persistent tab strip for the Agent Hub
 * main content area.
 *
 * Why
 * ---
 * The original layout navigated away from a session the moment the user
 * clicked another one, losing scroll state, open panels, and the mental
 * thread of multi-tasking ("I was comparing run A with run B"). This
 * context introduces an editor-group model inspired by VS Code:
 *
 *   - A **group** contains an ordered list of **tabs** and an active
 *     tab index.
 *   - A **tab** is a lightweight handle — ``{id, kind, path, title}`` —
 *     not the content itself. The router still renders the content for
 *     whichever tab is currently active, so state is preserved via
 *     React Router's normal lifecycle (plus ``Suspense`` caching).
 *   - The URL is the source of truth for which tab is active. When the
 *     URL changes to a known "tabbable" path we auto-ensure a tab for
 *     it; when the user clicks a tab we push its path to the URL.
 *
 * This deliberately starts with a single group. The shape is carried
 * forward as ``groups: Group[]`` so drag-to-split can be bolted on
 * later without changing the public API.
 *
 * State is persisted to sessionStorage so the tab set survives full
 * reloads within a browser session but doesn't leak across devices.
 */

import React, {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useReducer,
    useRef,
} from "react";
import { useHistory, useLocation } from "react-router-dom";

export type TabKind = "session" | "new" | "home" | "agents" | "pbifixer" | "settings";

export interface TabDescriptor {
    /** Stable id derived from ``kind`` + optional path param. Reused so
     *  re-opening the same session activates its existing tab.
     *  Duplicable tabs (see below) carry a nonce so every request
     *  produces a fresh id. */
    id: string;
    kind: TabKind;
    /** Route to navigate to when this tab is activated. */
    path: string;
    /** Human-readable label. For sessions this starts as the id and is
     *  replaced with the task description once loaded. */
    title: string;
    /** Secondary descriptor shown on hover, e.g. the session id. */
    subtitle?: string;
    /** When true, opening this descriptor bypasses the global id-dedup.
     *  Used for "New Session" drafts where every click is a fresh
     *  untitled document (VS Code's "New File" behaviour). */
    duplicable?: boolean;
}

interface Group {
    id: string;
    tabs: TabDescriptor[];
    activeTabId: string | null;
    /** Vertical flex-weight within this group's column. Groups stacked
     *  in the same column share its height proportionally to their
     *  ``size`` values. */
    size: number;
    /** The column this group belongs to. Groups sharing a column id are
     *  stacked vertically (top-to-bottom in the order they appear in
     *  ``state.groups``). Different column ids render side-by-side in
     *  the order defined by ``state.columnOrder``. */
    column: string;
}

interface TabsState {
    groups: Group[];
    activeGroupId: string;
    /** Left→right ordering of column ids. Exactly one entry per
     *  distinct column referenced by ``groups[*].column``. */
    columnOrder: string[];
    /** Horizontal flex-weight per column id. Missing entries default
     *  to 1. */
    columnSizes: Record<string, number>;
}

type Action =
    | { type: "open"; tab: TabDescriptor; groupId?: string; activate?: boolean }
    | { type: "close"; tabId: string; groupId?: string }
    | { type: "activate"; tabId: string; groupId?: string }
    | { type: "move"; tabId: string; fromGroupId: string; toGroupId: string; toIndex: number }
    | { type: "split"; tabId: string; fromGroupId: string; side: "left" | "right" | "top" | "bottom" }
    | { type: "open-in-new-group"; tab: TabDescriptor; side: "left" | "right" | "top" | "bottom"; fromGroupId?: string }
    | { type: "replace-active"; tab: TabDescriptor; groupId?: string }
    | { type: "close-group"; groupId: string }
    | { type: "focus-group"; groupId: string }
    | { type: "resize"; groupId: string; size: number }
    | { type: "resize-column"; columnId: string; size: number }
    | { type: "update-title"; tabId: string; title: string; subtitle?: string }
    | { type: "restore"; state: TabsState };

const INITIAL_GROUP_ID = "g0";
const INITIAL_COLUMN_ID = "c0";
const INITIAL_STATE: TabsState = {
    groups: [{ id: INITIAL_GROUP_ID, tabs: [], activeTabId: null, size: 1, column: INITIAL_COLUMN_ID }],
    activeGroupId: INITIAL_GROUP_ID,
    columnOrder: [INITIAL_COLUMN_ID],
    columnSizes: { [INITIAL_COLUMN_ID]: 1 },
};

let nextGroupCounter = 1;
function newGroupId(): string {
    return `g${Date.now().toString(36)}${nextGroupCounter++}`;
}
let nextColumnCounter = 1;
function newColumnId(): string {
    return `c${Date.now().toString(36)}${nextColumnCounter++}`;
}

/** Drop any column ids that no longer have groups pointing at them. */
function pruneColumns(state: TabsState): TabsState {
    const used = new Set(state.groups.map((g) => g.column));
    const order = state.columnOrder.filter((c) => used.has(c));
    if (order.length === state.columnOrder.length) return state;
    const sizes: Record<string, number> = {};
    for (const c of order) if (state.columnSizes[c] != null) sizes[c] = state.columnSizes[c];
    return { ...state, columnOrder: order, columnSizes: sizes };
}

function reducer(state: TabsState, action: Action): TabsState {
    switch (action.type) {
        case "open": {
            // Duplicable tabs (``New Session`` drafts) skip the dedup
            // check — each click is a fresh untitled document.
            if (!action.tab.duplicable) {
                // If the tab id already exists in any group, focus that
                // group + activate it rather than opening a duplicate.
                for (const g of state.groups) {
                    const existingIdx = g.tabs.findIndex((t) => t.id === action.tab.id);
                    if (existingIdx >= 0) {
                        return {
                            ...state,
                            activeGroupId: action.activate === false ? state.activeGroupId : g.id,
                            groups: state.groups.map((grp) => {
                                if (grp.id !== g.id) return grp;
                                const tabs = [...grp.tabs];
                                tabs[existingIdx] = { ...tabs[existingIdx], ...action.tab };
                                return {
                                    ...grp,
                                    tabs,
                                    activeTabId: action.activate === false ? grp.activeTabId : action.tab.id,
                                };
                            }),
                        };
                    }
                }
            }
            const targetGroupId = action.groupId ?? state.activeGroupId;
            return {
                ...state,
                activeGroupId: targetGroupId,
                groups: state.groups.map((g) => {
                    if (g.id !== targetGroupId) return g;
                    return {
                        ...g,
                        tabs: [...g.tabs, action.tab],
                        activeTabId: action.activate === false ? g.activeTabId : action.tab.id,
                    };
                }),
            };
        }
        case "close": {
            const gid = action.groupId ?? state.activeGroupId;
            return {
                ...state,
                groups: state.groups.map((g) => {
                    if (g.id !== gid) return g;
                    const idx = g.tabs.findIndex((t) => t.id === action.tabId);
                    if (idx < 0) return g;
                    const tabs = g.tabs.filter((t) => t.id !== action.tabId);
                    let activeTabId = g.activeTabId;
                    if (activeTabId === action.tabId) {
                        // Activate the neighbour to the right, or left.
                        activeTabId = tabs[idx]?.id ?? tabs[idx - 1]?.id ?? null;
                    }
                    return { ...g, tabs, activeTabId };
                }),
            };
        }
        case "activate": {
            const gid = action.groupId ?? state.activeGroupId;
            return {
                ...state,
                activeGroupId: gid,
                groups: state.groups.map((g) =>
                    g.id === gid ? { ...g, activeTabId: action.tabId } : g,
                ),
            };
        }
        case "move": {
            const from = state.groups.find((g) => g.id === action.fromGroupId);
            const tab = from?.tabs.find((t) => t.id === action.tabId);
            if (!from || !tab) return state;
            const groupsA = state.groups.map((g) =>
                g.id === action.fromGroupId
                    ? {
                        ...g,
                        tabs: g.tabs.filter((t) => t.id !== action.tabId),
                        activeTabId: g.activeTabId === action.tabId
                            ? (g.tabs.find((t) => t.id !== action.tabId)?.id ?? null)
                            : g.activeTabId,
                    }
                    : g,
            );
            return {
                ...state,
                groups: groupsA.map((g) => {
                    if (g.id !== action.toGroupId) return g;
                    const tabs = [...g.tabs];
                    const clamped = Math.max(0, Math.min(action.toIndex, tabs.length));
                    tabs.splice(clamped, 0, tab);
                    return { ...g, tabs, activeTabId: tab.id };
                }),
                activeGroupId: action.toGroupId,
            };
        }
        case "split": {
            const from = state.groups.find((g) => g.id === action.fromGroupId);
            const tab = from?.tabs.find((t) => t.id === action.tabId);
            if (!from || !tab) return state;
            const vertical = action.side === "top" || action.side === "bottom";

            // If the source only has this one tab, splitting by *moving*
            // it would leave the source group empty — the cleanup pass
            // would then drop the source group and ``pruneColumns``
            // would delete its column, collapsing the result back to a
            // single group. Users perceive this as "the split doesn't
            // work". VS Code sidesteps it by duplicating the document
            // in the new group so both halves stay populated; we do
            // the same, generating a fresh id so the duplicate has its
            // own tab identity.
            const duplicateInsteadOfMove = from.tabs.length === 1;

            const srcArrIdx = state.groups.findIndex((g) => g.id === action.fromGroupId);
            const newGid = newGroupId();
            const newColId = vertical ? from.column : newColumnId();
            const newTab = duplicateInsteadOfMove
                ? { ...tab, id: `${tab.id}-split-${Date.now().toString(36)}` }
                : tab;
            const newGroup: Group = {
                id: newGid,
                tabs: [newTab],
                activeTabId: newTab.id,
                size: 1,
                column: newColId,
            };
            const groupsA = duplicateInsteadOfMove
                ? state.groups                                   // source keeps the tab
                : state.groups.map((g) =>
                    g.id === action.fromGroupId
                        ? {
                            ...g,
                            tabs: g.tabs.filter((t) => t.id !== action.tabId),
                            activeTabId: g.activeTabId === action.tabId
                                ? (g.tabs.find((t) => t.id !== action.tabId)?.id ?? null)
                                : g.activeTabId,
                        }
                        : g,
                );
            // For vertical splits we want the new group inserted
            // adjacent to the source within the same column — the
            // array position is what determines top/bottom ordering.
            // For horizontal splits the column order drives layout,
            // so we just append to the array (column placement comes
            // from ``columnOrder`` below).
            const insertAt = vertical
                ? (action.side === "top" ? srcArrIdx : srcArrIdx + 1)
                : groupsA.length;
            const groupsB = [...groupsA];
            groupsB.splice(insertAt, 0, newGroup);

            // Column-order update (only for horizontal splits).
            let columnOrder = state.columnOrder;
            const columnSizes = { ...state.columnSizes };
            if (!vertical) {
                const srcColIdx = columnOrder.indexOf(from.column);
                const colInsertAt = action.side === "left" ? srcColIdx : srcColIdx + 1;
                columnOrder = [...columnOrder];
                columnOrder.splice(colInsertAt, 0, newColId);
                columnSizes[newColId] = 1;
            }

            // Drop empty groups EXCEPT keep at least one overall.
            const cleaned = groupsB.filter((g, _, arr) => g.tabs.length > 0 || arr.length === 1);
            const nextState: TabsState = {
                ...state,
                groups: cleaned.length ? cleaned : [newGroup],
                activeGroupId: newGid,
                columnOrder,
                columnSizes,
            };
            return pruneColumns(nextState);
        }
        case "open-in-new-group": {
            // Open a brand-new tab into a freshly created group. For
            // horizontal sides we create a new column; for vertical
            // sides we stack the new group inside the anchor's column.
            const anchorId = action.fromGroupId ?? state.activeGroupId;
            const anchor = state.groups.find((g) => g.id === anchorId) ?? state.groups[0];
            if (!anchor) return state;
            const vertical = action.side === "top" || action.side === "bottom";
            const anchorArrIdx = state.groups.findIndex((g) => g.id === anchor.id);
            const newGid = newGroupId();
            const newColId = vertical ? anchor.column : newColumnId();
            const newGroup: Group = {
                id: newGid,
                tabs: [action.tab],
                activeTabId: action.tab.id,
                size: 1,
                column: newColId,
            };
            const insertAt = vertical
                ? (action.side === "top" ? anchorArrIdx : anchorArrIdx + 1)
                : state.groups.length;
            const groups = [...state.groups];
            groups.splice(insertAt, 0, newGroup);
            let columnOrder = state.columnOrder;
            const columnSizes = { ...state.columnSizes };
            if (!vertical) {
                const anchorColIdx = columnOrder.indexOf(anchor.column);
                const colInsertAt = action.side === "left" ? anchorColIdx : anchorColIdx + 1;
                columnOrder = [...columnOrder];
                columnOrder.splice(colInsertAt, 0, newColId);
                columnSizes[newColId] = 1;
            }
            return { ...state, groups, activeGroupId: newGid, columnOrder, columnSizes };
        }
        case "replace-active": {
            const gid = action.groupId ?? state.activeGroupId;
            return {
                ...state,
                activeGroupId: gid,
                groups: state.groups.map((g) => {
                    if (g.id !== gid) return g;
                    if (g.tabs.length === 0) {
                        return { ...g, tabs: [action.tab], activeTabId: action.tab.id };
                    }
                    const idx = Math.max(0, g.tabs.findIndex((t) => t.id === g.activeTabId));
                    const tabs = [...g.tabs];
                    tabs[idx] = action.tab;
                    return { ...g, tabs, activeTabId: action.tab.id };
                }),
            };
        }
        case "close-group": {
            if (state.groups.length <= 1) return state;
            const groups = state.groups.filter((g) => g.id !== action.groupId);
            const activeGroupId = state.activeGroupId === action.groupId
                ? groups[0].id
                : state.activeGroupId;
            return pruneColumns({ ...state, groups, activeGroupId });
        }
        case "focus-group":
            return { ...state, activeGroupId: action.groupId };
        case "resize":
            return {
                ...state,
                groups: state.groups.map((g) =>
                    g.id === action.groupId ? { ...g, size: Math.max(0.1, action.size) } : g,
                ),
            };
        case "resize-column":
            return {
                ...state,
                columnSizes: {
                    ...state.columnSizes,
                    [action.columnId]: Math.max(0.1, action.size),
                },
            };
        case "update-title": {
            return {
                ...state,
                groups: state.groups.map((g) => ({
                    ...g,
                    tabs: g.tabs.map((t) =>
                        t.id === action.tabId
                            ? { ...t, title: action.title, subtitle: action.subtitle ?? t.subtitle }
                            : t,
                    ),
                })),
            };
        }
        case "restore":
            return action.state;
    }
}

export interface EditorTabsApi {
    state: TabsState;
    /** Open a tab (or activate it if the id already exists). Navigates to its path. */
    openTab: (tab: TabDescriptor) => void;
    /** Open a tab in a brand-new group adjacent to the active group. */
    openTabInNewGroup: (tab: TabDescriptor, side?: "left" | "right" | "top" | "bottom") => void;
    /** Replace the active tab of the active (or given) group with a new
     *  descriptor. Content swaps in place; no new tab is created. */
    replaceActiveTab: (tab: TabDescriptor, groupId?: string) => void;
    closeTab: (tabId: string, groupId?: string) => void;
    activateTab: (tabId: string, groupId?: string) => void;
    moveTab: (tabId: string, fromGroupId: string, toGroupId: string, toIndex: number) => void;
    splitGroup: (tabId: string, fromGroupId: string, side: "left" | "right" | "top" | "bottom") => void;
    closeGroup: (groupId: string) => void;
    focusGroup: (groupId: string) => void;
    resizeGroup: (groupId: string, size: number) => void;
    resizeColumn: (columnId: string, size: number) => void;
    updateTitle: (tabId: string, title: string, subtitle?: string) => void;
}

const Ctx = createContext<EditorTabsApi | null>(null);
const STORAGE_KEY = "agentHub.editorTabs.v2";

/**
 * Whether the current document was loaded via a reload (F5 or
 * Ctrl+Shift+R). `sessionStorage` survives reloads by design, so
 * without this signal the previous tab bar would be restored and the
 * user would never actually land on the default view after refreshing.
 *
 * We deliberately treat soft-reload and hard-reload the same: browsers
 * do not expose a reliable way to distinguish them in JS, and in
 * practice "pressing F5" and "pressing Ctrl+Shift+R" have the same
 * mental model here — "give me a clean slate".
 */
export function isReloadNavigation(): boolean {
    if (typeof performance === "undefined") return false;
    try {
        const entries = performance.getEntriesByType("navigation") as PerformanceNavigationTiming[];
        if (entries && entries[0]) return entries[0].type === "reload";
    } catch { /* ignore — some browsers throw if navigation timing is blocked */ }
    // Fallback for older engines (IE/legacy) — harmless otherwise.
    const legacy = (performance as unknown as { navigation?: { type?: number } }).navigation;
    return legacy?.type === 1;
}

/**
 * Module-level side-effect: on any reload, forget the persisted tab
 * state *before* the reducer runs its lazy initializer. Kept here (and
 * not inside the component) so it fires exactly once per page load, no
 * matter how many `EditorTabsProvider` instances mount during the
 * lifetime of the app (e.g. React strict mode, route remounts).
 */
if (typeof sessionStorage !== "undefined" && isReloadNavigation()) {
    try {
        sessionStorage.removeItem(STORAGE_KEY);
    } catch { /* quota / disabled — nothing to do */ }
}

/** Derive a tab descriptor from a URL path (+ optional search), or
 *  null if it isn't a "tabbable" surface (sign-in pages, deep-link
 *  redirects, etc). */
export function descriptorFromPath(path: string, search?: string): TabDescriptor | null {
    // Strip any base prefix (e.g. /agent-hub) — we store the path as
    // given and navigate to it verbatim.
    const clean = path.replace(/\/+$/, "");
    const draftId = (() => {
        if (!search) return null;
        const sp = new URLSearchParams(search);
        return sp.get("draft");
    })();
    const sessionMatch = clean.match(/\/session\/([^/?#]+)/);
    if (sessionMatch) {
        const id = sessionMatch[1];
        return {
            id: `session:${id}`,
            kind: "session",
            path: path + (search ?? ""),
            title: `Session ${id.slice(0, 8)}`,
            subtitle: id,
        };
    }
    if (/\/orchestrator(?:\b|$)/.test(clean)) {
        // Drafts carry a nonce in the URL — one per tab. Landing on the
        // raw ``/orchestrator`` URL (e.g. via the sidebar default
        // redirect) collapses to a single "New Session" tab.
        if (draftId) {
            return {
                id: `new:${draftId}`,
                kind: "new",
                path: path + (search ?? ""),
                title: "New Session",
                duplicable: false,   // the draft id is already unique per tab
            };
        }
        return { id: "new", kind: "new", path, title: "New Session" };
    }
    if (/\/home(?:\b|$)/.test(clean)) {
        return { id: "home", kind: "home", path, title: "Sessions" };
    }
    if (/\/agents(?:\b|$)/.test(clean)) {
        return { id: "agents", kind: "agents", path, title: "Agents" };
    }
    if (/\/pbifixer(?:\b|$)/.test(clean)) {
        // Each PBI Fixer sub-page (Model, Report, Fixer, …) becomes its
        // own tab keyed on the ``nav`` query param so clicking the
        // sidebar opens a fresh tab without overwriting the visible one.
        const navKey = (() => {
            if (!search) return null;
            return new URLSearchParams(search).get("nav");
        })();
        if (navKey) {
            // Capitalize for the title (best-effort: 'modelBpa' → 'ModelBpa').
            const pretty = navKey.charAt(0).toUpperCase() + navKey.slice(1);
            return {
                id: `pbifixer:${navKey}`,
                kind: "pbifixer",
                path: path + (search ?? ""),
                title: `PBI Fixer · ${pretty}`,
            };
        }
        return { id: "pbifixer", kind: "pbifixer", path, title: "Power BI Fixer" };
    }
    if (/\/settings(?:\b|$)/.test(clean)) {
        return { id: "settings", kind: "settings", path, title: "Settings" };
    }
    return null;
}

/** Build a fresh New Session descriptor. Each call produces a unique
 *  ``draft`` id so two successive clicks of "New Session" yield two
 *  separate untitled tabs. */
export function makeNewSessionDescriptor(basePath: string = "/agent-hub/orchestrator"): TabDescriptor {
    const draftId = Math.random().toString(36).slice(2, 10);
    return {
        id: `new:${draftId}`,
        kind: "new",
        path: `${basePath}?draft=${draftId}`,
        title: "New Session",
        duplicable: false,  // unique id already; do not re-flag
    };
}

export function EditorTabsProvider({ children }: { children: React.ReactNode }) {
    const history = useHistory();
    const location = useLocation();

    const [state, dispatch] = useReducer(
        reducer,
        undefined,
        (): TabsState => {
            try {
                const raw = sessionStorage.getItem(STORAGE_KEY);
                if (raw) {
                    const parsed = JSON.parse(raw) as TabsState;
                    if (parsed?.groups?.length && parsed.columnOrder?.length) return parsed;
                }
            } catch { /* fall through */ }
            return INITIAL_STATE;
        },
    );

    // Persist on every change. sessionStorage is fine — the tab set is
    // per-window state and shouldn't bleed across devices.
    useEffect(() => {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
        } catch { /* quota / disabled storage — silently ignore */ }
    }, [state]);

    // URL → tab sync. Whenever the route changes to a tabbable surface,
    // ensure a tab exists and is active in the current group. This keeps
    // deep links, back/forward, and programmatic navigation working
    // without every call site having to know about tabs.
    const lastSyncedPathRef = useRef<string>("");
    useEffect(() => {
        const path = location.pathname + location.search;
        if (lastSyncedPathRef.current === path) return;
        lastSyncedPathRef.current = path;
        const desc = descriptorFromPath(location.pathname, location.search);
        if (!desc) return;
        // Preserve the verbatim URL on the descriptor so hash-carried
        // state survives the round-trip.
        dispatch({ type: "open", tab: { ...desc, path } });
    }, [location.pathname, location.search]);

    const openTab = useCallback((tab: TabDescriptor) => {
        dispatch({ type: "open", tab });
        if (location.pathname + location.search !== tab.path) {
            history.push(tab.path);
        }
    }, [history, location.pathname, location.search]);

    const activateTab = useCallback((tabId: string, groupId?: string) => {
        const gid = groupId ?? state.activeGroupId;
        const g = state.groups.find((x) => x.id === gid);
        const t = g?.tabs.find((x) => x.id === tabId);
        dispatch({ type: "activate", tabId, groupId: gid });
        if (t && location.pathname + location.search !== t.path) {
            history.push(t.path);
        }
    }, [history, location.pathname, location.search, state.activeGroupId, state.groups]);

    const closeTab = useCallback((tabId: string, groupId?: string) => {
        const gid = groupId ?? state.activeGroupId;
        const g = state.groups.find((x) => x.id === gid);
        if (!g) return;
        const wasActive = g.activeTabId === tabId;

        // ── Special case: closing the last tab of a non-only group ──
        // Collapse the whole group. Do NOT navigate — navigating would
        // force the URL sync to open (or re-focus) a tab in the
        // *surviving* group, perturbing whatever the user was looking
        // at there. Instead we keep the surviving group's active tab
        // intact and mirror its path to the URL.
        const isLastTab = g.tabs.length === 1 && g.tabs[0].id === tabId;
        if (isLastTab && state.groups.length > 1) {
            dispatch({ type: "close-group", groupId: gid });
            const idx = state.groups.findIndex((x) => x.id === gid);
            const survivor =
                state.groups[idx + 1] ??
                state.groups[idx - 1] ??
                state.groups.find((x) => x.id !== gid);
            const survivorTab = survivor?.tabs.find((t) => t.id === survivor.activeTabId);
            if (survivorTab && location.pathname + location.search !== survivorTab.path) {
                history.push(survivorTab.path);
            }
            return;
        }

        dispatch({ type: "close", tabId, groupId });
        if (!wasActive) return;
        // After close, navigate to whatever the neighbour is (or home
        // if we just closed the last tab of the only group).
        const idx = g.tabs.findIndex((t) => t.id === tabId);
        const next = g.tabs[idx + 1] ?? g.tabs[idx - 1] ?? null;
        if (next) {
            history.push(next.path);
        } else {
            history.push("/agent-hub/home");
        }
    }, [history, location.pathname, location.search, state.activeGroupId, state.groups]);

    const moveTab = useCallback((tabId: string, fromGroupId: string, toGroupId: string, toIndex: number) => {
        dispatch({ type: "move", tabId, fromGroupId, toGroupId, toIndex });
    }, []);

    const splitGroup = useCallback((tabId: string, fromGroupId: string, side: "left" | "right" | "top" | "bottom") => {
        dispatch({ type: "split", tabId, fromGroupId, side });
    }, []);

    const openTabInNewGroup = useCallback((tab: TabDescriptor, side: "left" | "right" | "top" | "bottom" = "right") => {
        dispatch({ type: "open-in-new-group", tab, side });
        if (location.pathname + location.search !== tab.path) {
            history.push(tab.path);
        }
    }, [history, location.pathname, location.search]);

    const replaceActiveTab = useCallback((tab: TabDescriptor, groupId?: string) => {
        dispatch({ type: "replace-active", tab, groupId });
        if (location.pathname + location.search !== tab.path) {
            history.push(tab.path);
        }
    }, [history, location.pathname, location.search]);

    const closeGroup = useCallback((groupId: string) => {
        // Compute the survivor BEFORE dispatching so we navigate to a
        // tab that's still alive. Prefer the neighbour to the right,
        // then the left. If the closed group wasn't the active one we
        // leave the URL alone.
        const wasActive = state.activeGroupId === groupId;
        const idx = state.groups.findIndex((x) => x.id === groupId);
        const survivor = state.groups[idx + 1] ?? state.groups[idx - 1];
        dispatch({ type: "close-group", groupId });
        if (wasActive && survivor) {
            const survivorTab = survivor.tabs.find((t) => t.id === survivor.activeTabId);
            if (survivorTab && location.pathname + location.search !== survivorTab.path) {
                history.push(survivorTab.path);
            }
        }
    }, [history, location.pathname, location.search, state.activeGroupId, state.groups]);

    const focusGroup = useCallback((groupId: string) => {
        dispatch({ type: "focus-group", groupId });
        // Mirror the newly focused group's active tab to the URL so
        // deep-linking, back/forward and the topbar breadcrumb keep
        // pointing at what the user is looking at.
        const g = state.groups.find((x) => x.id === groupId);
        const t = g?.tabs.find((x) => x.id === g.activeTabId);
        if (t && location.pathname + location.search !== t.path) {
            history.push(t.path);
        }
    }, [history, location.pathname, location.search, state.groups]);

    const resizeGroup = useCallback((groupId: string, size: number) => {
        dispatch({ type: "resize", groupId, size });
    }, []);

    const resizeColumn = useCallback((columnId: string, size: number) => {
        dispatch({ type: "resize-column", columnId, size });
    }, []);

    const updateTitle = useCallback((tabId: string, title: string, subtitle?: string) => {
        dispatch({ type: "update-title", tabId, title, subtitle });
    }, []);

    const api: EditorTabsApi = useMemo(
        () => ({ state, openTab, openTabInNewGroup, replaceActiveTab, closeTab, activateTab, moveTab, splitGroup, closeGroup, focusGroup, resizeGroup, resizeColumn, updateTitle }),
        [state, openTab, openTabInNewGroup, replaceActiveTab, closeTab, activateTab, moveTab, splitGroup, closeGroup, focusGroup, resizeGroup, resizeColumn, updateTitle],
    );

    return <Ctx.Provider value={api}>{children}</Ctx.Provider>;
}

export function useEditorTabs(): EditorTabsApi {
    const ctx = useContext(Ctx);
    if (!ctx) throw new Error("useEditorTabs must be used within EditorTabsProvider");
    return ctx;
}
