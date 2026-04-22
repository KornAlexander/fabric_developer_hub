import React, { useMemo, useState } from "react";
import {
    BuildingFactory20Filled,
    ShieldCheckmark20Filled,
    DataPie20Filled,
    DataHistogram20Filled,
    Person20Filled,
    Flash20Filled,
} from "@fluentui/react-icons";
import type { Team, TeamEdge, TeamNode, TeamPattern } from "../plan/types";

/**
 * Pixel-faithful orchestration canvas ported from
 * ``Design/agent-ux/prototypes/03-mission-control``.
 *
 * The canvas is a fixed 740-wide virtual drawing area (responsive-scaled
 * by CSS). Nodes are real DOM elements (focusable, accessible, hoverable)
 * placed absolutely on top of an SVG edge layer.
 *
 * Supports every orchestration pattern the product ships:
 *   - supervisor  : one lead, N workers (delegate edges + peer handoffs)
 *   - sequential  : pipeline A → B → C → D
 *   - network     : peer mesh, no lead
 *   - hierarchical: lead → sub-leads → workers
 *   - solo        : single centred node
 *   - mixed       : supervisor layout fallback
 */

/** Default virtual-canvas width used when the caller doesn't measure
 *  its container. The caller (Step2View) drives layout using the
 *  ``canvasWidth`` prop so the graph spreads to the card's true inner
 *  width rather than a fixed virtual canvas. */
const CANVAS_W_DEFAULT = 860;
/** Node + spacing constants. These govern the **natural** size of the
 *  graph: when the container is narrower, the outer wrapper applies a
 *  CSS transform to zoom-out uniformly rather than collapsing spacing. */
const NODE_W = 208;
const NODE_W_NARROW = 184;
/** Routing height used for edge anchor math — matches a node *without*
 *  skills (head + state only). Visual height is larger when a node
 *  renders skills; use ``NODE_CONTENT_H`` to budget canvas height. */
const NODE_H = 112;
/** Budgeted visual height for a node with a head, skills row, and
 *  state footer. Used solely for canvas.height so the card doesn't
 *  leave a dead strip of grid below the last row. */
const NODE_CONTENT_H = 188;
/** Minimum horizontal gap between neighbouring nodes so edges breathe. */
const MIN_GAP_X = 64;
/** Vertical row separation (orchestrator → workers). Mirrors the
 *  design mockup — generous enough for the edge curves to breathe. */
const ROW_GAP_Y = 140;
/** Horizontal padding reserved inside the virtual canvas. */
const CANVAS_PAD_X = 64;
/** Natural canvas width floor — small teams still get a sensible card
 *  footprint rather than a pill-shaped sliver. */
const NATURAL_W_FLOOR = 640;

export interface OrchCanvasProps {
    team: Team;
    activeAgentId?: string | null;
    showLegend?: boolean;
    compact?: boolean;
    /** Virtual canvas width. Caller (Step2View) measures the container
     *  and passes the clamped width so the layout adapts to the card,
     *  rather than always baking to 740 and relying on a CSS scale. */
    canvasWidth?: number;
}

export function agentKind(agent: string, id: string): string {
    const a = (agent + " " + id).toLowerCase();
    if (id === "orchestrator" || a.includes("orchestr") || a.includes("supervisor") || a.includes("lead")) return "orch";
    if (a.includes("fabricdataengineer") || a.includes("dataengineer") || a.includes("ingest") || a.includes("spark") || a.includes("notebook") || a.includes("pipeline") || a.includes("lakehouse") || a.includes("warehouse")) return "fde";
    if (a.includes("admin") || a.includes("govern") || a.includes("security") || a.includes("policy")) return "admin";
    if (a.includes("report") || a.includes("powerbi") || a.includes("dashboard") || a.includes("semantic")) return "reporter";
    if (a.includes("realtime") || a.includes("kusto") || a.includes("stream") || a.includes("event")) return "rt";
    return "generic";
}

export function agentIcon(kind: string): React.ReactElement {
    switch (kind) {
        case "orch":     return <Flash20Filled />;
        case "fde":      return <BuildingFactory20Filled />;
        case "admin":    return <ShieldCheckmark20Filled />;
        case "reporter": return <DataHistogram20Filled />;
        case "rt":       return <DataPie20Filled />;
        default:         return <Person20Filled />;
    }
}

/** Domain acronyms that must stay uppercase when we sentence-case
 *  an all-uppercase role label from the backend. */
const ROLE_ACRONYMS = new Set([
    "RLS", "SQL", "API", "DAX", "KQL", "ETL", "ELT", "OLAP", "AI", "ML",
    "BI", "RAG", "PII", "GDPR", "MLOps", "UI", "UX", "ID", "IDs", "URL",
    "URLs", "JSON", "YAML", "CI", "CD",
]);

/**
 * Normalise a role label for display. Backend sends roles like
 * ``"HANDLES DATA INGESTION AND INITIAL STAGING."`` (uppercase,
 * trailing period). The old CSS force-uppercased + line-clamped the
 * text which looked fine for short labels but turned every sentence
 * into a shouting truncation. We now convert to sentence case and
 * let the node grow to fit so the reviewer can read the full "why
 * this agent?" payload. Acronyms in ``ROLE_ACRONYMS`` stay upper.
 */
export function formatRole(role: string): string {
    if (!role) return role;
    // Only sentence-case if the input is ALL CAPS (with optional
    // trailing punctuation); otherwise assume the backend already
    // provided a nicely-cased string and leave it alone.
    const letters = role.replace(/[^A-Za-z]/g, "");
    if (letters.length === 0) return role;
    if (letters !== letters.toUpperCase()) return role;
    const words = role.toLowerCase().split(/(\s+|[-/])/);
    const result = words
        .map((w, i) => {
            const up = w.toUpperCase();
            if (ROLE_ACRONYMS.has(up)) return up;
            if (/^\s+$/.test(w) || w === "-" || w === "/") return w;
            // Capitalize first word only (sentence case).
            if (i === 0 && w.length > 0) {
                return w.charAt(0).toUpperCase() + w.slice(1);
            }
            return w;
        })
        .join("");
    // Preserve any trailing punctuation that survived the split —
    // .split with a capturing group keeps them as-is.
    return result;
}

interface NodePos { x: number; y: number; w: number; }

/**
 * Compute the **natural** (design-faithful) width for a team's layout.
 *
 * Callers (Step2View) use this to size their container and decide
 * whether to CSS-scale the canvas down. The key insight vs. the old
 * code: layout is always done at natural size with full breathing
 * room — we never squash. Narrow viewports get a uniform zoom-out
 * instead, which preserves edge curvature + spacing.
 */
export function naturalCanvasWidth(team: Team): number {
    return naturalCanvasSize(team).width;
}

/** Returns both natural width and height so Step2View can reserve the
 *  right amount of vertical space when scaling the canvas down. */
export function naturalCanvasSize(team: Team): { width: number; height: number } {
    const w = _naturalWidthInner(team);
    const { height } = layoutFor(team, w);
    return { width: w, height };
}

function _naturalWidthInner(team: Team): number {
    const pattern: TeamPattern = team.pattern || "supervisor";
    const n = team.nodes.length;
    if (n === 0) return NATURAL_W_FLOOR;

    const rowWidth = (count: number, w = NODE_W) =>
        2 * CANVAS_PAD_X + count * w + Math.max(0, count - 1) * MIN_GAP_X;

    if (pattern === "solo" || n === 1) return NATURAL_W_FLOOR;

    if (pattern === "sequential") {
        // Sequential is a single horizontal pipeline. Use narrow nodes
        // to match the design; let it grow as wide as it needs.
        return Math.max(NATURAL_W_FLOOR, rowWidth(n, NODE_W_NARROW));
    }

    if (pattern === "network") {
        // Circular layout — radius grows with node count.
        const r = Math.min(240, 120 + 18 * n);
        return Math.max(NATURAL_W_FLOOR, 2 * r + NODE_W + 80);
    }

    if (pattern === "hierarchical") {
        // Widest row wins: typically the sub-leads row.
        const orchestrator = team.nodes.find((x) => x.id === "orchestrator") || team.nodes[0];
        const subleadIds = new Set<string>();
        for (const e of team.edges) {
            if (e.from === orchestrator.id && e.kind === "delegate") subleadIds.add(e.to);
        }
        const workersCount = n - 1 - subleadIds.size;
        const widest = Math.max(subleadIds.size, workersCount, 1);
        return Math.max(NATURAL_W_FLOOR, rowWidth(widest));
    }

    // supervisor + mixed: one row of (n-1) workers.
    const workers = n - 1;
    return Math.max(NATURAL_W_FLOOR, rowWidth(workers));
}

function layoutFor(team: Team, canvasW: number): { positions: Map<string, NodePos>; height: number; canvasClass?: string } {
    const pattern: TeamPattern = team.pattern || "supervisor";
    const nodes = team.nodes;
    const out = new Map<string, NodePos>();
    if (nodes.length === 0) return { positions: out, height: 420 };

    if (pattern === "solo" || nodes.length === 1) {
        out.set(nodes[0].id, { x: (canvasW - NODE_W) / 2, y: 140, w: NODE_W });
        return { positions: out, height: 340 };
    }

    if (pattern === "sequential") {
        const n = nodes.length;
        const rowW = n * NODE_W_NARROW + (n - 1) * MIN_GAP_X;
        const startX = (canvasW - rowW) / 2;
        nodes.forEach((node, i) => {
            out.set(node.id, {
                x: startX + i * (NODE_W_NARROW + MIN_GAP_X),
                y: 40,
                w: NODE_W_NARROW,
            });
        });
        return { positions: out, height: 40 + NODE_CONTENT_H + 24, canvasClass: "mc-canvas--sequential" };
    }

    if (pattern === "network") {
        const cx = canvasW / 2;
        const r = Math.min(240, 120 + 18 * nodes.length);
        const cy = r + 70;
        nodes.forEach((node, i) => {
            const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
            out.set(node.id, {
                x: cx + r * Math.cos(angle) - NODE_W / 2,
                y: cy + r * Math.sin(angle) - NODE_H / 2,
                w: NODE_W,
            });
        });
        return { positions: out, height: Math.max(480, 2 * r + 140) };
    }

    if (pattern === "hierarchical") {
        const orchestrator = nodes.find((n) => n.id === "orchestrator") || nodes[0];
        const subleadIds = new Set<string>();
        const workerParent = new Map<string, string>();
        for (const e of team.edges) {
            if (e.from === orchestrator.id && e.kind === "delegate") subleadIds.add(e.to);
        }
        for (const e of team.edges) {
            if (subleadIds.has(e.from) && e.kind === "delegate") workerParent.set(e.to, e.from);
        }
        const subleads = nodes.filter((n) => subleadIds.has(n.id));
        const workers = nodes.filter(
            (n) => n.id !== orchestrator.id && !subleadIds.has(n.id),
        );

        // Rows must be separated by the real visual node height
        // (``NODE_CONTENT_H`` — head + skills + state footer), NOT by
        // ``NODE_H`` (the routing anchor height, which excludes the
        // skills and footer). Using NODE_H caused the third row to
        // overlap the second row by roughly 48px when the sub-lead
        // cards rendered skills.
        out.set(orchestrator.id, { x: (canvasW - NODE_W) / 2, y: 30, w: NODE_W });

        const subRowW = subleads.length * NODE_W + Math.max(0, subleads.length - 1) * MIN_GAP_X;
        const subStartX = (canvasW - subRowW) / 2;
        const subleadY = 30 + NODE_CONTENT_H + ROW_GAP_Y;
        subleads.forEach((s, i) => {
            out.set(s.id, {
                x: subStartX + i * (NODE_W + MIN_GAP_X),
                y: subleadY,
                w: NODE_W,
            });
        });

        const workerY = subleadY + NODE_CONTENT_H + ROW_GAP_Y;
        workers.forEach((w) => {
            const parent = workerParent.get(w.id);
            const parentPos = parent ? out.get(parent) : undefined;
            const x = parentPos ? parentPos.x : (canvasW - NODE_W) / 2;
            out.set(w.id, { x, y: workerY, w: NODE_W });
        });

        return { positions: out, height: workerY + NODE_CONTENT_H + 24 };
    }

    // supervisor + mixed — design-faithful layout:
    // Orchestrator centered at top, workers spread evenly in one row
    // below with generous gaps. No wrapping — let the canvas be as
    // wide as it needs; the wrapper handles zoom-to-fit.
    const orchestrator = nodes.find((n) => n.id === "orchestrator") || nodes[0];
    const workers = nodes.filter((n) => n.id !== orchestrator.id);
    out.set(orchestrator.id, { x: (canvasW - NODE_W) / 2, y: 30, w: NODE_W });
    if (workers.length === 0) return { positions: out, height: 200 };

    const rowW = workers.length * NODE_W + (workers.length - 1) * MIN_GAP_X;
    const startX = (canvasW - rowW) / 2;
    const workerY = 30 + NODE_H + ROW_GAP_Y;
    workers.forEach((worker, i) => {
        out.set(worker.id, {
            x: startX + i * (NODE_W + MIN_GAP_X),
            y: workerY,
            w: NODE_W,
        });
    });
    return { positions: out, height: workerY + NODE_CONTENT_H + 24 };
}

function pathSmooth(from: NodePos, to: NodePos): string {
    const x1 = from.x + from.w / 2;
    const y1 = from.y + NODE_H;
    const x2 = to.x + to.w / 2;
    const y2 = to.y;
    if (Math.abs(x1 - x2) < 4) return `M ${x1} ${y1} L ${x2} ${y2}`;
    const midY = (y1 + y2) / 2;
    return `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
}

function pathHorizontal(from: NodePos, to: NodePos): string {
    const x1 = from.x + from.w;
    const y1 = from.y + NODE_H / 2;
    const x2 = to.x;
    const y2 = to.y + NODE_H / 2;
    return `M ${x1} ${y1} L ${x2} ${y2}`;
}

function pathPeer(from: NodePos, to: NodePos): string {
    const x1 = from.x + from.w / 2;
    const y1 = from.y + NODE_H / 2;
    const x2 = to.x + to.w / 2;
    const y2 = to.y + NODE_H / 2;
    return `M ${x1} ${y1} L ${x2} ${y2}`;
}

function stateFor(node: TeamNode, active: boolean): string {
    if (active) return "active";
    return node.status || "planned";
}

const STATE_LABEL: Record<string, string> = {
    planned: "Planned",
    active: "Running",
    done: "Done",
    waiting: "Waiting",
};

/**
 * Skills block on a node — shows ALL selected ("probably useful for
 * this task") skills as highlighted chips, and rolls every other
 * declared skill into the ``+N`` overflow popover. Greyed-out
 * available skills never render inline on the node; the popover is
 * the single place to browse "what else can this agent do".
 *
 * UX rationale: keeping the node focused on the chosen skills makes
 * the decision legible at a glance, and spares nodes from ballooning
 * on agents with large skill catalogs. The popover keeps the full
 * surface one click away, with selected items visually marked.
 */
function NodeSkills({
    skills,
    allSkills,
    nodeLabel,
}: {
    skills?: string[];
    allSkills?: string[];
    nodeLabel: string;
}) {
    const [open, setOpen] = useState(false);
    const selected = skills && skills.length ? skills : [];
    // Full ordered list: selected first, then remaining allSkills.
    // Only used inside the popover.
    const full = useMemo(() => {
        const seen = new Set<string>();
        const out: string[] = [];
        for (const s of selected) { if (!seen.has(s)) { seen.add(s); out.push(s); } }
        for (const s of (allSkills || [])) { if (!seen.has(s)) { seen.add(s); out.push(s); } }
        return out;
    }, [selected, allSkills]);
    if (full.length === 0) return null;
    const overflow = full.length - selected.length;

    return (
        <div className="mc-node__skills">
            {selected.map((s) => (
                <span
                    key={`sel-${s}`}
                    className="mc-node__skill mc-node__skill--selected"
                    title={`${s} — chosen for this task`}
                >
                    {s}
                </span>
            ))}
            {overflow > 0 && (
                <button
                    type="button"
                    className="mc-node__skill mc-node__skill--more"
                    aria-label={`${overflow} more skill${overflow === 1 ? "" : "s"} for ${nodeLabel}`}
                    aria-expanded={open}
                    onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
                    onBlur={() => setOpen(false)}
                >
                    +{overflow}
                    {open && (
                        <span className="mc-node__skill-pop" role="tooltip">
                            <span className="mc-node__skill-pop-title">
                                {nodeLabel} — all skills
                            </span>
                            <span className="mc-node__skill-pop-list">
                                {full.map((s, i) => (
                                    <span
                                        key={s}
                                        className="mc-node__skill-pop-item"
                                        data-selected={i < selected.length ? "true" : undefined}
                                    >
                                        {s}
                                    </span>
                                ))}
                            </span>
                            <span className="mc-node__skill-pop-hint">
                                Highlighted = chosen for this task
                            </span>
                        </span>
                    )}
                </button>
            )}
        </div>
    );
}

export function OrchCanvas({
    team,
    activeAgentId,
    showLegend = true,
    compact = false,
    canvasWidth,
}: OrchCanvasProps) {
    const effectiveW = Math.max(NATURAL_W_FLOOR, canvasWidth ?? CANVAS_W_DEFAULT);
    const { positions, height, canvasClass } = useMemo(
        () => layoutFor(team, effectiveW),
        [team, effectiveW],
    );
    const viewH = compact ? Math.min(420, height) : height;

    // Group edges by their unordered endpoint pair so a delegate + report
    // round-trip between the same two nodes collapses into a single
    // midpoint label ("delegates & reports") instead of two overlapping
    // pills. Labels are drawn as absolutely positioned HTML inside the
    // canvas so typography stays crisp regardless of the SVG's
    // preserveAspectRatio stretching.
    //
    // Fan-out dedupe: when one source has ≥2 children all sharing the
    // same relationship label (supervisor/hierarchical from orchestrator
    // to workers), we would otherwise render the same "delegates &
    // reports" pill on every edge. At 3+ fan-out those pills line up on
    // a single y-band and visibly overlap. To avoid the clutter we
    // render the label ONCE per (source, label) group, positioned on
    // the edge going to the child closest to the source's vertical
    // axis. The other parallel edges stay label-less — the arrow
    // direction + single pill communicate the relationship clearly.
    const edgeLabels = useMemo(() => {
        type Entry = {
            key: string;
            kinds: Set<string>;
            from: NodePos;
            to: NodePos;
            fromId: string;
            primaryKind: string;
        };
        const byPair = new Map<string, Entry>();
        for (const e of team.edges) {
            const from = positions.get(e.from);
            const to = positions.get(e.to);
            if (!from || !to) continue;
            // Unordered key so A→B and B→A (delegate + report) merge.
            const pair = [e.from, e.to].sort().join("↔");
            const existing = byPair.get(pair);
            if (existing) {
                existing.kinds.add(e.kind);
                continue;
            }
            byPair.set(pair, {
                key: pair,
                kinds: new Set([e.kind]),
                from,
                to,
                // Preserve the directional "from" for fan-out grouping;
                // if either endpoint is the orchestrator/lead, treat
                // that as the source for dedupe purposes.
                fromId: e.from,
                primaryKind: e.kind,
            });
        }
        const labelFor = (kinds: Set<string>): string => {
            const hasDelegate = kinds.has("delegate");
            const hasReport = kinds.has("report");
            const hasPeer = kinds.has("peer");
            // Sequential pipelines lay out left-to-right; the arrow
            // direction IS the label. Suppressing here avoids clunky
            // overlapping pills on the short between-node segments.
            if (team.pattern === "sequential") return "";
            if (hasDelegate && hasReport) return "delegates & reports";
            if (hasDelegate) return "delegates";
            if (hasReport) return "reports back";
            if (hasPeer) return "hands off";
            return "";
        };
        const classFor = (kinds: Set<string>): string => {
            if (kinds.has("peer")) return "peer";
            if (kinds.has("delegate") || kinds.has("report")) return "delegate";
            return "delegate";
        };
        const entries = [...byPair.values()].map((entry) => ({
            entry,
            text: labelFor(entry.kinds),
            klass: classFor(entry.kinds),
        })).filter((x) => x.text);

        // Group fan-outs with identical label coming from the same
        // source. A "source" for a delegate/report pair is the node
        // with the shallower (smaller y) position — typically the
        // orchestrator/sub-lead sitting on the row above.
        type Group = { sourceId: string; text: string; picks: typeof entries };
        const groupKey = (x: typeof entries[number]) => {
            const { from, to } = x.entry;
            const src = from.y <= to.y ? x.entry.fromId : (
                // pair is unordered; recover the "other" id from the key
                x.entry.key.split("↔").find((id) => id !== x.entry.fromId) || x.entry.fromId
            );
            return `${src}::${x.text}`;
        };
        const groups = new Map<string, typeof entries>();
        for (const x of entries) {
            const k = groupKey(x);
            const arr = groups.get(k);
            if (arr) arr.push(x);
            else groups.set(k, [x]);
        }

        return entries.map((x) => {
            const { entry } = x;
            const { from, to } = entry;
            // Midpoint along the same curve the SVG renders.
            let cx: number, cy: number;
            if (team.pattern === "sequential") {
                cx = (from.x + from.w + to.x) / 2;
                cy = from.y + NODE_H / 2;
            } else if (entry.kinds.has("peer") || team.pattern === "network") {
                cx = (from.x + from.w / 2 + to.x + to.w / 2) / 2;
                cy = (from.y + NODE_H / 2 + to.y + NODE_H / 2) / 2;
            } else {
                // pathSmooth anchors bottom-center of ``from`` to
                // top-center of ``to``; the geometric midpoint of that
                // cubic happens to fall at the midpoint of its chord.
                const x1 = from.x + from.w / 2;
                const y1 = from.y + NODE_H;
                const x2 = to.x + to.w / 2;
                const y2 = to.y;
                cx = (x1 + x2) / 2;
                cy = (y1 + y2) / 2;
            }

            // Fan-out suppression: keep the label only on the edge
            // whose target is closest to the source's centerline,
            // drop it on all other siblings in the group.
            const group = groups.get(groupKey(x)) || [];
            if (group.length > 1) {
                const srcIsFrom = from.y <= to.y;
                const srcCenter = (srcIsFrom ? from : to).x + NODE_W / 2;
                const distFor = (e: typeof x) => {
                    const t = srcIsFrom ? e.entry.to : e.entry.from;
                    return Math.abs(t.x + NODE_W / 2 - srcCenter);
                };
                const winner = group.reduce((best, curr) =>
                    distFor(curr) < distFor(best) ? curr : best,
                );
                if (winner !== x) {
                    return {
                        key: entry.key,
                        text: "",
                        cx,
                        cy,
                        kind: x.klass,
                    };
                }
            }

            return {
                key: entry.key,
                text: x.text,
                cx,
                cy,
                kind: classFor(entry.kinds),
            };
        }).filter(l => l.text);
    }, [team, positions]);

    return (
        <>
            <div
                className={`mc-canvas${canvasClass ? " " + canvasClass : ""}`}
                style={{ width: effectiveW, height: viewH }}
                role="img"
                aria-label={`Orchestration graph, ${team.pattern} pattern`}
            >
                <svg
                    className="mc-canvas__edges"
                    viewBox={`0 0 ${effectiveW} ${viewH}`}
                    preserveAspectRatio="none"
                    aria-hidden="true"
                >
                    {team.edges.map((e: TeamEdge, i: number) => {
                        const from = positions.get(e.from);
                        const to = positions.get(e.to);
                        if (!from || !to) return null;
                        let d: string;
                        if (team.pattern === "sequential") {
                            d = pathHorizontal(from, to);
                        } else if (e.kind === "peer" || team.pattern === "network") {
                            d = pathPeer(from, to);
                        } else {
                            d = pathSmooth(from, to);
                        }
                        const active =
                            !!activeAgentId &&
                            (e.from === activeAgentId || e.to === activeAgentId);
                        return (
                            <path
                                key={`e-${i}`}
                                className="mc-edge"
                                data-kind={e.kind}
                                data-active={active ? "true" : undefined}
                                d={d}
                            />
                        );
                    })}
                </svg>

                {team.nodes.map((n) => {
                    const pos = positions.get(n.id);
                    if (!pos) return null;
                    const kind = agentKind(n.agent, n.id);
                    const active = activeAgentId === n.id;
                    const state = stateFor(n, active);
                    return (
                        <div
                            key={n.id}
                            className="mc-node"
                            data-agent={kind}
                            data-state={state}
                            tabIndex={0}
                            style={{ left: pos.x, top: pos.y, width: pos.w }}
                        >
                            <div className="mc-node__head">
                                <span
                                    className="mc-node__icon"
                                    data-agent={kind}
                                    aria-hidden="true"
                                >
                                    {agentIcon(kind)}
                                </span>
                                <div style={{ minWidth: 0 }}>
                                    <div className="mc-node__title">{n.agent}</div>
                                    <div className="mc-node__role">{formatRole(n.role)}</div>
                                </div>
                            </div>
                            {n.id !== "orchestrator" && (n.skills?.length || n.allSkills?.length) ? (
                                <NodeSkills
                                    skills={n.skills}
                                    allSkills={n.allSkills}
                                    nodeLabel={n.agent}
                                />
                            ) : null}
                            {/* Pre-run (``planned``) adds no value here — the
                                dashed border + legend already communicate it.
                                Only render the state row once a node has an
                                actual runtime status worth calling out. */}
                            {state !== "planned" && (
                                <div className="mc-node__state">
                                    <span className="mc-node__state-dot" />
                                    {STATE_LABEL[state] || state}
                                </div>
                            )}
                        </div>
                    );
                })}

                {/* Edge labels — positioned at each connection's midpoint
                    so users can read what each line means without
                    hunting for a legend. Kept subtle (tiny uppercase
                    pill on white) so they never compete with the node
                    cards. */}
                {edgeLabels.map((lbl) => (
                    <div
                        key={lbl.key}
                        className={`mc-edge-label mc-edge-label--${lbl.kind}`}
                        style={{ left: lbl.cx, top: lbl.cy }}
                    >
                        {lbl.text}
                    </div>
                ))}
            </div>
            {/* No bottom legend anymore — edge labels explain the
                connections inline, and per-node state pills (see
                .mc-node__state) explain live status without forcing
                the user's eyes away from the graph. ``showLegend`` is
                kept in the prop signature for API stability but is no
                longer consulted. */}
        </>
    );
}
