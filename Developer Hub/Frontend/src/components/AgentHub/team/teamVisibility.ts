import type { Team, TeamNode } from "../plan/types";

const INTERNAL_AGENT_IDS = new Set(["orchestrator"]);

export function isInternalTeamNode(node: TeamNode): boolean {
    const id = String(node.id || "").toLowerCase();
    const agent = String(node.agent || "").toLowerCase();
    return INTERNAL_AGENT_IDS.has(id) || INTERNAL_AGENT_IDS.has(agent);
}

export function visibleTeam(team: Team): Team {
    const nodes = team.nodes.filter((node) => !isInternalTeamNode(node));
    if (nodes.length === team.nodes.length) return team;

    const visibleIds = new Set(nodes.map((node) => node.id));
    return {
        ...team,
        nodes,
        edges: team.edges.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to)),
    };
}