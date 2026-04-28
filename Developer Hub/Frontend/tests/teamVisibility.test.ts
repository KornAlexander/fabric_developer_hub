import { describe, expect, it } from "vitest";

import { visibleTeam } from "../src/components/AgentHub/team/teamVisibility";
import type { Team } from "../src/components/AgentHub/plan/types";

describe("visibleTeam", () => {
    it("removes internal orchestrator nodes and connected edges", () => {
        const team: Team = {
            pattern: "supervisor",
            nodes: [
                { id: "orchestrator", agent: "Orchestrator", role: "Internal control plane", status: "planned" },
                { id: "worker", agent: "Architect", role: "Plan", status: "planned" },
            ],
            edges: [
                { from: "orchestrator", to: "worker", kind: "delegate" },
                { from: "worker", to: "orchestrator", kind: "report" },
            ],
        };

        const visible = visibleTeam(team);

        expect(visible.nodes).toEqual([
            { id: "worker", agent: "Architect", role: "Plan", status: "planned" },
        ]);
        expect(visible.edges).toEqual([]);
    });
});