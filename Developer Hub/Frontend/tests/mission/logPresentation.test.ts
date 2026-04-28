import { describe, expect, it } from "vitest";

import { formatToolArgsSummary, formatToolCommand, formatVisibleRuntimeText } from "../../src/components/AgentHub/mission/logPresentation";

describe("mission log presentation", () => {
    it("formats detailed tool blocks without raw Fabric tool tokens", () => {
        const command = formatToolCommand("fabric_create_item", {
            workspace_id: "8bdca8af-1db1-4fd8-9564-0c98b4dbdffc",
            display_name: "Quarterly Inventory Review",
            item_type: "Notebook",
            terminalLines: ["fabric_create_item should stay hidden"],
        });
        const args = formatToolArgsSummary({
            workspace_id: "8bdca8af-1db1-4fd8-9564-0c98b4dbdffc",
            display_name: "Quarterly Inventory Review",
            item_type: "Notebook",
            terminalLabel: "internal",
        });

        expect(command).toContain("Create Fabric item");
        expect(command).toContain("workspace id");
        expect(args).toBe("workspace id, display name, item type");
        expect(`${command}\n${args}`).not.toMatch(/fabric_create_item|workspace_id|display_name|item_type|terminalLines/);
    });

    it("formats runtime text without internal trace tokens", () => {
        expect(formatVisibleRuntimeText("TOOL_ERROR while calling fabric_list_items → undefined"))
            .toBe("Tool issue while calling read workspace inventory to details unavailable");
    });
});