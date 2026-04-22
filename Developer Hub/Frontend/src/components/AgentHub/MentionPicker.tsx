/**
 * MentionPicker — keyboard-driven @-mention popover for the task composer.
 *
 * UX goals:
 *   • Discoverability — rendered near the caret when the user types "@"
 *     (preceded by whitespace or start of input).
 *   • Fuzzy search — reuses {@link fuzzyFilter} so "pi" picks Pipeline_1
 *     and "mark" picks Marketing Analytics.
 *   • Grouped results — Workspaces, Fabric items, and attached files get
 *     their own section headers with colored type icons.
 *   • Keyboard-first — ↑/↓ to navigate, Tab / Enter to accept, Esc to close.
 *   • Tight integration — on accept, we hand the chosen entry back to the
 *     parent so it can (a) replace the "@query" text in the textarea and
 *     (b) add the resource to the existing context-pill rail.
 *
 * The picker itself does not manage the textarea's text or the pill rail —
 * those are owned by OrchestratorPage. We stay presentational on purpose.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
    PeopleTeam20Regular,
    DocumentPdf20Regular,
    Document20Regular,
    Image20Regular,
    Map20Regular,
} from "@fluentui/react-icons";
// Official Fabric portal icons — match the vocabulary users see in the
// workspace explorer so the @-picker feels like a natural extension of
// the Fabric UI.
import {
    Apps20Item,
    CopyJob20Item,
    Dashboard20Item,
    DataAgent20Item,
    DataFactory20Item,
    DataWarehouse20Item,
    Dataflow20Item,
    DataflowGen220Item,
    Datamart20Item,
    Environment20Item,
    EventHouse20Item,
    Eventstream20Item,
    Experiments20Item,
    Exploration20Item,
    FunctionSet20Item,
    GenericPlaceholder20Item,
    KqlDatabase20Item,
    KqlQueryset20Item,
    KqlScript20Item,
    Lakehouse20Item,
    MetricSets20Item,
    MirroredGenericDatabase20Item,
    MobileReport20Item,
    Model20Item,
    Notebook20Item,
    OperationsAgent20Item,
    PaginatedReport20Item,
    Pipeline20Item,
    RdlReport20Item,
    RealTimeDashboard20Item,
    Report20Item,
    SchemaModel20Item,
    Scorecard20Item,
    SemanticModel20Item,
    SparkJobDirection20Item,
    SqlDatabase20Item,
    UserDataFunction20Item,
    VariableLibrary20Item,
} from "@fabric-msft/svg-icons";
import { fuzzyFilter, fuzzyScore } from "./fuzzySearch";

/** One row in the picker. `kind` drives icon + color; `payload` is opaque
 *  data handed back to the parent on accept. */
export interface MentionSuggestion {
    /** Stable id so React keys and "already picked" checks work. */
    id: string;
    /** Display name shown in the popover and written into the textarea. */
    name: string;
    /** Optional subtitle (e.g. "Lakehouse · Marketing Analytics"). */
    meta?: string;
    /** Logical kind — selects icon palette and grouping. Kept in sync
     *  with {@link MentionKind} in RichComposer so a suggestion can be
     *  accepted directly into the composer as an inline chip. */
    kind:
        | "workspace"
        | "lakehouse" | "warehouse" | "notebook" | "pipeline"
        | "sqldb" | "sqlendpoint" | "kqldatabase" | "eventhouse"
        | "kqlqueryset" | "kqlscript"
        | "semantic" | "dataflow" | "dataflowgen2" | "eventstream"
        | "mirrored" | "mlmodel" | "mlexperiment" | "environment"
        | "report" | "dashboard" | "paginated" | "sparkjob"
        | "rdlreport" | "mobilereport" | "rtdashboard"
        | "scorecard" | "metric" | "schemamodel"
        | "userfunction" | "functionset" | "variables"
        | "exploration" | "dataagent" | "opsagent" | "app"
        | "map"
        | "reflex" | "datafactory" | "copyjob" | "datamart"
        | "item"
        | "pdf" | "image" | "file";
    /** Group label used in the popover header. Defaults to a sensible value
     *  derived from `kind`. */
    group?: string;
    /** Opaque data handed back on accept. */
    payload: unknown;
}

export interface MentionPickerProps {
    /** Controls visibility. When false, nothing is rendered. */
    open: boolean;
    /** Current query string (text after the "@"). */
    query: string;
    /** Anchor rectangle used to position the popover (DOM coords, i.e.
     *  viewport). Usually the caret bounding box. */
    anchor: { top: number; left: number; bottom: number } | null;
    /** Pool of suggestions to rank. */
    suggestions: MentionSuggestion[];
    /** Invoked when the user accepts a row (Enter / Tab / click). */
    onAccept: (s: MentionSuggestion) => void;
    /** Invoked when the picker should close (Esc, outside click, no query). */
    onDismiss: () => void;
    /** True while the parent is still loading more suggestions into the
     *  pool (e.g. fanning out across workspaces in the background).
     *  Drives a subtle spinner + "still indexing…" pill in the header
     *  so users don't see the result count silently jump. */
    loading?: boolean;
    /** Optional progress snapshot ({indexed, total}) used while `loading`
     *  is true so the header can show "Indexing 3/12…" instead of an
     *  opaque spinner. */
    progress?: { indexed: number; total: number };
}

// ─── internal helpers ──────────────────────────────────────────────────

// Fallback group order used when the user hasn't typed a query (so we
// have no score signal to rank groups by). When a query IS active we
// rank groups by their best match score instead — see the grouping
// pass below — which is what makes a well-matched item surface to the
// top of the popover regardless of its category.
const GROUP_ORDER: Array<MentionSuggestion["kind"]> = [
    "workspace",
    "lakehouse", "warehouse", "sqldb", "sqlendpoint",
    "kqldatabase", "eventhouse", "kqlqueryset", "kqlscript",
    "mirrored", "schemamodel",
    "semantic", "datamart",
    "notebook", "sparkjob", "environment",
    "pipeline", "dataflow", "dataflowgen2", "copyjob", "datafactory",
    "eventstream", "reflex",
    "userfunction", "functionset", "variables",
    "mlmodel", "mlexperiment", "exploration",
    "dataagent", "opsagent",
    "report", "paginated", "rdlreport", "mobilereport",
    "dashboard", "rtdashboard",
    "scorecard", "metric",
    "app", "map",
    "item",
    "pdf", "image", "file",
];

const GROUP_LABELS: Record<MentionSuggestion["kind"], string> = {
    workspace:    "Workspaces",
    lakehouse:    "Lakehouses",
    warehouse:    "Warehouses",
    sqldb:        "SQL Databases",
    sqlendpoint:  "SQL Endpoints",
    kqldatabase:  "KQL Databases",
    eventhouse:   "Eventhouses",
    kqlqueryset:  "KQL Querysets",
    kqlscript:    "KQL Scripts",
    mirrored:     "Mirrored Databases",
    schemamodel:  "Schema Models",
    semantic:     "Semantic Models",
    datamart:     "Datamarts",
    notebook:     "Notebooks",
    sparkjob:     "Spark Job Definitions",
    environment:  "Environments",
    pipeline:     "Pipelines",
    dataflow:     "Dataflows",
    dataflowgen2: "Dataflows Gen2",
    copyjob:      "Copy Jobs",
    datafactory:  "Data Factories",
    eventstream:  "Eventstreams",
    reflex:       "Activators",
    userfunction: "User Data Functions",
    functionset:  "Function Sets",
    variables:    "Variable Libraries",
    mlmodel:      "ML Models",
    mlexperiment: "ML Experiments",
    exploration:  "Data Explorations",
    dataagent:    "Data Agents",
    opsagent:     "Operations Agents",
    report:       "Reports",
    paginated:    "Paginated Reports",
    rdlreport:    "RDL Reports",
    mobilereport: "Mobile Reports",
    dashboard:    "Dashboards",
    rtdashboard:  "Real-Time Dashboards",
    scorecard:    "Scorecards",
    metric:       "Metric Sets",
    app:          "Org Apps",
    map:          "Maps",
    item:         "Items",
    pdf:          "Documents",
    image:        "Images",
    file:         "Files",
};

function KindIcon({ kind }: { kind: MentionSuggestion["kind"] }) {
    switch (kind) {
        case "workspace":    return <PeopleTeam20Regular />;
        case "lakehouse":    return <Lakehouse20Item />;
        case "warehouse":    return <DataWarehouse20Item />;
        case "sqldb":        return <SqlDatabase20Item />;
        case "sqlendpoint":  return <SqlDatabase20Item />;
        case "kqldatabase":  return <KqlDatabase20Item />;
        case "eventhouse":   return <EventHouse20Item />;
        case "kqlqueryset":  return <KqlQueryset20Item />;
        case "kqlscript":    return <KqlScript20Item />;
        case "mirrored":     return <MirroredGenericDatabase20Item />;
        case "schemamodel":  return <SchemaModel20Item />;
        case "semantic":     return <SemanticModel20Item />;
        case "datamart":     return <Datamart20Item />;
        case "notebook":     return <Notebook20Item />;
        case "sparkjob":     return <SparkJobDirection20Item />;
        case "environment":  return <Environment20Item />;
        case "pipeline":     return <Pipeline20Item />;
        case "dataflow":     return <Dataflow20Item />;
        case "dataflowgen2": return <DataflowGen220Item />;
        case "copyjob":      return <CopyJob20Item />;
        case "datafactory":  return <DataFactory20Item />;
        case "eventstream":  return <Eventstream20Item />;
        case "reflex":       return <OperationsAgent20Item />;
        case "userfunction": return <UserDataFunction20Item />;
        case "functionset":  return <FunctionSet20Item />;
        case "variables":    return <VariableLibrary20Item />;
        case "mlmodel":      return <Model20Item />;
        case "mlexperiment": return <Experiments20Item />;
        case "exploration":  return <Exploration20Item />;
        case "dataagent":    return <DataAgent20Item />;
        case "opsagent":     return <OperationsAgent20Item />;
        case "report":       return <Report20Item />;
        case "paginated":    return <PaginatedReport20Item />;
        case "rdlreport":    return <RdlReport20Item />;
        case "mobilereport": return <MobileReport20Item />;
        case "dashboard":    return <Dashboard20Item />;
        case "rtdashboard":  return <RealTimeDashboard20Item />;
        case "scorecard":    return <Scorecard20Item />;
        case "metric":       return <MetricSets20Item />;
        case "app":          return <Apps20Item />;
        case "map":          return <Map20Regular />;
        case "pdf":          return <DocumentPdf20Regular />;
        case "image":        return <Image20Regular />;
        case "file":         return <Document20Regular />;
        case "item":         return <GenericPlaceholder20Item />;
        default:             return <GenericPlaceholder20Item />;
    }
}

/** Wrap matching substring in <mark>. Case-insensitive, contiguous match.
 *  For no-query we just escape + return the name. */
function highlightMatch(name: string, query: string): React.ReactNode {
    if (!query) return name;
    const lower = name.toLowerCase();
    const needle = query.toLowerCase();
    const idx = lower.indexOf(needle);
    if (idx < 0) return name;
    return (
        <>
            {name.slice(0, idx)}
            <mark className="mention-pop__mark">
                {name.slice(idx, idx + needle.length)}
            </mark>
            {name.slice(idx + needle.length)}
        </>
    );
}

// ─── component ─────────────────────────────────────────────────────────

/**
 * Pure-presentational popover. Listens for keyboard events on window while
 * open so the parent doesn't need to wire anything up — the composer
 * textarea retains focus and the popover intercepts navigation keys.
 */
export const MentionPicker: React.FC<MentionPickerProps> = ({
    open, query, anchor, suggestions, onAccept, onDismiss, loading = false, progress,
}) => {
    // Ranked + grouped results. `flat` is the full ranked list — the
    // count shown in the header always reflects the *true* total so
    // users know how many matches exist. Rendering is windowed via
    // `renderLimit` below so the DOM stays cheap even at 1000+ items.
    //
    // Grouping strategy:
    //   • When a query is active we rank GROUPS by the best fuzzy score
    //     of any item in them. An item that's a near-perfect match
    //     ("map" → "map_1test") pulls its whole category to the top,
    //     so users never have to scroll past a wall of weakly-matching
    //     workspaces to find the thing they literally typed.
    //   • When the query is empty we fall back to the curated
    //     GROUP_ORDER (workspaces, then data stores, then compute…)
    //     because we have no score signal to sort by.
    //   • The synthetic "Attached" group is always pinned on top — by
    //     definition it's already-selected context the user pinned
    //     themselves, so it deserves prime real estate.
    const { flat, groups } = useMemo(() => {
        const ranked = fuzzyFilter(
            query,
            suggestions,
            (s) => [s.name, s.meta || ""],
        );
        const hasQuery = query.trim().length > 0;
        // Per-item score (only needed when we rank groups by score).
        const scoreFor = (s: MentionSuggestion) =>
            hasQuery ? fuzzyScore(query, [s.name, s.meta || ""]) : 0;
        // Bucket by group key. `ranked` is already sorted best-first,
        // so the first item we push into a bucket is that bucket's
        // top-scorer. Map preserves insertion order — we use that to
        // implement "first-appeared wins" tie-breaking.
        const byKey = new Map<string, { label: string; items: MentionSuggestion[]; top: number; canonical: boolean }>();
        for (const s of ranked) {
            const key = s.group || s.kind;
            const isCanonical = !s.group; // explicit s.group means custom bucket (e.g. "Attached")
            const label = isCanonical ? GROUP_LABELS[s.kind] : key;
            const existing = byKey.get(key);
            if (existing) {
                existing.items.push(s);
            } else {
                byKey.set(key, { label, items: [s], top: scoreFor(s), canonical: isCanonical });
            }
        }
        // Build ordering.
        const orderedKeys: string[] = [];
        // 1. Pinned custom groups (Attached) always first — in insertion order.
        for (const [key, g] of byKey) if (!g.canonical) orderedKeys.push(key);
        // 2. Remaining groups: by best-match score (desc) when a query
        //    is active; otherwise by the curated GROUP_ORDER.
        const remaining = [...byKey.entries()].filter(([, g]) => g.canonical);
        if (hasQuery) {
            remaining.sort((a, b) => b[1].top - a[1].top);
            for (const [key] of remaining) orderedKeys.push(key);
        } else {
            const canonicalOrder = new Map(GROUP_ORDER.map((k, i) => [k as string, i]));
            remaining.sort((a, b) => {
                const ai = canonicalOrder.get(a[0]) ?? 999;
                const bi = canonicalOrder.get(b[0]) ?? 999;
                return ai - bi;
            });
            for (const [key] of remaining) orderedKeys.push(key);
        }
        const flat: MentionSuggestion[] = [];
        const groups: Array<{ label: string; items: MentionSuggestion[] }> = [];
        for (const key of orderedKeys) {
            const g = byKey.get(key)!;
            groups.push({ label: g.label, items: g.items });
            flat.push(...g.items);
        }
        return { flat, groups };
    }, [query, suggestions]);

    // ── Lazy-render window ───────────────────────────────────────
    // Start with one screen's worth (~50 rows at 44px row height fits
    // comfortably in the 300px popover with some scroll-room) and grow
    // by the same chunk each time the bottom sentinel comes into view
    // *or* the user keyboard-navigates near the end. This keeps React
    // renders O(50) regardless of catalog size while the ranked count
    // in the header always reflects the full pool.
    const INITIAL_RENDER = 50;
    const RENDER_CHUNK = 50;
    const [renderLimit, setRenderLimit] = useState(INITIAL_RENDER);

    const [active, setActive] = useState(0);
    // Reset selection + render window whenever the ranked list changes.
    useEffect(() => {
        setActive(0);
        setRenderLimit(INITIAL_RENDER);
    }, [flat.length, query]);

    // If the user keyboard-walks toward the end of the rendered window,
    // grow the window proactively so they never hit an artificial wall.
    useEffect(() => {
        if (active >= renderLimit - 5 && renderLimit < flat.length) {
            setRenderLimit((n) => Math.min(n + RENDER_CHUNK, flat.length));
        }
    }, [active, renderLimit, flat.length]);

    // Keyboard navigation — attach to window so the textarea keeps focus.
    useEffect(() => {
        if (!open) return undefined;
        function onKey(e: KeyboardEvent) {
            if (e.key === "ArrowDown") {
                if (flat.length === 0) return;
                e.preventDefault();
                setActive((i) => (i + 1) % flat.length);
            } else if (e.key === "ArrowUp") {
                if (flat.length === 0) return;
                e.preventDefault();
                setActive((i) => (i - 1 + flat.length) % flat.length);
            } else if (e.key === "Enter" || e.key === "Tab") {
                if (flat.length === 0) {
                    // Close so the Tab/Enter performs its normal action next
                    // time (e.g. inserts a tab character, submits form).
                    onDismiss();
                    return;
                }
                e.preventDefault();
                onAccept(flat[active]);
            } else if (e.key === "Escape") {
                e.preventDefault();
                onDismiss();
            }
        }
        window.addEventListener("keydown", onKey, true);
        return () => window.removeEventListener("keydown", onKey, true);
    }, [open, flat, active, onAccept, onDismiss]);

    // Scroll active row into view.
    const listRef = useRef<HTMLDivElement | null>(null);
    useEffect(() => {
        const el = listRef.current?.querySelector<HTMLDivElement>(
            `[data-mention-idx="${active}"]`,
        );
        el?.scrollIntoView({ block: "nearest" });
    }, [active]);

    // IntersectionObserver on a bottom sentinel — when the user scrolls
    // it into view, load the next chunk. Falls back to onScroll (see
    // `onListScroll` below) for environments without IO support.
    const sentinelRef = useRef<HTMLDivElement | null>(null);
    useEffect(() => {
        if (!open) return undefined;
        const root = listRef.current;
        const target = sentinelRef.current;
        if (!root || !target || typeof IntersectionObserver === "undefined") {
            return undefined;
        }
        const io = new IntersectionObserver(
            (entries) => {
                for (const e of entries) {
                    if (e.isIntersecting) {
                        setRenderLimit((n) => {
                            if (n >= flat.length) return n;
                            return Math.min(n + RENDER_CHUNK, flat.length);
                        });
                    }
                }
            },
            { root, rootMargin: "120px 0px", threshold: 0 },
        );
        io.observe(target);
        return () => io.disconnect();
    }, [open, flat.length]);

    // Slice the groups to the current render window — we preserve group
    // boundaries, so we walk groups in order and fill until we've
    // emitted `renderLimit` rows. Empty groups are dropped from the
    // visible set. `visibleCount` is the actual number rendered (may
    // be slightly less than renderLimit if a group boundary lands
    // mid-chunk).
    const { visibleGroups, visibleCount } = useMemo(() => {
        const out: Array<{ label: string; items: MentionSuggestion[] }> = [];
        let remaining = renderLimit;
        for (const g of groups) {
            if (remaining <= 0) break;
            if (g.items.length <= remaining) {
                out.push(g);
                remaining -= g.items.length;
            } else {
                out.push({ label: g.label, items: g.items.slice(0, remaining) });
                remaining = 0;
            }
        }
        return { visibleGroups: out, visibleCount: renderLimit - remaining };
    }, [groups, renderLimit]);
    const hasMore = visibleCount < flat.length;

    if (!open || !anchor) return null;

    // Position: prefer below the caret, flip above if near viewport bottom.
    const POP_W = 340;
    const POP_H = 300;
    let top = anchor.bottom + 6;
    let left = anchor.left;
    if (anchor.bottom + POP_H + 16 > window.innerHeight) {
        top = anchor.top - POP_H - 6;
    }
    const maxLeft = window.innerWidth - POP_W - 12;
    if (left > maxLeft) left = Math.max(12, maxLeft);
    if (left < 12) left = 12;

    return (
        <div
            className="mention-pop"
            role="listbox"
            aria-label="Reference picker"
            style={{
                position: "fixed",
                top,
                left,
                width: POP_W,
                maxHeight: POP_H,
            }}
            // Prevent focus loss on mousedown — we want Enter/Tab to keep
            // working inside the textarea after click-acceptance.
            onMouseDown={(e) => e.preventDefault()}
        >
            <div className="mention-pop__header">
                <span>
                    {query
                        ? <>Results for <strong>@{query}</strong></>
                        : "Reference a workspace, item, or file"}
                </span>
                <span
                    className={"mention-pop__count" + (loading ? " is-loading" : "")}
                    aria-live="polite"
                >
                    {loading && (
                        <span
                            className="mention-pop__spinner"
                            aria-hidden="true"
                        />
                    )}
                    {flat.length === 0
                        ? (loading
                            ? (progress && progress.total > 0
                                ? `Indexing ${progress.indexed}/${progress.total}…`
                                : "Indexing…")
                            : "No matches")
                        : (loading && progress && progress.total > 0
                            ? `${flat.length}+ results · ${progress.indexed}/${progress.total}`
                            : `${flat.length}${loading ? "+" : ""} ${flat.length === 1 ? "result" : "results"}`)}
                </span>
            </div>

            <div
                className="mention-pop__list"
                ref={listRef}
                // Fallback for environments without IntersectionObserver
                // (very old browsers, some embedded iframes). Cheap
                // because it's throttled via `renderLimit` — once we've
                // rendered everything, further scrolls are no-ops.
                onScroll={(e) => {
                    if (typeof IntersectionObserver !== "undefined") return;
                    const el = e.currentTarget;
                    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 120) {
                        setRenderLimit((n) =>
                            n >= flat.length ? n : Math.min(n + RENDER_CHUNK, flat.length),
                        );
                    }
                }}
            >
                {flat.length === 0 ? (
                    <div className="mention-pop__empty">
                        No matches for <em>@{query}</em>.
                        <br />
                        <span style={{ fontSize: 11, opacity: 0.7 }}>
                            Try a workspace or item name.
                        </span>
                    </div>
                ) : (() => {
                    let running = 0;
                    return (
                        <>
                            {visibleGroups.map((g, gi) => (
                                <div key={gi} className="mention-pop__group">
                                    <div className="mention-pop__group-label">{g.label}</div>
                                    {g.items.map((s) => {
                                        const idx = running++;
                                        const isActive = idx === active;
                                        return (
                                            <div
                                                key={s.id}
                                                role="option"
                                                aria-selected={isActive}
                                                data-mention-idx={idx}
                                                className={
                                                    "mention-pop__item" +
                                                    (isActive ? " is-active" : "") +
                                                    ` mention-pop__item--${s.kind}`
                                                }
                                                onMouseEnter={() => setActive(idx)}
                                                onClick={(e) => {
                                                    e.preventDefault();
                                                    onAccept(s);
                                                }}
                                            >
                                                <span className="mention-pop__icon">
                                                    <KindIcon kind={s.kind} />
                                                </span>
                                                <span className="mention-pop__main">
                                                    <span className="mention-pop__name">
                                                        {highlightMatch(s.name, query)}
                                                    </span>
                                                    {s.meta && (
                                                        <span className="mention-pop__meta">{s.meta}</span>
                                                    )}
                                                </span>
                                                {isActive && (
                                                    <span className="mention-pop__kbd">↵</span>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            ))}
                            {/* Sentinel + "load more" indicator. Stays in
                                the DOM only while there are more rows to
                                reveal — IntersectionObserver above turns
                                it into implicit pagination. */}
                            {hasMore && (
                                <div
                                    ref={sentinelRef}
                                    className="mention-pop__more"
                                    aria-hidden="true"
                                >
                                    Loading more… ({flat.length - visibleCount} remaining)
                                </div>
                            )}
                        </>
                    );
                })()}
            </div>

            <div className="mention-pop__footer">
                <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
                <span><kbd>Tab</kbd> / <kbd>↵</kbd> select</span>
                <span><kbd>Esc</kbd> close</span>
            </div>
        </div>
    );
};

// ── Caret-rect helper ──────────────────────────────────────────────────
// Computing the caret position of a <textarea> requires mirroring the
// textarea into an off-screen element. This is the same technique used by
// Slack, Linear, Notion, etc. It handles line wraps, scroll offset, and
// font metrics without depending on input events.
const MIRROR_COPY_PROPS = [
    "direction", "boxSizing",
    "width", "height",
    "overflowX", "overflowY",
    "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
    "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "fontStyle", "fontVariant", "fontWeight", "fontStretch", "fontSize",
    "fontSizeAdjust", "lineHeight", "fontFamily",
    "textAlign", "textTransform", "textIndent", "textDecoration",
    "letterSpacing", "wordSpacing",
    "tabSize", "MozTabSize",
] as const;

/** Compute viewport-space caret rect for an HTMLTextAreaElement at
 *  `position` (character index). Returns { top, left, bottom } in px. */
export function getCaretRect(
    el: HTMLTextAreaElement,
    position: number,
): { top: number; left: number; bottom: number } {
    const doc = el.ownerDocument;
    const div = doc.createElement("div");
    const style = div.style;
    const computed = window.getComputedStyle(el);

    style.position = "absolute";
    style.visibility = "hidden";
    style.whiteSpace = "pre-wrap";
    style.wordWrap = "break-word";
    style.top = "0";
    style.left = "0";

    for (const prop of MIRROR_COPY_PROPS) {
        // @ts-expect-error index signature — we know these are valid
        style[prop] = computed[prop];
    }

    div.textContent = el.value.substring(0, position);
    const span = doc.createElement("span");
    // A zero-width char renders a visible box in most engines (via the
    // span's layout) and also becomes the caret's anchor. "\u200b" works
    // but some engines collapse trailing whitespace — use "|" and measure
    // leading edge instead.
    span.textContent = el.value.substring(position) || ".";
    div.appendChild(span);
    doc.body.appendChild(div);

    const elRect = el.getBoundingClientRect();
    const spanTopInMirror = span.offsetTop;
    const spanLeftInMirror = span.offsetLeft;
    const lineHeight = parseInt(computed.lineHeight, 10) || 18;

    doc.body.removeChild(div);

    const top = elRect.top + spanTopInMirror - el.scrollTop;
    const left = elRect.left + spanLeftInMirror - el.scrollLeft;
    return { top, left, bottom: top + lineHeight };
}
