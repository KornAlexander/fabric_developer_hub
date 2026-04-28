/**
 * EditorGroupsRoot — renders the 2D editor-group mosaic with tab
 * strips, drop zones for split, and draggable resizers between
 * adjacent groups.
 *
 * Layout model
 * ------------
 * ``state.columnOrder`` defines a left→right list of column ids.
 * Each ``state.groups[*].column`` points at one of those ids. All
 * groups sharing a column id render stacked vertically inside that
 * column. Groups within a column are ordered by their position in
 * ``state.groups`` (top → bottom).
 *
 *   ┌───────┬───────┬───────┐
 *   │ col 1 │ col 2 │ col 3 │   ← flex-row container
 *   │       ├───────┤       │
 *   │       │ group │       │   ← col 2 contains two stacked groups
 *   │       ├───────┤       │
 *   │       │ group │       │
 *   └───────┴───────┴───────┘
 *
 * Column widths flex by ``state.columnSizes``; row heights within a
 * column flex by each group's ``size`` weight.
 *
 * State preservation across group moves is handled by portalling
 * every tab's content into its owning group's content host (see
 * ``TabPortal``). Re-parenting via portal doesn't remount React
 * components, so SSE streams, scroll positions, and form drafts
 * survive drag-to-split and drag-between-groups.
 */

import React, { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { createPortal } from "react-dom";
import { Dismiss20Regular, SplitHorizontal20Regular } from "@fluentui/react-icons";
import { useEditorTabs, type TabDescriptor } from "./EditorTabsContext";
import { EditorTabsBar, EditorGroupDropZones, type DragPayload } from "./EditorTabsBar";

export interface EditorGroupsRootProps {
    /** Factory that maps a tab descriptor to its content. Called once
     *  per active tab per group. The factory should be stable across
     *  renders (wrap in ``useCallback``). */
    renderTab: (tab: TabDescriptor) => React.ReactNode;
    /** Fallback node when a group has no active tab (freshly emptied). */
    emptyFallback?: React.ReactNode;
}

export function EditorGroupsRoot({ renderTab, emptyFallback }: EditorGroupsRootProps) {
    const { state, splitGroup, closeGroup, focusGroup, resizeGroup, resizeColumn, openTabInNewGroup } = useEditorTabs();
    const rootRef = useRef<HTMLDivElement | null>(null);

    // ── Per-group content-host refs ──────────────────────────────
    // Every group owns an empty ``.editor-group__content`` div. We
    // record its DOM node by group id so the portal pass (below) can
    // project tab content into it. A bump counter forces a re-render
    // once hosts attach, because the first render has ``null``
    // everywhere.
    const hostsRef = useRef<Map<string, HTMLDivElement | null>>(new Map());
    // Stable ref-callbacks keyed by group id. Critical: recreating
    // these on every render would cause React to detach/attach refs
    // each render, and if each attach triggers a re-render (as our
    // ``bumpHosts`` does), we get an infinite loop and the whole UI
    // freezes — even Ctrl+R becomes unresponsive.
    const refCallbacks = useRef<Map<string, (el: HTMLDivElement | null) => void>>(new Map());
    const [, bumpHosts] = useReducer((n: number) => n + 1, 0);
    const getHostRef = useCallback((groupId: string) => {
        let cb = refCallbacks.current.get(groupId);
        if (!cb) {
            cb = (el: HTMLDivElement | null) => {
                const prev = hostsRef.current.get(groupId);
                if (prev === el) return;
                hostsRef.current.set(groupId, el);
                if ((prev == null) !== (el == null)) {
                    queueMicrotask(bumpHosts);
                }
            };
            refCallbacks.current.set(groupId, cb);
        }
        return cb;
    }, []);

    // Prune hosts for groups that no longer exist so maps don't leak.
    useEffect(() => {
        const live = new Set(state.groups.map((g) => g.id));
        for (const id of Array.from(hostsRef.current.keys())) {
            if (!live.has(id)) hostsRef.current.delete(id);
        }
        for (const id of Array.from(refCallbacks.current.keys())) {
            if (!live.has(id)) refCallbacks.current.delete(id);
        }
    }, [state.groups]);

    const onSplit = useCallback((payload: DragPayload, side: "left" | "right" | "top" | "bottom", targetGroupId: string) => {
        splitGroup(payload.tabId, payload.fromGroupId, side, targetGroupId);
    }, [splitGroup]);

    const onSplitNewTab = useCallback((desc: TabDescriptor, side: "left" | "right" | "top" | "bottom", targetGroupId: string) => {
        openTabInNewGroup(desc, side, targetGroupId);
    }, [openTabInNewGroup]);

    // ── Column (horizontal) resizer drag ──
    const startColResize = useCallback((e: React.PointerEvent<HTMLDivElement>, leftColId: string, rightColId: string) => {
        e.preventDefault();
        const root = rootRef.current;
        if (!root) return;
        const leftEl = root.querySelector<HTMLElement>(`[data-column-id="${leftColId}"]`);
        const rightEl = root.querySelector<HTMLElement>(`[data-column-id="${rightColId}"]`);
        if (!leftEl || !rightEl) return;
        const startX = e.clientX;
        const leftStart = leftEl.getBoundingClientRect().width;
        const rightStart = rightEl.getBoundingClientRect().width;
        const combined = leftStart + rightStart;
        const leftSize = state.columnSizes[leftColId] ?? 1;
        const rightSize = state.columnSizes[rightColId] ?? 1;
        const totalSize = leftSize + rightSize;

        function onMove(ev: PointerEvent) {
            const dx = ev.clientX - startX;
            const newLeftPx = Math.max(160, Math.min(combined - 160, leftStart + dx));
            const ratio = newLeftPx / combined;
            resizeColumn(leftColId, totalSize * ratio);
            resizeColumn(rightColId, totalSize * (1 - ratio));
        }
        function onUp() {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        }
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
    }, [resizeColumn, state.columnSizes]);

    // ── Row (vertical) resizer drag — adjusts group ``size`` within
    //    a column based on the two stacked groups' heights. ──
    const startRowResize = useCallback((e: React.PointerEvent<HTMLDivElement>, topGroupId: string, bottomGroupId: string) => {
        e.preventDefault();
        const root = rootRef.current;
        if (!root) return;
        const topEl = root.querySelector<HTMLElement>(`[data-group-id="${topGroupId}"]`);
        const bottomEl = root.querySelector<HTMLElement>(`[data-group-id="${bottomGroupId}"]`);
        if (!topEl || !bottomEl) return;
        const startY = e.clientY;
        const topStart = topEl.getBoundingClientRect().height;
        const bottomStart = bottomEl.getBoundingClientRect().height;
        const combined = topStart + bottomStart;
        const topSize = state.groups.find((g) => g.id === topGroupId)?.size ?? 1;
        const bottomSize = state.groups.find((g) => g.id === bottomGroupId)?.size ?? 1;
        const totalSize = topSize + bottomSize;

        function onMove(ev: PointerEvent) {
            const dy = ev.clientY - startY;
            const newTopPx = Math.max(120, Math.min(combined - 120, topStart + dy));
            const ratio = newTopPx / combined;
            resizeGroup(topGroupId, totalSize * ratio);
            resizeGroup(bottomGroupId, totalSize * (1 - ratio));
        }
        function onUp() {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        }
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
        document.body.style.cursor = "row-resize";
        document.body.style.userSelect = "none";
    }, [resizeGroup, state.groups]);

    // Clean up empty non-last groups left behind by drag-away moves.
    useEffect(() => {
        if (state.groups.length <= 1) return;
        for (const g of state.groups) {
            if (g.tabs.length === 0) {
                closeGroup(g.id);
                break;
            }
        }
    }, [closeGroup, state.groups]);

    // Portal plan — one entry per tab regardless of group; the portal
    // target is derived from the tab's owning group id.
    const portalPlan = useMemo(() => {
        const out: Array<{ tab: TabDescriptor; groupId: string; active: boolean }> = [];
        for (const g of state.groups) {
            for (const t of g.tabs) {
                out.push({ tab: t, groupId: g.id, active: t.id === g.activeTabId });
            }
        }
        return out;
    }, [state.groups]);

    // Bucket groups by column id while preserving their array order
    // (top→bottom) within each bucket.
    const columns = useMemo(() => {
        return state.columnOrder.map((colId) => ({
            id: colId,
            size: Math.max(0.1, state.columnSizes[colId] ?? 1),
            groups: state.groups.filter((g) => g.column === colId),
        })).filter((c) => c.groups.length > 0);
    }, [state.columnOrder, state.columnSizes, state.groups]);

    const showCloseGroupBtn = state.groups.length > 1;

    return (
        <div ref={rootRef} className="editor-groups">
            {columns.map((col, colIdx) => (
                <React.Fragment key={col.id}>
                    <div
                        className="editor-column"
                        data-column-id={col.id}
                        style={{ flex: `${col.size} 1 0` }}
                    >
                        {col.groups.map((g, gIdx) => {
                            const isActiveGroup = g.id === state.activeGroupId;
                            const sizeFlex = Math.max(0.1, g.size || 1);
                            return (
                                <React.Fragment key={g.id}>
                                    <section
                                        className={`editor-group${isActiveGroup ? " editor-group--active" : ""}`}
                                        data-group-id={g.id}
                                        style={{ flex: `${sizeFlex} 1 0` }}
                                        onMouseDown={() => focusGroup(g.id)}
                                    >
                                        <div className="editor-group__head">
                                            <EditorTabsBar
                                                groupId={g.id}
                                                tabs={g.tabs}
                                                activeTabId={g.activeTabId}
                                                isActiveGroup={isActiveGroup}
                                            />
                                            {showCloseGroupBtn && (
                                                <button
                                                    type="button"
                                                    className="editor-group__close-group"
                                                    title="Close group"
                                                    aria-label="Close editor group"
                                                    onClick={() => closeGroup(g.id)}
                                                >
                                                    <Dismiss20Regular />
                                                </button>
                                            )}
                                        </div>
                                        <div className="editor-group__body">
                                            <div
                                                className="editor-group__content"
                                                ref={getHostRef(g.id)}
                                            />
                                            {g.tabs.length === 0 && (emptyFallback ?? (
                                                <div className="editor-group__empty">
                                                    <SplitHorizontal20Regular aria-hidden />
                                                    <div>Drag a tab here to open it in this group.</div>
                                                </div>
                                            ))}
                                            <EditorGroupDropZones
                                                groupId={g.id}
                                                onSplit={onSplit}
                                                onSplitNewTab={onSplitNewTab}
                                            />
                                        </div>
                                    </section>
                                    {gIdx < col.groups.length - 1 && (
                                        <div
                                            className="editor-groups__resizer editor-groups__resizer--row"
                                            role="separator"
                                            aria-orientation="horizontal"
                                            aria-label="Resize stacked editor groups"
                                            onPointerDown={(e) => startRowResize(e, g.id, col.groups[gIdx + 1].id)}
                                        />
                                    )}
                                </React.Fragment>
                            );
                        })}
                    </div>
                    {colIdx < columns.length - 1 && (
                        <div
                            className="editor-groups__resizer editor-groups__resizer--col"
                            role="separator"
                            aria-orientation="vertical"
                            aria-label="Resize editor columns"
                            onPointerDown={(e) => startColResize(e, col.id, columns[colIdx + 1].id)}
                        />
                    )}
                </React.Fragment>
            ))}

            {/* Tab content store — one portal per tab, stable-keyed. */}
            {portalPlan.map(({ tab, groupId, active }) => (
                <TabPortal
                    key={tab.id}
                    host={hostsRef.current.get(groupId) ?? null}
                    visible={active}
                >
                    {renderTab(tab)}
                </TabPortal>
            ))}
        </div>
    );
}

/**
 * Projects ``children`` into ``host`` via a stable per-tab wrapper
 * ``<div>``. When ``host`` changes (tab moved to a different group)
 * we simply re-parent the wrapper — React keeps the inner content
 * fully mounted, so component state is preserved.
 *
 * A dedicated wrapper per tab is necessary because multiple tabs
 * share a host (the active one plus any number of hidden siblings),
 * and we need to toggle visibility independently without disturbing
 * their neighbours.
 */
function TabPortal({
    host,
    visible,
    children,
}: {
    host: HTMLElement | null;
    visible: boolean;
    children: React.ReactNode;
}) {
    // Create the wrapper exactly once per instance.
    const wrapperRef = useRef<HTMLDivElement | null>(null);
    if (wrapperRef.current === null && typeof document !== "undefined") {
        const el = document.createElement("div");
        el.className = "editor-group__pane";
        wrapperRef.current = el;
    }
    const wrapper = wrapperRef.current;

    // Attach / re-attach the wrapper to the current host.
    useEffect(() => {
        if (!wrapper || !host) return undefined;
        host.appendChild(wrapper);
        return () => {
            if (wrapper.parentNode === host) host.removeChild(wrapper);
        };
    }, [host, wrapper]);

    // Toggle visibility without affecting inner DOM layout state.
    useEffect(() => {
        if (!wrapper) return;
        wrapper.style.display = visible ? "" : "none";
        if (visible) wrapper.removeAttribute("aria-hidden");
        else wrapper.setAttribute("aria-hidden", "true");
    }, [visible, wrapper]);

    if (!wrapper) return null;
    return createPortal(children, wrapper);
}
