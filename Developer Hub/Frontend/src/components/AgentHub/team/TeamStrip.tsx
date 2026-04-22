import React from "react";
import type { Team, TeamNode, TeamPattern } from "../plan/types";

/**
 * Compact one-line team strip — default state of the TeamPanel.
 *
 * Renders the orchestrator and its workers as Fluent-styled chips with
 * a single light chevron connector. Topology variants reshape the
 * strip to match ``team.pattern``:
 *   - supervisor: orchestrator › worker1 · worker2 · worker3
 *   - sequential: n1 → n2 → n3
 *   - network:    all-peer row (no chevron, gap only)
 *   - solo:       single chip
 *   - mixed:      same as supervisor with a "+N sub-teams" overflow
 *
 * Not a node editor — fully read-only.
 */

interface TeamStripProps {
    team: Team;
    activeAgentId?: string | null;
}

function chipClass(node: TeamNode, activeAgentId?: string | null): string {
    const cls = ["team-strip__chip", `team-strip__chip--${node.status}`];
    if (activeAgentId && node.id === activeAgentId) cls.push("team-strip__chip--active");
    return cls.join(" ");
}

export function TeamStrip({ team, activeAgentId }: TeamStripProps) {
    const orchestrator = team.nodes.find((n) => n.id === "orchestrator")
        || team.nodes[0];
    const workers = team.nodes.filter((n) => n.id !== orchestrator?.id);

    const pattern: TeamPattern = team.pattern || "supervisor";

    if (!orchestrator) return null;

    if (pattern === "solo") {
        return (
            <div className="team-strip team-strip--solo">
                <span className={chipClass(orchestrator, activeAgentId)} title={orchestrator.role}>
                    <span className="team-strip__chip-agent">{orchestrator.agent}</span>
                    <span className="team-strip__chip-role">{orchestrator.role}</span>
                </span>
            </div>
        );
    }

    if (pattern === "sequential") {
        return (
            <div className="team-strip team-strip--sequential">
                {team.nodes.map((n, i) => (
                    <React.Fragment key={n.id}>
                        <span className={chipClass(n, activeAgentId)} title={n.role}>
                            <span className="team-strip__chip-agent">{n.agent}</span>
                        </span>
                        {i < team.nodes.length - 1 && (
                            <span className="team-strip__arrow" aria-hidden>→</span>
                        )}
                    </React.Fragment>
                ))}
            </div>
        );
    }

    if (pattern === "network") {
        return (
            <div className="team-strip team-strip--network">
                {team.nodes.map((n) => (
                    <span key={n.id} className={chipClass(n, activeAgentId)} title={n.role}>
                        <span className="team-strip__chip-agent">{n.agent}</span>
                    </span>
                ))}
            </div>
        );
    }

    // supervisor + mixed share the same strip layout
    return (
        <div className={`team-strip team-strip--${pattern}`}>
            <span className={chipClass(orchestrator, activeAgentId)} title={orchestrator.role}>
                <span className="team-strip__chip-agent">{orchestrator.agent}</span>
                <span className="team-strip__chip-role">{orchestrator.role}</span>
            </span>
            <span className="team-strip__chevron" aria-hidden>›</span>
            <div className="team-strip__workers">
                {workers.map((n) => (
                    <span key={n.id} className={chipClass(n, activeAgentId)} title={n.role}>
                        <span className="team-strip__chip-agent">{n.agent}</span>
                    </span>
                ))}
            </div>
        </div>
    );
}
