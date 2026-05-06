import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";

export default function fabricClawHubMissionUi(pi: ExtensionAPI) {
  pi.on("session_start", async (_event, ctx) => {
    ctx.ui.notify("Fabric ClawHub Mission UI extension loaded", "info");
    ctx.ui.setStatus("fabric-clawhub", "Pi orchestration bridge active");
  });

  pi.registerTool({
    name: "fabric_clawhub_pi_orchestration_start",
    label: "Fabric ClawHub Pi Orchestration Start",
    description: "Declare that Mission Control execution is attached to the local Pi extension surface.",
    parameters: Type.Object({
      runtimePackage: Type.String({ description: "Pi orchestration package used for the mission." }),
      executionSurfaceExtension: Type.String({ description: "Pi extension rendering the live execution surface." }),
      streamTransport: Type.String({ description: "Transport feeding typed Pi events to the extension surface." }),
    }),
    async execute(_toolCallId, params) {
      return {
        content: [{ type: "text", text: `Pi orchestration attached via ${params.runtimePackage}` }],
        details: {
          source: "fabric-clawhub-mission-ui",
          runtime: "pi",
          runtimePackage: params.runtimePackage,
          executionSurfaceExtension: params.executionSurfaceExtension,
          streamTransport: params.streamTransport,
        },
      };
    },
  });

  pi.registerTool({
    name: "fabric_clawhub_mission_ui_event",
    label: "Fabric ClawHub Mission UI Event",
    description: "Publish a structured Mission Control UI event for the embedded Pi frontend host.",
    parameters: Type.Object({
      kind: Type.String({ description: "Event kind to surface in Mission Control." }),
      title: Type.String({ description: "Short title shown in the frontend." }),
      summary: Type.Optional(Type.String({ description: "Optional event summary." })),
    }),
    async execute(_toolCallId, params) {
      return {
        content: [{ type: "text", text: `${params.kind}: ${params.title}${params.summary ? ` - ${params.summary}` : ""}` }],
        details: {
          source: "fabric-clawhub-mission-ui",
          kind: params.kind,
          title: params.title,
          summary: params.summary ?? null,
        },
      };
    },
  });
}
