import React, { useMemo, useState } from "react";

import * as api from "../../../../controller/AgentHubApi";
import { externalLinkOnClick } from "../../openExternalTab";
import { formatDurationMs } from "../logPresentation";
import type { MissionState } from "../missionReducer";
import { buildPiMissionViewModel, type PiApprovalCardView, type PiClarificationCardView, type PiMissionViewModel, type PiToolCardView } from "./piMissionReducer";
import { derivePiMissionEventsFromState } from "./piMissionAdapter";
import { MissionPiRuntimeHost } from "./MissionPiRuntimeHost";
import {
    PI_AI_PACKAGE,
    PI_ARCHITECTURE_LAYER_IDS,
    PI_AGENTIC_ENGINEERING_EXTENSION,
    PI_CODING_AGENT_PACKAGE,
    PI_BABYSITTER_PACKAGE,
    PI_CONTEXT_MODE_PACKAGE,
    PI_EXTENSION_PACKAGES,
    PI_FRONTEND_RUNTIME_PACKAGE,
    PI_LAYERED_ARCHITECTURE,
    PI_LOG_COMPACTION_EXTENSION,
    PI_MCP_ADAPTER_PACKAGE,
    PI_MISSION_UI_EXTENSION,
    PI_ORCHESTRATION_RUNTIME_PACKAGE,
    PI_QRSPI_PHASE_MODEL,
    PI_QRSPI_PROTOCOL,
    PI_SUBAGENTS_PACKAGE,
    PI_TUI_PACKAGE,
} from "./piExtensionPackages";
import type { PiMissionExtensionMetadata, PiMissionUiEvent } from "../events";
import type { WorkloadClientAPI } from "@ms-fabric/workload-client";

interface MissionPiRuntimeSlotView {
    slotId: string;
    agentId: string;
    role: string;
    agentName: string;
    lifecycle: string;
    status: string;
    isActive: boolean;
    reason?: string;
}

interface MissionPiSurfaceProps {
    state: MissionState;
    runtimeSlots: MissionPiRuntimeSlotView[];
    streamStatus?: string | null;
    sessionId: string;
    githubToken: string;
    fabricToken?: string;
    workloadClient: WorkloadClientAPI;
    variant?: "terminal" | "web-ui";
}

function statusLabel(status: string | null | undefined): string {
    if (status === "completed") return "done";
    if (status === "failed" || status === "error") return "error";
    if (status === "waiting" || status === "confirm_required" || status === "approval_required") return "waiting";
    if (status === "queued") return "queued";
    if (status === "blocked") return "blocked";
    if (status === "ok") return "done";
    return "running";
}

function riskLabel(risk: PiApprovalCardView["risk"]): string {
    if (risk === "high") return "high risk";
    if (risk === "medium") return "medium risk";
    return "low risk";
}

function extensionLabel(extension?: PiMissionExtensionMetadata): string | null {
    if (!extension) return null;
    return extension.packageName || extension.label || extension.id || null;
}

function extensionChipLabel(extension: PiMissionExtensionMetadata): string {
    if (extension.packageName && extension.label && extension.packageName !== extension.label) {
        return `${extension.label} ${extension.packageName}`;
    }
    return extension.packageName || extension.label || extension.id || "Pi extension";
}

function formatMetadata(metadata?: Record<string, string | number | boolean | null>): string[] {
    return Object.entries(metadata || {})
        .filter(([, value]) => value !== null && value !== undefined && String(value).trim().length > 0)
        .slice(0, 5)
        .map(([key, value]) => `${key.replace(/[_-]/g, " ")}: ${String(value)}`);
}

function trustLabel(tool: PiToolCardView): string {
    const trust = tool.trust;
    if (!trust) return "summary";
    if (trust.redacted || trust.level === "redacted") return "redacted";
    if (trust.level === "untrusted") return "untrusted";
    return trust.source || "trusted";
}

function redactSecrets(value: string | null | undefined): string {
    return (value || "")
        .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [redacted]")
        .replace(/\b(FABRIC_API_TOKEN|POWERBI_API_TOKEN|GITHUB_TOKEN|AZURE_TOKEN|SECRET_INTERNAL_TRACE_DO_NOT_RENDER)\b/gi, "[redacted]")
        .replace(/\b(token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+/gi, "$1=[redacted]");
}

function eventKey(event: PiMissionUiEvent): string {
    const named = "turnId" in event ? event.turnId : "toolCallId" in event ? event.toolCallId : "requestId" in event ? event.requestId : "artifactId" in event ? event.artifactId : "agentId" in event ? event.agentId : event.type;
    return `${event.seq}-${event.type}-${named}`;
}

function PiCliLine({ event, kind, label, children }: { event: PiMissionUiEvent; kind: string; label: string; children: React.ReactNode }) {
    return (
        <div className={`pi-cli-line pi-cli-line--${kind}`} data-pi-cli-line="true" data-pi-cli-event-type={event.type} data-pi-seq={String(event.seq)} data-pi-kind={kind}>
            <span className="pi-cli-line__label">{label}</span>
            <div className="pi-cli-line__content">{children}</div>
        </div>
    );
}

function PiCliToolStart({ event }: { event: Extract<PiMissionUiEvent, { type: "pi.tool.start" }> }) {
    const extension = extensionLabel(event.extension);
    return (
        <article className="pi-cli-tool pi-cli-tool--pending" data-pi-kind="tool-card" data-pi-cli-line="true" data-pi-cli-event-type={event.type} data-pi-seq={String(event.seq)}>
            <header className="pi-cli-tool__title">
                <span>[tool: {event.toolName}]</span>
                <strong>running</strong>
            </header>
            <div className="pi-cli-tool__body">
                <p>{redactSecrets(event.summary)}</p>
                {event.argsSummary && <pre>{redactSecrets(event.argsSummary)}</pre>}
                <div className="pi-cli-tool__meta">
                    {event.sensitivity && <span>{event.sensitivity}</span>}
                    {event.agentName && <span>{event.agentName}</span>}
                    {extension && <span>{extension}</span>}
                </div>
            </div>
        </article>
    );
}

function PiCliToolEnd({ event, tool }: { event: Extract<PiMissionUiEvent, { type: "pi.tool.end" }>; tool?: PiToolCardView }) {
    const duration = event.durationMs != null ? formatDurationMs(event.durationMs) : null;
    const status = statusLabel(event.status);
    const toolName = tool?.toolName || event.toolCallId;
    const output = event.display?.outputPreview || event.display?.details || event.display?.summary || event.errorPreview || "Tool finished.";
    return (
        <article className={`pi-cli-tool pi-cli-tool--${status}`} data-pi-kind="tool-result" data-pi-cli-line="true" data-pi-cli-event-type={event.type} data-pi-seq={String(event.seq)}>
            <header className="pi-cli-tool__title">
                <span>[tool: {toolName}]</span>
                <strong>{status}</strong>
            </header>
            <div className="pi-cli-tool__body">
                <p>{redactSecrets(output)}</p>
                <div className="pi-cli-tool__meta">
                    {duration && <span>{duration}</span>}
                    {tool && <span>{trustLabel(tool)}</span>}
                    {event.status === "confirm_required" && <span>confirmation required</span>}
                    {extensionLabel(event.extension) && <span>{extensionLabel(event.extension)}</span>}
                </div>
            </div>
        </article>
    );
}

function PiCliApprovalPrompt({ approval, sessionId, githubToken, fabricToken }: {
    approval: PiApprovalCardView;
    sessionId: string;
    githubToken: string;
    fabricToken?: string;
}) {
    const [busyAction, setBusyAction] = useState<"approve" | "decline" | null>(null);
    const [status, setStatus] = useState<string | null>(null);
    const resolve = async (action: "approve" | "decline") => {
        if (busyAction) return;
        setBusyAction(action);
        setStatus(action === "approve" ? "approving" : "declining");
        try {
            await api.resolveApproval(sessionId, approval.requestId, action, null, { githubToken, fabricToken, agentHubSessionId: sessionId });
            setStatus(action === "approve" ? "approval sent" : "decision sent");
        } catch (error) {
            setStatus(error instanceof Error ? error.message.slice(0, 180) : "unable to send decision");
        } finally {
            setBusyAction(null);
        }
    };

    return (
        <article className={`pi-cli-extension-ui pi-cli-extension-ui--approval pi-approval-card pi-approval-card--${approval.risk}`} data-pi-kind="approval-card" data-pi-cli-line="true" data-pi-cli-event-type="pi.approval.request" data-pi-seq={String(approval.seq)} data-request-id={approval.requestId}>
            <header>
                <span>[extension-ui: confirm]</span>
                <strong>{approval.title}</strong>
                <em>{riskLabel(approval.risk)}</em>
            </header>
            {approval.summary && <p>{redactSecrets(approval.summary)}</p>}
            <div className="pi-cli-meta-line">
                {formatMetadata(approval.metadata).map((item) => <span key={item}>{redactSecrets(item)}</span>)}
                {extensionLabel(approval.extension) && <span>{extensionLabel(approval.extension)}</span>}
            </div>
            <div className="pi-cli-actions">
                <button type="button" className="pi-action pi-action--primary" disabled={!!busyAction} onClick={() => void resolve("approve")}>Approve</button>
                <button type="button" className="pi-action" disabled={!!busyAction} onClick={() => void resolve("decline")}>Decline</button>
                {status && <span role="status">{status}</span>}
            </div>
        </article>
    );
}

function PiCliClarificationPrompt({ clarification, sessionId, githubToken, fabricToken }: {
    clarification: PiClarificationCardView;
    sessionId: string;
    githubToken: string;
    fabricToken?: string;
}) {
    const initialValue = clarification.options[0]?.value || "";
    const [answer, setAnswer] = useState(initialValue);
    const [status, setStatus] = useState<string | null>(null);
    const submit = async () => {
        const trimmed = answer.trim();
        if (!trimmed) return;
        setStatus("sending");
        try {
            await api.sendMessage(sessionId, `Pi UI response ${clarification.requestId}: ${trimmed}`, null, { githubToken, fabricToken, agentHubSessionId: sessionId }, "queue");
            setStatus("answer sent");
        } catch (error) {
            setStatus(error instanceof Error ? error.message.slice(0, 180) : "unable to send answer");
        }
    };

    return (
        <article className="pi-cli-extension-ui pi-cli-extension-ui--question pi-clarification-card" data-pi-kind="clarification-card" data-pi-cli-line="true" data-pi-cli-event-type="pi.clarification.request" data-pi-seq={String(clarification.seq)} data-request-id={clarification.requestId}>
            <header>
                <span>[extension-ui: {clarification.control}]</span>
                <strong>{clarification.title}</strong>
                {extensionLabel(clarification.extension) && <em>{extensionLabel(clarification.extension)}</em>}
            </header>
            <p>{redactSecrets(clarification.prompt)}</p>
            <div className="pi-cli-actions">
                {clarification.control === "input" ? (
                    <input aria-label={clarification.title} value={answer} onChange={(event) => setAnswer(event.currentTarget.value)} />
                ) : (
                    <select aria-label={clarification.title} value={answer} onChange={(event) => setAnswer(event.currentTarget.value)}>
                        {clarification.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                )}
                <button type="button" className="pi-action pi-action--primary" onClick={() => void submit()} disabled={!answer.trim()}>Send answer</button>
                {status && <span role="status">{status}</span>}
            </div>
        </article>
    );
}

function PiCliEditor({ sessionId, githubToken, fabricToken }: { sessionId: string; githubToken: string; fabricToken?: string }) {
    const [message, setMessage] = useState("");
    const [mode, setMode] = useState<"queue" | "interrupt">("queue");
    const [status, setStatus] = useState<string | null>(null);
    const submit = async () => {
        const trimmed = message.trim();
        if (!trimmed) return;
        setStatus(mode === "queue" ? "queued" : "interrupting");
        try {
            await api.sendMessage(sessionId, trimmed, null, { githubToken, fabricToken, agentHubSessionId: sessionId }, mode);
            setMessage("");
            setStatus(mode === "queue" ? "message queued" : "interrupt sent");
        } catch (error) {
            setStatus(error instanceof Error ? error.message.slice(0, 180) : "unable to send message");
        }
    };

    return (
        <section className="pi-cli-editor" aria-label="Mission reply editor" data-pi-cli-editor="true">
            <label className="pi-cli-editor__prompt" htmlFor="pi-cli-message-editor">mission&gt;</label>
            <textarea id="pi-cli-message-editor" aria-label="Mission message editor" value={message} onChange={(event) => setMessage(event.currentTarget.value)} />
            <div className="pi-cli-editor__controls">
                <select aria-label="Mission message delivery" value={mode} onChange={(event) => setMode(event.currentTarget.value as "queue" | "interrupt")}>
                    <option value="queue">queue</option>
                    <option value="interrupt">interrupt</option>
                </select>
                <button type="button" className="pi-action pi-action--primary" onClick={() => void submit()} disabled={!message.trim()}>Queue</button>
                {status && <span role="status">{status}</span>}
            </div>
        </section>
    );
}

function renderPiCliEvent(
    event: PiMissionUiEvent,
    model: PiMissionViewModel,
    toolsById: Map<string, PiToolCardView>,
    props: Pick<MissionPiSurfaceProps, "sessionId" | "githubToken" | "fabricToken" | "workloadClient">,
): React.ReactNode {
    switch (event.type) {
        case "pi.orchestration.start":
            return (
                <PiCliLine event={event} kind="orchestration-start" label="system">
                    <span>Mission stream connected</span>
                    <span>{event.toolCount ? `${event.toolCount} tools available` : "Tools ready"}</span>
                </PiCliLine>
            );
        case "pi.subagent.update":
            return (
                <PiCliLine event={event} kind="subagent-update" label="subagent">
                    <strong>{event.agentName || event.agentId}</strong>
                    <span>{event.state} - {redactSecrets(event.task || event.role)}</span>
                    {event.summary && <small>{redactSecrets(event.summary)}</small>}
                </PiCliLine>
            );
        case "pi.turn.start":
            return (
                <PiCliLine event={event} kind="assistant-turn-start" label="assistant">
                    <strong>{event.agentName || event.agentId}</strong>
                    <span>{[event.model, event.title].filter(Boolean).join(" - ")}</span>
                </PiCliLine>
            );
        case "pi.turn.delta":
            return (
                <article className="pi-cli-message pi-cli-message--assistant" data-pi-kind="assistant-turn" data-pi-cli-line="true" data-pi-cli-event-type={event.type} data-pi-seq={String(event.seq)}>
                    <span className="pi-cli-message__label">assistant</span>
                    <p>{redactSecrets(event.textDelta)}</p>
                </article>
            );
        case "pi.turn.end":
            return (
                <PiCliLine event={event} kind="assistant-turn-end" label="assistant">
                    <span>turn {statusLabel(event.status)}</span>
                    {event.reason && <small>{redactSecrets(event.reason)}</small>}
                </PiCliLine>
            );
        case "pi.tool.start":
            return <PiCliToolStart event={event} />;
        case "pi.tool.end":
            return <PiCliToolEnd event={event} tool={toolsById.get(event.toolCallId)} />;
        case "pi.artifact.upsert":
            return (
                <article className="pi-cli-artifact" data-pi-kind="artifact-card" data-pi-cli-line="true" data-pi-cli-event-type={event.type} data-pi-seq={String(event.seq)}>
                    <header>
                        <span>[artifact: {event.kind}]</span>
                        <strong>{event.title}</strong>
                        {event.webUrl && (
                            <a href={event.webUrl} target="_blank" rel="noopener noreferrer" onClick={externalLinkOnClick(props.workloadClient, event.webUrl)} aria-label={`Open ${event.title}`}>open</a>
                        )}
                    </header>
                    {event.summary && <p>{redactSecrets(event.summary)}</p>}
                    {event.previewText && <pre>{redactSecrets(event.previewText)}</pre>}
                    {extensionLabel(event.extension) && <div className="pi-cli-meta-line"><span>{extensionLabel(event.extension)}</span></div>}
                </article>
            );
        case "pi.approval.request": {
            const approval = model.approvals.find((item) => item.requestId === event.requestId);
            return approval ? <PiCliApprovalPrompt approval={approval} sessionId={props.sessionId} githubToken={props.githubToken} fabricToken={props.fabricToken} /> : null;
        }
        case "pi.clarification.request": {
            const clarification = model.clarifications.find((item) => item.requestId === event.requestId);
            return clarification ? <PiCliClarificationPrompt clarification={clarification} sessionId={props.sessionId} githubToken={props.githubToken} fabricToken={props.fabricToken} /> : null;
        }
        case "pi.ui.request":
            return (
                <PiCliLine event={event} kind="ui-marker" label="ui">
                    <strong>{event.title}</strong>
                    <span>{redactSecrets(event.message || event.widgetKind || event.control)}</span>
                    <small>{[event.control, event.status, extensionLabel(event.extension)].filter(Boolean).join(" - ")}</small>
                </PiCliLine>
            );
        case "pi.retry":
            return (
                <PiCliLine event={event} kind="retry-marker" label="retry">
                    <strong>{event.status}</strong>
                    {event.reason && <span>{redactSecrets(event.reason)}</span>}
                </PiCliLine>
            );
        case "pi.context.compaction":
            return (
                <PiCliLine event={event} kind="compaction-marker" label="compact">
                    <strong>{event.status}</strong>
                    {event.summary && <span>{redactSecrets(event.summary)}</span>}
                </PiCliLine>
            );
        default:
            return null;
    }
}

export function MissionPiSurface({ state, runtimeSlots, streamStatus, sessionId, githubToken, fabricToken, workloadClient, variant = "terminal" }: MissionPiSurfaceProps) {
    const piEvents = useMemo(() => derivePiMissionEventsFromState(state), [state]);
    const model = useMemo(() => buildPiMissionViewModel(piEvents), [piEvents]);
    const sortedEvents = useMemo(() => [...piEvents].sort((a, b) => (a.seq || 0) - (b.seq || 0)), [piEvents]);
    const toolsById = useMemo(() => new Map(model.tools.map((tool) => [tool.toolCallId, tool])), [model.tools]);
    const activeSlot = runtimeSlots.find((slot) => slot.isActive) || runtimeSlots.find((slot) => slot.status === "running") || null;
    const activeSubagent = [...model.subagents].reverse().find((subagent) => subagent.state === "running" || subagent.state === "blocked") || null;
    const activeTurn = model.turns[model.turns.length - 1] || null;
    const activeAgentName = activeSlot?.agentName || activeSubagent?.agentName || activeTurn?.agentName || "Agent";
    const currentStatus = statusLabel(activeSlot?.status || activeSubagent?.state || activeTurn?.status || streamStatus || "waiting");
    const visibleExtensionNames = model.extensions.filter((extension) => !PI_EXTENSION_PACKAGES.some((pkg) => pkg.label === extension || pkg.packageName === extension || pkg.source === extension)).slice(0, 4);
    const backendToolCount = model.orchestration?.toolCount || model.availableTools.length;
    const rpiProtocol = model.orchestration?.rpiProtocol || "research-plan-implement-context-gates";
    const qrspiProtocol = model.orchestration?.qrspiProtocol || PI_QRSPI_PROTOCOL;
    const qrspiPhaseModel = (model.orchestration?.qrspiPhaseModel?.length ? model.orchestration.qrspiPhaseModel : PI_QRSPI_PHASE_MODEL).join("->");
    const qrspiQuestionPolicy = "question-first-neutral";
    const qrspiResearchPolicy = "blind-factual-before-design";
    const qrspiInstructionBudget = "instructions-not-only-tokens";
    const qrspiVerticalSlicePolicy = "thin-end-to-end-slice-before-horizontal-layers";
    const qrspiBacktrackPolicy = "phase-backtracking-enabled";
    const qrspiReviewPolicy = "fresh-context-code-review-required";
    const contextPackSchema = model.orchestration?.contextPackSchema || "ContextPackV2";
    const subagentWorkModel = model.orchestration?.subagentWorkModel || "context-window-fork";
    const contextModeFacade = model.orchestration?.contextModeFacade || "agenthub-governed-context-mode";
    const contextWindowPolicy = "agent_id=execution-template;context_pack=primary-work-unit;implementation=approved-plan-plus-selected-snippets;verification=fresh-plan-evidence-receipts";

    if (variant === "web-ui") {
        return (
            <div
                className="pi-mission-surface pi-mission-surface--web-ui"
                role="log"
                aria-label="Mission stream"
                data-runtime="pi"
                data-pi-runtime-package={PI_FRONTEND_RUNTIME_PACKAGE.packageName}
                data-pi-architecture-layers={PI_ARCHITECTURE_LAYER_IDS}
                data-pi-application-layer={[PI_CODING_AGENT_PACKAGE.packageName, PI_FRONTEND_RUNTIME_PACKAGE.packageName, PI_MISSION_UI_EXTENSION.packageName, PI_LOG_COMPACTION_EXTENSION.packageName, PI_AGENTIC_ENGINEERING_EXTENSION.packageName].join(",")}
                data-pi-core-layer={PI_ORCHESTRATION_RUNTIME_PACKAGE.packageName}
                data-pi-foundation-layer={[PI_AI_PACKAGE.packageName, PI_TUI_PACKAGE.packageName, PI_MCP_ADAPTER_PACKAGE.packageName].join(",")}
                data-pi-cli-package={PI_CODING_AGENT_PACKAGE.packageName}
                data-pi-tui-package={PI_TUI_PACKAGE.packageName}
                data-pi-ai-package={PI_AI_PACKAGE.packageName}
                data-pi-mcp-adapter-package={PI_MCP_ADAPTER_PACKAGE.packageName}
                data-pi-mcp-access-mode="pi-mcp-adapter-proxy-via-agenthub-policy"
                data-pi-context-mode-package={PI_CONTEXT_MODE_PACKAGE.packageName}
                data-pi-context-mode-status={PI_CONTEXT_MODE_PACKAGE.adoption}
                data-pi-babysitter-package={PI_BABYSITTER_PACKAGE.packageName}
                data-pi-babysitter-status={PI_BABYSITTER_PACKAGE.adoption}
                data-pi-log-compaction-package={PI_LOG_COMPACTION_EXTENSION.packageName}
                data-pi-log-compaction-status={PI_LOG_COMPACTION_EXTENSION.adoption || "active"}
                data-pi-agentic-engineering-package={PI_AGENTIC_ENGINEERING_EXTENSION.packageName}
                data-pi-agentic-engineering-status={PI_AGENTIC_ENGINEERING_EXTENSION.adoption || "active"}
                data-pi-rpi-protocol={rpiProtocol}
                data-pi-qrspi-protocol={qrspiProtocol}
                data-pi-qrspi-phase-model={qrspiPhaseModel}
                data-pi-qrspi-question-policy={qrspiQuestionPolicy}
                data-pi-qrspi-research-policy={qrspiResearchPolicy}
                data-pi-qrspi-instruction-budget={qrspiInstructionBudget}
                data-pi-qrspi-vertical-slice-policy={qrspiVerticalSlicePolicy}
                data-pi-qrspi-backtrack-policy={qrspiBacktrackPolicy}
                data-pi-qrspi-review-policy={qrspiReviewPolicy}
                data-pi-context-pack-schema={contextPackSchema}
                data-pi-context-mode-facade={contextModeFacade}
                data-pi-context-window-policy={contextWindowPolicy}
                data-pi-subagent-work-model={subagentWorkModel}
                data-pi-orchestration-runtime="pi"
                data-pi-orchestration-package={PI_ORCHESTRATION_RUNTIME_PACKAGE.packageName}
                data-pi-orchestration-harness={model.orchestration?.orchestrationHarness || "pi-agent-core"}
                data-pi-subagent-runtime={model.orchestration?.subagentRuntime || "pi-subagents"}
                data-pi-subagent-package={model.orchestration?.subagentPackage || PI_SUBAGENTS_PACKAGE.packageName}
                data-pi-subagent-stream={model.subagentStatuses.length > 0 ? "status-control-result" : "awaiting-status"}
                data-pi-observability-stream="pi-subagents-native-events"
                data-pi-backend-tool-count={String(backendToolCount)}
                data-pi-tool-registry={model.orchestration?.toolRegistry || "agenthub-tool-runtime"}
                data-pi-extension-surface={PI_MISSION_UI_EXTENSION.packageName}
                data-pi-execution-stream="typed-pi-events"
                data-pi-stream-interface="pi-web-ui-agent-interface"
                data-pi-extension-packages={PI_EXTENSION_PACKAGES.map((pkg) => pkg.source).join(",")}
                data-design-intent="native-pi-web-ui-session"
            >
                <MissionPiRuntimeHost
                    state={state}
                    model={model}
                    sessionId={sessionId}
                    githubToken={githubToken}
                    fabricToken={fabricToken}
                    fullPage
                />
            </div>
        );
    }

    return (
        <div
            className="pi-mission-surface"
            role="log"
            aria-label="Mission stream"
            data-runtime="pi"
            data-pi-runtime-package={PI_FRONTEND_RUNTIME_PACKAGE.packageName}
            data-pi-architecture-layers={PI_ARCHITECTURE_LAYER_IDS}
                data-pi-application-layer={[PI_CODING_AGENT_PACKAGE.packageName, PI_FRONTEND_RUNTIME_PACKAGE.packageName, PI_MISSION_UI_EXTENSION.packageName, PI_LOG_COMPACTION_EXTENSION.packageName, PI_AGENTIC_ENGINEERING_EXTENSION.packageName].join(",")}
            data-pi-core-layer={PI_ORCHESTRATION_RUNTIME_PACKAGE.packageName}
            data-pi-foundation-layer={[PI_AI_PACKAGE.packageName, PI_TUI_PACKAGE.packageName, PI_MCP_ADAPTER_PACKAGE.packageName].join(",")}
            data-pi-cli-package={PI_CODING_AGENT_PACKAGE.packageName}
            data-pi-tui-package={PI_TUI_PACKAGE.packageName}
            data-pi-ai-package={PI_AI_PACKAGE.packageName}
            data-pi-mcp-adapter-package={PI_MCP_ADAPTER_PACKAGE.packageName}
            data-pi-mcp-access-mode="pi-mcp-adapter-proxy-via-agenthub-policy"
            data-pi-context-mode-package={PI_CONTEXT_MODE_PACKAGE.packageName}
            data-pi-context-mode-status={PI_CONTEXT_MODE_PACKAGE.adoption}
            data-pi-babysitter-package={PI_BABYSITTER_PACKAGE.packageName}
            data-pi-babysitter-status={PI_BABYSITTER_PACKAGE.adoption}
            data-pi-log-compaction-package={PI_LOG_COMPACTION_EXTENSION.packageName}
            data-pi-log-compaction-status={PI_LOG_COMPACTION_EXTENSION.adoption || "active"}
            data-pi-agentic-engineering-package={PI_AGENTIC_ENGINEERING_EXTENSION.packageName}
            data-pi-agentic-engineering-status={PI_AGENTIC_ENGINEERING_EXTENSION.adoption || "active"}
            data-pi-rpi-protocol={rpiProtocol}
            data-pi-qrspi-protocol={qrspiProtocol}
            data-pi-qrspi-phase-model={qrspiPhaseModel}
            data-pi-qrspi-question-policy={qrspiQuestionPolicy}
            data-pi-qrspi-research-policy={qrspiResearchPolicy}
            data-pi-qrspi-instruction-budget={qrspiInstructionBudget}
            data-pi-qrspi-vertical-slice-policy={qrspiVerticalSlicePolicy}
            data-pi-qrspi-backtrack-policy={qrspiBacktrackPolicy}
            data-pi-qrspi-review-policy={qrspiReviewPolicy}
            data-pi-context-pack-schema={contextPackSchema}
            data-pi-context-mode-facade={contextModeFacade}
            data-pi-context-window-policy={contextWindowPolicy}
            data-pi-subagent-work-model={subagentWorkModel}
            data-pi-orchestration-runtime="pi"
            data-pi-orchestration-package={PI_ORCHESTRATION_RUNTIME_PACKAGE.packageName}
            data-pi-orchestration-harness={model.orchestration?.orchestrationHarness || "pi-agent-core"}
            data-pi-subagent-runtime={model.orchestration?.subagentRuntime || "pi-subagents"}
            data-pi-subagent-package={model.orchestration?.subagentPackage || PI_SUBAGENTS_PACKAGE.packageName}
            data-pi-subagent-stream={model.subagentStatuses.length > 0 ? "status-control-result" : "awaiting-status"}
            data-pi-observability-stream="pi-subagents-native-events"
            data-pi-backend-tool-count={String(backendToolCount)}
            data-pi-tool-registry={model.orchestration?.toolRegistry || "agenthub-tool-runtime"}
            data-pi-extension-surface={PI_MISSION_UI_EXTENSION.packageName}
            data-pi-execution-stream="typed-pi-events"
            data-pi-stream-interface="pi-cli-extension"
            data-pi-extension-packages={PI_EXTENSION_PACKAGES.map((pkg) => pkg.source).join(",")}
            data-design-intent="embedded-pi-cli-terminal"
        >
            <section className="pi-cli-terminal" aria-label="Embedded mission terminal" data-pi-cli-surface="embedded">
                <header className="pi-cli-startup" aria-label="Mission startup header">
                    <div className="pi-cli-startup__title" data-pi-cli-line="true" data-pi-cli-event-type="startup">
                        <span className="pi-cli-brand">Mission</span>
                        <span>live</span>
                        <strong>{activeAgentName}</strong>
                        <em>{currentStatus}</em>
                    </div>
                    <div className="pi-cli-startup__context" data-pi-cli-line="true" data-pi-cli-event-type="startup-context">
                        <span>Session {sessionId}</span>
                        <span>Agent updates and tool results appear below</span>
                    </div>
                </header>

                <div className="pi-cli-transcript" aria-label="Mission transcript" data-pi-cli-transcript="true">
                    {sortedEvents.map((event) => <React.Fragment key={eventKey(event)}>{renderPiCliEvent(event, model, toolsById, { sessionId, githubToken, fabricToken, workloadClient })}</React.Fragment>)}
                    {sortedEvents.length === 0 && (
                        <div className="pi-cli-empty" role="status" data-pi-cli-line="true">
                            <span>pi</span>
                            <strong>{redactSecrets(activeSlot?.reason || streamStatus || "waiting for the first mission update")}</strong>
                        </div>
                    )}
                </div>

                <PiCliEditor sessionId={sessionId} githubToken={githubToken} fabricToken={fabricToken} />

                <footer className="pi-cli-footer" aria-label="Mission footer" data-pi-cli-footer="true">
                    <span>Mission activity</span>
                    <span>events {model.rawEventCount}</span>
                    <span>tools {backendToolCount}</span>
                    <span>calls {model.tools.length}</span>
                    <span>waiting {model.approvals.length + model.clarifications.length}</span>
                </footer>
            </section>

            <MissionPiRuntimeHost state={state} model={model} sessionId={sessionId} />
            <details className="pi-proof-drawer" aria-label="Pi orchestration proof" data-pi-kind="orchestration-proof">
                <summary>
                    <span>Pi verified</span>
                    <strong>{PI_CODING_AGENT_PACKAGE.packageName}</strong>
                </summary>
                <section className="pi-orchestration-panel">
                    {PI_LAYERED_ARCHITECTURE.map((layer) => (
                        <article key={layer.id} data-pi-architecture-layer={layer.id}>
                            <span>{layer.id}</span>
                            <strong>{layer.packages.map((pkg) => pkg.packageName).join(" + ")}</strong>
                        </article>
                    ))}
                    <article>
                        <span>cli</span>
                        <strong>{PI_CODING_AGENT_PACKAGE.packageName}</strong>
                    </article>
                    <article>
                        <span>tui</span>
                        <strong>{PI_TUI_PACKAGE.packageName}</strong>
                    </article>
                    <article>
                        <span>mcp</span>
                        <strong>{PI_MCP_ADAPTER_PACKAGE.packageName}</strong>
                    </article>
                    <article>
                        <span>context</span>
                        <strong>{PI_CONTEXT_MODE_PACKAGE.packageName}</strong>
                    </article>
                    <article>
                        <span>governance</span>
                        <strong>{PI_BABYSITTER_PACKAGE.packageName}</strong>
                    </article>
                    <article>
                        <span>logs</span>
                        <strong>{PI_LOG_COMPACTION_EXTENSION.packageName}</strong>
                    </article>
                    <article>
                        <span>RPI</span>
                        <strong>{PI_AGENTIC_ENGINEERING_EXTENSION.packageName}</strong>
                    </article>
                    <article>
                        <span>QRSPI</span>
                        <strong>{qrspiProtocol}</strong>
                    </article>
                    <article>
                        <span>phases</span>
                        <strong>{qrspiPhaseModel}</strong>
                    </article>
                    <article>
                        <span>budget</span>
                        <strong>{qrspiInstructionBudget}</strong>
                    </article>
                    <article>
                        <span>slice</span>
                        <strong>{qrspiVerticalSlicePolicy}</strong>
                    </article>
                    <article>
                        <span>backtrack</span>
                        <strong>{qrspiBacktrackPolicy}</strong>
                    </article>
                    <article>
                        <span>context pack</span>
                        <strong>{contextPackSchema}</strong>
                    </article>
                    <article>
                        <span>stream</span>
                        <strong>{model.orchestration?.streamTransport || "agenthub-sse-to-pi-extension"}</strong>
                    </article>
                    <article>
                        <span>harness</span>
                        <strong>{model.orchestration?.orchestrationHarness || "pi-agent-core"}</strong>
                    </article>
                    <article>
                        <span>tools</span>
                        <strong>{backendToolCount}</strong>
                    </article>
                </section>
                <div className="pi-extension-strip" aria-label="Pi extensions used">
                    {PI_EXTENSION_PACKAGES.map((extension) => (
                        <span key={extension.source} className="pi-extension-chip" data-pi-package={extension.source} data-pi-package-status={extension.adoption || "active"}>{extensionChipLabel(extension)}</span>
                    ))}
                    {visibleExtensionNames.map((extension) => (
                        <span key={extension} className="pi-extension-chip">{extension}</span>
                    ))}
                </div>
            </details>
            <details className="pi-trace-drawer" aria-label="Pi raw event trace">
                <summary>Diagnostics - {model.rawEventCount} events</summary>
                <ol>
                    {piEvents.slice(-40).map((event) => <li key={`${event.seq}-${event.type}`}>{event.seq}: {event.type}</li>)}
                </ol>
            </details>
        </div>
    );
}
