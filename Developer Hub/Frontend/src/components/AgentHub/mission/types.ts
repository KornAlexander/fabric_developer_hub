/**
 * Mission Control — TypeScript mirror of the backend Composition model.
 *
 * Only fields the mission-control UI reads are typed here; the Python
 * model (``Backend/src/domain/models/composition.py``) is the source
 * of truth and carries additional budget / handoff-condition fields
 * the run surface does not render.
 *
 * The ``Team`` view needed by the existing TeamPanel is derived from
 * Composition via ``teamFromComposition`` — no separate backend field.
 */

import type { Team, TeamNode, TeamEdge, TeamPattern, TeamEdgeKind } from "../plan/types";

export interface CompositionSkill { id: string; name: string; }

export interface CompositionSlot {
    id: string;
    agentId: string;
    role: string;
    skills: CompositionSkill[];
    parentId?: string | null;
    subteam?: string | null;
    status: "planned" | "active" | "done" | "waiting" | "error";
}

export interface CompositionHandoff {
    from: string;
    to: string;
    kind: "delegate" | "report" | "peer" | "handoff" | "critique";
    condition?: string | null;
}

export interface Composition {
    architecture: string;
    task: string;
    slots: CompositionSlot[];
    handoffs: CompositionHandoff[];
    budget?: Record<string, unknown>;
    rationale?: string;
}

const ARCHITECTURE_TO_PATTERN: Record<string, TeamPattern> = {
    supervisor: "supervisor",
    sequential: "sequential",
    network: "network",
    hierarchical: "hierarchical",
    solo: "solo",
    mixed: "mixed",
    // Compose-only variants fold onto the closest UI pattern.
    parallel: "network",
    router: "supervisor",
    reflection: "network",
    debate: "network",
    magentic: "hierarchical",
};

const HANDOFF_TO_EDGE_KIND: Record<string, TeamEdgeKind> = {
    delegate: "delegate",
    report: "report",
    peer: "peer",
    handoff: "peer",
    critique: "peer",
};

/**
 * Build the ``Team`` view the TeamPanel renders from a Composition.
 * Pure function — safe to call in render / reducer paths.
 */
export function teamFromComposition(c: Composition | null | undefined): Team | null {
    if (!c) return null;
    const pattern: TeamPattern = ARCHITECTURE_TO_PATTERN[c.architecture] || "supervisor";
    const nodes: TeamNode[] = (c.slots || []).map(s => ({
        id: s.id,
        agent: s.agentId,
        role: s.role,
        status: s.status === "active" ? "active"
             : s.status === "done" ? "done"
             : s.status === "waiting" ? "waiting"
             : "planned",
    }));
    const edges: TeamEdge[] = (c.handoffs || []).map(h => ({
        from: h.from,
        to: h.to,
        kind: HANDOFF_TO_EDGE_KIND[h.kind] || "delegate",
    }));
    return { pattern, nodes, edges };
}
