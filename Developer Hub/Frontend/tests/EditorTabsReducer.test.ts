/**
 * Unit tests for the EditorTabs reducer — specifically the split and
 * open-in-new-group actions that govern the editor grid layout.
 *
 * These are pure reducer tests with no DOM, React, or browser needed.
 */
import { describe, it, expect } from "vitest";
import {
    reducer,
    INITIAL_STATE,
    INITIAL_GROUP_ID,
    INITIAL_COLUMN_ID,
    type TabsState,
    type TabDescriptor,
} from "../src/components/AgentHub/EditorTabs/EditorTabsContext";

/** Helper: create a tab descriptor. */
function tab(id: string, title = id): TabDescriptor {
    return { id, kind: "new", path: `/agent-hub/orchestrator?draft=${id}`, title };
}

/** Helper: seed initial state with a tab in the first group. */
function stateWithOneTab(): TabsState {
    return reducer(INITIAL_STATE, { type: "open", tab: tab("t1") });
}

/** Helper: create state with two side-by-side columns (left has t1, right has t2).
 *  This mirrors the user's "two windows next to each other" scenario. */
function stateWithTwoColumns(): TabsState {
    // Start with one tab
    let s = stateWithOneTab();
    // Add a second tab so the source group keeps one after the split
    s = reducer(s, { type: "open", tab: tab("t2") });
    // Split right — moves t2 to a new column
    s = reducer(s, {
        type: "split",
        tabId: "t2",
        fromGroupId: INITIAL_GROUP_ID,
        side: "right",
    });
    return s;
}

/** Helper: create state with two rows (top has t1, bottom has t2) stacked in one column. */
function stateWithTwoRows(): TabsState {
    let s = stateWithOneTab();
    s = reducer(s, { type: "open", tab: tab("t2") });
    s = reducer(s, {
        type: "split",
        tabId: "t2",
        fromGroupId: INITIAL_GROUP_ID,
        side: "bottom",
    });
    return s;
}

/** Collect groups per column, preserving column order. */
function columnLayout(s: TabsState) {
    return s.columnOrder.map((colId) => ({
        colId,
        groups: s.groups.filter((g) => g.column === colId),
    }));
}

describe("EditorTabs reducer: split", () => {
    // ── Basic split operations ──────────────────────────────

    it("split right creates a new column next to the source", () => {
        const s = stateWithTwoColumns();
        const layout = columnLayout(s);

        expect(layout).toHaveLength(2);
        expect(layout[0].groups).toHaveLength(1);
        expect(layout[1].groups).toHaveLength(1);
        // Left column has t1, right has t2
        expect(layout[0].groups[0].tabs.map((t) => t.id)).toContain("t1");
        expect(layout[1].groups[0].tabs.map((t) => t.id)).toContain("t2");
    });

    it("split down creates a second row in the same column", () => {
        const s = stateWithTwoRows();
        const layout = columnLayout(s);

        expect(layout).toHaveLength(1);
        expect(layout[0].groups).toHaveLength(2);
    });

    // ── THE CORE BUG: split down on left column with two columns ──

    it("split down on LEFT column adds a row in the LEFT column (not right)", () => {
        let s = stateWithTwoColumns();
        const layout = columnLayout(s);
        const leftColId = layout[0].colId;
        const leftGroupId = layout[0].groups[0].id;
        const rightColId = layout[1].colId;

        // Need a second tab in the left group so the split has a tab to move
        s = reducer(s, { type: "open", tab: tab("t3"), groupId: leftGroupId });

        // Split down the left group — the new group should land in the LEFT column
        s = reducer(s, {
            type: "split",
            tabId: "t3",
            fromGroupId: leftGroupId,
            side: "bottom",
        });

        const afterLayout = columnLayout(s);
        // Left column should now have 2 groups (rows), right still has 1
        const leftCol = afterLayout.find((c) => c.colId === leftColId);
        const rightCol = afterLayout.find((c) => c.colId === rightColId);

        expect(leftCol?.groups).toHaveLength(2);
        expect(rightCol?.groups).toHaveLength(1);
    });

    it("split down on RIGHT column adds a row in the RIGHT column (not left)", () => {
        let s = stateWithTwoColumns();
        const layout = columnLayout(s);
        const leftColId = layout[0].colId;
        const rightColId = layout[1].colId;
        const rightGroupId = layout[1].groups[0].id;

        // Need a second tab in the right group
        s = reducer(s, { type: "open", tab: tab("t3"), groupId: rightGroupId });

        // Split down the right group — the new group should land in the RIGHT column
        s = reducer(s, {
            type: "split",
            tabId: "t3",
            fromGroupId: rightGroupId,
            side: "bottom",
        });

        const afterLayout = columnLayout(s);
        const leftCol = afterLayout.find((c) => c.colId === leftColId);
        const rightCol = afterLayout.find((c) => c.colId === rightColId);

        expect(leftCol?.groups).toHaveLength(1);
        expect(rightCol?.groups).toHaveLength(2);
    });

    // ── Cross-group split with targetGroupId ──

    it("cross-group split bottom anchors to the TARGET group's column (the fix)", () => {
        let s = stateWithTwoColumns();
        const layout = columnLayout(s);
        const leftColId = layout[0].colId;
        const rightColId = layout[1].colId;
        const leftGroupId = layout[0].groups[0].id;
        const rightGroupId = layout[1].groups[0].id;

        // Simulate dragging a tab from the right group and dropping it on
        // the LEFT group's bottom drop zone. The targetGroupId should be
        // the left group, but fromGroupId is the right group (drag source).
        // Need a second tab in the right group so it doesn't duplicate
        s = reducer(s, { type: "open", tab: tab("t3"), groupId: rightGroupId });

        s = reducer(s, {
            type: "split",
            tabId: "t3",
            fromGroupId: rightGroupId,
            side: "bottom",
            targetGroupId: leftGroupId,
        });

        const afterLayout = columnLayout(s);
        const leftCol = afterLayout.find((c) => c.colId === leftColId);
        const rightCol = afterLayout.find((c) => c.colId === rightColId);

        // The new row should appear in the LEFT column (target) not the right (source)
        expect(leftCol?.groups).toHaveLength(2);
        expect(rightCol?.groups).toHaveLength(1);
    });

    it("cross-group split right anchors to the TARGET group's column", () => {
        const s0 = stateWithTwoRows();
        const layout0 = columnLayout(s0);
        const topGroupId = layout0[0].groups[0].id;
        const bottomGroupId = layout0[0].groups[1].id;

        // Need a second tab in the top group
        let s = reducer(s0, { type: "open", tab: tab("t3"), groupId: topGroupId });

        // Drag tab from top group, drop on bottom group's right zone
        s = reducer(s, {
            type: "split",
            tabId: "t3",
            fromGroupId: topGroupId,
            side: "right",
            targetGroupId: bottomGroupId,
        });

        const afterLayout = columnLayout(s);
        // Should now have 2 columns
        expect(afterLayout).toHaveLength(2);
    });

    // ── Two rows + add column ──

    it("two rows + split right creates a second column", () => {
        let s = stateWithTwoRows();
        const layout = columnLayout(s);
        const topGroupId = layout[0].groups[0].id;

        // Need a second tab in the top group
        s = reducer(s, { type: "open", tab: tab("t3"), groupId: topGroupId });

        // Split right the top group
        s = reducer(s, {
            type: "split",
            tabId: "t3",
            fromGroupId: topGroupId,
            side: "right",
        });

        const afterLayout = columnLayout(s);
        expect(afterLayout.length).toBeGreaterThanOrEqual(2);
    });

    // ── Cross-group drag of the sole tab MOVES rather than duplicates ──

    it("cross-group drag of the only tab MOVES it (no duplicate)", () => {
        // Two columns each with one tab — dragging the left tab onto
        // the right group's bottom zone should MOVE t1, not clone it.
        let s = stateWithTwoColumns();
        const layout = columnLayout(s);
        const leftGroupId = layout[0].groups[0].id;
        const rightGroupId = layout[1].groups[0].id;

        // Left group has only t1. Cross-group drag to right bottom.
        s = reducer(s, {
            type: "split",
            tabId: "t1",
            fromGroupId: leftGroupId,
            side: "bottom",
            targetGroupId: rightGroupId,
        });

        const afterLayout = columnLayout(s);
        // The left column's group should be gone (empty → pruned).
        // Right column should now have 2 groups (original + new from drag).
        const totalGroups = afterLayout.reduce((n, c) => n + c.groups.length, 0);
        expect(totalGroups).toBe(2); // t2's group + the new group with t1

        // t1 should appear exactly once across all groups.
        const allTabIds = afterLayout.flatMap(c => c.groups.flatMap(g => g.tabs.map(t => t.id)));
        expect(allTabIds.filter(id => id === "t1")).toHaveLength(1);
    });

    it("same-group split of the only tab DUPLICATES (VS Code behavior)", () => {
        // Context menu "Split Right" on the only tab in a group should
        // duplicate so both halves stay populated.
        let s = stateWithOneTab();

        s = reducer(s, {
            type: "split",
            tabId: "t1",
            fromGroupId: INITIAL_GROUP_ID,
            side: "right",
            // No targetGroupId → same-group split.
        });

        const layout = columnLayout(s);
        expect(layout).toHaveLength(2);
        // Both groups should have a tab (the duplicate).
        expect(layout[0].groups[0].tabs).toHaveLength(1);
        expect(layout[1].groups[0].tabs).toHaveLength(1);
        // The two tabs should have different ids (one is the clone).
        const ids = layout.flatMap(c => c.groups.flatMap(g => g.tabs.map(t => t.id)));
        expect(new Set(ids).size).toBe(2);
    });
});

describe("EditorTabs reducer: open-in-new-group", () => {
    it("open-in-new-group bottom with fromGroupId anchors to that group's column", () => {
        let s = stateWithTwoColumns();
        const layout = columnLayout(s);
        const leftColId = layout[0].colId;
        const rightColId = layout[1].colId;
        const leftGroupId = layout[0].groups[0].id;

        // Open a new tab at the bottom of the LEFT group
        s = reducer(s, {
            type: "open-in-new-group",
            tab: tab("t3"),
            side: "bottom",
            fromGroupId: leftGroupId,
        });

        const afterLayout = columnLayout(s);
        const leftCol = afterLayout.find((c) => c.colId === leftColId);
        const rightCol = afterLayout.find((c) => c.colId === rightColId);

        // New row should be in the LEFT column
        expect(leftCol?.groups).toHaveLength(2);
        expect(rightCol?.groups).toHaveLength(1);
    });

    it("open-in-new-group right creates a new column", () => {
        let s = stateWithOneTab();

        s = reducer(s, {
            type: "open-in-new-group",
            tab: tab("t2"),
            side: "right",
        });

        const layout = columnLayout(s);
        expect(layout).toHaveLength(2);
    });

    it("open-in-new-group without fromGroupId defaults to activeGroupId", () => {
        let s = stateWithTwoColumns();
        // Active group is the newly split right group (most recent split sets it active)
        const activeGroupId = s.activeGroupId;
        const activeGroup = s.groups.find((g) => g.id === activeGroupId);
        const activeCol = activeGroup!.column;

        // Open without fromGroupId → anchors to active group
        s = reducer(s, {
            type: "open-in-new-group",
            tab: tab("t3"),
            side: "bottom",
        });

        const afterLayout = columnLayout(s);
        const col = afterLayout.find((c) => c.colId === activeCol);
        expect(col?.groups).toHaveLength(2);
    });
});
