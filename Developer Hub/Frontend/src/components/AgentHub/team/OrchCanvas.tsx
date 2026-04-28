import React, { useLayoutEffect, useMemo, useRef, useState } from "react";
import {
    BuildingFactory20Filled,
    ShieldCheckmark20Filled,
    DataPie20Filled,
    DataHistogram20Filled,
    Person20Filled,
    Flash20Filled,
} from "@fluentui/react-icons";
import type { Team, TeamEdge, TeamNode, TeamPattern } from "../plan/types";
import { visibleTeam } from "./teamVisibility";

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
const NODE_W_NARROW = 208;
/** Routing height for edge anchor math — node with skills (head + role
 *  + compacted skills row). This is deliberately close to the rendered
 *  card footprint so connectors start outside cards instead of through
 *  wrapped skill chips. For nodes without skills, use
 *  ``NODE_H_COMPACT``. */
const NODE_H = 156;
/** Routing height for a node without skills (head + role only).
 *  Matches the CSS: 12px pad + 32px icon + ~14px role gap/text +
 *  12px pad + 2px border ≈ 72px. */
const NODE_H_COMPACT = 72;
/** Budgeted visual height for a node with a head, skills row, and
 *  state footer. Used solely for row spacing and canvas.height so the
 *  card doesn't leave a dead strip of grid below the last row. */
const NODE_CONTENT_H = 176;
/** Minimum horizontal gap between neighbouring nodes so edges breathe. */
const MIN_GAP_X = 64;
/** Sequential edges carry inline labels in the horizontal gaps; use a
 *  wider gutter so those captions never touch adjacent cards. */
const SEQUENTIAL_GAP_X = 96;
/** Vertical row separation (orchestrator → workers). Mirrors the
 *  design mockup — generous enough for the edge curves to breathe. */
const ROW_GAP_Y = 140;
/** Horizontal padding reserved inside the virtual canvas. */
const CANVAS_PAD_X = 64;
/** Natural canvas width floor — small teams still get a sensible card
 *  footprint rather than a pill-shaped sliver. */
const NATURAL_W_FLOOR = 640;
/** Inline skill chips shown before the overflow popover takes over.
 *  Keeps graph nodes readable even when an agent template exposes a
 *  broad Fabric capability catalog. */
const VISIBLE_SKILL_LIMIT = 2;

const COMPACT_SKILL_LABELS: Record<string, string> = {
    "Warehouse / SQL authoring": "SQL authoring",
    "Warehouse / SQL consumption": "SQL consumption",
    "Spark consumption": "Spark read",
    "Eventhouse authoring": "Eventhouse",
    "Eventhouse consumption": "Eventhouse read",
    "Power BI consumption": "Power BI read",
    "End-to-end Medallion": "Medallion",
    "Paginated report authoring": "Report authoring",
    "Paginated report ops": "Report ops",
    "Fabric API grounding": "API grounding",
};

function compactSkillLabel(skill: string): string {
    return COMPACT_SKILL_LABELS[skill] || skill;
}

export interface OrchCanvasProps {
    team: Team;
    activeAgentId?: string | null;
    showLegend?: boolean;
    compact?: boolean;
    /** Virtual canvas width. Caller (Step2View) measures the container
     *  and passes the clamped width so the layout adapts to the card,
     *  rather than always baking to 740 and relying on a CSS scale. */
    canvasWidth?: number;
    /** Hover/focus callbacks so the parent can cross-highlight a
     *  matching role card in the sidebar. Pass ``null`` on leave/blur
     *  to clear the hover. Click is intentionally not wired — the
     *  canvas-to-sidebar highlight is a transient "what is this?"
     *  affordance, not a selection. */
    onNodeHover?: (agentId: string | null) => void;
}

export function agentKind(agent: string, id: string): string {
    const a = (agent + " " + id).toLowerCase();
    if (id === "orchestrator" || a.includes("orchestr") || a.includes("supervisor") || (a.includes("lead") && !a.includes("sub"))) return "orch";
    if (a.includes("fabricdataengineer") || a.includes("dataengineer") || a.includes("ingest") || a.includes("spark") || a.includes("notebook") || a.includes("pipeline") || a.includes("lakehouse") || a.includes("warehouse")) return "fde";
    if (a.includes("admin") || a.includes("govern") || a.includes("security") || a.includes("policy") || a.includes("architect")) return "admin";
    if (a.includes("report") || a.includes("powerbi") || a.includes("dashboard") || a.includes("semantic") || a.includes("modeler") || a.includes("creator")) return "reporter";
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

interface NodePos { x: number; y: number; w: number; h: number; }

/** Pick the routing height for a node based on whether it renders
 *  a skills row. Keeps edge anchors flush with the card bottom. */
function nodeH(node: TeamNode, measuredHeights?: Map<string, number>): number {
    const measured = measuredHeights?.get(node.id);
    if (measured && Number.isFinite(measured)) return measured;
    return (node.skills?.length || node.allSkills?.length) ? NODE_H : NODE_H_COMPACT;
}

function maxNodeH(nodes: TeamNode[], measuredHeights?: Map<string, number>): number {
    return nodes.reduce((max, node) => Math.max(max, nodeH(node, measuredHeights)), 0);
}

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
    const displayTeam = visibleTeam(team);
    const w = _naturalWidthInner(displayTeam);
    const { height } = layoutFor(displayTeam, w);
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
        return Math.max(
            NATURAL_W_FLOOR,
            2 * CANVAS_PAD_X + n * NODE_W_NARROW + Math.max(0, n - 1) * SEQUENTIAL_GAP_X,
        );
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

    if (pattern === "mixed") {
        const orchestrator = team.nodes.find((x) => x.id === "orchestrator") || team.nodes[0];
        const directChildIds = new Set<string>();
        const nestedChildIds = new Set<string>();
        for (const e of team.edges) {
            if (e.kind !== "delegate") continue;
            if (e.from === orchestrator.id) directChildIds.add(e.to);
            else nestedChildIds.add(e.to);
        }
        if (nestedChildIds.size > 0) {
            const directCount = Math.max(directChildIds.size, 1);
            const nestedCount = Math.max(nestedChildIds.size, n - 1 - directChildIds.size, 1);
            return Math.max(NATURAL_W_FLOOR, rowWidth(Math.max(directCount, nestedCount)));
        }
    }

    // supervisor + mixed: one row of (n-1) workers.
    const workers = n - 1;
    return Math.max(NATURAL_W_FLOOR, rowWidth(workers));
}

function layoutFor(team: Team, canvasW: number, measuredHeights?: Map<string, number>): { positions: Map<string, NodePos>; height: number; canvasClass?: string } {
    const pattern: TeamPattern = team.pattern || "supervisor";
    const nodes = team.nodes;
    const out = new Map<string, NodePos>();
    if (nodes.length === 0) return { positions: out, height: 420 };

    if (pattern === "solo" || nodes.length === 1) {
        out.set(nodes[0].id, { x: (canvasW - NODE_W) / 2, y: 140, w: NODE_W, h: nodeH(nodes[0], measuredHeights) });
        return { positions: out, height: 340 };
    }

    if (pattern === "sequential") {
        const n = nodes.length;
        const rowW = n * NODE_W_NARROW + (n - 1) * SEQUENTIAL_GAP_X;
        const startX = (canvasW - rowW) / 2;
        nodes.forEach((node, i) => {
            out.set(node.id, {
                x: startX + i * (NODE_W_NARROW + SEQUENTIAL_GAP_X),
                y: 40,
                w: NODE_W_NARROW,
                h: nodeH(node, measuredHeights),
            });
        });
        return { positions: out, height: 40 + Math.max(NODE_CONTENT_H, maxNodeH(nodes, measuredHeights)) + 24, canvasClass: "mc-canvas--sequential" };
    }

    if (pattern === "network") {
        const cx = canvasW / 2;
        const r = Math.min(240, 120 + 18 * nodes.length);
        const maxMeasuredH = Math.max(NODE_H_COMPACT, maxNodeH(nodes, measuredHeights));
        const cy = r + maxMeasuredH / 2 + 32;
        let maxBottom = 0;
        nodes.forEach((node, i) => {
            const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
            const nh = nodeH(node, measuredHeights);
            const y = cy + r * Math.sin(angle) - nh / 2;
            maxBottom = Math.max(maxBottom, y + nh);
            out.set(node.id, {
                x: cx + r * Math.cos(angle) - NODE_W / 2,
                y,
                w: NODE_W,
                h: nh,
            });
        });
        return { positions: out, height: Math.max(480, maxBottom + 32) };
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
        out.set(orchestrator.id, { x: (canvasW - NODE_W) / 2, y: 30, w: NODE_W, h: nodeH(orchestrator, measuredHeights) });

        const subRowW = subleads.length * NODE_W + Math.max(0, subleads.length - 1) * MIN_GAP_X;
        const subStartX = (canvasW - subRowW) / 2;
        const subleadY = 30 + Math.max(NODE_CONTENT_H, nodeH(orchestrator, measuredHeights)) + ROW_GAP_Y;
        subleads.forEach((s, i) => {
            out.set(s.id, {
                x: subStartX + i * (NODE_W + MIN_GAP_X),
                y: subleadY,
                w: NODE_W,
                h: nodeH(s, measuredHeights),
            });
        });

        const workerY = subleadY + Math.max(NODE_CONTENT_H, maxNodeH(subleads, measuredHeights)) + ROW_GAP_Y;
        const groupedWorkers = [
            ...subleads.flatMap((s) => workers.filter((w) => workerParent.get(w.id) === s.id)),
            ...workers.filter((w) => !workerParent.has(w.id)),
        ];
        const workerRowW = groupedWorkers.length * NODE_W + Math.max(0, groupedWorkers.length - 1) * MIN_GAP_X;
        const workerStartX = (canvasW - workerRowW) / 2;
        groupedWorkers.forEach((w, i) => {
            out.set(w.id, {
                x: workerStartX + i * (NODE_W + MIN_GAP_X),
                y: workerY,
                w: NODE_W,
                h: nodeH(w, measuredHeights),
            });
        });

        return { positions: out, height: workerY + Math.max(NODE_CONTENT_H, maxNodeH(groupedWorkers, measuredHeights)) + 24 };
    }

    if (pattern === "mixed") {
        const orchestrator = nodes.find((n) => n.id === "orchestrator") || nodes[0];
        const directChildIds = new Set<string>();
        const parentByChild = new Map<string, string>();
        for (const e of team.edges) {
            if (e.kind !== "delegate") continue;
            if (e.from === orchestrator.id) directChildIds.add(e.to);
            else parentByChild.set(e.to, e.from);
        }

        if (parentByChild.size > 0) {
            const directChildren = nodes.filter((n) => n.id !== orchestrator.id && directChildIds.has(n.id));
            const nestedChildren = [
                ...directChildren.flatMap((p) => nodes.filter((n) => parentByChild.get(n.id) === p.id)),
                ...nodes.filter((n) => n.id !== orchestrator.id && !directChildIds.has(n.id) && !parentByChild.has(n.id)),
            ];
            const topY = 30;
            out.set(orchestrator.id, { x: (canvasW - NODE_W) / 2, y: topY, w: NODE_W, h: nodeH(orchestrator, measuredHeights) });

            const subleadY = topY + Math.max(NODE_CONTENT_H, nodeH(orchestrator, measuredHeights)) + ROW_GAP_Y;
            const directRowW = directChildren.length * NODE_W + Math.max(0, directChildren.length - 1) * MIN_GAP_X;
            const directStartX = (canvasW - directRowW) / 2;
            directChildren.forEach((child, i) => {
                out.set(child.id, {
                    x: directStartX + i * (NODE_W + MIN_GAP_X),
                    y: subleadY,
                    w: NODE_W,
                    h: nodeH(child, measuredHeights),
                });
            });

            const nestedY = subleadY + Math.max(NODE_CONTENT_H, maxNodeH(directChildren, measuredHeights)) + ROW_GAP_Y;
            const nestedRowW = nestedChildren.length * NODE_W + Math.max(0, nestedChildren.length - 1) * MIN_GAP_X;
            const nestedStartX = (canvasW - nestedRowW) / 2;
            nestedChildren.forEach((child, i) => {
                out.set(child.id, {
                    x: nestedStartX + i * (NODE_W + MIN_GAP_X),
                    y: nestedY,
                    w: NODE_W,
                    h: nodeH(child, measuredHeights),
                });
            });

            return { positions: out, height: nestedY + Math.max(NODE_CONTENT_H, maxNodeH(nestedChildren, measuredHeights)) + 24 };
        }
    }

    // supervisor + mixed — design-faithful layout:
    // Orchestrator centered at top, workers spread evenly in one row
    // below with generous gaps. No wrapping — let the canvas be as
    // wide as it needs; the wrapper handles zoom-to-fit.
    const orchestrator = nodes.find((n) => n.id === "orchestrator") || nodes[0];
    const workers = nodes.filter((n) => n.id !== orchestrator.id);
    const orchH = nodeH(orchestrator, measuredHeights);
    out.set(orchestrator.id, { x: (canvasW - NODE_W) / 2, y: 30, w: NODE_W, h: orchH });
    if (workers.length === 0) return { positions: out, height: 200 };

    const rowW = workers.length * NODE_W + (workers.length - 1) * MIN_GAP_X;
    const startX = (canvasW - rowW) / 2;
    const workerY = 30 + orchH + ROW_GAP_Y;
    workers.forEach((worker, i) => {
        out.set(worker.id, {
            x: startX + i * (NODE_W + MIN_GAP_X),
            y: workerY,
            w: NODE_W,
            h: nodeH(worker, measuredHeights),
        });
    });
    return { positions: out, height: workerY + Math.max(NODE_CONTENT_H, maxNodeH(workers, measuredHeights)) + 24 };
}

function pathSmooth(from: NodePos, to: NodePos): string {
    const x1 = from.x + from.w / 2;
    const y1 = from.y + from.h;
    const x2 = to.x + to.w / 2;
    const y2 = to.y;
    if (Math.abs(x1 - x2) < 4) return `M ${x1} ${y1} L ${x2} ${y2}`;
    const midY = (y1 + y2) / 2;
    return `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
}

function pathHorizontal(from: NodePos, to: NodePos): string {
    const leftToRight = from.x <= to.x;
    const x1 = leftToRight ? from.x + from.w : from.x;
    const y1 = from.y + from.h / 2;
    const x2 = leftToRight ? to.x : to.x + to.w;
    const y2 = to.y + to.h / 2;
    return `M ${x1} ${y1} L ${x2} ${y2}`;
}

function pathPeer(from: NodePos, to: NodePos): string {
    const x1 = from.x + from.w / 2;
    const y1 = from.y + from.h / 2;
    const x2 = to.x + to.w / 2;
    const y2 = to.y + to.h / 2;
    return `M ${x1} ${y1} L ${x2} ${y2}`;
}

function stateFor(node: TeamNode, active: boolean): string {
    if (node.lifecycle) {
        if (active && node.lifecycle !== "finished" && node.lifecycle !== "failed") {
            return "running";
        }
        return node.lifecycle;
    }
    if (active) return "running";
    if (node.status === "active") return "running";
    if (node.status === "done") return "finished";
    if (node.status === "failed") return "failed";
    if (node.status === "waiting") return "waiting";
    return "planned";
}

const STATE_LABEL: Record<string, string> = {
    planned: "Planned",
    spinning_up: "Spinning up",
    waiting: "Waiting",
    running: "Running",
    finished: "Finished",
    failed: "Failed",
    active: "Running",
    done: "Finished",
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
    const visibleSelected = selected.slice(0, VISIBLE_SKILL_LIMIT);
    const selectedSet = new Set(selected);
    const overflow = full.length - visibleSelected.length;

    return (
        <div className="mc-node__skills">
            {visibleSelected.map((s) => (
                <span
                    key={`sel-${s}`}
                    className="mc-node__skill mc-node__skill--selected"
                    title={`${s} — chosen for this task`}
                >
                    {compactSkillLabel(s)}
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
                                {full.map((s) => (
                                    <span
                                        key={s}
                                        className="mc-node__skill-pop-item"
                                        data-selected={selectedSet.has(s) ? "true" : undefined}
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
    onNodeHover,
}: OrchCanvasProps) {
    const displayTeam = useMemo(() => visibleTeam(team), [team]);
    const effectiveW = Math.max(NATURAL_W_FLOOR, canvasWidth ?? CANVAS_W_DEFAULT);
    const nodeRefs = useRef(new Map<string, HTMLDivElement>());
    const [measuredHeights, setMeasuredHeights] = useState<Map<string, number>>(() => new Map());
    const { positions, height, canvasClass } = useMemo(
        () => layoutFor(displayTeam, effectiveW, measuredHeights),
        [displayTeam, effectiveW, measuredHeights],
    );
    const viewH = compact ? Math.min(420, height) : height;

    useLayoutEffect(() => {
        const next = new Map<string, number>();
        for (const node of displayTeam.nodes) {
            const element = nodeRefs.current.get(node.id);
            if (!element) continue;
            next.set(node.id, Math.ceil(element.offsetHeight));
        }

        let changed = next.size !== measuredHeights.size;
        if (!changed) {
            for (const [id, heightValue] of next) {
                if (Math.abs((measuredHeights.get(id) || 0) - heightValue) > 1) {
                    changed = true;
                    break;
                }
            }
        }

        if (changed) setMeasuredHeights(next);
    }, [displayTeam.nodes, effectiveW, measuredHeights]);

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
            fromNodeId: string;
            toNodeId: string;
            fromId: string;
            primaryKind: string;
        };
        const byPair = new Map<string, Entry>();
        for (const e of displayTeam.edges) {
            const from = positions.get(e.from);
            const to = positions.get(e.to);
            if (!from || !to) continue;
            // Unordered key so A→B and B→A (delegate + report) merge.
            const pair = [e.from, e.to].sort().join("↔");
            const existing = byPair.get(pair);
            if (existing) {
                existing.kinds.add(e.kind);
                if (e.kind === "delegate" && existing.primaryKind !== "delegate") {
                    existing.from = from;
                    existing.to = to;
                    existing.fromNodeId = e.from;
                    existing.toNodeId = e.to;
                    existing.fromId = e.from;
                    existing.primaryKind = e.kind;
                }
                continue;
            }
            byPair.set(pair, {
                key: pair,
                kinds: new Set([e.kind]),
                from,
                to,
                fromNodeId: e.from,
                toNodeId: e.to,
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
            if (hasDelegate && hasReport) return "delegates & reports";
            if (hasDelegate) return "delegates";
            if (hasReport) return "reports";
            if (hasPeer) return "hands off";
            if (kinds.has("handoff")) return "hands off";
            if (kinds.has("critique")) return "critiques";
            if (kinds.has("verify")) return "verifies";
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

        const labels = entries.map((x) => {
            const { entry } = x;
            const { from, to } = entry;
            // Midpoint along the same curve the SVG renders.
            let cx: number, cy: number;
            let sourceId = entry.fromId;
            let axisDistance = 0;
            const sameRow = Math.abs(from.y - to.y) < 20;
            if (displayTeam.pattern === "sequential") {
                cx = (from.x + from.w + to.x) / 2;
                cy = from.y + from.h / 2;
            } else if (sameRow && !entry.kinds.has("peer") && displayTeam.pattern !== "network") {
                // Horizontal edge between same-row nodes — label in gap
                const lf = from.x < to.x ? from : to;
                const rt = from.x < to.x ? to : from;
                cx = (lf.x + lf.w + rt.x) / 2;
                cy = from.y + from.h / 2;
            } else if (entry.kinds.has("peer") || displayTeam.pattern === "network") {
                cx = (from.x + from.w / 2 + to.x + to.w / 2) / 2;
                cy = (from.y + from.h / 2 + to.y + to.h / 2) / 2;
            } else {
                const source = from.y <= to.y ? from : to;
                const target = from.y <= to.y ? to : from;
                sourceId = from.y <= to.y ? entry.fromNodeId : entry.toNodeId;
                axisDistance = Math.abs(
                    (target.x + target.w / 2) - (source.x + source.w / 2),
                );
                const sourceBottom = source.y + source.h;
                const targetTop = target.y;
                cx = (source.x + source.w / 2 + target.x + target.w / 2) / 2;
                cy = sourceBottom + (targetTop - sourceBottom) * 0.58;
            }

            return {
                key: entry.key,
                text: x.text,
                cx,
                cy,
                kind: classFor(entry.kinds),
                sourceId,
                axisDistance,
                orientation: sameRow || displayTeam.pattern === "sequential" ? "horizontal" : "vertical",
            };
        }).filter(l => l.text);

        if (displayTeam.pattern === "network") {
            const peerLabels = labels.filter((l) => l.kind === "peer");
            if (peerLabels.length > 1) {
                let cx = 0;
                let cy = 0;
                positions.forEach((p) => {
                    cx += p.x + p.w / 2;
                    cy += p.y + p.h / 2;
                });
                const n = Math.max(1, positions.size);
                return [
                    ...labels.filter((l) => l.kind !== "peer"),
                    {
                        key: "network-peer-label",
                        text: "hands off",
                        cx: cx / n,
                        cy: cy / n,
                        kind: "peer",
                        orientation: "network",
                    },
                ];
            }
        }

        const singles: typeof labels = [];
        const fanoutGroups = new Map<string, typeof labels>();
        for (const label of labels) {
            const isFanoutLabel =
                label.kind === "delegate"
                && displayTeam.pattern !== "sequential"
                && !!label.sourceId;
            if (!isFanoutLabel) {
                singles.push(label);
                continue;
            }
            const key = `${label.sourceId}|${label.kind}|${label.text}`;
            const group = fanoutGroups.get(key);
            if (group) group.push(label);
            else fanoutGroups.set(key, [label]);
        }

        fanoutGroups.forEach((group) => {
            if (group.length <= 1) {
                singles.push(...group);
                return;
            }
            singles.push([...group].sort((a, b) => a.axisDistance - b.axisDistance)[0]);
        });

        return singles;
    }, [displayTeam, positions]);

    return (
        <>
            <div
                className={`mc-canvas${canvasClass ? " " + canvasClass : ""}`}
                style={{ width: effectiveW, height: viewH }}
                role="img"
                aria-label={`Orchestration graph, ${displayTeam.pattern} pattern`}
            >
                <svg
                    className="mc-canvas__edges"
                    width={effectiveW}
                    height={viewH}
                    viewBox={`0 0 ${effectiveW} ${viewH}`}
                    aria-hidden="true"
                >
                    <defs>
                        <marker id="ah-delegate" viewBox="0 0 8 6" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto-start-reverse" markerUnits="userSpaceOnUse"><polygon points="0 0.5, 7 3, 0 5.5" fill="#a3c9ff" /></marker>
                        <marker id="ah-report"   viewBox="0 0 8 6" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto-start-reverse" markerUnits="userSpaceOnUse"><polygon points="0 0.5, 7 3, 0 5.5" fill="#c8e6c9" /></marker>
                        <marker id="ah-peer"     viewBox="0 0 8 6" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto-start-reverse" markerUnits="userSpaceOnUse"><polygon points="0 0.5, 7 3, 0 5.5" fill="#ffb689" /></marker>
                        <marker id="ah-default"  viewBox="0 0 8 6" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto-start-reverse" markerUnits="userSpaceOnUse"><polygon points="0 0.5, 7 3, 0 5.5" fill="#c0c7d4" /></marker>
                    </defs>
                    {(() => {
                        // Collapse unordered endpoint pairs so a
                        // delegate (A→B) + report (B→A) round-trip
                        // renders as ONE curve instead of two nearly
                        // identical paths that just bow slightly
                        // differently (because pathSmooth anchors at
                        // bottom→top relative to each edge's direction).
                        // Direction preference: delegate first (parent
                        // on top anchors curve naturally), then peer,
                        // then report (swapped so it still draws
                        // top→bottom). Rendering dedupe mirrors the
                        // label dedupe performed in edgeLabels above.
                        type Merged = {
                            key: string;
                            kinds: Set<string>;
                            from: string;
                            to: string;
                            primary: TeamEdge;
                            count: number;
                        };
                        const byPair = new Map<string, Merged>();
                        for (const e of displayTeam.edges) {
                            const pair = [e.from, e.to].sort().join("↔");
                            const existing = byPair.get(pair);
                            if (!existing) {
                                byPair.set(pair, {
                                    key: pair,
                                    kinds: new Set([e.kind]),
                                    from: e.from,
                                    to: e.to,
                                    primary: e,
                                    count: 1,
                                });
                                continue;
                            }
                            existing.kinds.add(e.kind);
                            existing.count++;
                            // Prefer a delegate-direction path so the
                            // smooth curve flows top-down.
                            if (e.kind === "delegate" && existing.primary.kind !== "delegate") {
                                existing.from = e.from;
                                existing.to = e.to;
                                existing.primary = e;
                            }
                        }
                        return [...byPair.values()].map((m, i) => {
                            const from = positions.get(m.from);
                            const to = positions.get(m.to);
                            if (!from || !to) return null;
                            let d: string;
                            const sameRow = Math.abs(from.y - to.y) < 20;
                            if (displayTeam.pattern === "sequential") {
                                d = pathHorizontal(from, to);
                            } else if (m.kinds.has("peer") && !m.kinds.has("delegate") || displayTeam.pattern === "network") {
                                d = pathPeer(from, to);
                            } else if (sameRow) {
                                d = pathHorizontal(from, to);
                            } else {
                                // If only ``report`` is present (no
                                // matching delegate), swap direction so
                                // the curve still anchors parent→child.
                                const srcIsFrom = from.y <= to.y;
                                d = srcIsFrom ? pathSmooth(from, to) : pathSmooth(to, from);
                            }
                            const kindAttr = m.kinds.has("delegate") && m.kinds.has("report")
                                ? "delegate"
                                : m.primary.kind;
                            const active =
                                !!activeAgentId &&
                                (m.from === activeAgentId || m.to === activeAgentId);
                            // Arrow markers: bidirectional for merged pairs,
                            // unidirectional for single delegate/report,
                            // none for single peer (undirected connection).
                            const bidir = m.count >= 2;
                            const singlePeer = m.count === 1 && m.kinds.has("peer") && !m.kinds.has("delegate");
                            const arrowId = ["delegate", "report", "peer"].includes(kindAttr) ? kindAttr : "default";
                            const mEnd = !singlePeer ? `url(#ah-${arrowId})` : undefined;
                            const mStart = bidir && !singlePeer ? `url(#ah-${arrowId})` : undefined;
                            return (
                                <path
                                    key={`e-${i}-${m.key}`}
                                    className="mc-edge"
                                    data-kind={kindAttr}
                                    data-active={active ? "true" : undefined}
                                    d={d}
                                    markerEnd={mEnd}
                                    markerStart={mStart}
                                />
                            );
                        });
                    })()}
                </svg>

                {displayTeam.nodes.map((n) => {
                    const pos = positions.get(n.id);
                    if (!pos) return null;
                    const kind = agentKind(n.agent, n.id);
                    const active = activeAgentId === n.id;
                    const state = stateFor(n, active);
                    const stateLabel = STATE_LABEL[state] || state;
                    return (
                        <div
                            key={n.id}
                            ref={(element) => {
                                if (element) nodeRefs.current.set(n.id, element);
                                else nodeRefs.current.delete(n.id);
                            }}
                            className="mc-node"
                            data-agent={kind}
                            data-state={state}
                            data-active={active ? "true" : undefined}
                            tabIndex={0}
                            style={{ left: pos.x, top: pos.y, width: pos.w }}
                            onMouseEnter={onNodeHover ? () => onNodeHover(n.id) : undefined}
                            onMouseLeave={onNodeHover ? () => onNodeHover(null) : undefined}
                            onFocus={onNodeHover ? () => onNodeHover(n.id) : undefined}
                            onBlur={onNodeHover ? () => onNodeHover(null) : undefined}
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
                            {(n.skills?.length || n.allSkills?.length) ? (
                                <NodeSkills
                                    skills={n.skills}
                                    allSkills={n.allSkills}
                                    nodeLabel={n.agent}
                                />
                            ) : null}
                            {/* Runtime status row surfaces lifecycle state
                                directly when available in live execution. */}
                            {state !== "planned" && (
                                <div className="mc-node__state">
                                    <span className="mc-node__state-dot" />
                                    {stateLabel}
                                    {n.stateReason && <span className="mc-node__state-reason">· {n.stateReason}</span>}
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
                        data-orientation={lbl.orientation}
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
