import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";

export default function fabricClawHubLogCompactor(pi: ExtensionAPI) {
  pi.on("session_start", async (_event, ctx) => {
    ctx.ui.notify("Fabric ClawHub log compactor loaded", "info");
    ctx.ui.setStatus("fabric-clawhub-logs", "Recent details expanded, older activity collapsed");
  });

  pi.registerTool({
    name: "fabric_clawhub_log_compaction_policy",
    label: "Fabric ClawHub Log Compaction Policy",
    description: "Declare the self-collapsing live-log policy used by the Mission Control Pi surface.",
    parameters: Type.Object({
      recentWindowMs: Type.Number({ description: "How long a live log row stays expanded before collapsing." }),
      maxRecentRows: Type.Number({ description: "Minimum number of newest rows that remain expanded." }),
      strategy: Type.String({ description: "Grouping strategy used for older log details." }),
    }),
    async execute(_toolCallId, params) {
      return {
        content: [{ type: "text", text: `Self-collapsing logs active: ${params.recentWindowMs} ms recent window, ${params.maxRecentRows} newest rows expanded.` }],
        details: {
          source: "fabric-clawhub-log-compactor",
          recentWindowMs: params.recentWindowMs,
          maxRecentRows: params.maxRecentRows,
          strategy: params.strategy,
          collapseMode: "details-summary",
        },
      };
    },
  });

  pi.registerTool({
    name: "fabric_clawhub_log_rollup_event",
    label: "Fabric ClawHub Log Rollup Event",
    description: "Publish a high-level summary for older Mission Control log details.",
    parameters: Type.Object({
      agent: Type.String({ description: "Agent or runtime lane represented by the rollup." }),
      category: Type.String({ description: "Grouped log category, such as tool, thinking, or runtime." }),
      detailCount: Type.Number({ description: "Number of hidden detail rows covered by this summary." }),
      summary: Type.String({ description: "High-level summary shown in the collapsed row." }),
    }),
    async execute(_toolCallId, params) {
      return {
        content: [{ type: "text", text: `${params.agent}: ${params.detailCount} older ${params.category} events - ${params.summary}` }],
        details: {
          source: "fabric-clawhub-log-compactor",
          agent: params.agent,
          category: params.category,
          detailCount: params.detailCount,
          summary: params.summary,
        },
      };
    },
  });
}