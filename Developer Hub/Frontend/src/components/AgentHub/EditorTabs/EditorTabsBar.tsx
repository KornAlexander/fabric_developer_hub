/**
 * EditorTabsBar — the VS Code-inspired persistent tab strip that sits
 * above the routed page content.
 *
 * Interaction model
 * -----------------
 * - **Click** a tab: activates + navigates.
 * - **Middle-click** a tab or click the × button: close.
 * - **Drag** a tab: reorders within its group; dragging over a side
 *   drop-zone on another group moves it there; dragging over the centre
 *   of a group-less panel splits the editor into a new group on that
 *   side (``top`` / ``right`` / ``bottom`` / ``left``).
 * - **Scroll wheel** on the strip: horizontal scroll (familiar from
 *   VS Code when the strip overflows).
 * - **Keyboard**: ``Ctrl+Tab`` / ``Ctrl+Shift+Tab`` cycles tabs,
 *   ``Ctrl+W`` closes the active tab (bound on the strip container —
 *   matches the VS Code muscle memory). ``⌘`` substitutes for ``Ctrl``
 *   on macOS.
 *
 * Accessibility
 * -------------
 * The strip uses ``role="tablist"`` with ``role="tab"`` children and
 * honours arrow-key navigation. Close buttons are real ``<button>``
 * elements with ``aria-label`` so assistive tech can dismiss tabs.
 */

import React, {
    useCallback,
    useEffect,
    useRef,
    useState,
} from "react";
import { createPortal } from "react-dom";
import {
    Dismiss12Regular,
    DocumentRegular,
    FlashRegular,
    Bot20Regular,
    Wrench20Regular,
    Settings20Regular,
    Home20Regular,
    AddCircle20Regular,
    Info20Regular,
} from "@fluentui/react-icons";
import { useEditorTabs, makeNewSessionDescriptor, type TabDescriptor, type TabKind } from "./EditorTabsContext";
import { NAV_ITEMS as PBIFIXER_NAV_ITEMS } from "../../PbiFixer";

type DropSide = "left" | "right" | "top" | "bottom" | "center";

interface DragPayload {
    tabId: string;
    fromGroupId: string;
}

/** MIME carried by a side-nav item being dragged into the editor. The
 *  payload is a full ``TabDescriptor`` so the drop handler can open it
 *  directly without needing to know which nav item was dragged. */
const DRAG_NAVITEM_MIME = "application/x-agenthub-navitem";

const DRAG_MIME = "application/x-agenthub-tab";

function iconForKind(kind: TabKind): React.ReactNode {
    switch (kind) {
        case "session":  return <FlashRegular />;
        case "new":      return <AddCircle20Regular />;
        case "home":     return <Home20Regular />;
        case "agents":   return <Bot20Regular />;
        case "pbifixer": return <Wrench20Regular />;
        case "settings": return <Settings20Regular />;
        case "about":    return <Info20Regular />;
        default:         return <DocumentRegular />;
    }
}

/** Icon for an open editor tab. PBI Fixer subpages encode their nav
 *  key in the tab id (``pbifixer:<navKey>``) so we can look up the
 *  matching sidebar icon and keep the tab strip visually aligned with
 *  the left-rail navigation. Falls back to ``iconForKind`` for any
 *  tab that doesn't carry a sub-key (e.g. the bare "Power BI Fixer"
 *  landing tab). */
function iconForTab(tab: TabDescriptor): React.ReactNode {
    if (tab.kind === "pbifixer" && tab.id.startsWith("pbifixer:")) {
        const navKey = tab.id.slice("pbifixer:".length);
        const navItem = PBIFIXER_NAV_ITEMS.find((i) => i.key === navKey);
        if (navItem) return navItem.icon;
    }
    return iconForKind(tab.kind);
}

/** IS_MAC sniff kept in sync with AgentHubLayout's — duplicated here
 *  instead of exported to avoid a cross-file import cycle. */
const IS_MAC: boolean = (() => {
    try {
        const uaData = (navigator as unknown as { userAgentData?: { platform?: string } }).userAgentData;
        const platform = uaData?.platform || navigator.platform || "";
        if (/mac/i.test(platform)) return true;
        return /Mac|iPad|iPhone|iPod/i.test(navigator.userAgent || "");
    } catch { return false; }
})();

export interface EditorTabsBarProps {
    groupId: string;
    tabs: TabDescriptor[];
    activeTabId: string | null;
    /** True when this group is the currently focused one — used to tint
     *  the active-tab underline more vividly, matching VS Code's
     *  "active editor group" emphasis. */
    isActiveGroup: boolean;
    /** Called when a tab from another group is dropped onto a side zone
     *  of *this* group's body (not the strip). Used to implement split. */
    onSplit?: (payload: DragPayload, side: Exclude<DropSide, "center">) => void;
}

export function EditorTabsBar({ groupId, tabs, activeTabId, isActiveGroup }: EditorTabsBarProps) {
    const { activateTab, closeTab, moveTab, openTab, splitGroup, openTabInNewGroup, focusGroup } = useEditorTabs();
    const stripRef = useRef<HTMLDivElement | null>(null);
    const [dragOverIdx, setDragOverIdx] = useState<number | null>(null);

    // Right-click context menu state — VS Code parity. Stores the tab
    // that was right-clicked plus the viewport-relative position so a
    // portal can render the menu over any ancestor overflow.
    const [ctxMenu, setCtxMenu] = useState<{
        tabId: string; x: number; y: number;
    } | null>(null);
    useEffect(() => {
        if (!ctxMenu) return undefined;
        const onDismiss = () => setCtxMenu(null);
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setCtxMenu(null); };
        // Using capture phase + a frame delay so the click that opened
        // the menu doesn't immediately close it.
        const raf = requestAnimationFrame(() => {
            window.addEventListener("mousedown", onDismiss);
            window.addEventListener("contextmenu", onDismiss);
            window.addEventListener("resize", onDismiss);
            window.addEventListener("blur", onDismiss);
            window.addEventListener("keydown", onKey);
        });
        return () => {
            cancelAnimationFrame(raf);
            window.removeEventListener("mousedown", onDismiss);
            window.removeEventListener("contextmenu", onDismiss);
            window.removeEventListener("resize", onDismiss);
            window.removeEventListener("blur", onDismiss);
            window.removeEventListener("keydown", onKey);
        };
    }, [ctxMenu]);

    // ── Horizontal wheel scrolling ──
    useEffect(() => {
        const el = stripRef.current;
        if (!el) return undefined;
        const onWheel = (e: WheelEvent) => {
            if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
                el.scrollLeft += e.deltaY;
                e.preventDefault();
            }
        };
        el.addEventListener("wheel", onWheel, { passive: false });
        return () => el.removeEventListener("wheel", onWheel);
    }, []);

    // ── Keyboard: Ctrl/Cmd+Tab, Ctrl/Cmd+W ──
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            // Respect typing contexts.
            const t = e.target as HTMLElement | null;
            if (t && (
                t.tagName === "INPUT" ||
                t.tagName === "TEXTAREA" ||
                t.isContentEditable
            )) return;
            const mod = IS_MAC ? e.metaKey : e.ctrlKey;
            if (!mod) return;
            if (!isActiveGroup) return;
            if (e.key === "Tab") {
                if (tabs.length === 0) return;
                const idx = tabs.findIndex((t) => t.id === activeTabId);
                const next = e.shiftKey
                    ? (idx - 1 + tabs.length) % tabs.length
                    : (idx + 1) % tabs.length;
                activateTab(tabs[next].id, groupId);
                e.preventDefault();
            } else if (e.key === "w" || e.key === "W") {
                if (activeTabId) {
                    closeTab(activeTabId, groupId);
                    e.preventDefault();
                }
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [activateTab, activeTabId, closeTab, groupId, isActiveGroup, tabs]);

    const onDragStart = useCallback((e: React.DragEvent<HTMLDivElement>, tabId: string) => {
        const payload: DragPayload = { tabId, fromGroupId: groupId };
        e.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload));
        e.dataTransfer.effectAllowed = "move";
        // Set a translucent drag image anchored near the cursor — browser
        // defaults snapshot the entire row which looks clumsy.
        const node = e.currentTarget;
        e.dataTransfer.setDragImage(node, 16, 16);
    }, [groupId]);

    const onTabDragOver = useCallback((e: React.DragEvent<HTMLDivElement>, idx: number) => {
        const types = e.dataTransfer.types;
        if (!types.includes(DRAG_MIME) && !types.includes(DRAG_NAVITEM_MIME)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        // Bias the insertion index toward the half of the tab the cursor
        // is over so dropping right-of-midpoint lands after the tab.
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        const after = e.clientX - rect.left > rect.width / 2;
        setDragOverIdx(after ? idx + 1 : idx);
    }, []);

    const onStripDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        const types = e.dataTransfer.types;
        if (!types.includes(DRAG_MIME) && !types.includes(DRAG_NAVITEM_MIME)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        if (tabs.length === 0) setDragOverIdx(0);
    }, [tabs.length]);

    const onStripDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        // Nav-item drag: open as a new tab in this group at the drop index.
        const navRaw = e.dataTransfer.getData(DRAG_NAVITEM_MIME);
        if (navRaw) {
            e.preventDefault();
            let desc: TabDescriptor;
            try { desc = JSON.parse(navRaw); } catch { return; }
            openTab(desc);
            setDragOverIdx(null);
            return;
        }
        const raw = e.dataTransfer.getData(DRAG_MIME);
        if (!raw) return;
        e.preventDefault();
        let payload: DragPayload;
        try { payload = JSON.parse(raw); } catch { return; }
        const idx = dragOverIdx ?? tabs.length;
        moveTab(payload.tabId, payload.fromGroupId, groupId, idx);
        setDragOverIdx(null);
    }, [dragOverIdx, groupId, moveTab, openTab, tabs.length]);

    const onStripDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        // Only clear when the leave actually exits the strip (not a
        // child-boundary crossing).
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        setDragOverIdx(null);
    }, []);

    const onTabClick = useCallback((e: React.MouseEvent, tab: TabDescriptor) => {
        if (e.button !== 0) return;
        activateTab(tab.id, groupId);
    }, [activateTab, groupId]);

    const onTabAuxClick = useCallback((e: React.MouseEvent, tab: TabDescriptor) => {
        // Middle-click = close, matching VS Code + browser tabs.
        if (e.button === 1) {
            e.preventDefault();
            closeTab(tab.id, groupId);
        }
    }, [closeTab, groupId]);

    const onTabContextMenu = useCallback((e: React.MouseEvent, tab: TabDescriptor) => {
        e.preventDefault();
        e.stopPropagation();
        // Activate the right-clicked tab first so the action target is
        // visible even if the menu itself obscures part of the strip.
        if (tab.id !== activeTabId) activateTab(tab.id, groupId);
        setCtxMenu({ tabId: tab.id, x: e.clientX, y: e.clientY });
    }, [activateTab, activeTabId, groupId]);

    // Double-click on the empty portion of the strip opens a fresh
    // New Session draft in this group — matches VS Code's "double
    // click the tab bar to create a new untitled file" gesture.
    // Clicks that land on an existing tab bubble up here too, so we
    // gate on the event target to ignore those.
    const onStripDoubleClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        const target = e.target as HTMLElement | null;
        if (target && target.closest(".editor-tab")) return;
        // ``openTab`` adds the tab to the *active* group, so make sure
        // the group that was double-clicked is the active one first.
        focusGroup(groupId);
        openTab(makeNewSessionDescriptor());
    }, [focusGroup, groupId, openTab]);

    return (
        <div
            ref={stripRef}
            className={`editor-tabs ${isActiveGroup ? "editor-tabs--active" : ""}`}
            role="tablist"
            aria-label="Open sessions"
            onDragOver={onStripDragOver}
            onDrop={onStripDrop}
            onDragLeave={onStripDragLeave}
            onDoubleClick={onStripDoubleClick}
        >
            {tabs.length === 0 ? (
                <div className="editor-tabs__empty">No tabs open</div>
            ) : (
                tabs.map((tab, i) => {
                    const active = tab.id === activeTabId;
                    return (
                        <React.Fragment key={tab.id}>
                            {dragOverIdx === i && (
                                <div className="editor-tabs__drop-indicator" aria-hidden />
                            )}
                            <div
                                className={`editor-tab ${active ? "editor-tab--active" : ""}`}
                                role="tab"
                                aria-selected={active}
                                tabIndex={active ? 0 : -1}
                                draggable
                                onDragStart={(e) => onDragStart(e, tab.id)}
                                onDragOver={(e) => onTabDragOver(e, i)}
                                onDragEnd={() => setDragOverIdx(null)}
                                onMouseDown={(e) => onTabAuxClick(e, tab)}
                                onClick={(e) => onTabClick(e, tab)}
                                onContextMenu={(e) => onTabContextMenu(e, tab)}
                                title={tab.subtitle ? `${tab.title} — ${tab.subtitle}` : tab.title}
                            >
                                <span className="editor-tab__icon" aria-hidden>
                                    {iconForTab(tab)}
                                </span>
                                <span className="editor-tab__label">{tab.title}</span>
                                <button
                                    type="button"
                                    className="editor-tab__close"
                                    aria-label={`Close ${tab.title}`}
                                    onClick={(e) => { e.stopPropagation(); closeTab(tab.id, groupId); }}
                                >
                                    <Dismiss12Regular />
                                </button>
                            </div>
                        </React.Fragment>
                    );
                })
            )}
            {dragOverIdx !== null && dragOverIdx >= tabs.length && (
                <div className="editor-tabs__drop-indicator" aria-hidden />
            )}
            {ctxMenu && createPortal((
                <TabContextMenu
                    x={ctxMenu.x}
                    y={ctxMenu.y}
                    tabs={tabs}
                    tabId={ctxMenu.tabId}
                    groupId={groupId}
                    onClose={() => setCtxMenu(null)}
                    closeTab={closeTab}
                    splitGroup={splitGroup}
                    openTabInNewGroup={openTabInNewGroup}
                />
            ), document.body)}
        </div>
    );
}

/**
 * VS Code-style right-click menu for a tab. Rendered via a portal to
 * ``document.body`` so it escapes any ancestor ``overflow`` / clipping
 * (e.g. the tab strip itself is horizontally scrollable). Auto-flips
 * when close to the viewport edges.
 */
function TabContextMenu({
    x, y, tabs, tabId, groupId, onClose,
    closeTab, splitGroup, openTabInNewGroup,
}: {
    x: number; y: number;
    tabs: TabDescriptor[];
    tabId: string;
    groupId: string;
    onClose: () => void;
    closeTab: (id: string, gid?: string) => void;
    splitGroup: (id: string, fromGroupId: string, side: "left" | "right" | "top" | "bottom") => void;
    openTabInNewGroup: (tab: TabDescriptor, side?: "left" | "right" | "top" | "bottom") => void;
}) {
    const menuRef = useRef<HTMLDivElement | null>(null);
    const [pos, setPos] = useState<{ top: number; left: number }>({ top: y, left: x });

    // Flip the menu if it would overflow the viewport.
    useEffect(() => {
        const el = menuRef.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const MARGIN = 6;
        let top = y;
        let left = x;
        if (left + r.width > vw - MARGIN) left = Math.max(MARGIN, vw - MARGIN - r.width);
        if (top + r.height > vh - MARGIN) top = Math.max(MARGIN, vh - MARGIN - r.height);
        if (top !== pos.top || left !== pos.left) setPos({ top, left });
    }, [x, y, pos.left, pos.top]);

    const idx = tabs.findIndex((t) => t.id === tabId);
    const tab = tabs[idx];
    const tabsToRight = idx >= 0 ? tabs.slice(idx + 1) : [];
    const otherTabs = tabs.filter((t) => t.id !== tabId);

    const run = (fn: () => void) => {
        fn();
        onClose();
    };

    const items: Array<
        | { kind: "item"; label: string; onClick: () => void; disabled?: boolean; accel?: string }
        | { kind: "sep" }
    > = [
        {
            kind: "item",
            label: "Close",
            accel: "Ctrl+W",
            onClick: () => run(() => closeTab(tabId, groupId)),
            disabled: !tab,
        },
        {
            kind: "item",
            label: "Close Others",
            onClick: () => run(() => { for (const t of otherTabs) closeTab(t.id, groupId); }),
            disabled: otherTabs.length === 0,
        },
        {
            kind: "item",
            label: "Close to the Right",
            onClick: () => run(() => { for (const t of tabsToRight) closeTab(t.id, groupId); }),
            disabled: tabsToRight.length === 0,
        },
        {
            kind: "item",
            label: "Close All",
            onClick: () => run(() => { for (const t of tabs) closeTab(t.id, groupId); }),
            disabled: tabs.length === 0,
        },
        { kind: "sep" },
        {
            kind: "item",
            label: "Split Right",
            onClick: () => run(() => splitGroup(tabId, groupId, "right")),
            disabled: !tab,
        },
        {
            kind: "item",
            label: "Split Down",
            onClick: () => run(() => splitGroup(tabId, groupId, "bottom")),
            disabled: !tab,
        },
        { kind: "sep" },
        {
            kind: "item",
            label: "Open Duplicate in New Group",
            onClick: () => run(() => { if (tab) openTabInNewGroup({ ...tab, id: `${tab.id}-dup-${Date.now()}` }, "right"); }),
            disabled: !tab,
        },
    ];

    return (
        <div
            ref={menuRef}
            className="tab-context-menu"
            role="menu"
            style={{ top: `${pos.top}px`, left: `${pos.left}px` }}
            // Prevent the global mousedown dismisser from firing when
            // the user clicks inside the menu itself.
            onMouseDown={(e) => e.stopPropagation()}
            onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); }}
        >
            {items.map((it, i) => it.kind === "sep" ? (
                <div key={`sep-${i}`} className="tab-context-menu__sep" role="separator" />
            ) : (
                <button
                    key={it.label}
                    type="button"
                    role="menuitem"
                    className="tab-context-menu__item"
                    disabled={it.disabled}
                    onClick={it.onClick}
                >
                    <span className="tab-context-menu__label">{it.label}</span>
                    {it.accel && <span className="tab-context-menu__accel">{it.accel}</span>}
                </button>
            ))}
        </div>
    );
}

/**
 * Drop zones that overlay the page body when a tab is being dragged.
 * Splits the current group when dropping on a side edge, matching the
 * VS Code editor split gesture. Also accepts side-nav items — in that
 * case a brand-new tab is opened in a freshly-created group.
 */
export function EditorGroupDropZones({
    groupId,
    onSplit,
    onSplitNewTab,
}: {
    groupId: string;
    onSplit: (payload: DragPayload, side: Exclude<DropSide, "center">) => void;
    /** Called when a side-nav item is dropped onto a side zone.
     *  Opens a new tab from the dragged descriptor in a new group. */
    onSplitNewTab?: (descriptor: TabDescriptor, side: Exclude<DropSide, "center">) => void;
}) {
    const [dragging, setDragging] = useState(false);
    const [hoverSide, setHoverSide] = useState<DropSide | null>(null);

    useEffect(() => {
        const onStart = (e: DragEvent) => {
            const t = e.dataTransfer?.types;
            if (t?.includes(DRAG_MIME) || t?.includes(DRAG_NAVITEM_MIME)) setDragging(true);
        };
        const onEnd = () => { setDragging(false); setHoverSide(null); };
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onEnd(); };
        // Cleanup strategy: the primary exit events are ``dragend`` and
        // ``drop`` — both reliably fire when the OS finishes the drag.
        // We intentionally do NOT listen for ``blur`` / ``pointerup`` /
        // ``mouseup`` / ``visibilitychange`` here: the browser fires
        // those spuriously during a legitimate HTML5 drag (the drag
        // image briefly takes focus, some browsers emit pointerup as
        // soon as the drag starts), which would prematurely tear down
        // the drop zones and make the browser show the "forbidden"
        // cursor over what should be a valid drop target.
        window.addEventListener("dragstart", onStart);
        window.addEventListener("dragend", onEnd);
        window.addEventListener("drop", onEnd);
        window.addEventListener("keydown", onKey);
        return () => {
            window.removeEventListener("dragstart", onStart);
            window.removeEventListener("dragend", onEnd);
            window.removeEventListener("drop", onEnd);
            window.removeEventListener("keydown", onKey);
        };
    }, []);

    // Hit-test the cursor against the four diamond wedges of the
    // group body. VS Code partitions a drop target into four
    // triangles meeting at the centre — the wedge the cursor sits in
    // determines the split side. We approximate that with a "closest
    // edge" test, which yields the same partition (each diagonal is
    // the locus where two distances are equal).
    const hitTestSide = useCallback((e: React.DragEvent<HTMLDivElement>): Exclude<DropSide, "center"> => {
        const rect = e.currentTarget.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;
        // Distance to each edge, normalised to the [0, 0.5] range so
        // the four wedges are equal-area.
        const distances = {
            left: x,
            right: 1 - x,
            top: y,
            bottom: 1 - y,
        } as const;
        let winner: Exclude<DropSide, "center"> = "left";
        let min = distances.left;
        (Object.keys(distances) as Array<keyof typeof distances>).forEach((k) => {
            if (distances[k] < min) { min = distances[k]; winner = k; }
        });
        return winner;
    }, []);

    const onDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        const t = e.dataTransfer.types;
        if (!t.includes(DRAG_MIME) && !t.includes(DRAG_NAVITEM_MIME)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setHoverSide(hitTestSide(e));
    }, [hitTestSide]);

    const onDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        const side = hitTestSide(e);
        // Prefer the nav-item payload — user is opening something new.
        const navRaw = e.dataTransfer.getData(DRAG_NAVITEM_MIME);
        if (navRaw) {
            e.preventDefault();
            let desc: TabDescriptor;
            try { desc = JSON.parse(navRaw); } catch { return; }
            if (onSplitNewTab) onSplitNewTab(desc, side);
            setHoverSide(null);
            setDragging(false);
            return;
        }
        const raw = e.dataTransfer.getData(DRAG_MIME);
        if (!raw) return;
        e.preventDefault();
        let payload: DragPayload;
        try { payload = JSON.parse(raw); } catch { return; }
        onSplit(payload, side);
        setHoverSide(null);
        setDragging(false);
    }, [hitTestSide, onSplit, onSplitNewTab]);

    const onDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        // Only clear when the leave actually exits the overlay (not a
        // child-boundary crossing — though there are no children here,
        // browsers sometimes fire spurious events at subpixel seams).
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        setHoverSide(null);
    }, []);

    if (!dragging) return null;

    return (
        <div
            className={`editor-group__drop-zones${hoverSide ? " editor-group__drop-zones--hover" : ""}`}
            data-group={groupId}
        >
            {/* Single overlay spanning the whole group body. The split
                side is computed from cursor position (closest edge
                wins), matching VS Code's four-wedge drop model. The
                preview beneath reflects the wedge currently under the
                cursor so the user can see which half the group will
                split into before releasing. */}
            <div
                className="editor-group__drop-zone editor-group__drop-zone--full"
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
            />
            {hoverSide && hoverSide !== "center" && (
                <>
                    {/* Post-split preview — shows BOTH halves so the
                        width split is unambiguous. The solid blue
                        dashed half represents the new group; the
                        faded ghost half represents what the existing
                        group will shrink to. A centre divider rail
                        indicates the resizer position. After a split
                        both groups have ``size: 1`` → exactly 50/50,
                        which is what we render here. */}
                    <div className={`editor-group__drop-preview editor-group__drop-preview--${hoverSide}`} aria-hidden />
                    <div className={`editor-group__drop-preview-ghost editor-group__drop-preview-ghost--${hoverSide}`} aria-hidden />
                    <div className={`editor-group__drop-preview-divider editor-group__drop-preview-divider--${hoverSide === "top" || hoverSide === "bottom" ? "horizontal" : "vertical"}`} aria-hidden />
                </>
            )}
        </div>
    );
}

export { DRAG_MIME, DRAG_NAVITEM_MIME };
export type { DragPayload };
