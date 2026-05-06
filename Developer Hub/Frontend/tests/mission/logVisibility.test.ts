import { describe, expect, it } from "vitest";

import type { PublicLogCategory } from "../../src/components/AgentHub/mission/events";
import type { LogEntry } from "../../src/components/AgentHub/mission/missionReducer";
import { isHighSignalLog, logCategoryIncludedInView, logVisibleInCategory } from "../../src/components/AgentHub/mission/logVisibility";

function entry(logCategory: PublicLogCategory, overrides: Partial<LogEntry> = {}): LogEntry {
    return {
        seq: 1,
        ts: "2025-01-01T00:00:00.000Z",
        level: "info",
        message: `${logCategory} message`,
        logCategory,
        kind: "log",
        ...overrides,
    };
}

describe("mission log visibility", () => {
    it("includes every public log category in every view", () => {
        expect(logCategoryIncludedInView("high_level", "high_level")).toBe(true);
        expect(logCategoryIncludedInView("detailed", "high_level")).toBe(true);
        expect(logCategoryIncludedInView("high_level", "detailed")).toBe(true);
        expect(logCategoryIncludedInView("detailed", "detailed")).toBe(true);
        expect(logCategoryIncludedInView("diagnostic", "detailed")).toBe(true);
        expect(logCategoryIncludedInView("high_level", "diagnostic")).toBe(true);
        expect(logCategoryIncludedInView("detailed", "diagnostic")).toBe(true);
        expect(logCategoryIncludedInView("diagnostic", "diagnostic")).toBe(true);
    });

    it("shows diagnostic failures without category promotion rules", () => {
        const diagnosticFailure = entry("diagnostic", { level: "error", message: "Tool failed", kind: "error" });

        expect(logVisibleInCategory(diagnosticFailure, "high_level")).toBe(true);
        expect(logVisibleInCategory(diagnosticFailure, "detailed")).toBe(true);
        expect(logVisibleInCategory(diagnosticFailure, "diagnostic")).toBe(true);
    });

    it("shows high-level entries in every public view", () => {
        const highLevelEntry = entry("high_level");

        expect(logVisibleInCategory(highLevelEntry, "high_level")).toBe(true);
        expect(logVisibleInCategory(highLevelEntry, "detailed")).toBe(true);
        expect(logVisibleInCategory(highLevelEntry, "diagnostic")).toBe(true);
    });

    it("shows detailed entries in every public view", () => {
        const detailedEntry = entry("detailed");

        expect(logVisibleInCategory(detailedEntry, "high_level")).toBe(true);
        expect(logVisibleInCategory(detailedEntry, "detailed")).toBe(true);
        expect(logVisibleInCategory(detailedEntry, "diagnostic")).toBe(true);
    });

    it("treats rollup and steering entries as high-signal summaries", () => {
        const rollup = entry("high_level", { kind: "rollup", message: "Created report receipt" });
        const steering = entry("high_level", { kind: "steering", level: "warn", message: "Steering queued" });
        const quietDiagnostic = entry("diagnostic", { kind: "diagnostic", message: "Baseline captured" });
        const noisyDiagnostic = entry("diagnostic", { kind: "diagnostic", level: "error", message: "New issue" });

        expect(isHighSignalLog(rollup)).toBe(true);
        expect(isHighSignalLog(steering)).toBe(true);
        expect(isHighSignalLog(quietDiagnostic)).toBe(false);
        expect(isHighSignalLog(noisyDiagnostic)).toBe(true);
    });
});