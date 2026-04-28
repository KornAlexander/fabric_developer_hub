import { describe, expect, it } from "vitest";

import type { PublicLogCategory } from "../../src/components/AgentHub/mission/events";
import type { LogEntry } from "../../src/components/AgentHub/mission/missionReducer";
import { logCategoryIncludedInView, logVisibleInCategory } from "../../src/components/AgentHub/mission/logVisibility";

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
    it("treats public log categories as cumulative visibility levels", () => {
        expect(logCategoryIncludedInView("high_level", "high_level")).toBe(true);
        expect(logCategoryIncludedInView("detailed", "high_level")).toBe(false);
        expect(logCategoryIncludedInView("high_level", "detailed")).toBe(true);
        expect(logCategoryIncludedInView("detailed", "detailed")).toBe(true);
        expect(logCategoryIncludedInView("diagnostic", "detailed")).toBe(false);
        expect(logCategoryIncludedInView("high_level", "diagnostic")).toBe(true);
        expect(logCategoryIncludedInView("detailed", "diagnostic")).toBe(true);
        expect(logCategoryIncludedInView("diagnostic", "diagnostic")).toBe(true);
    });

    it("does not promote high-signal diagnostic failures into higher-level views", () => {
        const diagnosticFailure = entry("diagnostic", { level: "error", message: "Tool failed", kind: "error" });

        expect(logVisibleInCategory(diagnosticFailure, "high_level")).toBe(false);
        expect(logVisibleInCategory(diagnosticFailure, "detailed")).toBe(false);
        expect(logVisibleInCategory(diagnosticFailure, "diagnostic")).toBe(true);
    });

    it("shows high-level entries in every public view", () => {
        const highLevelEntry = entry("high_level");

        expect(logVisibleInCategory(highLevelEntry, "high_level")).toBe(true);
        expect(logVisibleInCategory(highLevelEntry, "detailed")).toBe(true);
        expect(logVisibleInCategory(highLevelEntry, "diagnostic")).toBe(true);
    });

    it("shows detailed entries in detailed and diagnostic views only", () => {
        const detailedEntry = entry("detailed");

        expect(logVisibleInCategory(detailedEntry, "high_level")).toBe(false);
        expect(logVisibleInCategory(detailedEntry, "detailed")).toBe(true);
        expect(logVisibleInCategory(detailedEntry, "diagnostic")).toBe(true);
    });
});