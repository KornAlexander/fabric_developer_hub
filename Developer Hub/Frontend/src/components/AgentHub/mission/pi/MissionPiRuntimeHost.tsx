import React, { useEffect, useMemo, useRef, useState } from "react";
import { Agent, type AgentMessage, type AgentTool } from "@mariozechner/pi-agent-core";
import { AssistantMessageEventStream, type AssistantMessage, type Context, type Model, type SimpleStreamOptions, type ToolResultMessage, type Usage } from "@mariozechner/pi-ai";
import {
    AppStorage,
    ChatPanel,
    CustomProvidersStore,
    defaultConvertToLlm,
    getAppStorage,
    IndexedDBStorageBackend,
    ProviderKeysStore,
    SessionsStore,
    SettingsStore,
    setAppStorage,
} from "@mariozechner/pi-web-ui";
import "@mariozechner/pi-web-ui/app.css";

import * as api from "../../../../controller/AgentHubApi";
import { formatVisibleRuntimeText } from "../logPresentation";
import type { LogEntry, MissionState } from "../missionReducer";
import type { PiMissionViewModel, PiToolCardView } from "./piMissionReducer";
import { PI_AGENTIC_ENGINEERING_EXTENSION, PI_EXTENSION_PACKAGES, PI_FRONTEND_RUNTIME_PACKAGE, PI_LOG_COMPACTION_EXTENSION, PI_QRSPI_PHASE_MODEL, PI_QRSPI_PROTOCOL } from "./piExtensionPackages";
import { PI_LOG_COMPACTION_REFRESH_MS, applyPiLogCompactionExtension, type PiLiveLogDetailRow, type PiSelfCollapsingLiveLogRow } from "./piLogCompactionExtension";

type PiChatPanelElement = InstanceType<typeof ChatPanel> & HTMLElement;

interface MissionPiRuntimeHostProps {
    state: MissionState;
    model: PiMissionViewModel;
    sessionId: string;
    githubToken?: string;
    fabricToken?: string;
    fullPage?: boolean;
}

let appStorageReady = false;

function ensurePiAppStorage() {
    if (appStorageReady || typeof window === "undefined") return;

    const settings = new SettingsStore();
    const providerKeys = new ProviderKeysStore();
    const sessions = new SessionsStore();
    const customProviders = new CustomProvidersStore();
    const backend = new IndexedDBStorageBackend({
        dbName: "fabric-clawhub-pi-web-ui",
        version: 1,
        stores: [
            settings.getConfig(),
            SessionsStore.getMetadataConfig(),
            providerKeys.getConfig(),
            customProviders.getConfig(),
            sessions.getConfig(),
        ],
    });

    settings.setBackend(backend);
    providerKeys.setBackend(backend);
    customProviders.setBackend(backend);
    sessions.setBackend(backend);
    setAppStorage(new AppStorage(settings, providerKeys, sessions, customProviders, backend));
    appStorageReady = true;
}

function zeroUsage(): Usage {
    return {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        totalTokens: 0,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    };
}

const AGENTHUB_PI_MODEL: Model<"agenthub-mission"> = {
    id: "agenthub-pi-bridge",
    name: "AgentHub Pi Bridge",
    api: "agenthub-mission",
    provider: "fabric-clawhub",
    baseUrl: "agenthub://mission",
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 4096,
};

function timestampFromSeq(seq?: number): number {
    return Date.now() + Math.round((seq || 0) * 10);
}

function stopReason(status: string): AssistantMessage["stopReason"] {
    if (status === "failed") return "error";
    if (status === "waiting" || status === "queued") return "aborted";
    return "stop";
}

function toolResultText(tool: PiToolCardView): string {
    return tool.displaySummary || tool.outputPreview || tool.errorPreview || tool.summary || tool.status;
}

function missionTask(state: MissionState): string {
    return state.composition?.task || "Live AgentHub mission";
}

function safeLiveLogMessage(value?: string | null, maxLength = 220): string {
    const normalized = formatVisibleRuntimeText(value || "")
        .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [redacted]")
        .replace(/\b[A-Z0-9_]*(?:TOKEN|SECRET|KEY)[A-Z0-9_]*\s*=\s*\S+/gi, "[credential redacted]")
        .replace(/\s+/g, " ")
        .trim();
    if (!normalized) return "Mission event received";
    return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}...` : normalized;
}

function shortAgentName(value?: string | null): string {
    const normalized = safeLiveLogMessage(value || "Mission", 44);
    return normalized || "Mission";
}

function rowFromLog(log: LogEntry): PiLiveLogDetailRow | null {
    if (log.logCategory === "trace") return null;
    const message = safeLiveLogMessage(log.message);
    if (!message) return null;
    return {
        type: "detail",
        key: `log-${log.seq}-${log.kind}`,
        seq: log.seq,
        ts: log.ts,
        level: log.level,
        agent: shortAgentName(log.agentName || log.agentId || "Mission"),
        message,
        kind: log.kind,
    };
}

function buildPiLiveLogRows(state: MissionState, model: PiMissionViewModel, nowMs = Date.now()): PiSelfCollapsingLiveLogRow[] {
    const now = new Date(nowMs).toISOString();
    const syntheticTsForSeq = (seq: number) => {
        const seqLag = Math.max(0, (model.latestSeq || seq) - seq);
        return new Date(nowMs - Math.round(seqLag * 1200)).toISOString();
    };
    const rows: PiLiveLogDetailRow[] = state.logs
        .map(rowFromLog)
        .filter((row): row is PiLiveLogDetailRow => !!row);

    if (model.orchestration) {
        rows.push({
            type: "detail",
            key: `pi-orchestration-${model.orchestration.seq}`,
            seq: model.orchestration.seq,
            ts: now,
            level: "info",
            agent: "Mission control",
            message: "Mission stream connected.",
            kind: "orchestration",
        });
    }

    for (const subagent of model.subagents) {
        rows.push({
            type: "detail",
            key: `pi-subagent-${subagent.agentId}-${subagent.seq}`,
            seq: subagent.seq,
            ts: syntheticTsForSeq(subagent.seq),
            level: subagent.state === "failed" ? "error" : subagent.state === "blocked" ? "warn" : "info",
            agent: shortAgentName(subagent.agentName),
            message: safeLiveLogMessage(subagent.summary || subagent.task || subagent.role || subagent.state),
            kind: "subagent",
        });
    }

    for (const turn of model.turns) {
        rows.push({
            type: "detail",
            key: `pi-turn-${turn.turnId}-${turn.endedSeq || turn.startedSeq}`,
            seq: turn.endedSeq || turn.startedSeq,
            ts: syntheticTsForSeq(turn.endedSeq || turn.startedSeq),
            level: turn.status === "failed" ? "error" : turn.status === "waiting" ? "warn" : "info",
            agent: shortAgentName(turn.agentName),
            message: safeLiveLogMessage(turn.text || turn.title || turn.reason || "Assistant turn is running."),
            kind: "turn",
        });
    }

    for (const tool of model.tools) {
        const status = tool.status === "completed" ? "Completed" : tool.status === "failed" ? "Failed" : tool.status === "waiting" ? "Waiting" : "Running";
        rows.push({
            type: "detail",
            key: `pi-tool-${tool.toolCallId}-${tool.endedSeq || tool.startedSeq}`,
            seq: tool.endedSeq || tool.startedSeq,
            ts: syntheticTsForSeq(tool.endedSeq || tool.startedSeq),
            level: tool.status === "failed" ? "error" : tool.status === "waiting" ? "warn" : "info",
            agent: shortAgentName(tool.agentName || tool.agentId || "Tool"),
            message: safeLiveLogMessage(`${status}: ${tool.displaySummary || tool.summary || toolResultText(tool)}`),
            kind: "tool",
        });
    }

    for (const artifact of model.artifacts) {
        rows.push({
            type: "detail",
            key: `pi-artifact-${artifact.artifactId}-${artifact.seq}`,
            seq: artifact.seq,
            ts: syntheticTsForSeq(artifact.seq),
            level: "info",
            agent: shortAgentName(artifact.agentId || "Artifact"),
            message: safeLiveLogMessage(`Artifact ready: ${artifact.summary || artifact.title}`),
            kind: "artifact",
        });
    }

    for (const approval of model.approvals) {
        rows.push({
            type: "detail",
            key: `pi-approval-${approval.requestId}-${approval.seq}`,
            seq: approval.seq,
            ts: syntheticTsForSeq(approval.seq),
            level: approval.risk === "high" ? "error" : "warn",
            agent: shortAgentName(approval.agentId || "Approval"),
            message: safeLiveLogMessage(`Waiting for approval: ${approval.summary || approval.title}`),
            kind: "approval",
        });
    }

    for (const marker of model.markers) {
        rows.push({
            type: "detail",
            key: `pi-marker-${marker.key}`,
            seq: marker.seq,
            ts: syntheticTsForSeq(marker.seq),
            level: marker.status === "failed" ? "error" : "info",
            agent: "Mission control",
            message: safeLiveLogMessage(marker.message || marker.title),
            kind: marker.kind,
        });
    }

    const seen = new Set<string>();
    const compactableRows = rows
        .filter((row) => {
            const signature = `${row.seq}:${row.agent}:${row.message}`;
            if (seen.has(signature)) return false;
            seen.add(signature);
            return true;
        })
        .sort((a, b) => a.seq - b.seq);

    return applyPiLogCompactionExtension(compactableRows, { nowMs });
}

function displayTime(value: string): string {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "--:--:--";
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatMs(value?: number): string | null {
    if (value == null || !Number.isFinite(value)) return null;
    if (value < 1000) return `${Math.max(0, Math.round(value))} ms`;
    const seconds = value / 1000;
    if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m ${Math.round(seconds % 60)}s`;
}

function logKindLabel(kind: string): string {
    return (kind || "activity").replace(/[_-]/g, " ");
}

function logStateLabel(row: PiLiveLogDetailRow, collapsedDetail: boolean): string {
    if (collapsedDetail) return "hidden detail";
    return row.ageState === "older" ? "older" : "current";
}

function hiddenDetailCount(rows: PiSelfCollapsingLiveLogRow[]): number {
    return rows.reduce((count, row) => count + (row.type === "rollup" ? row.detailCount : 0), 0);
}

function latestLogSeq(rows: PiSelfCollapsingLiveLogRow[]): number {
    return rows.reduce((latest, row) => Math.max(latest, row.seq), 0);
}

interface PiAgentWorkSummaryRow {
    key: string;
    kind: "status" | "control" | "result" | "async";
    agent: string;
    state: string;
    summary: string;
    meta: string[];
    seq: number;
    runId: string;
}

function friendlyWorkState(value?: string | null): string {
    const normalized = String(value || "running").toLowerCase().replace(/[_-]/g, " ");
    if (normalized === "complete" || normalized === "completed" || normalized === "done" || normalized === "ok") return "done";
    if (normalized === "needs attention" || normalized === "blocked" || normalized === "paused" || normalized === "confirm required") return "needs review";
    if (normalized === "failed" || normalized === "error") return "failed";
    if (normalized === "queued" || normalized === "pending") return "queued";
    return normalized || "running";
}

function focusPiLiveLogSeq(seq: number): void {
    if (typeof document === "undefined") return;
    const node = document.querySelector<HTMLElement>(`[data-pi-log-seq="${String(seq)}"]`);
    if (!node) return;
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    node.classList.add("is-focus-pulse");
    window.setTimeout(() => node.classList.remove("is-focus-pulse"), 1800);
}

function workRowPriority(row: PiAgentWorkSummaryRow): number {
    if (row.kind === "control" || /needs review|failed|error/i.test(row.state)) return 4;
    if (row.kind === "result") return 3;
    if (row.kind === "async") return 2;
    return 1;
}

function mergeWorkRows(rows: PiAgentWorkSummaryRow[]): PiAgentWorkSummaryRow[] {
    const byAgent = new Map<string, PiAgentWorkSummaryRow>();
    for (const row of [...rows].sort((a, b) => a.seq - b.seq)) {
        const key = shortAgentName(row.agent).toLowerCase().replace(/\s+/g, "") || row.runId;
        const current = byAgent.get(key);
        if (!current) {
            byAgent.set(key, { ...row, state: friendlyWorkState(row.state) });
            continue;
        }
        const rowPriority = workRowPriority(row);
        const currentPriority = workRowPriority(current);
        const promoted = rowPriority > currentPriority || (rowPriority === currentPriority && row.seq >= current.seq) ? row : current;
        const supporting = promoted === row ? current : row;
        byAgent.set(key, {
            ...promoted,
            state: friendlyWorkState(promoted.state),
            summary: safeLiveLogMessage(promoted.summary || supporting.summary, 190),
            meta: Array.from(new Set([...promoted.meta, ...supporting.meta].filter(Boolean))).slice(0, 4),
        });
    }
    return [...byAgent.values()].sort((a, b) => a.seq - b.seq);
}

function PiSubagentsObservabilityPanel({ model }: { model: PiMissionViewModel }) {
    const activeStatuses = model.subagentStatuses.slice(-6);
    const controls = model.subagentControls.slice(-4);
    const results = model.subagentResults.slice(-4);
    const asyncRuns = model.subagentAsync.slice(-4);
    const workRows = mergeWorkRows([
        ...activeStatuses.map((status): PiAgentWorkSummaryRow => ({
            key: `status-${status.runId}-${status.seq}`,
            kind: "status",
            agent: shortAgentName(status.agentName || status.agent),
            state: status.state,
            summary: safeLiveLogMessage(status.summary || status.task || status.currentTool || "Agent activity", 190),
            meta: [status.currentTool && `Tool: ${status.currentTool}`, status.turnCount != null && `${status.turnCount} turn${status.turnCount === 1 ? "" : "s"}`, status.toolCount != null && `${status.toolCount} tool${status.toolCount === 1 ? "" : "s"}`, formatMs(status.durationMs)].filter(Boolean) as string[],
            seq: status.seq,
            runId: status.runId,
        })),
        ...controls.map((control): PiAgentWorkSummaryRow => ({
            key: control.key,
            kind: "control",
            agent: shortAgentName(control.agentName || control.agent),
            state: control.controlType,
            summary: safeLiveLogMessage(control.message || control.reason || "Attention requested", 190),
            meta: [control.currentTool && `Tool: ${control.currentTool}`, formatMs(control.elapsedMs)].filter(Boolean) as string[],
            seq: control.seq,
            runId: control.runId,
        })),
        ...results.map((result): PiAgentWorkSummaryRow => ({
            key: result.key,
            kind: "result",
            agent: shortAgentName(result.agentName || result.agent),
            state: result.status,
            summary: safeLiveLogMessage(result.summary || "Agent finished its assigned work", 190),
            meta: [result.sessionFile && "Session saved", (result.artifactPath || result.artifactPaths) && "Artifacts linked"].filter(Boolean) as string[],
            seq: result.seq,
            runId: result.runId,
        })),
        ...asyncRuns.map((run): PiAgentWorkSummaryRow => ({
            key: `async-${run.asyncId}-${run.seq}`,
            kind: "async",
            agent: shortAgentName(run.agent || run.agents?.[0] || "Background work"),
            state: run.state,
            summary: safeLiveLogMessage(run.summary || "Background verification is running", 190),
            meta: [run.mode, run.outputFile && "Output linked"].filter(Boolean) as string[],
            seq: run.seq,
            runId: run.runId || run.asyncId,
        })),
    ]);

    return (
        <section
            className="pi-subagents-observability"
            aria-label="Agent work summary"
            data-pi-subagents-observability="true"
            data-pi-subagents-runtime={model.orchestration?.subagentRuntime || "pi-subagents"}
            data-pi-subagents-status-count={activeStatuses.length}
            data-pi-subagents-control-count={controls.length}
            data-pi-subagents-result-count={results.length}
        >
            <header className="pi-subagents-observability__header">
                <div>
                    <strong>Agent work summary</strong>
                    <p>Who is active, what needs review, and what finished.</p>
                </div>
                <span>{workRows.filter((row) => row.state === "running").length} active</span>
                <span>{workRows.filter((row) => /review|failed|error/i.test(row.state)).length} needs review</span>
                <span>{workRows.filter((row) => row.state === "done").length} done</span>
            </header>

            <div className="pi-subagents-observability__grid" data-pi-subagents-lane="summary">
                {workRows.length === 0 ? (
                    <div className="pi-subagent-status pi-subagent-status--empty" data-pi-subagents-status-row="true">
                        <strong>Waiting for agent work</strong>
                        <span>New work will appear here as soon as agents report progress.</span>
                    </div>
                ) : workRows.map((row) => (
                    <button
                        key={row.key}
                        type="button"
                        className={`pi-subagent-status pi-subagent-status--${row.state.replace(/\s+/g, "_")}`}
                        onClick={() => focusPiLiveLogSeq(row.seq)}
                        data-pi-subagents-status-row={row.kind === "status" ? "true" : undefined}
                        data-pi-subagents-control-row={row.kind === "control" ? "true" : undefined}
                        data-pi-subagents-result-row={row.kind === "result" ? "true" : undefined}
                        data-pi-subagents-async-row={row.kind === "async" ? "true" : undefined}
                        data-pi-run-id={row.runId}
                        data-pi-state={row.state}
                        data-pi-seq={row.seq}
                        aria-label={`Show ${row.agent} activity in the live log`}
                    >
                        <div>
                            <strong>{row.agent}</strong>
                            <span>{row.state}</span>
                        </div>
                        <p>{row.summary}</p>
                        {row.meta.length > 0 && <footer>{row.meta.map((item) => <span key={item}>{item}</span>)}</footer>}
                    </button>
                ))}
            </div>
        </section>
    );
}

function PiLiveLogDetail({ row, collapsedDetail = false }: { row: PiLiveLogDetailRow; collapsedDetail?: boolean }) {
    const stateLabel = logStateLabel(row, collapsedDetail);
    return (
        <div
            className={`pi-live-log-event pi-live-log-event--${row.level}${collapsedDetail ? " pi-live-log-event--collapsed-detail" : ""}`}
            data-pi-kind="live-log-row"
            data-pi-live-log-row={collapsedDetail ? undefined : "true"}
            data-pi-live-log-detail-row={collapsedDetail ? "true" : undefined}
            data-pi-log-collapse-state={collapsedDetail ? "hidden-detail" : row.ageState || "recent"}
            data-pi-log-seq={row.seq}
            data-pi-log-kind={row.kind}
            data-pi-log-level={row.level}
        >
            <div className="pi-live-log-event__rail" aria-hidden="true"><span /></div>
            <div className="pi-live-log-event__body">
                <div className="pi-live-log-event__headline">
                    <span className="pi-live-log-event__time">{displayTime(row.ts)}</span>
                    <strong>{row.agent}</strong>
                    <p>{row.message}</p>
                </div>
                <div className="pi-live-log-event__meta" data-pi-log-inline-tags="true">
                    <span className="pi-live-log-event__branch" aria-hidden="true">|-</span>
                    <span className="pi-live-log-event__badge">{logKindLabel(row.kind)}</span>
                    <span className="pi-live-log-event__state">{stateLabel}</span>
                </div>
            </div>
        </div>
    );
}

function PiLiveLogRollup({ row }: { row: Extract<PiSelfCollapsingLiveLogRow, { type: "rollup" }> }) {
    return (
        <details
            className={`pi-live-log-rollup pi-live-log-rollup--${row.level}`}
            data-pi-live-log-row="true"
            data-pi-live-log-rollup="true"
            data-pi-log-collapse-state="collapsed"
            data-pi-log-seq={row.seq}
            data-pi-log-kind={row.kind}
            data-pi-log-level={row.level}
            data-pi-log-detail-count={row.detailCount}
            data-pi-log-covered-seq-start={row.coveredSeqStart}
            data-pi-log-covered-seq-end={row.coveredSeqEnd}
        >
            <summary className="pi-live-log-rollup__summary">
                <div className={`pi-live-log-event pi-live-log-event--${row.level} pi-live-log-event--rollup`}>
                    <div className="pi-live-log-event__rail" aria-hidden="true"><span /></div>
                    <div className="pi-live-log-event__body">
                        <div className="pi-live-log-event__headline">
                            <span className="pi-live-log-event__time">{displayTime(row.ts)}</span>
                            <strong>{row.agent}</strong>
                            <p>{row.message}</p>
                        </div>
                        <div className="pi-live-log-event__meta" data-pi-log-inline-tags="true">
                            <span className="pi-live-log-event__branch" aria-hidden="true">|-</span>
                            <span className="pi-live-log-event__badge">compacted</span>
                            <span className="pi-live-log-event__state">{row.detailCount} details</span>
                        </div>
                    </div>
                </div>
            </summary>
            <div className="pi-live-log-rollup__details">
                {row.details.map((detail) => <PiLiveLogDetail key={detail.key} row={detail} collapsedDetail />)}
            </div>
        </details>
    );
}

function PiLiveLogStrip({ rows }: { rows: PiSelfCollapsingLiveLogRow[] }) {
    const logRef = useRef<HTMLDivElement | null>(null);
    const followRef = useRef(true);
    const collapsedCount = rows.filter((row) => row.type === "rollup").length;
    const detailRows = rows.filter((row) => row.type === "detail").length;
    const hiddenRows = hiddenDetailCount(rows);
    const latestSeq = latestLogSeq(rows);
    const policyLabel = `Newest rows stay open · older rows summarize`;
    const scrollToLatest = () => {
        const node = logRef.current;
        if (node) node.scrollTo({ top: node.scrollHeight, behavior: "auto" });
    };
    const onScroll = () => {
        const node = logRef.current;
        if (!node) return;
        followRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96;
    };

    useEffect(() => {
        const frame = window.requestAnimationFrame(scrollToLatest);
        return () => window.cancelAnimationFrame(frame);
    }, []);

    useEffect(() => {
        if (!followRef.current) return;
        const frame = window.requestAnimationFrame(scrollToLatest);
        return () => window.cancelAnimationFrame(frame);
    }, [rows.length, latestSeq]);

    if (rows.length === 0) {
        return (
            <section className="pi-runtime-host__live-log-shell" aria-label="Mission activity log" data-pi-live-log="true" data-pi-log-execution-streaming-ui="agenthub-code-inspired" data-pi-log-visual-language="agenthub-code-tree" data-pi-log-compaction-extension={PI_LOG_COMPACTION_EXTENSION.packageName} data-pi-live-log-row-count="0" data-pi-live-log-collapsed-count="0" data-pi-live-log-hidden-detail-count="0">
                <header className="pi-live-log-header">
                    <div>
                        <span className="pi-live-log-header__eyebrow">execution stream</span>
                        <strong>Mission activity</strong>
                    </div>
                    <span className="pi-live-log-extension-chip" data-pi-log-compaction-chip="true">Auto-summary</span>
                </header>
                <div className="pi-live-log-policy" data-pi-log-compaction-policy="true">
                    <span>{policyLabel}</span>
                    <span>waiting</span>
                </div>
                <div ref={logRef} className="pi-runtime-host__live-log" onScroll={onScroll}>
                    <div className="pi-live-log-event pi-live-log-event--empty" data-pi-kind="live-log-empty">
                        <div className="pi-live-log-event__rail" aria-hidden="true"><span /></div>
                        <div className="pi-live-log-event__body">
                            <div className="pi-live-log-event__meta">
                                <span className="pi-live-log-event__time">--:--:--</span>
                                <strong>Mission control</strong>
                                <span className="pi-live-log-event__badge">idle</span>
                            </div>
                            <p>Waiting for live mission events.</p>
                        </div>
                    </div>
                </div>
            </section>
        );
    }

    return (
        <section className="pi-runtime-host__live-log-shell" aria-label="Mission activity log" data-pi-live-log="true" data-pi-log-execution-streaming-ui="agenthub-code-inspired" data-pi-log-visual-language="agenthub-code-tree" data-pi-log-compaction-extension={PI_LOG_COMPACTION_EXTENSION.packageName} data-pi-live-log-row-count={rows.length} data-pi-live-log-collapsed-count={collapsedCount} data-pi-live-log-hidden-detail-count={hiddenRows} data-pi-live-log-latest-seq={latestSeq}>
            <header className="pi-live-log-header">
                <div>
                    <span className="pi-live-log-header__eyebrow">execution stream</span>
                    <strong>Mission activity</strong>
                </div>
                <span className="pi-live-log-extension-chip" data-pi-log-compaction-chip="true">Auto-summary</span>
                <div className="pi-live-log-header__metrics" aria-label="Mission activity metrics">
                    <span><strong>{detailRows}</strong> current</span>
                    <span><strong>{collapsedCount}</strong> summaries</span>
                    <span><strong>{hiddenRows}</strong> hidden</span>
                </div>
            </header>
            <div className="pi-live-log-policy" data-pi-log-compaction-policy="true">
                <span>{policyLabel}</span>
                <span>Click a summary to inspect details</span>
                <span>Following latest</span>
            </div>
            <div ref={logRef} className="pi-runtime-host__live-log" onScroll={onScroll} data-auto-follow="true">
                {rows.map((row) => row.type === "rollup"
                    ? <PiLiveLogRollup key={row.key} row={row} />
                    : <PiLiveLogDetail key={row.key} row={row} />)}
            </div>
        </section>
    );
}

function createReplayTool(tool: PiToolCardView): AgentTool<any> {
    return {
        name: tool.toolName,
        label: tool.summary || tool.toolName,
        description: `Mission Control replay tool surfaced through ${tool.extension?.packageName || PI_FRONTEND_RUNTIME_PACKAGE.packageName}.`,
        parameters: { type: "object", additionalProperties: true } as any,
        execute: async () => ({
            content: [{ type: "text", text: toolResultText(tool) }],
            details: {
                replayOnly: true,
                extension: tool.extension,
                status: tool.status,
            },
        }),
    };
}

function createHarnessReplayTool(tool: PiMissionViewModel["availableTools"][number]): AgentTool<any> {
    return {
        name: tool.name,
        label: tool.label || tool.name,
        description: tool.description || `AgentHub backend tool ${tool.name}`,
        parameters: (tool.parameters || { type: "object", additionalProperties: true }) as any,
        execute: async () => ({
            content: [{ type: "text", text: `${tool.name} is available through the AgentHub backend tool runtime.` }],
            details: {
                replayOnly: true,
                execution: tool.execution || "agenthub-tool-runtime-proxy",
                sensitivity: tool.sensitivity,
                autoAllowed: tool.autoAllowed,
            },
        }),
    };
}

function createReplayTools(model: PiMissionViewModel): AgentTool<any>[] {
    const tools = new Map<string, AgentTool<any>>();
    for (const tool of model.availableTools) {
        if (tool.name && !tools.has(tool.name)) tools.set(tool.name, createHarnessReplayTool(tool));
    }
    for (const tool of model.tools) {
        if (!tools.has(tool.toolName)) tools.set(tool.toolName, createReplayTool(tool));
    }
    return [...tools.values()];
}

function extractLastUserText(context: Context): string {
    const user = [...context.messages].reverse().find((message) => message.role === "user");
    if (!user) return "";
    if (typeof user.content === "string") return user.content;
    if (Array.isArray(user.content)) {
        return user.content
            .map((part) => part.type === "text" ? part.text : "")
            .join("\n")
            .trim();
    }
    return "";
}

function createBackendBridgeStream(sessionId: string, githubToken?: string, fabricToken?: string) {
    return (_model: Model<any>, context: Context, options?: SimpleStreamOptions) => {
        const stream = new AssistantMessageEventStream();
        const startedAt = Date.now();
        const baseMessage: AssistantMessage = {
            role: "assistant",
            api: AGENTHUB_PI_MODEL.api,
            provider: AGENTHUB_PI_MODEL.provider,
            model: AGENTHUB_PI_MODEL.id,
            content: [],
            usage: zeroUsage(),
            stopReason: "stop",
            timestamp: startedAt,
        };

        queueMicrotask(async () => {
            const partial: AssistantMessage = { ...baseMessage, content: [] };
            stream.push({ type: "start", partial });

            try {
                const message = extractLastUserText(context);
                if (!message) throw new Error("Message is empty.");
                if (options?.signal?.aborted) throw new Error("Request was aborted.");

                await api.sendMessage(sessionId, message, null, { githubToken, fabricToken }, "queue");
                const text = "Queued in AgentHub. New mission events will stream into this Pi Web UI session.";
                const finalMessage: AssistantMessage = {
                    ...baseMessage,
                    content: [{ type: "text", text }],
                    timestamp: Date.now(),
                };
                stream.push({ type: "text_start", contentIndex: 0, partial: finalMessage });
                stream.push({ type: "text_delta", contentIndex: 0, delta: text, partial: finalMessage });
                stream.push({ type: "text_end", contentIndex: 0, content: text, partial: finalMessage });
                stream.push({ type: "done", reason: "stop", message: finalMessage });
                stream.end(finalMessage);
            } catch (error) {
                const text = error instanceof Error ? error.message : String(error);
                const errorMessage: AssistantMessage = {
                    ...baseMessage,
                    content: [{ type: "text", text }],
                    stopReason: options?.signal?.aborted ? "aborted" : "error",
                    errorMessage: text,
                    timestamp: Date.now(),
                };
                stream.push({ type: "error", reason: errorMessage.stopReason as "aborted" | "error", error: errorMessage });
                stream.end(errorMessage);
            }
        });

        return stream;
    };
}

function buildPiMessages(state: MissionState, model: PiMissionViewModel): AgentMessage[] {
    const messages: AgentMessage[] = [{
        role: "user",
        content: missionTask(state),
        timestamp: timestampFromSeq(0),
    }];

    const toolsByTurn = new Map<string, PiToolCardView[]>();
    for (const tool of model.tools) {
        const turnId = tool.turnId || "mission-runtime";
        toolsByTurn.set(turnId, [...(toolsByTurn.get(turnId) || []), tool]);
    }

    const turns = model.turns.length > 0
        ? model.turns
        : [{
            turnId: "mission-runtime",
            agentId: "pi-agenthub-runtime",
            agentName: "AgentHub Pi runtime",
            status: "running" as const,
            text: "Pi Web UI is attached to Mission Control and waiting for live events.",
            startedSeq: 0,
        }];

    for (const turn of turns) {
        const turnTools = toolsByTurn.get(turn.turnId) || [];
        const assistantMessage: AssistantMessage = {
            role: "assistant",
            api: "agenthub-mission-replay",
            provider: "fabric-clawhub",
            model: turn.model || "pi-web-ui",
            content: [
                { type: "text", text: turn.text || `${turn.agentName} is running.` },
                ...turnTools.map((tool) => ({
                    type: "toolCall" as const,
                    id: tool.toolCallId,
                    name: tool.toolName,
                    arguments: {
                        tool: tool.toolName,
                        summary: tool.summary,
                        args: tool.argsSummary,
                        extension: tool.extension?.packageName,
                    },
                })),
            ],
            usage: zeroUsage(),
            stopReason: stopReason(turn.status),
            errorMessage: turn.status === "failed" ? turn.reason || "Mission turn failed" : undefined,
            timestamp: timestampFromSeq(turn.startedSeq),
        };
        messages.push(assistantMessage);

        for (const tool of turnTools) {
            const toolResult: ToolResultMessage = {
                role: "toolResult",
                toolCallId: tool.toolCallId,
                toolName: tool.toolName,
                content: [{ type: "text", text: toolResultText(tool) }],
                details: {
                    display: tool.displayDetails || tool.displaySummary,
                    durationMs: tool.durationMs,
                    trust: tool.trust,
                    extension: tool.extension,
                },
                isError: tool.status === "failed",
                timestamp: timestampFromSeq(tool.endedSeq || tool.startedSeq),
            };
            messages.push(toolResult);
        }
    }

    const unscopedTools = toolsByTurn.get("mission-runtime") || [];
    if (unscopedTools.length > 0 && turns.every((turn) => turn.turnId !== "mission-runtime")) {
        const assistantMessage: AssistantMessage = {
            role: "assistant",
            api: "agenthub-mission-replay",
            provider: "fabric-clawhub",
            model: "pi-web-ui",
            content: [
                { type: "text", text: "Mission runtime tool activity" },
                ...unscopedTools.map((tool) => ({
                    type: "toolCall" as const,
                    id: tool.toolCallId,
                    name: tool.toolName,
                    arguments: { tool: tool.toolName, summary: tool.summary, extension: tool.extension?.packageName },
                })),
            ],
            usage: zeroUsage(),
            stopReason: "stop",
            timestamp: timestampFromSeq(model.latestSeq),
        };
        messages.push(assistantMessage);
    }

    return messages;
}

function transcriptSignature(messages: AgentMessage[], model: PiMissionViewModel): string {
    return JSON.stringify({
        count: messages.length,
        latestSeq: model.latestSeq,
        rawEventCount: model.rawEventCount,
        last: messages[messages.length - 1],
    });
}

export function MissionPiRuntimeHost({ state, model, sessionId, githubToken, fabricToken, fullPage = false }: MissionPiRuntimeHostProps) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const chatPanelRef = useRef<PiChatPanelElement | null>(null);
    const [logCompactionNow, setLogCompactionNow] = useState(() => Date.now());

    const transcript = useMemo(() => {
        const messages = buildPiMessages(state, model);
        return { messages, signature: transcriptSignature(messages, model), tools: createReplayTools(model) };
    }, [state, model]);
    const liveLogRows = useMemo(() => buildPiLiveLogRows(state, model, logCompactionNow), [state, model, logCompactionNow]);

    useEffect(() => {
        if (!fullPage || typeof window === "undefined") return;
        const timer = window.setInterval(() => setLogCompactionNow(Date.now()), PI_LOG_COMPACTION_REFRESH_MS);
        return () => window.clearInterval(timer);
    }, [fullPage]);

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;
        ensurePiAppStorage();
        void getAppStorage().providerKeys.set(AGENTHUB_PI_MODEL.provider, "agenthub-backend-bridge");

        let chatPanel = chatPanelRef.current;
        if (!chatPanel) {
            chatPanel = new ChatPanel() as PiChatPanelElement;
            chatPanel.dataset.piWebComponent = "pi-chat-panel";
            chatPanel.dataset.piPackage = PI_FRONTEND_RUNTIME_PACKAGE.packageName || "@mariozechner/pi-web-ui";
            chatPanel.dataset.piPackageVersion = PI_FRONTEND_RUNTIME_PACKAGE.version || "0.71.1";
            chatPanelRef.current = chatPanel;
            container.appendChild(chatPanel);
        }

        const agent = new Agent({
            sessionId,
            initialState: {
                systemPrompt: "You are rendering an AgentHub Mission Control transcript inside the Pi Web UI runtime.",
                model: AGENTHUB_PI_MODEL,
                messages: transcript.messages,
                thinkingLevel: "off",
                tools: transcript.tools,
            },
            convertToLlm: defaultConvertToLlm,
            streamFn: createBackendBridgeStream(sessionId, githubToken, fabricToken),
        });

        agent.subscribe((event) => {
            if (event.type === "message_start" || event.type === "message_update" || event.type === "message_end" || event.type === "agent_end") {
                agent.state.messages = [...agent.state.messages];
                chatPanel?.agentInterface?.requestUpdate();
                chatPanel?.requestUpdate();
            }
        });

        void chatPanel.setAgent(agent, {
            onApiKeyRequired: async () => true,
            toolsFactory: () => transcript.tools,
        }).then(() => {
            const agentInterface = chatPanel?.agentInterface;
            if (agentInterface) {
                agentInterface.enableAttachments = false;
                agentInterface.enableModelSelector = false;
                agentInterface.enableThinkingSelector = false;
                agentInterface.setAttribute("data-pi-runtime", PI_FRONTEND_RUNTIME_PACKAGE.packageName || "@mariozechner/pi-web-ui");
                agentInterface.requestUpdate();
            }
            chatPanel?.requestUpdate();
        });
    }, [sessionId, transcript.signature, githubToken, fabricToken]);

    return (
        <section
            className={`pi-runtime-host${fullPage ? " pi-runtime-host--full" : ""}`}
            aria-label="Pi Web UI runtime transcript"
            data-pi-runtime="@mariozechner/pi-web-ui"
            data-pi-runtime-version={PI_FRONTEND_RUNTIME_PACKAGE.version}
            data-pi-extension-packages={PI_EXTENSION_PACKAGES.map((pkg) => pkg.source).join(",")}
            data-pi-log-compaction-extension={PI_LOG_COMPACTION_EXTENSION.packageName}
            data-pi-agentic-engineering-extension={PI_AGENTIC_ENGINEERING_EXTENSION.packageName}
            data-pi-rpi-protocol="research-plan-implement-context-gates"
            data-pi-qrspi-protocol={PI_QRSPI_PROTOCOL}
            data-pi-qrspi-phase-model={PI_QRSPI_PHASE_MODEL.join("->")}
            data-pi-qrspi-instruction-budget="instructions-not-only-tokens"
            data-pi-qrspi-backtrack-policy="phase-backtracking-enabled"
            data-pi-context-pack-schema="ContextPackV2"
            data-pi-subagent-work-model="context-window-fork"
        >
            {fullPage ? <PiSubagentsObservabilityPanel model={model} /> : null}
            {fullPage ? <PiLiveLogStrip rows={liveLogRows} /> : null}
            <div ref={containerRef} className="pi-runtime-host__mount" />
        </section>
    );
}