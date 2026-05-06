import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";

export default function fabricClawHubAgenticEngineering(pi: ExtensionAPI) {
  pi.on("session_start", async (_event, ctx) => {
    ctx.ui.notify("Fabric ClawHub agentic engineering protocol loaded", "info");
    ctx.ui.setStatus("fabric-clawhub-rpi", "RPI gates and ContextPackV2 active");
  });

  pi.registerTool({
    name: "fabric_clawhub_rpi_phase_gate",
    label: "Fabric ClawHub RPI Phase Gate",
    description: "Declare a research-plan-implement phase transition governed by AgentHub.",
    parameters: Type.Object({
      phase: Type.String({ description: "RPI phase: research, plan, plan_review, implement, verify, or repair." }),
      status: Type.String({ description: "Gate state, such as started, approved, blocked, or completed." }),
      contextPackDigest: Type.String({ description: "Digest of the ContextPackV2 artifact governing this phase." }),
      summary: Type.Optional(Type.String({ description: "Short phase summary for Mission Control." })),
    }),
    async execute(_toolCallId, params) {
      return {
        content: [{ type: "text", text: `${params.phase} gate ${params.status}: ${params.contextPackDigest}` }],
        details: {
          source: "fabric-clawhub-agentic-engineering",
          protocol: "research-plan-implement-context-gates",
          phase: params.phase,
          status: params.status,
          contextPackDigest: params.contextPackDigest,
          summary: params.summary ?? null,
        },
      };
    },
  });

  pi.registerTool({
    name: "fabric_clawhub_context_pack_policy",
    label: "Fabric ClawHub ContextPackV2 Policy",
    description: "Declare the context-window policy that makes subagents context forks instead of role labels.",
    parameters: Type.Object({
      phase: Type.String({ description: "Context phase represented by this pack." }),
      contextGoal: Type.String({ description: "What this context window must learn or change." }),
      maxTokens: Type.Number({ description: "Maximum input context tokens for the pack." }),
      compactionThreshold: Type.Number({ description: "Token threshold that triggers compaction." }),
      handoffDigest: Type.String({ description: "Digest used to hand compacted findings to the next phase." }),
    }),
    async execute(_toolCallId, params) {
      return {
        content: [{ type: "text", text: `ContextPackV2 ${params.phase}: ${params.maxTokens} token cap, handoff ${params.handoffDigest}` }],
        details: {
          source: "fabric-clawhub-agentic-engineering",
          schema: "ContextPackV2",
          subagentWorkModel: "context-window-fork",
          agentIdRole: "execution-template",
          phase: params.phase,
          contextGoal: params.contextGoal,
          maxTokens: params.maxTokens,
          compactionThreshold: params.compactionThreshold,
          handoffDigest: params.handoffDigest,
        },
      };
    },
  });

  pi.registerTool({
    name: "fabric_clawhub_context_mode_receipt",
    label: "Fabric ClawHub Context Mode Receipt",
    description: "Publish governed context-mode indexing, retrieval, compaction, or savings telemetry.",
    parameters: Type.Object({
      eventType: Type.String({ description: "Context-mode event type." }),
      packageSource: Type.String({ description: "Pinned context-mode package source." }),
      facade: Type.String({ description: "AgentHub facade that governed the package call." }),
      savedTokenEstimate: Type.Number({ description: "Estimated tokens avoided by compact context routing." }),
      purgeHandle: Type.String({ description: "Mission-scoped handle that can purge indexed artifacts." }),
    }),
    async execute(_toolCallId, params) {
      return {
        content: [{ type: "text", text: `${params.eventType}: ${params.savedTokenEstimate} tokens avoided via ${params.facade}` }],
        details: {
          source: "fabric-clawhub-agentic-engineering",
          eventType: params.eventType,
          packageSource: params.packageSource,
          facade: params.facade,
          savedTokenEstimate: params.savedTokenEstimate,
          purgeHandle: params.purgeHandle,
        },
      };
    },
  });
}
