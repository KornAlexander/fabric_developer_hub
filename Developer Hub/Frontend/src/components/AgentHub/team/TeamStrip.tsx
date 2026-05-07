import React from "react";
import type { Team, TeamNode, TeamPattern } from "../plan/types";
import { visibleTeam } from "./teamVisibility";

/**
 * Compact one-line team strip — default state of the TeamPanel.
 *
 * Renders visible specialist agents as Fluent-styled chips with
 * a single light chevron connector. Topology variants reshape the
 * strip to match ``team.pattern``:
 *   - supervisor: lead › worker1 · worker2 · worker3
 *   - sequential: n1 → n2 → n3
 *   - network:    all-peer row (no chevron, gap only)
 *   - solo:       single chip
 *   - mixed:      same as supervisor with grouped specialist sub-teams
 *
 * Not a node editor — fully read-only.
 */

interface TeamStripProps {
    team: Team;
    activeAgentId?: string | null;
}

function lifecycleFor(node: TeamNode, activeAgentId?: string | null): "planned" | "spinning_up" | "waiting" | "running" | "finished" | "failed" {
    if (node.lifecycle) {
        if (activeAgentId && node.id === activeAgentId && node.lifecycle !== "finished" && node.lifecycle !== "failed") {
            return "running";
        }
        return node.lifecycle;
    }
    if (activeAgentId && node.id === activeAgentId) return "running";
    if (node.status === "active") return "running";
    if (node.status === "done") return "finished";
    if (node.status === "waiting") return "waiting";
    if (node.status === "failed") return "failed";
    return "planned";
}

function lifecycleLabel(lifecycle: ReturnType<typeof lifecycleFor>): string {
    if (lifecycle === "spinning_up") return "Spinning up";
    if (lifecycle === "waiting") return "Waiting";
    if (lifecycle === "running") return "Running";
    if (lifecycle === "finished") return "Finished";
    if (lifecycle === "failed") return "Failed";
    return "Planned";
}

function chipClass(node: TeamNode, activeAgentId?: string | null): string {
    const lifecycle = lifecycleFor(node, activeAgentId);
    const cls = ["team-strip__chip", `team-strip__chip--${lifecycle}`];
    return cls.join(" ");
}

export function TeamStrip({ team, activeAgentId }: TeamStripProps) {
    const displayTeam = visibleTeam(team);
    const lead = displayTeam.nodes[0];
    const workers = displayTeam.nodes.filter((n) => n.id !== lead?.id);

    const pattern: TeamPattern = displayTeam.pattern || "supervisor";

    if (!lead) return null;

    if (pattern === "solo") {
        const lifecycle = lifecycleFor(lead, activeAgentId);
        return (
            <div className="team-strip team-strip--solo">
                <span className={chipClass(lead, activeAgentId)} title={lead.role}>
                    <span className="team-strip__chip-agent">{lead.agent}</span>
                    <span className="team-strip__chip-role">{lead.role}</span>
                    {lifecycle !== "planned" && <span className="team-strip__chip-state">{lifecycleLabel(lifecycle)}</span>}
                </span>
            </div>
        );
    }

    if (pattern === "sequential") {
        return (
            <div className="team-strip team-strip--sequential">
                {displayTeam.nodes.map((n, i) => {
                    const lifecycle = lifecycleFor(n, activeAgentId);
                    return (
                        <React.Fragment key={n.id}>
                            <span className={chipClass(n, activeAgentId)} title={n.role}>
                                <span className="team-strip__chip-agent">{n.agent}</span>
                                {lifecycle !== "planned" && (
                                    <span className="team-strip__chip-state">{lifecycleLabel(lifecycle)}</span>
                                )}
                            </span>
                            {i < displayTeam.nodes.length - 1 && (
                                <span className="team-strip__arrow" aria-hidden>→</span>
                            )}
                        </React.Fragment>
                    );
                })}
            </div>
        );
    }

    if (pattern === "network") {
        return (
            <div className="team-strip team-strip--network">
                {displayTeam.nodes.map((n) => {
                    const lifecycle = lifecycleFor(n, activeAgentId);
                    return (
                        <span key={n.id} className={chipClass(n, activeAgentId)} title={n.role}>
                            <span className="team-strip__chip-agent">{n.agent}</span>
                            {lifecycle !== "planned" && (
                                <span className="team-strip__chip-state">{lifecycleLabel(lifecycle)}</span>
                            )}
                        </span>
                    );
                })}
            </div>
        );
    }

    // supervisor + mixed share the same strip layout
    const leadLifecycle = lifecycleFor(lead, activeAgentId);
    return (
        <div className={`team-strip team-strip--${pattern}`}>
            <span className={chipClass(lead, activeAgentId)} title={lead.role}>
                <span className="team-strip__chip-agent">{lead.agent}</span>
                <span className="team-strip__chip-role">{lead.role}</span>
                {leadLifecycle !== "planned" && <span className="team-strip__chip-state">{lifecycleLabel(leadLifecycle)}</span>}
            </span>
            <span className="team-strip__chevron" aria-hidden>›</span>
            <div className="team-strip__workers">
                {workers.map((n) => {
                    const lifecycle = lifecycleFor(n, activeAgentId);
                    return (
                        <span key={n.id} className={chipClass(n, activeAgentId)} title={n.role}>
                            <span className="team-strip__chip-agent">{n.agent}</span>
                            {lifecycle !== "planned" && (
                                <span className="team-strip__chip-state">{lifecycleLabel(lifecycle)}</span>
                            )}
                        </span>
                    );
                })}
            </div>
        </div>
    );
}
