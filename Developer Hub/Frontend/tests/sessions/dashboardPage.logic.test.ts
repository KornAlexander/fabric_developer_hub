import { describe, expect, it } from "vitest";

import { computeDashboardCounts, dashboardNewSessionPath, dashboardSessionPath } from "../../src/components/AgentHub/DashboardPage";

describe("DashboardPage route helpers", () => {
    it("builds session path from /home route", () => {
        expect(dashboardSessionPath("/agent-hub/home", "abc123")).toBe("/agent-hub/session/abc123");
    });

    it("builds session path from /sessions route", () => {
        expect(dashboardSessionPath("/agent-hub/sessions", "abc123")).toBe("/agent-hub/session/abc123");
    });

    it("builds new-session path from both legacy and current sessions routes", () => {
        expect(dashboardNewSessionPath("/agent-hub/home")).toBe("/agent-hub/orchestrator");
        expect(dashboardNewSessionPath("/agent-hub/sessions")).toBe("/agent-hub/orchestrator");
    });
});

describe("DashboardPage count model", () => {
    const jobs = [
        { id: "1", status: "running" },
        { id: "2", status: "planned" },
        { id: "3", status: "failed" },
        { id: "4", status: "completed" },
        { id: "5", status: "cancelled" },
    ];

    it("falls back to client-side derivation when summary is unavailable", () => {
        const counts = computeDashboardCounts(jobs, null);
        expect(counts.runningCount).toBe(1);
        expect(counts.waitingCount).toBe(1);
        expect(counts.errorCount).toBe(1);
        expect(counts.activeTotal).toBe(3);
        expect(counts.historyTotal).toBe(2);
        expect(counts.totalSessions).toBe(5);
    });

    it("prefers backend summary counts for globally correct totals", () => {
        const counts = computeDashboardCounts(jobs, {
            total: 250,
            active_total: 37,
            history_total: 213,
            running: 4,
            waiting: 20,
            failed: 13,
            completed: 170,
            cancelled: 43,
            other_active: 0,
            by_status: {},
        });
        expect(counts.runningCount).toBe(4);
        expect(counts.waitingCount).toBe(20);
        expect(counts.errorCount).toBe(13);
        expect(counts.activeTotal).toBe(37);
        expect(counts.historyTotal).toBe(213);
        expect(counts.totalSessions).toBe(250);
    });
});
