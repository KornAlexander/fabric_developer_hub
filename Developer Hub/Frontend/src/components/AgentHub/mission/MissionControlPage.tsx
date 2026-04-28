/**
 * Mission Control — Step 3 · live run surface.
 *
 * Pixel-faithful port of prototype screens 3–6 from
 * ``Design/agent-ux/prototypes/03-mission-control/index.html``.
 *
 * Layout:
 *   ┌─ Identity strip ───────────────────────────────────────────────┐
 *   │  Task prompt recap (collapsed)                                 │
 *   │  Team panel (compact strip / expandable graph)                 │
 *   │  ┌─── Live log ──────────────┬── Run overview (right rail) ──┐ │
 *   │  │  step-connector entries   │  Plan progress                │ │
 *   │  │  with agent icons, code   │  Outputs                      │ │
 *   │  │  blocks, fabric badges    │  Changes & actions            │ │
 *   │  └──────────────────────────┴──────────────────────────────┘ │
 *   └────────────────────────────────────────────────────────────────┘
 */

import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useHistory } from "react-router-dom";
import { Spinner, Dropdown, Option, Badge, Button, Card, Tab, TabList, Tooltip } from "@fluentui/react-components";
import {
    Checkmark12Filled,
    Dismiss12Filled,
    Play16Filled,
    Document16Regular,
    Edit16Regular,
    Delete20Regular,
    Settings16Regular,
    Copy20Regular,
    ArrowDownload20Regular,
    Info16Regular,
    CheckmarkCircle16Filled,
    Flash16Regular,
    Open20Regular,
    ArrowResetRegular,
    Resize20Regular,
    Timer16Regular,
} from "@fluentui/react-icons";
import type { WorkloadClientAPI } from "@ms-fabric/workload-client";

import * as api from "../../../controller/AgentHubApi";
import { callAuthAcquireAccessToken } from "../../../controller/AgentHubController";
import { externalLinkOnClick } from "../openExternalTab";
import { TaskPromptRecap } from "../TaskPromptRecap";
import { TeamPanel } from "../team/TeamPanel";
import { ApprovalCard } from "../approvals/ApprovalCard";
import { useMissionStream } from "./useMissionStream";
import { teamFromComposition } from "./types";
import { agentIcon, agentKind } from "../team/OrchCanvas";
import type { LogEntry, PendingApproval, MissionState } from "./missionReducer";
import type { ChangeKind, ChangeRecord, JobStatusLite, PublicLogCategory } from "./events";
import type { PlanStep, RecoveryAction, Team, TeamNodeStatus } from "../plan/types";
import { formatDurationMs, formatToolLine, formatToolName } from "./logPresentation";
import { formatToolArgsSummary, formatToolCommand, formatVisibleRuntimeText } from "./logPresentation";
import { getMissionObservationSnapshot } from "./missionObservability";

// ────────────────────────────────────────────────────────────────────
// Props & helpers
// ────────────────────────────────────────────────────────────────────

export interface MissionControlPageProps {
    workloadClient: WorkloadClientAPI;
    sessionId: string;
    githubToken?: string;
    initialFabricToken?: string;
    initialJob?: {
        task_description?: string;
        workspace_id?: string;
        workspace_name?: string | null;
        started_at?: string | null;
        status?: string;
        context?: Record<string, any> | null;
    } | null;
}

type LogViewMode = "overview" | "detail";
type LogCategoryFilter = PublicLogCategory;
type CanvasViewPreference = "agents" | "logs";
type RailTab = "overview" | "diagnostics";

const LOG_CATEGORY_ORDER: LogCategoryFilter[] = ["high_level", "detailed", "diagnostic"];
const LOG_CATEGORY_LABEL: Record<LogCategoryFilter, string> = {
    high_level: "High level",
    detailed: "Detailed",
    diagnostic: "Diagnostics",
};
const LOG_CATEGORY_TAB_LABEL: Record<LogCategoryFilter, string> = {
    high_level: "High",
    detailed: "Detailed",
    diagnostic: "Diag",
};

function fmtElapsed(sec: number): string {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
function fmtLogTs(iso: string): string {
    try {
        const d = new Date(iso);
        return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
    } catch { return ""; }
}

function nameFor(state: MissionState, agentId: string): string {
    // 1) Direct lookup in slot progress (keyed by slotId = UUID)
    const s = state.slotProgress[agentId];
    if (s?.agentName) return s.agentName;

    // 2) Scan ALL slotProgress entries for a matching agentId field
    //    (covers the case where slotId ≠ agentId key)
    for (const p of Object.values(state.slotProgress)) {
        if ((p as any).agentId === agentId && (p as any).agentName) {
            return (p as any).agentName;
        }
    }

    // 3) Composition slots by id/agentId
    const comp: any = state.composition;
    if (comp?.slots) {
        const slot = comp.slots.find((x: any) => x.id === agentId || x.agentId === agentId);
        if (slot) return slot.agentId || slot.role || agentId;
    }

    // 4) Search log entries for one that carries an agentName
    for (const l of state.logs) {
        if (l.agentId === agentId && l.agentName) return l.agentName;
    }

    // 5) Fall back to first 8 chars of UUID
    return agentId.length > 12 ? agentId.slice(0, 8) : agentId;
}

function kindFor(agentId: string, comp: any): string {
    if (!comp?.slots) return "generic";
    // Direct lookup
    const slot = comp.slots.find((x: any) => x.id === agentId || x.agentId === agentId);
    if (slot) return agentKind(slot.agentId, slot.id);
    return "generic";
}

/** Build a lookup from runtime UUID agentId → composition slot agentId.
 *  Used by log entries to resolve the agent kind for icon coloring. */
function kindForWithProgress(agentId: string, comp: any, progress: Record<string, any>): string {
    // First try direct
    const direct = kindFor(agentId, comp);
    if (direct !== "generic") return direct;
    // Look up the agentName from progress, then find matching composition slot
    const p = progress[agentId];
    if (p?.agentName && comp?.slots) {
        const name = (p.agentName as string).toLowerCase().replace(/[\s-]/g, "");
        const slot = comp.slots.find((x: any) => {
            const key = (x.agentId || x.id || "").toLowerCase().replace(/-/g, "");
            return key === name || name.includes(key) || key.includes(name);
        });
        if (slot) return agentKind(slot.agentId, slot.id);
    }
    return "generic";
}

function canonicalAgentLabel(label: string): string {
    return label.toLowerCase().replace(/[\s_-]+/g, "");
}

function isInternalAgentRef(value?: string | null): boolean {
    const label = canonicalAgentLabel(String(value || ""));
    return label === "orchestrator"
        || label === "system"
        || label === "generalist"
        || label === "agenthubgeneralist"
        || label === "generalistmissioncontroller";
}

function isInternalRuntimeSlot(slot: { id?: string; agentId?: string; agentName?: string; role?: string }): boolean {
    return isInternalAgentRef(slot.id)
        || isInternalAgentRef(slot.agentId)
        || isInternalAgentRef(slot.agentName)
    || isInternalAgentRef(slot.role)
    || canonicalAgentLabel(String(slot.role || "")).includes("generalistmission");
}

function visibleAgentName(label?: string | null): string | null {
    if (!label) return null;
    return isInternalAgentRef(label) ? "System" : label;
}

function visibleRuntimeText(text: string): string {
    return formatVisibleRuntimeText(text).replace(/\borchestrator\b/gi, "system");
}

const MAJOR_ERROR_PATTERNS: RegExp[] = [
    /capacitynotactive/i,
    /capacity\s+not\s+active/i,
    /capacity\s+is\s+inactive/i,
    /inactive\s+due\s+to\s+the\s+`?capacitynotactive`?/i,
    /workspace\s+capacity\s+is\s+inactive/i,
    /cannot\s+proceed\s+until\s+capacity/i,
];

const MAJOR_WARNING_PATTERNS: RegExp[] = [
    /capacity\s+issue/i,
    /capacity\s+constraint/i,
    /throttl(ed|ing)?/i,
    /quota\s+exceeded/i,
];

function inferIssueSeverity(message: string): "error" | "warn" | null {
    if (!message) return null;
    if (MAJOR_ERROR_PATTERNS.some((re) => re.test(message))) return "error";
    if (MAJOR_WARNING_PATTERNS.some((re) => re.test(message))) return "warn";
    return null;
}

function resolvedLogLevel(entry: Pick<LogEntry, "level" | "message">): "info" | "warn" | "error" {
    if (entry.level === "error") return "error";
    const inferred = inferIssueSeverity(entry.message);
    if (inferred === "error") return "error";
    if (entry.level === "warn" || inferred === "warn") return "warn";
    return "info";
}

type RuntimeLifecycle = "planned" | "spinning_up" | "waiting" | "running" | "finished" | "failed";

interface RuntimeSlotView {
    slotId: string;
    agentId: string;
    role: string;
    agentName: string;
    lifecycle: RuntimeLifecycle;
    status: string;
    isActive: boolean;
    reason?: string;
}

interface CanvasNodeLayout {
    x: number;
    y: number;
    width?: number;
    logHeight?: number;
}

type CanvasLayoutOverrides = Record<string, CanvasNodeLayout>;

const MIN_AGENT_NODE_WIDTH = 176;
const MAX_AGENT_NODE_WIDTH = 520;
const MIN_AGENT_LOG_HEIGHT = 84;
const MAX_AGENT_LOG_HEIGHT = 300;

function clampNumber(value: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, value));
}

function defaultSubNodeLayout(index: number, total: number): CanvasNodeLayout {
    if (total <= 1) return { x: 50, y: 72 };
    if (total === 2) return { x: index === 0 ? 28 : 72, y: 70 };
    if (total === 3) return { x: [16, 50, 84][index] ?? 50, y: index === 1 ? 72 : 66 };
    const x = 12 + (index * (76 / Math.max(total - 1, 1)));
    return { x, y: index % 2 === 1 ? 74 : 66 };
}

function nodeStyle(layout: CanvasNodeLayout): React.CSSProperties {
    return {
        "--x": `${layout.x}%`,
        "--y": `${layout.y}%`,
        ...(layout.width ? { "--node-w": `${layout.width}px` } : {}),
        ...(layout.logHeight ? { "--agent-log-h": `${layout.logHeight}px` } : {}),
        transform: "translate(-50%, -50%)",
    } as React.CSSProperties;
}

function lifecycleToNodeStatus(lifecycle: RuntimeLifecycle): TeamNodeStatus {
    if (lifecycle === "running") return "active";
    if (lifecycle === "finished") return "done";
    if (lifecycle === "failed") return "failed";
    if (lifecycle === "waiting" || lifecycle === "spinning_up") return "waiting";
    return "planned";
}

function progressMatchesSlot(progress: any, slot: { id: string; agentId: string; role: string }): boolean {
    const slotId = canonicalAgentLabel(slot.id || "");
    const slotAgent = canonicalAgentLabel(slot.agentId || "");
    const slotRole = canonicalAgentLabel(slot.role || "");
    const pSlot = canonicalAgentLabel(String(progress?.slotId || ""));
    const pAgent = canonicalAgentLabel(String(progress?.agentId || ""));
    const pName = canonicalAgentLabel(String(progress?.agentName || ""));
    const pRole = canonicalAgentLabel(String(progress?.role || ""));

    const matchesId = [slotId, slotAgent]
        .filter(Boolean)
        .some((k) => pSlot === k || pAgent === k || pName === k || pName.includes(k) || k.includes(pName));
    if (matchesId) return true;

    return !!slotRole && !!pRole && (slotRole === pRole || slotRole.includes(pRole) || pRole.includes(slotRole));
}

function buildRuntimeSlotViews(state: MissionState): RuntimeSlotView[] {
    const comp: any = state.composition;
    const progressValues = Object.values(state.slotProgress || {}) as any[];
    const pendingApprovals = Object.values(state.approvals || {}).filter((a) => !a.resolved);
    const hasRuntimeStarted =
        state.jobStatus !== "planned" ||
        state.logs.length > 0 ||
        progressValues.length > 0;

    const baseSlots = (Array.isArray(comp?.slots) && comp.slots.length > 0
        ? comp.slots.map((slot: any) => ({
            id: String(slot.id || slot.agentId || ""),
            agentId: String(slot.agentId || slot.id || ""),
            role: String(slot.role || slot.agentId || slot.id || ""),
        }))
        : progressValues.map((p: any) => ({
            id: String(p.slotId || p.agentId || p.agentName || ""),
            agentId: String(p.agentId || p.slotId || p.agentName || ""),
            role: String(p.role || p.agentName || p.agentId || ""),
            agentName: String(p.agentName || ""),
        }))).filter((slot: any) => !isInternalRuntimeSlot(slot));
    const slots = [...baseSlots];
    for (const progress of progressValues) {
        const progressSlot = {
            id: String(progress.slotId || progress.agentId || progress.agentName || ""),
            agentId: String(progress.agentId || progress.slotId || progress.agentName || ""),
            role: String(progress.role || progress.agentName || progress.agentId || ""),
        };
        if (!progressSlot.id) continue;
        if (isInternalRuntimeSlot({ ...progressSlot, agentName: String(progress.agentName || "") })) continue;
        const alreadyRepresented = baseSlots.some((slot: any) => progressMatchesSlot(progress, slot));
        if (!alreadyRepresented) {
            slots.push(progressSlot);
        }
    }

    const seen = new Set<string>();
    const out: RuntimeSlotView[] = [];

    for (const slot of slots) {
        if (!slot.id) continue;
        if (seen.has(slot.id)) continue;
        seen.add(slot.id);

        let progress = state.slotProgress[slot.id] || state.slotProgress[slot.agentId];
        if (!progress) {
            progress = progressValues.find((p) => progressMatchesSlot(p, slot)) as any;
        }

        const runtimeAgentId = String((progress as any)?.agentId || slot.agentId || slot.id);
        const runtimeState =
            state.agentStatus[runtimeAgentId] ||
            state.agentStatus[slot.id] ||
            state.agentStatus[slot.agentId];
        const progressStatus = String((progress as any)?.status || "").toLowerCase();

        const slotKey = canonicalAgentLabel(slot.id);
        const slotAgentKey = canonicalAgentLabel(slot.agentId);
        const hasPendingApproval = pendingApprovals.some((ap) => {
            const apSlot = canonicalAgentLabel(String(ap.slotId || ""));
            const apAgent = canonicalAgentLabel(String(ap.agentId || ""));
            return (apSlot && (apSlot === slotKey || apSlot === slotAgentKey))
                || (apAgent && (apAgent === slotKey || apAgent === slotAgentKey));
        });

        const isActive = !!state.activeAgentId && [
            slot.id,
            slot.agentId,
            runtimeAgentId,
            String((progress as any)?.slotId || ""),
        ].includes(state.activeAgentId);

        let lifecycle: RuntimeLifecycle = "planned";
        if (runtimeState === "error" || progressStatus === "failed") {
            lifecycle = "failed";
        } else if (runtimeState === "completed" || progressStatus === "done") {
            lifecycle = "finished";
        } else if (hasPendingApproval || progressStatus === "approval_required" || runtimeState === "waiting") {
            lifecycle = "waiting";
        } else if (isActive || runtimeState === "running" || progressStatus === "running") {
            lifecycle = "running";
        } else if (runtimeState === "queued" || progressStatus === "queued") {
            // "Spinning up" should only describe agents that are actively
            // booting (queued by the runtime). Before the run begins they're
            // simply planned.
            lifecycle = hasRuntimeStarted ? "spinning_up" : "planned";
        } else if (hasRuntimeStarted && (state.jobStatus === "running" || state.jobStatus === "approved")) {
            // The job is running but this slot has no runtime status yet —
            // for sequential / mixed topologies the downstream agents are
            // simply queued for their turn, not booting. Show "Waiting"
            // instead of misleading "Spinning up" forever.
            lifecycle = "waiting";
        }

        if (state.jobStatus === "completed" && (lifecycle === "spinning_up" || lifecycle === "waiting")) {
            lifecycle = "finished";
        }

        const reason = hasPendingApproval
            ? "Approval required"
            : String((progress as any)?.reason || "") || undefined;

        const status = progressStatus
            || runtimeState
            || (lifecycle === "running" ? "running"
                : lifecycle === "finished" ? "done"
                    : lifecycle === "failed" ? "failed"
                        : lifecycle === "waiting" ? "approval_required"
                            : lifecycle === "spinning_up" ? "queued"
                                : "queued");

        out.push({
            slotId: slot.id,
            agentId: slot.agentId,
            role: slot.role,
            agentName: String((progress as any)?.agentName || slot.agentId || slot.id),
            lifecycle,
            status,
            isActive,
            reason,
        });
    }

    return out;
}

function applyRuntimeTeam(baseTeam: Team | null, runtimeSlots: RuntimeSlotView[], activeAgentId?: string | null): Team | null {
    if (!baseTeam) return null;
    const bySlotId = new Map(runtimeSlots.map((s) => [canonicalAgentLabel(s.slotId), s]));
    const byAgentId = new Map(runtimeSlots.map((s) => [canonicalAgentLabel(s.agentId), s]));

    return {
        ...baseTeam,
        nodes: baseTeam.nodes.map((node) => {
            const slot = bySlotId.get(canonicalAgentLabel(node.id))
                || byAgentId.get(canonicalAgentLabel(node.id))
                || byAgentId.get(canonicalAgentLabel(node.agent));
            const lifecycle: RuntimeLifecycle = slot?.lifecycle
                || (activeAgentId && node.id === activeAgentId ? "running"
                    : node.lifecycle
                        || (node.status === "active" ? "running"
                            : node.status === "done" ? "finished"
                                : node.status === "failed" ? "failed"
                                    : node.status === "waiting" ? "waiting"
                                        : "planned"));
            return {
                ...node,
                lifecycle,
                status: lifecycleToNodeStatus(lifecycle),
                stateReason: slot?.reason,
            };
        }),
    };
}

function isHighSignalLog(entry: LogEntry): boolean {
    const level = resolvedLogLevel(entry);
    if (entry.logCategory === "high_level") return true;
    if (level === "error" || entry.kind === "error") return true;
    if (level === "warn") return true;
    if (entry.kind === "action" || entry.kind === "decision") return true;
    if (entry.kind === "phase" && /complete|failed|approval/i.test(entry.message)) return true;
    if (entry.kind === "log" && /approval|required|decline|retry|blocked/i.test(entry.message)) return true;
    if (entry.kind === "tool_end") {
        if (resolvedLogLevel(entry) === "error") return true;
        const tool = String(entry.toolName || "").toLowerCase();
        if (entry.durationMs != null && entry.durationMs >= 5000) return true;
        if (/(create|update|delete|publish|deploy|write|apply|grant|revoke|execute)/.test(tool)) return true;
    }
    return false;
}

const CHANGE_SECTION_ORDER: ChangeKind[] = ["created", "updated", "deleted", "important_action"];

const CHANGE_SECTION_LABEL: Record<ChangeKind, string> = {
    created: "Created",
    updated: "Updated",
    deleted: "Deleted",
    important_action: "Important actions",
};

function groupChangeRecords(changes: ChangeRecord[]): Record<ChangeKind, ChangeRecord[]> {
    const groups: Record<ChangeKind, ChangeRecord[]> = {
        created: [],
        updated: [],
        deleted: [],
        important_action: [],
    };
    for (const change of changes) {
        groups[change.kind]?.push(change);
    }
    return groups;
}

function changeIcon(kind: ChangeKind, targetScope?: string) {
    if (kind === "created") return <Document16Regular />;
    if (kind === "updated") return <Edit16Regular />;
    if (kind === "deleted") return <Delete20Regular />;
    if (targetScope === "execution") return <Play16Filled />;
    return <Settings16Regular />;
}

function runtimeStatusLabel(slot: Pick<RuntimeSlotView, "lifecycle" | "status">): string {
    if (slot.lifecycle === "finished") return "Done";
    if (slot.lifecycle === "failed") return "Failed";
    if (slot.lifecycle === "waiting") return "Waiting";
    if (slot.lifecycle === "spinning_up") return "Starting";
    if (slot.lifecycle === "running") return "Running";
    return slot.status || "Planned";
}

function runtimeNodeClass(slot: RuntimeSlotView): string {
    if (slot.lifecycle === "finished") return " agent-node--done";
    if (slot.lifecycle === "failed" || slot.lifecycle === "waiting") return " agent-node--attention";
    if (slot.lifecycle === "spinning_up") return " agent-node--spawning";
    if (slot.lifecycle === "running" || slot.isActive) return " agent-node--running";
    return "";
}

function runtimePillClass(slot: RuntimeSlotView): string {
    if (slot.lifecycle === "finished") return "state-pill state-pill--done";
    if (slot.lifecycle === "failed" || slot.lifecycle === "waiting") return "state-pill state-pill--attention";
    if (slot.lifecycle === "spinning_up") return "state-pill state-pill--spawning";
    if (slot.lifecycle === "running" || slot.isActive) return "state-pill state-pill--running";
    return "state-pill";
}

function runtimeBadgeColor(slot: RuntimeSlotView): "success" | "warning" | "danger" | "brand" | "subtle" {
    if (slot.lifecycle === "finished" || slot.lifecycle === "running" || slot.isActive) return "success";
    if (slot.lifecycle === "spinning_up") return "brand";
    if (slot.lifecycle === "failed") return "danger";
    if (slot.lifecycle === "waiting") return "warning";
    return "subtle";
}

function changeBadgeColor(status?: string): "success" | "warning" | "danger" | "brand" | "subtle" {
    const normalized = String(status || "tracked").toLowerCase();
    if (normalized === "applied" || normalized === "completed" || normalized === "ready") return "success";
    if (normalized === "failed") return "danger";
    if (normalized === "creating" || normalized === "changing") return "brand";
    if (normalized === "pending" || normalized === "waiting" || normalized === "reviewing" || normalized === "draft") return "warning";
    return "subtle";
}

function changeStatusLabel(status?: string): string {
    const normalized = String(status || "tracked").toLowerCase();
    if (normalized === "applied") return "Applied";
    if (normalized === "pending" || normalized === "waiting") return "Waiting";
    if (normalized === "failed") return "Failed";
    if (normalized === "ready") return "Ready";
    if (normalized === "draft") return "Drafting";
    if (normalized === "creating") return "Creating";
    if (normalized === "changing") return "Changing";
    if (normalized === "reviewing") return "Reviewing";
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function changeLedgerRowClass(change: ChangeRecord): string {
    if (change.status && change.status !== "applied") return ` ledger-row--${change.kind} ledger-row--pending ledger-row--live`;
    if (change.kind === "important_action") return " ledger-row--action";
    return ` ledger-row--${change.kind}`;
}

function logCategoryMatches(entry: LogEntry, category: LogCategoryFilter): boolean {
    if (category === "high_level") return entry.logCategory === "high_level" || isHighSignalLog(entry);
    return entry.logCategory === category;
}

function logBelongsToSlot(entry: LogEntry, slot: RuntimeSlotView): boolean {
    const slotKeys = [slot.slotId, slot.agentId, slot.agentName].map(canonicalAgentLabel).filter(Boolean);
    const entryKeys = [entry.agentId || "", entry.agentName || ""].map(canonicalAgentLabel).filter(Boolean);
    return entryKeys.some((entryKey) => slotKeys.some((slotKey) => entryKey === slotKey || entryKey.includes(slotKey) || slotKey.includes(entryKey)));
}

function nodeLogsForSlot(state: MissionState, slot: RuntimeSlotView, category: LogCategoryFilter): LogEntry[] {
    return state.logs
        .filter((entry) => logCategoryMatches(entry, category) && logBelongsToSlot(entry, slot))
        .slice(-4);
}

function generalistLogs(state: MissionState, category: LogCategoryFilter): LogEntry[] {
    return state.logs
        .filter((entry) => logCategoryMatches(entry, category))
        .filter((entry) => !entry.agentId || isInternalAgentRef(entry.agentId) || isInternalAgentRef(entry.agentName || ""))
        .slice(-4);
}

function LogWindow({ logs, fallback, large = false }: { logs: LogEntry[]; fallback: string; large?: boolean }) {
    const visibleLogs = logs.length > 0 ? logs : null;
    return (
        <div className={`log-window${large ? " log-window--large" : ""}`}>
            {visibleLogs ? visibleLogs.map((log) => (
                <p key={log.seq}><time>[{fmtLogTs(log.ts)}]</time> {visibleRuntimeText(log.message)}</p>
            )) : <p>{fallback}</p>}
        </div>
    );
}

function readableSlotLabel(slot: RuntimeSlotView): string {
    return visibleAgentName(slot.agentName) || visibleAgentName(slot.agentId) || slot.role || "Agent";
}

function canvasLogAgent(log: LogEntry, slots: RuntimeSlotView[], state: MissionState): { label: string; kind: string } {
    const slot = slots.find((candidate) => logBelongsToSlot(log, candidate));
    if (slot) {
        return {
            label: visibleAgentName(log.agentName || slot.agentName || slot.agentId) || readableSlotLabel(slot),
            kind: kindForWithProgress(slot.agentId, state.composition, state.slotProgress),
        };
    }

    const agentRef = log.agentName || log.agentId || "";
    if (!agentRef || isInternalAgentRef(agentRef)) {
        return { label: "Generalist", kind: "generalist" };
    }

    return {
        label: visibleAgentName(agentRef) || "Runtime",
        kind: kindForWithProgress(log.agentId || agentRef, state.composition, state.slotProgress),
    };
}

function shouldUseCanvasLogStream(width: number, height: number, slotCount: number): boolean {
    if (slotCount <= 1 || width <= 0 || height <= 0) return false;
    const widthFloor = slotCount >= 4 ? 1100 : slotCount === 3 ? 940 : 760;
    const heightFloor = slotCount >= 4 ? 560 : 500;
    return width < widthFloor || height < heightFloor;
}

function CanvasLogStream({ state, slots, categoryFilter, autoCompact }: { state: MissionState; slots: RuntimeSlotView[]; categoryFilter: LogCategoryFilter; autoCompact: boolean }) {
    const logs = useMemo(
        () => state.logs.filter((entry) => logCategoryMatches(entry, categoryFilter)).slice(-48),
        [state.logs, categoryFilter],
    );

    return (
        <div className={`canvas-log-stream${autoCompact ? " canvas-log-stream--auto" : ""}`} role="log" aria-label="Agent log stream">
            {logs.length === 0 ? (
                <div className="canvas-log-empty">
                    <Badge appearance="tint" color="brand">Generalist</Badge>
                    <span>{state.jobStatus === "completed" ? "Run completed and summaries are ready." : "Waiting for the first runtime message."}</span>
                </div>
            ) : logs.map((log) => {
                const agent = canvasLogAgent(log, slots, state);
                return (
                    <article key={log.seq} className={`canvas-log-row canvas-log-row--${agent.kind}`}>
                        <span className={`canvas-log-row__agent canvas-log-row__agent--${agent.kind}`}>{agent.label}</span>
                        <time className="canvas-log-row__time">{fmtLogTs(log.ts)}</time>
                        <p>{visibleRuntimeText(log.message)}</p>
                    </article>
                );
            })}
        </div>
    );
}

function AgentNodeCard({
    slot,
    index,
    total,
    state,
    categoryFilter,
    layout,
    onMoveStart,
    onResizeStart,
}: {
    slot: RuntimeSlotView;
    index: number;
    total: number;
    state: MissionState;
    categoryFilter: LogCategoryFilter;
    layout: CanvasNodeLayout;
    onMoveStart: (event: React.PointerEvent<HTMLElement>, nodeId: string, layout: CanvasNodeLayout) => void;
    onResizeStart: (event: React.PointerEvent<HTMLElement>, nodeId: string, layout: CanvasNodeLayout) => void;
}) {
    const kind = kindForWithProgress(slot.agentId, state.composition, state.slotProgress);
    const logs = nodeLogsForSlot(state, slot, categoryFilter);
    const fallback = slot.reason || `${runtimeStatusLabel(slot)} · waiting for runtime output.`;
    const nodeId = slot.slotId || slot.agentId || String(index);
    return (
        <Card
            className={`agent-node agent-node--sub${runtimeNodeClass(slot)}${layout.width || layout.logHeight ? " agent-node--manual-size" : ""}`}
            data-agent-node-id={nodeId}
            data-manual-size={layout.width || layout.logHeight ? "true" : undefined}
            style={nodeStyle(layout)}
        >
            <div className="agent-head" onPointerDown={(event) => onMoveStart(event, nodeId, layout)}>
                <span className={`agent-icon agent-icon--${kind === "fde" ? "data" : kind === "admin" ? "admin" : kind === "reporter" ? "report" : "generalist"}`}>
                    {agentIcon(kind)}
                </span>
                <div>
                    <h2>{visibleAgentName(slot.agentName) || slot.agentId}</h2>
                    <p>{slot.role || slot.agentId}</p>
                </div>
                <Badge appearance="tint" color={runtimeBadgeColor(slot)} className={runtimePillClass(slot)}>
                    {runtimeStatusLabel(slot)}
                </Badge>
            </div>
            <LogWindow logs={logs} fallback={fallback} />
            <Tooltip content="Resize card" relationship="label">
                <button
                    type="button"
                    className="agent-card-resize"
                    aria-label={`Resize ${visibleAgentName(slot.agentName) || slot.agentId}`}
                    onPointerDown={(event) => onResizeStart(event, nodeId, layout)}
                >
                    <Resize20Regular />
                </button>
            </Tooltip>
        </Card>
    );
}

function DynamicMissionCanvas({
    state,
    runtimeSlots,
    categoryFilter,
    onCategoryChange,
}: {
    state: MissionState;
    runtimeSlots: RuntimeSlotView[];
    categoryFilter: LogCategoryFilter;
    onCategoryChange: (category: LogCategoryFilter) => void;
}) {
    const slots = runtimeSlots.length > 0 ? runtimeSlots : [];
    const canvasRef = useRef<HTMLElement | null>(null);
    const [nodeOverrides, setNodeOverrides] = useState<CanvasLayoutOverrides>({});
    const [canvasViewPreference, setCanvasViewPreference] = useState<CanvasViewPreference>("agents");
    const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
    const categoryCounts = useMemo(() => {
        return LOG_CATEGORY_ORDER.reduce((acc, category) => {
            acc[category] = state.logs.filter((entry) => logCategoryMatches(entry, category)).length;
            return acc;
        }, {} as Record<LogCategoryFilter, number>);
    }, [state.logs]);
    const generalistLogLines = generalistLogs(state, categoryFilter);
    const fallback = state.jobStatus === "completed"
        ? "Run completed and summaries are ready."
        : slots.length > 0
            ? `Coordinating ${slots.length} visible agent${slots.length === 1 ? "" : "s"}.`
            : "Building the run plan and waiting for the first agent.";
    const slotLayouts = useMemo(() => {
        return slots.map((slot, index) => {
            const nodeId = slot.slotId || slot.agentId || String(index);
            return {
                nodeId,
                layout: { ...defaultSubNodeLayout(index, slots.length), ...(nodeOverrides[nodeId] || {}) },
            };
        });
    }, [slots, nodeOverrides]);
    const autoLogStream = shouldUseCanvasLogStream(canvasSize.width, canvasSize.height, slots.length);
    const showLogStream = canvasViewPreference === "logs" || autoLogStream;

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const update = () => {
            const width = Math.round(canvas.clientWidth);
            const height = Math.round(canvas.clientHeight);
            setCanvasSize((current) => current.width === width && current.height === height
                ? current
                : { width, height });
        };
        update();
        if (typeof ResizeObserver === "undefined") {
            window.addEventListener("resize", update);
            return () => window.removeEventListener("resize", update);
        }
        const observer = new ResizeObserver(update);
        observer.observe(canvas);
        return () => observer.disconnect();
    }, []);

    const resetCanvasView = useCallback(() => setNodeOverrides({}), []);

    const startNodeMove = useCallback((event: React.PointerEvent<HTMLElement>, nodeId: string, layout: CanvasNodeLayout) => {
        if (event.button !== 0) return;
        if ((event.target as HTMLElement).closest("button, a")) return;
        const canvas = canvasRef.current;
        if (!canvas) return;
        event.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const start = { x: event.clientX, y: event.clientY, layout };
        const card = event.currentTarget.closest(".agent-node") as HTMLElement | null;
        card?.classList.add("is-dragging");
        const onMove = (moveEvent: PointerEvent) => {
            const nextX = clampNumber(start.layout.x + ((moveEvent.clientX - start.x) / rect.width) * 100, 7, 93);
            const nextY = clampNumber(start.layout.y + ((moveEvent.clientY - start.y) / rect.height) * 100, 18, 90);
            setNodeOverrides((prev) => ({ ...prev, [nodeId]: { ...(prev[nodeId] || start.layout), x: nextX, y: nextY } }));
        };
        const onUp = () => {
            card?.classList.remove("is-dragging");
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
        };
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp, { once: true });
    }, []);

    const startNodeResize = useCallback((event: React.PointerEvent<HTMLElement>, nodeId: string, layout: CanvasNodeLayout) => {
        if (event.button !== 0) return;
        const card = event.currentTarget.closest(".agent-node") as HTMLElement | null;
        if (!card) return;
        event.preventDefault();
        event.stopPropagation();
        const logWindow = card.querySelector(".log-window") as HTMLElement | null;
        const start = {
            x: event.clientX,
            y: event.clientY,
            width: layout.width || card.getBoundingClientRect().width,
            logHeight: layout.logHeight || logWindow?.getBoundingClientRect().height || 112,
        };
        card.classList.add("is-resizing");
        const onMove = (moveEvent: PointerEvent) => {
            const width = clampNumber(start.width + (moveEvent.clientX - start.x), MIN_AGENT_NODE_WIDTH, MAX_AGENT_NODE_WIDTH);
            const logHeight = clampNumber(start.logHeight + (moveEvent.clientY - start.y), MIN_AGENT_LOG_HEIGHT, MAX_AGENT_LOG_HEIGHT);
            setNodeOverrides((prev) => ({ ...prev, [nodeId]: { ...(prev[nodeId] || layout), width, logHeight } }));
        };
        const onUp = () => {
            card.classList.remove("is-resizing");
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
        };
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp, { once: true });
    }, []);

    return (
        <section ref={canvasRef} className="agent-canvas agent-canvas--dense mc3-dmc-canvas" aria-label="Agent mission canvas">
            <div className="canvas-toolbar">
                <TabList
                    className="log-mode-switch"
                    aria-label="Log category"
                    selectedValue={categoryFilter}
                    onTabSelect={(_, data) => onCategoryChange(data.value as LogCategoryFilter)}
                    size="small"
                >
                    {LOG_CATEGORY_ORDER.map((category) => (
                        <Tab
                            key={category}
                            value={category}
                            className={`log-mode-switch__option${categoryFilter === category ? " is-active" : ""}`}
                        >
                            <span className="log-mode-switch__label">{LOG_CATEGORY_TAB_LABEL[category]}</span>
                            <Badge appearance="tint" color={categoryFilter === category ? "brand" : "subtle"} size="small">
                                {categoryCounts[category]}
                            </Badge>
                        </Tab>
                    ))}
                </TabList>
                <div className="canvas-view-controls" aria-label="Canvas view controls">
                    <Button
                        className={`canvas-view-button canvas-view-button--mode${!showLogStream ? " is-active" : ""}`}
                        aria-label="Show agent cards"
                        aria-pressed={!showLogStream}
                        appearance="subtle"
                        size="small"
                        onClick={() => setCanvasViewPreference("agents")}
                        disabled={autoLogStream}
                    >
                        Agents
                    </Button>
                    <Button
                        className={`canvas-view-button canvas-view-button--mode${showLogStream ? " is-active" : ""}`}
                        aria-label="Show agent log stream"
                        aria-pressed={showLogStream}
                        appearance="subtle"
                        icon={<Document16Regular />}
                        size="small"
                        onClick={() => setCanvasViewPreference("logs")}
                    >
                        Logs
                    </Button>
                    {autoLogStream && <Badge appearance="tint" color="brand" size="small" className="canvas-view-auto-badge">Auto</Badge>}
                    <Tooltip content="Reset agent layout" relationship="label">
                        <Button
                            className="canvas-view-button"
                            aria-label="Reset agent layout"
                            appearance="subtle"
                            icon={<ArrowResetRegular />}
                            size="small"
                            onClick={resetCanvasView}
                            disabled={Object.keys(nodeOverrides).length === 0}
                        />
                    </Tooltip>
                </div>
            </div>

            {showLogStream ? (
                <CanvasLogStream state={state} slots={slots} categoryFilter={categoryFilter} autoCompact={autoLogStream} />
            ) : (
                <>
                    <svg className="canvas-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                        {slotLayouts.map(({ nodeId, layout }) => {
                            const { x, y } = layout;
                            return <path key={nodeId} className="line line--active" d={`M 50 29 C 50 42 ${x} 38 ${x} ${y - 11}`} />;
                        })}
                    </svg>

                    <Card
                        className="agent-node agent-node--generalist agent-node--running"
                        style={{ "--x": "50%", "--y": "29%", transform: "translate(-50%, -50%)" } as React.CSSProperties}
                    >
                        <div className="agent-head">
                            <span className="agent-icon agent-icon--generalist"><Flash16Regular /></span>
                            <div>
                                <h2>Generalist</h2>
                                <p>{state.jobStatus === "completed" ? "Merged summaries" : "Observe, plan, dispatch"}</p>
                            </div>
                            <Badge appearance="tint" color="success" className={state.jobStatus === "completed" ? "state-pill state-pill--done" : "state-pill state-pill--running"}>
                                {state.jobStatus === "completed" ? "Done" : "Running"}
                            </Badge>
                        </div>
                        <LogWindow logs={generalistLogLines} fallback={fallback} large />
                    </Card>

                    {slots.map((slot, index) => (
                        <AgentNodeCard
                            key={slot.slotId || slot.agentId || index}
                            slot={slot}
                            index={index}
                            total={slots.length}
                            state={state}
                            categoryFilter={categoryFilter}
                            layout={slotLayouts[index]?.layout || defaultSubNodeLayout(index, slots.length)}
                            onMoveStart={startNodeMove}
                            onResizeStart={startNodeResize}
                        />
                    ))}
                </>
            )}
        </section>
    );
}

function ChangeLedgerRail({ state, workloadClient }: { state: MissionState; workloadClient: WorkloadClientAPI }) {
    const changes = useMemo(
        () => state.changeOrder.map((id) => state.changes[id]).filter(Boolean),
        [state.changeOrder, state.changes],
    );
    const changeGroups = useMemo(() => groupChangeRecords(changes), [changes]);
    const pendingApprovals = useMemo(
        () => Object.values(state.approvals).filter((approval) => !approval.resolved),
        [state.approvals],
    );
    const badge = state.terminalType
        ? `${changes.filter((change) => change.status === "applied").length || changes.length} applied`
        : pendingApprovals.length > 0
            ? "Approval needed"
            : `${changes.length} tracked`;

    return (
        <aside className="right-rail" aria-label="Change overview">
            <section className={`change-ledger${pendingApprovals.length > 0 ? " change-ledger--approval" : ""}`}>
                <header className="change-ledger__header">
                    <h2>Change overview</h2>
                    <span>{badge}</span>
                </header>
                {CHANGE_SECTION_ORDER.map((kind) => {
                    const group = changeGroups[kind];
                    const approvalRows = kind === "important_action" && group.length === 0 ? pendingApprovals : [];
                    return (
                        <section key={kind} className="ledger-section">
                            <h3>{CHANGE_SECTION_LABEL[kind]}</h3>
                            {group.length === 0 && approvalRows.length === 0 ? (
                                <p className="empty-ledger">No {CHANGE_SECTION_LABEL[kind].toLowerCase()} yet.</p>
                            ) : group.length > 0 ? group.map((change) => (
                                <Card key={change.recordId} className={`ledger-row${changeLedgerRowClass(change)}`}>
                                    <span className="ledger-row__fluent-icon">{changeIcon(change.kind, change.targetScope)}</span>
                                    <div className="ledger-row__body">
                                        <div className="ledger-row__meta">
                                            <Badge appearance="outline" color="subtle" className="ledger-agent">{visibleAgentName(change.agentName || change.agentId) || "runtime"}</Badge>
                                            <Badge appearance="tint" color={changeBadgeColor(change.status)} className={`ledger-status ledger-status--${String(change.status || "tracked").toLowerCase()}`}>{changeStatusLabel(change.status)}</Badge>
                                        </div>
                                        <strong>{change.targetName}</strong>
                                        <small>{visibleRuntimeText(change.summary)}</small>
                                    </div>
                                    {change.webUrl && (
                                        <a
                                            href={change.webUrl}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="mc3-change-card__link"
                                            aria-label={`Open ${change.targetName}`}
                                            onClick={externalLinkOnClick(workloadClient, change.webUrl)}
                                        >
                                            <Open20Regular />
                                        </a>
                                    )}
                                </Card>
                            )) : approvalRows.map((approval) => (
                                <Card key={approval.approvalId} className="ledger-row ledger-row--pending">
                                    <span className="ledger-row__fluent-icon"><Settings16Regular /></span>
                                    <div className="ledger-row__body">
                                        <div className="ledger-row__meta">
                                            <Badge appearance="outline" color="subtle" className="ledger-agent">{visibleAgentName(approval.agentId) || "runtime"}</Badge>
                                            <Badge appearance="tint" color="warning" className="ledger-status ledger-status--waiting">Waiting</Badge>
                                        </div>
                                        <strong>Approval required</strong>
                                        <small>{approval.summary}</small>
                                    </div>
                                </Card>
                            ))}
                        </section>
                    );
                })}
            </section>
        </aside>
    );
}

// ════════════════════════════════════════════════════════════════════
// ROOT
// ════════════════════════════════════════════════════════════════════

export function MissionControlPage({
    workloadClient, sessionId, githubToken: githubTokenProp, initialFabricToken, initialJob,
}: MissionControlPageProps) {
    const githubToken = githubTokenProp || sessionStorage.getItem("github_token") || "";
    const [fabricToken, setFabricToken] = useState<string | undefined>(initialFabricToken);
    const history = useHistory();

    useEffect(() => {
        if (!sessionId) return;
        // When rendered inline from OrchestratorPage (initialJob present),
        // the parent already replaced the active tab and URL — skip the
        // history.replace to avoid spawning a duplicate tab.
        if (initialJob) return;
        const target = `/agent-hub/session/${sessionId}${history.location.search || ""}`;
        if (`${history.location.pathname}${history.location.search}` !== target) history.replace(target);
    }, [sessionId, history, initialJob]);

    useEffect(() => {
        if (initialFabricToken) return undefined;
        let c = false;
        (async () => {
            try { const tk = await callAuthAcquireAccessToken(workloadClient); if (!c) setFabricToken(tk?.token); }
            catch { /* best-effort */ }
        })();
        return () => { c = true; };
    }, [workloadClient, initialFabricToken]);

    const { state, isConnected, reconnectCount, error } = useMissionStream(sessionId, {
        getSessionOpts: { githubToken, fabricToken },
    });

    const [fetchedJob, setFetchedJob] = useState<MissionControlPageProps["initialJob"]>(null);
    useEffect(() => {
        if (initialJob || !sessionId || !fabricToken) return undefined;
        let c = false;
        (async () => {
            try {
                const j = await api.getSession(sessionId, { githubToken, fabricToken });
                if (c) return;
                setFetchedJob({
                    task_description: j.task_description, workspace_id: j.workspace_id,
                    workspace_name: j.context?.workspace_name ?? null,
                    started_at: j.started_at ?? null, status: j.status,
                    context: j.context ?? null,
                });
            } catch { /* best-effort */ }
        })();
        return () => { c = true; };
    }, [initialJob, sessionId, fabricToken, githubToken]);
    const job = initialJob ?? fetchedJob;

    const startedAtMs = useMemo(() => {
        const iso = job?.started_at ?? null;
        return iso ? new Date(iso).getTime() : Date.now();
    }, [job?.started_at]);
    const [elapsedSec, setElapsedSec] = useState(0);
    useEffect(() => {
        if (state.terminalType) return undefined;
        const tick = () => setElapsedSec(Math.max(0, Math.floor((Date.now() - startedAtMs) / 1000)));
        tick();
        const id = window.setInterval(tick, 1000);
        return () => window.clearInterval(id);
    }, [startedAtMs, state.terminalType]);

    const [terminating, setTerminating] = useState(false);
    const [optimisticCancelled, setOptimisticCancelled] = useState(false);
    const handleTerminate = useCallback(async () => {
        if (terminating) return;
        setTerminating(true); setOptimisticCancelled(true);
        try { await api.cancelSession(sessionId, { githubToken, fabricToken }); }
        catch (e) { console.warn("[mc] cancel failed", e); }
        finally { setTerminating(false); }
    }, [sessionId, githubToken, fabricToken, terminating]);

    const effectiveStatus: JobStatusLite = optimisticCancelled && !state.terminalType
        ? "cancelled" : state.jobStatus;
    const runtimeSlots = useMemo(
        () => buildRuntimeSlotViews(state),
        [state.composition, state.slotProgress, state.agentStatus, state.activeAgentId, state.approvals, state.logs.length, state.jobStatus],
    );
    const terminal = !!state.terminalType;
    const comp: any = state.composition;
    const patternLabel = comp?.architecture || "supervisor";
    const visibleCompositionSlotCount = Array.isArray(comp?.slots)
        ? comp.slots.filter((slot: any) => !isInternalRuntimeSlot({
            id: String(slot.id || ""),
            agentId: String(slot.agentId || ""),
            role: String(slot.role || ""),
        })).length
        : 0;
    const agentCount = Math.max(visibleCompositionSlotCount, runtimeSlots.length);
    const activeNameRaw = state.activeAgentId ? nameFor(state, state.activeAgentId) : null;
    const activeName = visibleAgentName(activeNameRaw);
    const [canvasLogCategory, setCanvasLogCategory] = useState<LogCategoryFilter>("high_level");
    const pendingApprovals = useMemo(
        () => Object.values(state.approvals).filter((a) => !a.resolved),
        [state.approvals],
    );
    const pendingApprovalCount = pendingApprovals.length;

    const slotStats = useMemo(() => {
        const done = runtimeSlots.filter((s) => s.lifecycle === "finished").length;
        const running = runtimeSlots.filter((s) => s.lifecycle === "running").length;
        const waiting = runtimeSlots.filter((s) => s.lifecycle === "waiting" || s.lifecycle === "spinning_up").length;
        const failed = runtimeSlots.filter((s) => s.lifecycle === "failed").length;
        return { done, running, waiting, failed, total: Math.max(agentCount, runtimeSlots.length) };
    }, [runtimeSlots, agentCount]);
    const statusPill: Record<string, [string, string]> = {
        planned: ["mc-pill mc-pill--planned", "Planned"],
        approved: ["mc-pill mc-pill--running", "Starting"],
        running: ["mc-pill mc-pill--running", "Running"],
        completed: ["mc-pill mc-pill--done", "Complete"],
        failed: ["mc-pill mc-pill--failed", "Failed"],
        cancelled: ["mc-pill mc-pill--waiting", "Cancelled"],
    };
    const [, pillLabel] = statusPill[effectiveStatus] ?? ["mc-pill", effectiveStatus];

    // Use the full task description; the heading clamps to 2 lines
    // via CSS so it never breaks the layout but still gives meaningful
    // context (a single-line ellipsis at 55 chars was too aggressive).
    const terminalActionLabel = effectiveStatus === "completed" ? "Completed" : "Terminated";
    const terminateButtonClass = terminal && effectiveStatus === "completed"
        ? "mc3-btn mc3-btn--muted"
        : "mc3-btn mc3-btn--danger";
    const TerminateIcon = terminal && effectiveStatus === "completed" ? Checkmark12Filled : Dismiss12Filled;
    const statusIntent = terminal && effectiveStatus === "completed" ? "success" : pendingApprovalCount > 0 ? "warning" : effectiveStatus === "failed" ? "danger" : "success";
    const visibleAgentTotal = runtimeSlots.length || agentCount;

    return (
        <div className={`mc3 mc3-dmc-live${terminal ? " mc3--terminal" : ""}`}>
            <div className="run-status-strip mc3-dmc-status-strip">
                <div className="mc3-dmc-status-main">
                    <span className="mc3-dmc-status-title"><Play16Filled /> Mission Control</span>
                    <Badge appearance="filled" color={statusIntent as any} icon={terminal && effectiveStatus === "completed" ? <CheckmarkCircle16Filled /> : undefined}>
                        {pillLabel}
                    </Badge>
                    <span className="mc3-dmc-status-chip">{patternLabel}</span>
                    <span className="mc3-dmc-status-chip">{visibleAgentTotal} agents</span>
                    {activeName && <span className="mc3-dmc-status-active">Active: {activeName}</span>}
                </div>
                <div className="mc3-dmc-status-actions">
                    {!isConnected && !terminal && (
                        <Badge appearance="tint" color="danger" className="mc3-dmc-reconnect-badge">
                            Reconnecting{reconnectCount > 0 ? ` (${reconnectCount})` : ""}…
                        </Badge>
                    )}
                    {error && <Badge appearance="tint" color="danger">{error}</Badge>}
                    <div className="mc3-identity__timer">
                        <span className={`mc3-identity__timer-dot${terminal ? " mc3-identity__timer-dot--done" : " mc3-identity__timer-dot--live"}`} />
                        <span className="mc3-identity__timer-val">{terminal && state.totalDuration ? state.totalDuration : fmtElapsed(elapsedSec)}</span>
                    </div>
                    <Tooltip content="Pause is coming soon" relationship="label">
                        <Button appearance="subtle" icon={<Timer16Regular />} disabled size="small">
                            Pause
                        </Button>
                    </Tooltip>
                    <Button
                        appearance={terminal && effectiveStatus === "completed" ? "subtle" : "outline"}
                        className={terminateButtonClass}
                        icon={<TerminateIcon />}
                        onClick={handleTerminate}
                        disabled={terminating || terminal}
                        size="small"
                    >
                        {terminating ? "Stopping…" : terminal ? terminalActionLabel : "Terminate"}
                    </Button>
                </div>
            </div>

            {/* ── Task prompt recap ──────────────────────────── */}
            <TaskPromptRecap
                task={job?.task_description || ""}
                workspaceName={(job?.workspace_name as string) || null}
                workspaceId={(job?.workspace_id as string) || null}
                workspaceItems={(job?.context?.context_items as any) || null}
                attachments={(job?.context?.prompt_attachments as any) || null}
                defaultOpen={false}
            />

            <div className="mission-grid mission-grid--right mc3-dmc-grid">
                <DynamicMissionCanvas
                    state={state}
                    runtimeSlots={runtimeSlots}
                    categoryFilter={canvasLogCategory}
                    onCategoryChange={setCanvasLogCategory}
                />
                <ChangeLedgerRail state={state} workloadClient={workloadClient} />
            </div>
        </div>
    );
}

// ════════════════════════════════════════════════════════════════════
// LIVE LOG
// ════════════════════════════════════════════════════════════════════

type LiveLogFilterMode = "all" | "agent" | "step" | "signals";

function LiveLog({ state, runtimeSlots, logFocusMode, onToggleLogFocus, onApproval, approvalBusy, mode, onModeChange }: {
    state: MissionState;
    runtimeSlots: RuntimeSlotView[];
    logFocusMode: boolean;
    onToggleLogFocus: () => void;
    onApproval: (ap: PendingApproval, action: RecoveryAction) => void;
    approvalBusy: string | null;
    mode: LogViewMode;
    onModeChange: (mode: LogViewMode) => void;
}) {
    const scrollRef = useRef<HTMLDivElement>(null);
    const pinnedRef = useRef(true);
    const [followStream, setFollowStream] = useState(true);
    const [categoryFilter, setCategoryFilter] = useState<LogCategoryFilter>("high_level");
    const [filterMode, setFilterMode] = useState<LiveLogFilterMode>("all");
    const [agentFilter, setAgentFilter] = useState("all");
    const [stepFilter, setStepFilter] = useState("all");

    const onScroll = useCallback(() => {
        const el = scrollRef.current;
        if (!el) return;
        const atBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 32;
        pinnedRef.current = atBottom;
        if (!atBottom) setFollowStream(false);
    }, []);

    const handleJumpToLatest = useCallback(() => {
        const el = scrollRef.current;
        if (!el) return;
        el.scrollTop = el.scrollHeight;
        pinnedRef.current = true;
        setFollowStream(true);
    }, []);

    useEffect(() => {
        if (!followStream || !pinnedRef.current) return;
        const el = scrollRef.current;
        if (el) el.scrollTop = el.scrollHeight;
    }, [state.logs.length, followStream]);

    const terminal = !!state.terminalType;

    const categoryCounts = useMemo(() => {
        const counts: Record<LogCategoryFilter, number> = {
            high_level: 0,
            detailed: 0,
            diagnostic: 0,
        };
        for (const entry of state.logs) {
            const category = entry.logCategory;
            if (category === "high_level" || category === "detailed" || category === "diagnostic") {
                counts[category] += 1;
            }
        }
        return counts;
    }, [state.logs]);

    const categoryLogs = useMemo(
        () => state.logs.filter((entry) => entry.logCategory === categoryFilter),
        [state.logs, categoryFilter],
    );

    const agentBuckets = useMemo(() => {
        const buckets = new Map<string, {
            key: string;
            label: string;
            ids: Set<string>;
            names: Set<string>;
            count: number;
            kind: string;
        }>();

        for (const l of categoryLogs) {
            if (!l.agentId) continue;
            const rawLabel = (l.agentName || nameFor(state, l.agentId) || l.agentId).trim();
            if (isInternalAgentRef(l.agentId) || isInternalAgentRef(rawLabel)) continue;
            const label = rawLabel;
            if (!label) continue;
            const key = canonicalAgentLabel(label);
            const kind = kindForWithProgress(l.agentId, state.composition, state.slotProgress);
            const existing = buckets.get(key);

            if (!existing) {
                buckets.set(key, {
                    key,
                    label,
                    ids: new Set([l.agentId]),
                    names: new Set([key]),
                    count: 1,
                    kind,
                });
                continue;
            }

            existing.ids.add(l.agentId);
            existing.names.add(key);
            existing.count += 1;
            if (existing.label === existing.label.toLowerCase() && /[A-Z]/.test(label)) {
                existing.label = label;
            }
            if (existing.kind === "generic" && kind !== "generic") {
                existing.kind = kind;
            }
        }

        return buckets;
    }, [categoryLogs, state.slotProgress, state.composition]);

    const agents = useMemo(() => Array.from(agentBuckets.values()), [agentBuckets]);

    const stepBuckets = useMemo(() => {
        return runtimeSlots.map((slot, idx) => {
            const ids = new Set<string>([slot.slotId, slot.agentId]);
            for (const p of Object.values(state.slotProgress || {})) {
                if (progressMatchesSlot(p, {
                    id: slot.slotId,
                    agentId: slot.agentId,
                    role: slot.role,
                })) {
                    const pid = String((p as any).agentId || "");
                    const psid = String((p as any).slotId || "");
                    if (pid) ids.add(pid);
                    if (psid) ids.add(psid);
                }
            }
            const count = categoryLogs.filter((l) => !!l.agentId && ids.has(l.agentId)).length;
            const title = `${idx + 1}. ${slot.role || slot.agentName || slot.agentId}`;
            return {
                key: slot.slotId,
                title,
                lifecycle: slot.lifecycle,
                ids,
                count,
            };
        });
    }, [runtimeSlots, categoryLogs, state.slotProgress]);

    useEffect(() => {
        if (agentFilter !== "all" && !agentBuckets.has(agentFilter)) {
            setAgentFilter("all");
        }
    }, [agentFilter, agentBuckets]);

    useEffect(() => {
        if (stepFilter !== "all" && !stepBuckets.some((s) => s.key === stepFilter)) {
            setStepFilter("all");
        }
    }, [stepFilter, stepBuckets]);

    const logs = useMemo(() => {
        if (filterMode === "signals") {
            return categoryLogs.filter(isHighSignalLog);
        }

        if (filterMode === "agent") {
            if (agentFilter === "all") return categoryLogs;
            const bucket = agentBuckets.get(agentFilter);
            if (!bucket) return categoryLogs;
            return categoryLogs.filter((l) => {
                if (!l.agentId) return false;
                if (bucket.ids.has(l.agentId)) return true;
                const logName = canonicalAgentLabel(String(l.agentName || ""));
                return !!logName && bucket.names.has(logName);
            });
        }

        if (filterMode === "step") {
            if (stepFilter === "all") return categoryLogs;
            const bucket = stepBuckets.find((s) => s.key === stepFilter);
            if (!bucket) return categoryLogs;
            return categoryLogs.filter((l) => !!l.agentId && bucket.ids.has(l.agentId));
        }

        return categoryLogs;
    }, [categoryLogs, filterMode, agentFilter, stepFilter, agentBuckets, stepBuckets]);
    const pending = Object.values(state.approvals).filter((a) => !a.resolved);

    // Determine which entries are "major" (get a step-connector dot)
    // vs "minor" (tool calls, detail lines — rendered inline)
    const entries = useMemo(() => {
        const MAJOR_KINDS = new Set(["phase", "decision", "log", "action", "error"]);
        const result: Array<{ entry: LogEntry; children: LogEntry[]; major: boolean }> = [];
        for (const e of logs) {
            if (filterMode === "signals") {
                result.push({ entry: e, children: [], major: true });
                continue;
            }
            if (MAJOR_KINDS.has(e.kind)) {
                result.push({ entry: e, children: [], major: true });
            } else if (mode === "detail") {
                // Tool calls are minor — attach to previous major entry
                const prev = result.length > 0 ? result[result.length - 1] : null;
                if (prev) {
                    prev.children.push(e);
                } else {
                    // No prior major entry — show as standalone
                    result.push({ entry: e, children: [], major: true });
                }
            }
        }
        return result;
    }, [logs, mode, filterMode]);

    const handleCopyLog = useCallback(() => {
        const text = logs.map(l => {
            const label = visibleAgentName(l.agentName || l.agentId || "") || "";
            const support = [
                l.eventId ? `event=${l.eventId}` : null,
                l.payloadDigest ? `digest=${l.payloadDigest}` : null,
            ].filter(Boolean).join(" ");
            return `[${fmtLogTs(l.ts)}] ${label}: ${visibleRuntimeText(l.message)}${support ? ` (${support})` : ""}`;
        }).join("\n");
        navigator.clipboard?.writeText(text);
    }, [logs]);

    // Next pending approval for footer hint
    const nextApproval = pending.length > 0 ? pending[0] : null;

    return (
        <section className="mc3-log">
            <div className="mc3-log__bar">
                <span className="mc3-log__bar-icon"><Play16Filled /></span>
                <span className="mc3-log__bar-title">{terminal ? "Full run log" : "Live log"}</span>
                {logs.length > 0 && (
                    <span className="mc3-log__bar-meta">
                        {logs.length} shown · {state.logs.length} total · {categoryCounts[categoryFilter]} {LOG_CATEGORY_LABEL[categoryFilter].toLowerCase()}{state.totalDuration ? ` · ${state.totalDuration}` : ""}
                    </span>
                )}
                <div className="mc3-log__bar-spacer" />
                <div className="mc3-log__bar-actions">
                    <div className="mc3-inline-toggle" role="tablist" aria-label="Log category">
                        {LOG_CATEGORY_ORDER.map((category) => (
                            <button
                                key={category}
                                role="tab"
                                className={`mc3-inline-toggle__btn${categoryFilter === category ? " is-active" : ""}`}
                                aria-selected={categoryFilter === category}
                                onClick={() => {
                                    setCategoryFilter(category);
                                    setAgentFilter("all");
                                    setStepFilter("all");
                                }}
                            >
                                {LOG_CATEGORY_LABEL[category]} ({categoryCounts[category]})
                            </button>
                        ))}
                    </div>
                    <div className="mc3-inline-toggle" role="tablist" aria-label="Live log density">
                        <button
                            role="tab"
                            className={`mc3-inline-toggle__btn${mode === "overview" ? " is-active" : ""}`}
                            aria-selected={mode === "overview"}
                            onClick={() => onModeChange("overview")}
                        >
                            Condensed
                        </button>
                        <button
                            role="tab"
                            className={`mc3-inline-toggle__btn${mode === "detail" ? " is-active" : ""}`}
                            aria-selected={mode === "detail"}
                            onClick={() => onModeChange("detail")}
                        >
                            Expanded
                        </button>
                    </div>
                    <button
                        className={`mc3-log__bar-action${followStream ? " is-active" : ""}`}
                        title={followStream ? "Following latest output" : "Resume following stream"}
                        onClick={handleJumpToLatest}
                    >
                        {followStream ? "Following" : "Follow"}
                    </button>
                    <div className="mc3-log-layout-toggle" role="tablist" aria-label="Log layout">
                        <button
                            role="tab"
                            className={`mc3-log-layout-toggle__btn${!logFocusMode ? " is-active" : ""}`}
                            aria-selected={!logFocusMode}
                            onClick={() => logFocusMode && onToggleLogFocus()}
                        >
                            Standard
                        </button>
                        <button
                            role="tab"
                            className={`mc3-log-layout-toggle__btn${logFocusMode ? " is-active" : ""}`}
                            aria-selected={logFocusMode}
                            onClick={() => !logFocusMode && onToggleLogFocus()}
                        >
                            Expanded log
                        </button>
                    </div>
                    <button className="mc3-log__bar-action" title="Copy log" onClick={handleCopyLog}>
                        <Copy20Regular /> Copy
                    </button>
                </div>
            </div>

            <div className="mc3-log__filters" aria-label="Log filters">
                <div className="mc3-log__filter-control">
                    <span className="mc3-log__filter-label">Focus</span>
                    <Dropdown
                        size="small"
                        selectedOptions={[filterMode]}
                        value={
                            filterMode === "all" ? `All activity (${categoryLogs.length})`
                            : filterMode === "agent" ? "By agent"
                            : filterMode === "step" ? "By route step"
                            : "Issues + key decisions"
                        }
                        onOptionSelect={(_, data) => {
                            const next = String(data.optionValue || "all") as LiveLogFilterMode;
                            setFilterMode(next);
                            if (next !== "agent") setAgentFilter("all");
                            if (next !== "step") setStepFilter("all");
                        }}
                    >
                        <Option value="all" text={`All activity (${categoryLogs.length})`}>All activity ({categoryLogs.length})</Option>
                        <Option value="agent" text="By agent" disabled={agents.length === 0}>By agent</Option>
                        <Option value="step" text="By route step" disabled={stepBuckets.length === 0}>By route step</Option>
                        <Option value="signals" text="Issues + key decisions">Issues + key decisions</Option>
                    </Dropdown>
                </div>

                {filterMode === "agent" && (
                    <div className="mc3-log__filter-control mc3-log__filter-control--wide">
                        <span className="mc3-log__filter-label">Agent</span>
                        <Dropdown
                            size="small"
                            selectedOptions={[agentFilter]}
                            onOptionSelect={(_, data) => setAgentFilter(String(data.optionValue || "all"))}
                        >
                            <Option value="all" text={`All agents (${categoryLogs.length})`}>All agents ({categoryLogs.length})</Option>
                            {agents.map((a) => (
                                <Option key={a.key} value={a.key} text={`${a.label} (${a.count})`}>{a.label} ({a.count})</Option>
                            ))}
                        </Dropdown>
                    </div>
                )}

                {filterMode === "step" && (
                    <div className="mc3-log__filter-control mc3-log__filter-control--wide">
                        <span className="mc3-log__filter-label">Step</span>
                        <Dropdown
                            size="small"
                            selectedOptions={[stepFilter]}
                            onOptionSelect={(_, data) => setStepFilter(String(data.optionValue || "all"))}
                        >
                            <Option value="all" text={`All steps (${categoryLogs.length})`}>All steps ({categoryLogs.length})</Option>
                            {stepBuckets.map((s) => (
                                <Option key={s.key} value={s.key} text={`${s.title} (${s.count})`}>{s.title} ({s.count})</Option>
                            ))}
                        </Dropdown>
                    </div>
                )}

                {filterMode === "signals" && (
                    <div className="mc3-log__signals-pill">
                        <Badge appearance="outline" color="informative">High-signal mode</Badge>
                    </div>
                )}
            </div>

            {/* Scrollable log body */}
            <div className="mc3-log__scroll" ref={scrollRef} onScroll={onScroll} role="log" aria-live="polite">
                {entries.map((g, gi) => (
                    <StreamEntry
                        key={g.entry.seq}
                        entry={g.entry}
                        children={g.children}
                        index={gi}
                        last={gi === entries.length - 1}
                        terminal={terminal}
                        state={state}
                        showChildren={mode === "detail"}
                    />
                ))}
                {pending.map((ap) => (
                    <InlineApproval key={ap.approvalId} approval={ap} busy={approvalBusy === ap.approvalId} onAction={(a) => onApproval(ap, a)} />
                ))}
                {!terminal && logs.length === 0 && (
                    <p className="mc3-log__empty">
                        {filterMode === "all"
                            ? "Waiting for the first agent to come online…"
                            : "No events match the current focus filter."}
                    </p>
                )}
                {!followStream && logs.length > 0 && (
                    <div className="mc3-log__tail">
                        <span className="mc3-log__tail-note">New output available</span>
                        <button className="mc3-log__tail-btn" onClick={handleJumpToLatest}>
                            Jump to latest
                        </button>
                    </div>
                )}
            </div>

            {/* Footer — shows next approval hint or general info */}
            {!terminal && nextApproval && (
                <div className="mc3-log__footer">
                    <span className="mc3-log__footer-info">
                        <Info16Regular /> Next approval expected: {nextApproval.summary.slice(0, 60)}
                    </span>
                </div>
            )}
        </section>
    );
}

// ── Stream entry: one step-connector entry per major event ──────────
// Matches design's step-connector pattern: each entry gets a dot
// (✓ done / ⚡ active / ✕ error), agent icon + name, message text,
// and optional inline tool-call children.

function StreamEntry({ entry, children, index, last, terminal, state, showChildren }: {
    entry: LogEntry;
    children: LogEntry[];
    index: number;
    last: boolean;
    terminal: boolean;
    state: MissionState;
    showChildren: boolean;
}) {
    const effectiveLevel = resolvedLogLevel(entry);
    const inferredMajorSeverity = inferIssueSeverity(entry.message);
    const isTerminalEntry = /\b(complet|done|finish|cancel|fail|error|Run complete)\b/i.test(entry.message)
        || entry.kind === "decision";
    const isActive = last && !terminal && !isTerminalEntry && effectiveLevel !== "error";
    const isError = effectiveLevel === "error";
    const isWarn = effectiveLevel === "warn";
    const isDone = !isActive && !isError;
    const rawAgentName = entry.agentName || (entry.agentId ? nameFor(state, entry.agentId) : null);
    const agentName = visibleAgentName(rawAgentName);
    const kind = entry.agentId && !isInternalAgentRef(entry.agentId) && !isInternalAgentRef(rawAgentName)
        ? kindForWithProgress(entry.agentId, state.composition, state.slotProgress)
        : "generic";

    // Extract a short label from the entry
    let label = "";
    if (entry.kind === "phase") {
        const colonIdx = entry.message.indexOf(":");
        label = colonIdx >= 0 ? entry.message.slice(colonIdx + 1).trim() : "";
    } else if (entry.kind === "action") {
        label = "action";
    } else if (entry.kind === "decision") {
        label = "decision";
    }

    // Truncate very long messages to keep the log readable
    let displayMsg = visibleRuntimeText(entry.message);
    if (displayMsg.length > 300) {
        displayMsg = displayMsg.slice(0, 300) + "…";
    }

    // Find tool duration from children
    const doneChild = children.find(c => c.kind === "tool_end");
    const doneLabel = doneChild && doneChild.durationMs != null
        ? `done · ${doneChild.durationMs < 1000
            ? `${doneChild.durationMs}ms`
            : `${Math.round(doneChild.durationMs / 1000)}s`}`
        : null;

    const connCls = isActive ? " active" : isDone ? " completed" : "";
    const entryCls = `mc3-entry stream-in${isActive ? " mc3-entry--active" : ""}${isError ? " mc3-entry--error" : ""}${isWarn ? " mc3-entry--warn" : ""}`;

    // Detect if this entry mentions fabric items for badge rendering
    const fabricBadges = extractFabricBadges(displayMsg);
    const supportTitle = [
        entry.sourceEventType ? `event type: ${entry.sourceEventType}` : null,
        entry.eventId ? `event id: ${entry.eventId}` : null,
        entry.payloadDigest ? `payload digest: ${entry.payloadDigest}` : null,
    ].filter(Boolean).join("\n");

    return (
        <div className={entryCls} title={supportTitle || undefined} style={{ animationDelay: `${Math.min(index, 12) * 22}ms` }}>
            {/* Vertical connector + dot */}
            <div className={`mc3-entry__connector${connCls}${last ? " mc3-entry__connector--last" : ""}`}>
                {isError ? (
                    <div className="mc3-entry__dot mc3-entry__dot--error"><Dismiss12Filled /></div>
                ) : isWarn ? (
                    <div className="mc3-entry__dot mc3-entry__dot--warn">!</div>
                ) : isActive ? (
                    <div className="mc3-entry__dot mc3-entry__dot--active think-pulse"><Flash16Regular /></div>
                ) : (
                    <div className="mc3-entry__dot mc3-entry__dot--done"><Checkmark12Filled /></div>
                )}
            </div>

            {/* Content */}
            <div className="mc3-entry__body">
                {/* Header row: agent icon + name + label + time */}
                <div className="mc3-entry__head">
                    {agentName && (
                        <span className="mc-node__icon mc3-entry__agent-icon" data-agent={kind} style={{ width: 22, height: 22 }}>
                        </span>
                    )}
                    {agentName && <span className="mc3-entry__agent-name">{agentName}</span>}
                    {label && <span className="mc3-entry__phase-label">{label}</span>}
                    {inferredMajorSeverity && (
                        <span className={`mc3-entry__major mc3-entry__major--${inferredMajorSeverity}`}>
                            {inferredMajorSeverity === "error" ? "Major issue" : "Warning signal"}
                        </span>
                    )}
                    {isDone && doneLabel && (
                        <span className="mc3-entry__done-label">{doneLabel}</span>
                    )}
                    {isActive && <span className="mc-pill mc-pill--running" style={{ marginLeft: "auto" }}>Running</span>}
                    <span className="mc3-entry__ts">{fmtLogTs(entry.ts)}</span>
                </div>

                {/* Entry body text */}
                {displayMsg && (
                    <p className="mc3-entry__text">{displayMsg}</p>
                )}

                {/* Fabric badges for mentioned items */}
                {fabricBadges.length > 0 && (
                    <div className="mc3-entry__badges">
                        {fabricBadges.map((b, i) => (
                            <span key={i} className={`fabric-badge fabric-badge-${b.type}`}>{b.name}</span>
                        ))}
                    </div>
                )}

                {/* Inline tool calls with dark code block styling */}
                {showChildren && children.length > 0 && (
                    <div className="mc3-entry__children">
                        <ToolCallStack children={children} />
                    </div>
                )}
            </div>
        </div>
    );
}

/** Extract fabric item references from message text for badge rendering. */
function extractFabricBadges(msg: string): Array<{ type: string; name: string }> {
    const badges: Array<{ type: string; name: string }> = [];
    const patterns: Array<[RegExp, string]> = [
        [/\b([\w-]+_raw|[\w-]+_lakehouse)\b/gi, "lakehouse"],
        [/\b(Gold_\w+|[\w-]+_DW)\b/gi, "warehouse"],
        [/\b([\w-]+\.py)\b/gi, "notebook"],
        [/OneLake:\/\/([^\s]+)/gi, "lakehouse"],
    ];
    for (const [re, type] of patterns) {
        let m: RegExpExecArray | null;
        while ((m = re.exec(msg)) !== null) {
            const name = m[1] || m[0];
            if (!badges.some(b => b.name === name)) {
                badges.push({ type, name });
            }
        }
    }
    return badges;
}

interface ToolGroup {
    key: string;
    start?: LogEntry;
    end?: LogEntry;
    other?: LogEntry;
}

function buildToolGroups(children: LogEntry[]): ToolGroup[] {
    const groups: ToolGroup[] = [];
    const openByCallId = new Map<string, number>();

    for (const child of children) {
        if (child.kind === "tool_start") {
            const group: ToolGroup = {
                key: child.callId || `tool-${child.seq}`,
                start: child,
            };
            groups.push(group);
            if (child.callId) {
                openByCallId.set(child.callId, groups.length - 1);
            }
            continue;
        }

        if (child.kind === "tool_end") {
            const idx = child.callId ? openByCallId.get(child.callId) : undefined;
            if (idx != null && groups[idx] && !groups[idx].end) {
                groups[idx].end = child;
                openByCallId.delete(child.callId as string);
            } else {
                groups.push({
                    key: child.callId || `tool-end-${child.seq}`,
                    end: child,
                });
            }
            continue;
        }

        groups.push({ key: `line-${child.seq}`, other: child });
    }

    return groups;
}

function ToolCallStack({ children }: { children: LogEntry[] }) {
    const groups = useMemo(() => buildToolGroups(children), [children]);
    return (
        <>
            {groups.map((group) => (
                <ToolCallBlock key={group.key} group={group} />
            ))}
        </>
    );
}

function ToolCallBlock({ group }: { group: ToolGroup }) {
    if (group.other) {
        return <div className="mc3-tool-block__line">{formatToolLine(group.other)}</div>;
    }

    const start = group.start;
    const end = group.end;
    const toolName = end?.toolName || start?.toolName;
    const terminalLabel = typeof start?.argsPreview?.terminalLabel === "string"
        ? start.argsPreview.terminalLabel
        : null;
    const terminalLines = Array.isArray(start?.argsPreview?.terminalLines)
        ? start.argsPreview.terminalLines.filter((line): line is string => typeof line === "string")
        : [];
    const toolLabel = terminalLabel || formatToolName(toolName);
    const status = end?.toolStatus || (end?.level === "error" ? "error" : start ? "running" : "ok");
    const command = formatToolCommand(toolName, start?.argsPreview);
    const argsSummary = terminalLines.length > 0 ? null : formatToolArgsSummary(start?.argsPreview);
    const duration = end?.durationMs != null ? formatDurationMs(end.durationMs) : null;
    const hasError = status === "error";

    if (terminalLines.length > 0) {
        return (
            <div className={`mc3-tool-block mc3-tool-block--terminal${hasError ? " mc3-tool-block--error" : ""}`}>
                {terminalLabel && <div className="mc3-tool-block__terminal-title">{terminalLabel}</div>}
                <div className="mc3-tool-block__terminal-lines">
                    {terminalLines.map((line, index) => (
                        <div key={`${line}-${index}`} className="mc3-tool-block__terminal-line">{line}</div>
                    ))}
                </div>
            </div>
        );
    }

    const copyCommand = () => {
        navigator.clipboard?.writeText(command);
    };

    return (
        <div className={`mc3-tool-block mc3-tool-block--terminal${hasError ? " mc3-tool-block--error" : ""}`}>
            <div className="mc3-tool-block__header">
                {status === "running" ? <Flash16Regular /> : status === "error" ? <Dismiss12Filled /> : <Checkmark12Filled />}
                <span>{toolLabel}</span>
                <span className={`mc3-tool-block__chip mc3-tool-block__chip--${status === "running" ? "running" : hasError ? "error" : "ok"}`}>
                    {status === "running" ? "running" : hasError ? "failed" : "done"}
                </span>
                {duration && <span className="mc3-tool-block__duration">{duration}</span>}
                <button className="mc3-tool-block__copy" onClick={copyCommand} title="Copy command">
                    Copy
                </button>
            </div>
            <div className="mc3-tool-block__cmd">$ {command}</div>
            {argsSummary && <div className="mc3-tool-block__meta">args: {argsSummary}</div>}
            {end?.errorPreview && <div className="mc3-tool-block__stderr">{visibleRuntimeText(end.errorPreview)}</div>}
            {!end && <div className="mc3-tool-block__line">{formatToolLine(start as LogEntry)} …</div>}
        </div>
    );
}

// ── Inline approval ─────────────────────────────────────────────────

function InlineApproval({ approval, busy, onAction }: {
    approval: PendingApproval; busy: boolean; onAction: (a: RecoveryAction) => void;
}) {
    const stub: PlanStep = {
        id: approval.approvalId, order: 0, title: approval.summary,
        action: "configure", target: { itemType: "", displayName: "", workspaceId: "" },
        inputs: [], dependsOn: [], rationale: approval.summary, risk: "medium",
        reversible: approval.reversible ?? true,
        blastRadius: (approval.blastRadius as any) || undefined,
        toolCallPreview: approval.toolCallPreview || undefined,
        recoveryActions: (approval.recoveryActions as any) || undefined,
    };
    return <ApprovalCard step={stub} summary={approval.summary}
        blastRadius={(approval.blastRadius as any) || undefined}
        reversible={approval.reversible ?? undefined}
        toolCallPreview={approval.toolCallPreview || undefined}
        recoveryActions={(approval.recoveryActions as any) || undefined}
        busy={busy} onAction={onAction} />;
}

// ════════════════════════════════════════════════════════════════════
// RIGHT RAIL — matches prototype "Run overview" panel
// ════════════════════════════════════════════════════════════════════

function RunOverviewRail({
    state,
    runtimeSlots,
    tab,
    onTabChange,
    workloadClient,
}: {
    state: MissionState;
    runtimeSlots: RuntimeSlotView[];
    tab: RailTab;
    onTabChange: (tab: RailTab) => void;
    workloadClient: WorkloadClientAPI;
}) {
    const pendingApprovals = useMemo(
        () => Object.values(state.approvals).filter((a) => !a.resolved),
        [state.approvals],
    );
    const errorCount = useMemo(
        () => state.logs.filter((l) => resolvedLogLevel(l) === "error" || l.kind === "error").length
            + runtimeSlots.filter((s) => s.lifecycle === "failed").length,
        [state.logs, runtimeSlots],
    );
    const warningCount = useMemo(
        () => state.logs.filter((l) => resolvedLogLevel(l) === "warn" && !/approval required/i.test(l.message)).length,
        [state.logs],
    );
    const issuesCount = errorCount + warningCount + pendingApprovals.length;

    const slots = runtimeSlots;

    const artifacts = state.artifactOrder.map((id) => state.artifacts[id]).filter(Boolean);
    const changes = useMemo(
        () => state.changeOrder.map((id) => state.changes[id]).filter(Boolean),
        [state.changeOrder, state.changes],
    );
    const appliedChanges = useMemo(
        () => changes.filter((change) => change.status === "applied"),
        [changes],
    );
    const changeGroups = useMemo(() => groupChangeRecords(appliedChanges), [appliedChanges]);
    const terminal = !!state.terminalType;
    const doneCount = slots.filter((s) => s.lifecycle === "finished").length;
    const inFlight = slots.some((s) => s.lifecycle === "running" || s.lifecycle === "waiting" || s.lifecycle === "spinning_up");
    const runStep = terminal
        ? slots.length  // When the job is done, show max step
        : Math.min(doneCount + (inFlight ? 1 : 0), slots.length);

    const artifactBadge = (kind: string) => {
        const map: Record<string, string> = {
            lakehouse: "lakehouse", warehouse: "warehouse", table: "warehouse",
            notebook: "notebook", pipeline: "pipeline", report: "report",
            semanticmodel: "semanticmodel", dataflow: "dataflow",
        };
        return map[kind?.toLowerCase()] || "generic";
    };

    const artifactState = (stateValue: "draft" | "written") => {
        if (stateValue === "written") return { className: "available", label: "Available" };
        if (pendingApprovals.length > 0) return { className: "waiting", label: "Waiting for approval" };
        if (errorCount > 0) return { className: "attention", label: "Needs issue review" };
        return { className: "planned", label: "Planned output" };
    };

    return (
        <aside className="mc3-rail">
            {/* Run overview header */}
            <div className="mc3-rail__header">
                <h2 className="mc3-rail__header-title">Run overview</h2>
                {slots.length > 0 && (
                    <span className="mc3-rail__step-badge">route {runStep} / {slots.length}</span>
                )}
            </div>
            <div className="mc3-rail__tabs" role="tablist" aria-label="Run overview panels">
                <button
                    role="tab"
                    className={`mc3-rail__tab${tab === "overview" ? " is-active" : ""}`}
                    aria-selected={tab === "overview"}
                    onClick={() => onTabChange("overview")}
                >
                    Overview
                </button>
                <button
                    role="tab"
                    className={`mc3-rail__tab${tab === "diagnostics" ? " is-active" : ""}`}
                    aria-selected={tab === "diagnostics"}
                    onClick={() => onTabChange("diagnostics")}
                >
                    Diagnostics
                    {issuesCount > 0 && <span className="mc3-rail__tab-count">{issuesCount}</span>}
                </button>
            </div>
            <div className="mc3-rail__scroll">
                {tab === "diagnostics" ? (
                    <DiagnosticsPanel state={state} runtimeSlots={runtimeSlots} />
                ) : (
                    <>

                {/* Execution route */}
                <div className="mc3-rail__section">
                    <h3 className="mc3-rail__section-title">Execution route</h3>
                    <ol className="mc3-progress">
                        {slots.map((s, i) => {
                            const done = s.lifecycle === "finished";
                            const running = s.lifecycle === "running" || s.isActive;
                            const approval = s.lifecycle === "waiting";
                            const failed = s.lifecycle === "failed";
                            const booting = s.lifecycle === "spinning_up";
                            const future = s.lifecycle === "planned";
                            return (
                                <li key={s.slotId} className={`mc3-progress__item${future ? " mc3-progress__item--future" : ""}`}>
                                    <div className={`mc3-progress__dot${done ? " done" : running ? " running" : approval ? " approval" : failed ? " failed" : booting ? " booting" : ""}`}>
                                        {done ? <Checkmark12Filled />
                                            : running ? <Spinner size="tiny" />
                                                : failed ? <Dismiss12Filled />
                                                    : approval ? "!"
                                                        : <span>{i + 1}</span>}
                                    </div>
                                    <div className="mc3-progress__text">
                                        <div className={`mc3-progress__name${running ? " mc3-progress__name--active" : ""}`}>
                                            {s.role || s.agentId}
                                        </div>
                                        <div className="mc3-progress__meta">
                                            {s.agentName}
                                            {done && " · done"}
                                            {booting && " · spinning up"}
                                            {running && " · running …"}
                                            {approval && <span className="mc3-progress__approval"> · approval required</span>}
                                            {failed && <span className="mc3-progress__failed"> · failed</span>}
                                            {s.reason && ` · ${s.reason}`}
                                        </div>
                                    </div>
                                </li>
                            );
                        })}
                        {slots.length === 0 && <li className="mc3-progress__empty">Team hasn&apos;t reported in yet.</li>}
                    </ol>
                </div>

                {/* Outputs */}
                <div className="mc3-rail__section">
                    <div className="mc3-rail__section-head">
                        <h3 className="mc3-rail__section-title">Outputs</h3>
                        {artifacts.length > 0 && <span className="mc3-rail__count">{artifacts.length}</span>}
                    </div>{/* end section-head */}
                    <div className="mc3-artifacts">
                        {artifacts.map((a) => {
                            const currentState = artifactState(a.state);
                            return (
                                <div
                                    key={a.artifactId}
                                    className={`mc3-artifact${currentState.className === "waiting" ? " mc3-artifact--waiting" : ""}${currentState.className === "attention" ? " mc3-artifact--attention" : ""}`}
                                >
                                    <div className={`mc3-artifact__icon fabric-badge-${artifactBadge(a.kind)}`} />
                                    <div className="mc3-artifact__info">
                                        <div className="mc3-artifact__name">{a.name}</div>
                                        <div className={`mc3-artifact__state ${currentState.className}`}>
                                            {a.state === "written" && <Checkmark12Filled />}
                                            {currentState.label}
                                        </div>
                                    </div>
                                    {a.webUrl && (
                                        <a
                                            href={a.webUrl}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="mc3-artifact__link"
                                            aria-label={`Open ${a.name}`}
                                            onClick={externalLinkOnClick(workloadClient, a.webUrl)}
                                        >
                                            <Open20Regular />
                                        </a>
                                    )}
                                </div>
                            );
                        })}
                        {artifacts.length === 0 && <p className="mc3-artifacts__empty">Outputs appear when agents create or update Fabric items.</p>}
                    </div>
                </div>

                {/* Change overview */}
                <div className="mc3-rail__section mc3-change-overview">
                    <div className="mc3-rail__section-head mc3-change-overview__head">
                        <h3 className="mc3-rail__section-title">Change overview</h3>
                        {appliedChanges.length > 0 && <span className="mc3-change-overview__badge">{appliedChanges.length} APPLIED</span>}
                    </div>
                    {appliedChanges.length === 0 ? (
                        <p className="mc3-changes__empty">No applied changes yet.</p>
                    ) : (
                        <div className="mc3-change-groups">
                            {CHANGE_SECTION_ORDER.map((kind) => {
                                const group = changeGroups[kind];
                                if (group.length === 0) return null;
                                return (
                                    <section key={kind} className="mc3-change-group">
                                        <div className="mc3-change-group__label">{CHANGE_SECTION_LABEL[kind]}</div>
                                        <div className="mc3-change-group__items">
                                            {group.map((change) => (
                                                <article key={change.recordId} className={`mc3-change-card mc3-change-card--${change.kind}`}>
                                                    <div className="mc3-change-card__icon">{changeIcon(change.kind, change.targetScope)}</div>
                                                    <div className="mc3-change-card__body">
                                                        <div className="mc3-change-card__meta">
                                                            <span className="mc3-change-card__agent">{visibleAgentName(change.agentName || change.agentId) || "runtime"}</span>
                                                            <span className="mc3-change-card__status">Applied</span>
                                                        </div>
                                                        <div className="mc3-change-card__title">{change.targetName}</div>
                                                        <p className="mc3-change-card__summary">{visibleRuntimeText(change.summary)}</p>
                                                    </div>
                                                    {change.webUrl && (
                                                        <a
                                                            href={change.webUrl}
                                                            target="_blank"
                                                            rel="noopener noreferrer"
                                                            className="mc3-change-card__link"
                                                            aria-label={`Open ${change.targetName}`}
                                                            onClick={externalLinkOnClick(workloadClient, change.webUrl)}
                                                        >
                                                            <Open20Regular />
                                                        </a>
                                                    )}
                                                </article>
                                            ))}
                                        </div>
                                    </section>
                                );
                            })}
                        </div>
                    )}
                </div>

                {/* Completion summary */}
                {terminal && (
                    <CompletionPanel
                        state={state}
                        warnings={warningCount}
                        errors={errorCount}
                        appliedChanges={appliedChanges.length}
                    />
                )}
                    </>
                )}
            </div>
        </aside>
    );
}

function DiagnosticsPanel({ state, runtimeSlots }: { state: MissionState; runtimeSlots: RuntimeSlotView[] }) {
    const issues = useMemo(() => {
        return state.logs
            .filter((l) => resolvedLogLevel(l) === "error" || l.kind === "error")
            .slice(-12)
            .reverse();
    }, [state.logs]);

    const warnings = useMemo(() => {
        return state.logs
            .filter((l) => resolvedLogLevel(l) === "warn" && !/approval required/i.test(l.message))
            .slice(-12)
            .reverse();
    }, [state.logs]);

    const failedSlots = useMemo(
        () => runtimeSlots.filter((s) => s.lifecycle === "failed"),
        [runtimeSlots],
    );
    const pendingApprovals = useMemo(
        () => Object.values(state.approvals).filter((a) => !a.resolved),
        [state.approvals],
    );

    const recentEvents = useMemo(() => state.logs.slice(-20).reverse(), [state.logs]);

    const recentTools = useMemo(() => {
        return state.logs
            .filter((l) => l.kind === "tool_end")
            .slice(-12)
            .reverse();
    }, [state.logs]);

    return (
        <>
            <div className="mc3-rail__section">
                <h3 className="mc3-rail__section-title">Critical issues</h3>
                {issues.length === 0 && failedSlots.length === 0 ? (
                    <p className="mc3-changes__empty">No runtime errors detected.</p>
                ) : (
                    <div className="mc3-diagnostics-list">
                        {issues.map((i) => (
                            <article key={i.seq} className="mc3-diagnostics-item mc3-diagnostics-item--error">
                                <div className="mc3-diagnostics-item__head">
                                    <strong>{visibleAgentName(i.agentName || i.agentId) || "runtime"}</strong>
                                    <span>{fmtLogTs(i.ts)}</span>
                                </div>
                                <p>{visibleRuntimeText(i.message)}</p>
                            </article>
                        ))}
                        {failedSlots.map((slot) => (
                            <article key={slot.slotId} className="mc3-diagnostics-item mc3-diagnostics-item--error">
                                <div className="mc3-diagnostics-item__head">
                                    <strong>{slot.role || slot.agentName}</strong>
                                    <span>{slot.agentName}</span>
                                </div>
                                <p>{slot.reason || "Execution failed in this route node."}</p>
                            </article>
                        ))}
                    </div>
                )}
            </div>

            <div className="mc3-rail__section">
                <h3 className="mc3-rail__section-title">Warnings & approvals</h3>
                {warnings.length === 0 && pendingApprovals.length === 0 ? (
                    <p className="mc3-changes__empty">No warnings or pending approvals.</p>
                ) : (
                    <div className="mc3-diagnostics-list">
                        {warnings.map((w) => (
                            <article key={w.seq} className="mc3-diagnostics-item mc3-diagnostics-item--warn">
                                <div className="mc3-diagnostics-item__head">
                                    <strong>{visibleAgentName(w.agentName || w.agentId) || "runtime"}</strong>
                                    <span>{fmtLogTs(w.ts)}</span>
                                </div>
                                <p>{visibleRuntimeText(w.message)}</p>
                            </article>
                        ))}
                        {pendingApprovals.map((ap) => (
                            <article key={ap.approvalId} className="mc3-diagnostics-item mc3-diagnostics-item--warn">
                                <div className="mc3-diagnostics-item__head">
                                    <strong>Approval required</strong>
                                    <span>{fmtLogTs(ap.raisedAt)}</span>
                                </div>
                                <p>{ap.summary}</p>
                            </article>
                        ))}
                    </div>
                )}
            </div>

            <div className="mc3-rail__section">
                <h3 className="mc3-rail__section-title">Recent tool results</h3>
                {recentTools.length === 0 ? (
                    <p className="mc3-changes__empty">No tool calls completed yet.</p>
                ) : (
                    <div className="mc3-diagnostics-list">
                        {recentTools.map((t) => (
                            <article key={t.seq} className={`mc3-diagnostics-item${resolvedLogLevel(t) === "error" ? " mc3-diagnostics-item--error" : ""}`}>
                                <div className="mc3-diagnostics-item__head">
                                    <strong>{formatToolName(t.toolName)}</strong>
                                    <span>{t.durationMs != null ? formatDurationMs(t.durationMs) : fmtLogTs(t.ts)}</span>
                                </div>
                                <p>{visibleRuntimeText(t.message)}</p>
                            </article>
                        ))}
                    </div>
                )}
            </div>

            <div className="mc3-rail__section">
                <h3 className="mc3-rail__section-title">Event stream (latest first)</h3>
                <div className="mc3-diagnostics-feed">
                    {recentEvents.map((e) => (
                        <div
                            key={e.seq}
                            className={`mc3-diagnostics-feed__row${resolvedLogLevel(e) === "error" ? " is-error" : resolvedLogLevel(e) === "warn" ? " is-warn" : ""}`}
                        >
                            <span className="mc3-diagnostics-feed__time">{fmtLogTs(e.ts)}</span>
                            <span className="mc3-diagnostics-feed__kind">{e.kind}</span>
                            <span className="mc3-diagnostics-feed__msg">{visibleRuntimeText(e.message)}</span>
                        </div>
                    ))}
                </div>
            </div>
        </>
    );
}

// ── Completion ──────────────────────────────────────────────────────

function CompletionPanel({
    state,
    warnings,
    errors,
    appliedChanges,
}: {
    state: MissionState;
    warnings: number;
    errors: number;
    appliedChanges: number;
}) {
    const artifacts = useMemo(() => state.artifactOrder.map((id) => state.artifacts[id]).filter(Boolean), [state.artifactOrder, state.artifacts]);
    const writtenArtifacts = useMemo(() => artifacts.filter((a) => a.state === "written").length, [artifacts]);

    const handleExport = () => {
        const blob = new Blob([JSON.stringify({
            jobStatus: state.jobStatus, totalDuration: state.totalDuration,
            artifacts: Object.values(state.artifacts), changes: Object.values(state.changes), logs: state.logs,
            streamObservability: getMissionObservationSnapshot(),
        }, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = `mission-${new Date().toISOString()}.json`;
        a.click(); URL.revokeObjectURL(url);
    };

    const isSuccess = state.terminalType === "job_complete";
    const isFailed = state.terminalType === "job_failed";

    return (
        <div className={`mc3-rail__section mc3-completion${isSuccess ? " mc3-completion--success" : isFailed ? " mc3-completion--failed" : ""}`}>
            <div className="mc3-completion__header">
                {isSuccess && <CheckmarkCircle16Filled />}
                <h3 className="mc3-completion__title">
                    {isSuccess ? "Run complete" : isFailed ? "Run failed" : "Run cancelled"}
                    {state.totalDuration && <span className="mc3-completion__duration"> · {state.totalDuration}</span>}
                </h3>
            </div>
            <div className="mc3-completion__summary-grid">
                <div className="mc3-completion__tile">
                    <span className="mc3-completion__tile-label">Artifacts written</span>
                    <strong className="mc3-completion__tile-value">{writtenArtifacts} / {artifacts.length}</strong>
                </div>
                <div className="mc3-completion__tile">
                    <span className="mc3-completion__tile-label">Applied changes</span>
                    <strong className="mc3-completion__tile-value">{appliedChanges}</strong>
                </div>
                <div className="mc3-completion__tile">
                    <span className="mc3-completion__tile-label">Warnings</span>
                    <strong className="mc3-completion__tile-value">{warnings}</strong>
                </div>
                <div className="mc3-completion__tile">
                    <span className="mc3-completion__tile-label">Errors</span>
                    <strong className="mc3-completion__tile-value">{errors}</strong>
                </div>
            </div>
            <div className="mc3-completion__cta">
                <button className="mc3-btn mc3-btn--muted" onClick={handleExport}>
                    <ArrowDownload20Regular /> Export
                </button>
                <button className="mc3-btn mc3-btn--primary" onClick={() => window.location.assign("/agent-hub/orchestrator")}>
                    Start another
                </button>
            </div>
        </div>
    );
}

const legacyMissionSurfaceReferences = [
    TeamPanel,
    applyRuntimeTeam,
    LiveLog,
    RunOverviewRail,
    DiagnosticsPanel,
    CompletionPanel,
];
void legacyMissionSurfaceReferences;
