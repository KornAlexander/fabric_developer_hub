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
import { Spinner, Badge, Card, Button, Dropdown, Option, Textarea, Tooltip } from "@fluentui/react-components";
import {
    Checkmark12Filled,
    Dismiss12Filled,
    Play16Filled,
    Document16Regular,
    Edit16Regular,
    Delete20Regular,
    Settings16Regular,
    Copy20Regular,
    Info16Regular,
    Flash16Regular,
    Send20Regular,
    Stop20Regular,
    Target20Regular,
    Open20Regular,
    PeopleTeam20Regular,
    Database20Regular,
    BuildingFactory20Regular,
    DocumentPdf20Regular,
    Image20Regular,
    Folder20Regular,
} from "@fluentui/react-icons";
import type { WorkloadClientAPI } from "@ms-fabric/workload-client";

import * as api from "../../../controller/AgentHubApi";
import { callAuthAcquireAccessToken } from "../../../controller/AgentHubController";
import { externalLinkOnClick } from "../openExternalTab";
import { TeamPanel } from "../team/TeamPanel";
import { ApprovalCard } from "../approvals/ApprovalCard";
import { useMissionStream } from "./useMissionStream";
import { agentKind } from "../team/OrchCanvas";
import type { LogEntry, PendingApproval, MissionState } from "./missionReducer";
import type { Artifact, ChangeKind, ChangeRecord, MissionOutputCreatedItem } from "./events";
import type { PlanStep, RecoveryAction, Team, TeamNodeStatus } from "../plan/types";
import { formatDurationMs, formatLatencyBreakdownMs, formatToolLine, formatToolName } from "./logPresentation";
import { formatToolArgsSummary, formatToolCommand, formatVisibleRuntimeText } from "./logPresentation";
import { buildExecutionTranscriptRows, type ExecutionRowState } from "./executionStream";
import { MissionPiSurface } from "./pi/MissionPiSurface";
import {
    PI_LOG_COMPACTION_EXTENSION,
    PI_MISSION_UI_EXTENSION,
    PI_ORCHESTRATION_RUNTIME_PACKAGE,
    PI_SUBAGENTS_PACKAGE,
} from "./pi/piExtensionPackages";

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
        runtime?: string | null;
        started_at?: string | null;
        status?: string;
        context?: Record<string, any> | null;
        composition?: any | null;
    } | null;
}

type LogViewMode = "overview" | "detail";
type RailTab = "overview" | "diagnostics";

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

function activeAgentNameFor(state: MissionState, runtimeSlots: RuntimeSlotView[]): string | null {
    const latestAgentLog = [...state.logs].reverse().find((entry) => entry.agentName && !isInternalAgentRef(entry.agentName));
    if (latestAgentLog?.agentName) return latestAgentLog.agentName;

    const activeProgress = [...Object.values(state.slotProgress || {})].reverse().find((progress: any) => {
        const status = String(progress?.status || "").toLowerCase();
        return /^(running|waiting|approval_required)$/.test(status) && progress?.agentName;
    }) as any;
    if (activeProgress?.agentName) return String(activeProgress.agentName);

    const activeRuntimeSlot = runtimeSlots.find((slot) => slot.lifecycle === "running" || slot.lifecycle === "waiting")
        || runtimeSlots.find((slot) => slot.isActive);
    return activeRuntimeSlot?.agentName || (state.activeAgentId ? nameFor(state, state.activeAgentId) : null);
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

type PiSubagentsBridgeRowKind = "status" | "control" | "result" | "async";

interface PiSubagentsBridgeRow {
    key: string;
    kind: PiSubagentsBridgeRowKind;
    seq: number;
    runId: string;
    title: string;
    state: string;
    summary: string;
    meta: string[];
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
            String((progress as any)?.agentName || ""),
            String((progress as any)?.role || ""),
        ].includes(state.activeAgentId);

        let lifecycle: RuntimeLifecycle = "planned";
        if (runtimeState === "error" || progressStatus === "failed") {
            lifecycle = "failed";
        } else if (runtimeState === "completed" || progressStatus === "done") {
            lifecycle = "finished";
        } else if (hasPendingApproval || progressStatus === "approval_required" || progressStatus === "waiting" || runtimeState === "waiting") {
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

        if (state.jobStatus === "completed" || state.terminalType === "job_complete") {
            lifecycle = "finished";
        } else if (state.jobStatus === "cancelled" || state.terminalType === "job_cancelled") {
            lifecycle = lifecycle === "failed" ? "failed" : "finished";
        }

        const reason = hasPendingApproval
            ? "Approval required"
            : String((progress as any)?.currentStep || (progress as any)?.reason || "") || undefined;

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

const HIDDEN_OUTPUT_TYPES = new Set(["browser_visual_render", "browser_visual", "browser_screenshot", "validation", "solution_validation", "inventory_validation"]);

type MissionOutputNode = {
    key: string;
    id?: string;
    name: string;
    type: string;
    mode: string;
    status?: string;
    webUrl?: string;
    summary?: string;
    isFolder: boolean;
    folderId?: string;
    folderName?: string;
    parentFolderId?: string;
    children: MissionOutputNode[];
};

function outputString(value: unknown): string | undefined {
    if (typeof value !== "string") return undefined;
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : undefined;
}

function outputTypeKey(value: unknown): string {
    return String(value || "item").trim().toLowerCase().replace(/[\s-]+/g, "_");
}

function isHiddenOutputType(value: unknown): boolean {
    return HIDDEN_OUTPUT_TYPES.has(outputTypeKey(value));
}

function isFolderOutputType(value: unknown): boolean {
    return outputTypeKey(value) === "folder" || outputTypeKey(value) === "workspaceinventorysolution";
}

function outputModeLabel(mode?: string): string {
    const normalized = String(mode || "updated").toLowerCase();
    if (normalized === "created" || normalized === "written") return "Created";
    if (normalized === "updated") return "Updated";
    if (normalized === "deleted") return "Deleted";
    if (normalized === "draft") return "Draft";
    if (normalized === "important_action") return "Action";
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function outputModeBadgeColor(mode?: string): "success" | "warning" | "danger" | "brand" | "subtle" {
    const normalized = String(mode || "updated").toLowerCase();
    if (normalized === "created" || normalized === "written") return "success";
    if (normalized === "updated") return "brand";
    if (normalized === "deleted") return "danger";
    if (normalized === "draft" || normalized === "important_action") return "warning";
    return "subtle";
}

function outputTypeLabel(type?: string): string {
    const raw = String(type || "Item").trim() || "Item";
    if (raw === "WorkspaceInventorySolution") return "Folder";
    return raw.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
}

function outputDetails(details: unknown): Record<string, unknown> {
    if (!details || typeof details !== "object" || Array.isArray(details)) return {};
    return details as Record<string, unknown>;
}

function visibleCreatedItems(items?: MissionOutputCreatedItem[] | null): MissionOutputCreatedItem[] {
    if (!Array.isArray(items)) return [];
    return items.filter((item) => {
        const type = item.type || item.itemType;
        return !isHiddenOutputType(type);
    });
}

function createdItemToOutputNode(item: MissionOutputCreatedItem, mode: string, status?: string): MissionOutputNode | null {
    const type = item.type || item.itemType || "Item";
    if (isHiddenOutputType(type)) return null;
    const name = outputString(item.displayName) || outputString(item.name) || outputString(item.id) || outputTypeLabel(type);
    const id = outputString(item.id) || outputString(item.itemId);
    const folderLike = isFolderOutputType(type);
    const key = `${folderLike ? "folder" : "item"}:${id || `${outputTypeKey(type)}:${name}`}`;
    return {
        key,
        id,
        name,
        type: folderLike ? "Folder" : type,
        mode,
        status: outputString(item.status) || status,
        webUrl: outputString(item.webUrl) || outputString(item.url),
        summary: outputString(item.description),
        isFolder: folderLike,
        folderId: folderLike ? undefined : outputString(item.folderId),
        folderName: outputString(item.folderName),
        parentFolderId: folderLike ? outputString(item.parentFolderId) || outputString(item.folderId) : undefined,
        children: [],
    };
}

function changeToOutputNode(change: ChangeRecord): MissionOutputNode | null {
    if (isHiddenOutputType(change.targetType)) return null;
    const folderLike = change.targetScope === "folder" || isFolderOutputType(change.targetType);
    const name = outputString(change.targetName) || outputString(change.folderName) || outputString(change.targetId) || outputTypeLabel(change.targetType);
    const id = outputString(change.targetId) || outputString(change.folderId);
    return {
        key: `${folderLike ? "folder" : "change"}:${id || `${outputTypeKey(change.targetType)}:${name}`}`,
        id,
        name,
        type: folderLike ? "Folder" : change.targetType,
        mode: change.kind,
        status: change.status,
        webUrl: change.webUrl,
        summary: change.summary,
        isFolder: folderLike,
        folderId: folderLike ? undefined : change.folderId,
        folderName: change.folderName,
        parentFolderId: folderLike ? change.parentFolderId : undefined,
        children: [],
    };
}

function artifactToOutputNode(artifact: Artifact): MissionOutputNode | null {
    if (isHiddenOutputType(artifact.kind)) return null;
    const details = outputDetails(artifact.details);
    const type = outputString(details.type) || artifact.kind;
    if (isHiddenOutputType(type)) return null;
    const folderLike = isFolderOutputType(type);
    const id = outputString(details.id) || outputString(details.itemId) || outputString(details.folderId) || artifact.artifactId;
    const name = outputString(details.displayName) || outputString(details.name) || outputString(details.folderName) || artifact.name;
    return {
        key: `${folderLike ? "folder" : "artifact"}:${id || `${outputTypeKey(type)}:${name}`}`,
        id,
        name,
        type: folderLike ? "Folder" : type,
        mode: artifact.state || "draft",
        status: artifact.state,
        webUrl: artifact.webUrl || outputString(details.webUrl) || outputString(details.url),
        summary: artifact.summary || outputString(details.description),
        isFolder: folderLike,
        folderId: folderLike ? undefined : outputString(details.folderId),
        folderName: outputString(details.folderName),
        parentFolderId: folderLike ? outputString(details.parentFolderId) : undefined,
        children: [],
    };
}

function mergeOutputNodes(existing: MissionOutputNode, incoming: MissionOutputNode): MissionOutputNode {
    return {
        ...existing,
        name: existing.name || incoming.name,
        type: existing.type || incoming.type,
        mode: existing.mode === "draft" ? incoming.mode : existing.mode,
        status: existing.status || incoming.status,
        webUrl: existing.webUrl || incoming.webUrl,
        summary: existing.summary || incoming.summary,
        id: existing.id || incoming.id,
        folderId: existing.folderId || incoming.folderId,
        folderName: existing.folderName || incoming.folderName,
        parentFolderId: existing.parentFolderId || incoming.parentFolderId,
        isFolder: existing.isFolder || incoming.isFolder,
    };
}

function buildMissionOutputTree(changes: ChangeRecord[], artifacts: Artifact[]): MissionOutputNode[] {
    const orderedKeys: string[] = [];
    const nodes = new Map<string, MissionOutputNode>();
    const addNode = (node: MissionOutputNode | null) => {
        if (!node || isHiddenOutputType(node.type)) return;
        const existing = nodes.get(node.key);
        if (existing) {
            nodes.set(node.key, mergeOutputNodes(existing, node));
            return;
        }
        nodes.set(node.key, node);
        orderedKeys.push(node.key);
    };

    for (const change of changes) {
        const items = visibleCreatedItems(change.createdItems);
        if (items.length > 0) {
            const parentNode = changeToOutputNode(change);
            if (parentNode?.isFolder) addNode(parentNode);
            items.forEach((item) => addNode(createdItemToOutputNode(item, change.kind, change.status)));
            continue;
        }
        addNode(changeToOutputNode(change));
    }

    for (const artifact of artifacts) {
        const details = outputDetails(artifact.details);
        const createdItems = visibleCreatedItems(details.createdItems as MissionOutputCreatedItem[] | undefined);
        if (createdItems.length > 0) {
            const parentNode = artifactToOutputNode(artifact);
            if (parentNode?.isFolder) addNode(parentNode);
            createdItems.forEach((item) => addNode(createdItemToOutputNode(item, artifact.state || "draft", artifact.state)));
            continue;
        }
        addNode(artifactToOutputNode(artifact));
    }

    const flatNodes = orderedKeys.map((key) => nodes.get(key)).filter(Boolean).map((node) => ({ ...node!, children: [] }));
    const folderById = new Map<string, MissionOutputNode>();
    const folderByName = new Map<string, MissionOutputNode>();
    for (const node of flatNodes) {
        if (!node.isFolder) continue;
        if (node.id) folderById.set(node.id, node);
        folderByName.set(node.name.toLowerCase(), node);
    }

    const roots: MissionOutputNode[] = [];
    for (const node of flatNodes) {
        const parent = node.isFolder
            ? (node.parentFolderId ? folderById.get(node.parentFolderId) : undefined)
            : (node.folderId ? folderById.get(node.folderId) : undefined) || (node.folderName ? folderByName.get(node.folderName.toLowerCase()) : undefined);
        if (parent && parent.key !== node.key) {
            parent.children.push(node);
        } else {
            roots.push(node);
        }
    }
    return roots;
}

function countOutputNodes(nodes: MissionOutputNode[]): number {
    return nodes.reduce((total, node) => total + 1 + countOutputNodes(node.children), 0);
}

function changeLedgerRowClass(change: ChangeRecord): string {
    if (change.status && change.status !== "applied") return ` ledger-row--${change.kind} ledger-row--pending ledger-row--live`;
    if (change.kind === "important_action") return " ledger-row--action";
    return ` ledger-row--${change.kind}`;
}

function logBelongsToSlot(entry: LogEntry, slot: RuntimeSlotView): boolean {
    const slotKeys = [slot.slotId, slot.agentId, slot.agentName].map(canonicalAgentLabel).filter(Boolean);
    const entryKeys = [entry.agentId || "", entry.agentName || ""].map(canonicalAgentLabel).filter(Boolean);
    return entryKeys.some((entryKey) => slotKeys.some((slotKey) => entryKey === slotKey || entryKey.includes(slotKey) || slotKey.includes(entryKey)));
}

function readableSlotLabel(slot: RuntimeSlotView): string {
    return visibleAgentName(slot.agentName) || visibleAgentName(slot.agentId) || slot.role || "Agent";
}

function lifecycleLabel(lifecycle: RuntimeLifecycle): string {
    if (lifecycle === "running") return "running";
    if (lifecycle === "waiting") return "waiting";
    if (lifecycle === "spinning_up") return "starting";
    if (lifecycle === "finished") return "done";
    if (lifecycle === "failed") return "failed";
    return "queued";
}

function slotLatestActivity(state: MissionState, slot: RuntimeSlotView): string {
    if (slot.lifecycle === "finished") {
        if (slot.reason) return visibleRuntimeText(slot.reason).slice(0, 140);
        return `Completed ${slot.role || readableSlotLabel(slot)}`;
    }
    const matchingLog = [...state.logs].reverse().find((entry) => logBelongsToSlot(entry, slot));
    if (matchingLog) return visibleRuntimeText(matchingLog.message).slice(0, 140);
    if (slot.reason) return visibleRuntimeText(slot.reason).slice(0, 140);
    if (slot.lifecycle === "running") return `Working on ${slot.role || readableSlotLabel(slot)}`;
    if (slot.lifecycle === "waiting") return "Waiting for the next safe step";
    if (slot.lifecycle === "spinning_up") return `Preparing ${slot.role || readableSlotLabel(slot)}`;
    return slot.role || readableSlotLabel(slot);
}

function missionStreamStatusText(state: MissionState, streamStatus?: string | null): string {
    if (state.terminalType === "job_failed") return "Run failed";
    if (state.terminalType === "job_cancelled") return "Run cancelled";
    if (state.terminalType === "job_complete") return latestVerifierIssueMessage(state) ? "Verifier rejected; review evidence" : "Run completed";
    if (streamStatus) return visibleRuntimeText(streamStatus);
    if (state.jobStatus === "planned") return "Planning route";
    if (state.jobStatus === "approved") return "Launch approved";
    if (state.jobStatus === "running") return "Waiting for agent telemetry";
    return "Watching mission state";
}

function latestFailureMessage(state: MissionState): string | null {
    const entry = [...state.logs].reverse().find((log) => resolvedLogLevel(log) === "error" || log.kind === "error");
    return entry ? visibleRuntimeText(entry.message) : null;
}

function latestVerifierIssueMessage(state: MissionState): string | null {
    const entry = [...state.logs].reverse().find((log) => /verifier\s+rejected|verifier.*failed/i.test(log.message));
    return entry ? visibleRuntimeText(entry.message) : null;
}

function hasPendingApproval(state: MissionState): boolean {
    return Object.values(state.approvals || {}).some((approval) => !approval.resolved);
}

function missionHeaderStatus(state: MissionState): { statusLabel: string; connectionLabel: string; tone: "running" | "complete" | "failed" | "cancelled" | "waiting" } {
    if (state.terminalType === "job_failed" || state.jobStatus === "failed") {
        return { statusLabel: "failed", connectionLabel: "error", tone: "failed" };
    }
    if (state.terminalType === "job_cancelled" || state.jobStatus === "cancelled") {
        return { statusLabel: "cancelled", connectionLabel: "stopped", tone: "cancelled" };
    }
    if (state.terminalType === "job_complete" || state.jobStatus === "completed") {
        return latestVerifierIssueMessage(state)
            ? { statusLabel: "verifier rejected", connectionLabel: "complete", tone: "failed" }
            : { statusLabel: "completed", connectionLabel: "complete", tone: "complete" };
    }
    if (state.jobStatus === "planned" || state.jobStatus === "approved") {
        return { statusLabel: state.jobStatus, connectionLabel: "starting", tone: "waiting" };
    }
    if (hasPendingApproval(state)) {
        return { statusLabel: "waiting for approval", connectionLabel: "waiting", tone: "waiting" };
    }
    return { statusLabel: state.jobStatus || "running", connectionLabel: "streaming", tone: "running" };
}

type MissionCompactStatusTone = "running" | "success" | "warning" | "error" | "waiting" | "cancelled" | "quiet";

function missionCompactStatus(
    state: MissionState,
    {
        patternLabel,
        visibleAgentTotal,
        streamStatus,
    }: {
        patternLabel: string;
        visibleAgentTotal: number;
        streamStatus?: string | null;
    },
): { tone: MissionCompactStatusTone; label: string; detail: string } {
    const warningCount = state.logs.filter((entry) => resolvedLogLevel(entry) === "warn" && !/approval required/i.test(entry.message)).length;
    const errorCount = state.logs.filter((entry) => resolvedLogLevel(entry) === "error" || entry.kind === "error").length;
    const pendingApprovalCount = Object.values(state.approvals || {}).filter((approval) => !approval.resolved).length;
    const runShape = `${patternLabel} · ${visibleAgentTotal} agent${visibleAgentTotal === 1 ? "" : "s"}`;

    if (state.terminalType === "job_failed" || state.jobStatus === "failed") {
        return {
            tone: "error",
            label: "Error occurred",
            detail: latestFailureMessage(state) || `${errorCount || 1} error${errorCount === 1 ? "" : "s"} · ${runShape}`,
        };
    }
    if (state.terminalType === "job_cancelled" || state.jobStatus === "cancelled") {
        return { tone: "cancelled", label: "Cancelled", detail: runShape };
    }
    if (state.terminalType === "job_complete" || state.jobStatus === "completed") {
        const verifierIssue = latestVerifierIssueMessage(state);
        if (verifierIssue) return { tone: "error", label: "Verifier rejected", detail: verifierIssue };
        if (errorCount > 0) return { tone: "error", label: "Completed with errors", detail: `${errorCount} error${errorCount === 1 ? "" : "s"} · ${runShape}` };
        if (warningCount > 0) return { tone: "warning", label: "Successful with warnings", detail: `${warningCount} warning${warningCount === 1 ? "" : "s"} · ${runShape}` };
        return { tone: "success", label: "Successful", detail: runShape };
    }
    if (pendingApprovalCount > 0) {
        return { tone: "waiting", label: "Waiting for approval", detail: `${pendingApprovalCount} approval${pendingApprovalCount === 1 ? "" : "s"} · ${runShape}` };
    }
    if (streamStatus) {
        return { tone: "quiet", label: "Reconnecting", detail: visibleRuntimeText(streamStatus).slice(0, 160) };
    }
    if (state.jobStatus === "planned" || state.jobStatus === "approved") {
        return { tone: "waiting", label: "Starting", detail: runShape };
    }
    return { tone: "running", label: "Running", detail: runShape };
}

function MissionChatStatusBar({
    state,
    streamStatus,
    connectionLabel,
    connectionState,
    connected,
    patternLabel,
    visibleAgentTotal,
}: {
    state: MissionState;
    streamStatus?: string | null;
    connectionLabel: string;
    connectionState: string;
    connected: boolean;
    patternLabel: string;
    visibleAgentTotal: number;
}) {
    const status = missionCompactStatus(state, { patternLabel, visibleAgentTotal, streamStatus });
    return (
        <div className={`mc3-chat-status-bar mc3-chat-status-bar--${status.tone}`} role="status" aria-live="polite" aria-label={`Mission status: ${status.label}. ${status.detail}`}>
            <span className="mc3-chat-status-bar__dot" aria-hidden="true" />
            <strong>{status.label}</strong>
            <span className="mc3-chat-status-bar__detail">{status.detail}</span>
            <span className="mc3-chat-status-bar__connection" data-state={connectionState} data-connected={String(connected)}>{connectionLabel}</span>
        </div>
    );
}

function emptyTranscriptCopy(state: MissionState, streamStatus?: string | null): { badge: string; headline: string; detail: string; chips: string[]; active: boolean } {
    if (state.jobStatus === "completed" || state.terminalType === "job_complete") {
        return {
            badge: "Complete",
            headline: "Run completed",
            detail: "No public transcript rows were persisted for this session.",
            chips: ["Evidence ready", "Session closed"],
            active: false,
        };
    }
    if (state.jobStatus === "failed" || state.terminalType === "job_failed") {
        const failure = latestFailureMessage(state);
        return {
            badge: "Failed",
            headline: "Mission failed before work could start",
            detail: failure || streamStatus ? visibleRuntimeText(failure || streamStatus || "") : "AgentHub stopped the mission before public work rows were published. The most likely cause is a backend or isolated runtime startup error.",
            chips: ["Runtime error", "Mission halted", "No user action needed yet"],
            active: false,
        };
    }
    if (streamStatus) {
        return {
            badge: "Quiet stream",
            headline: "No agent telemetry is attached yet",
            detail: visibleRuntimeText(streamStatus),
            chips: ["Event channel checked", "Snapshot checked", "Awaiting first row"],
            active: true,
        };
    }
    if (state.jobStatus === "planned") {
        return {
            badge: "Queued",
            headline: "Mission is queued for launch",
            detail: "The transcript will start as soon as the first public agent event arrives.",
            chips: ["Route pending", "No tools started", "Ready to stream"],
            active: true,
        };
    }
    return {
        badge: "Listening",
        headline: "Waiting for the first agent event",
        detail: "The session is open, but no public execution row has reached the transcript yet.",
        chips: ["Event stream open", "Snapshot polling", "No rows yet"],
        active: true,
    };
}

function EmptyTranscriptState({ state, streamStatus }: { state: MissionState; streamStatus?: string | null }) {
    const copy = emptyTranscriptCopy(state, streamStatus);
    return (
        <div className={`canvas-log-empty mc3-exec-empty${copy.active ? " is-active" : ""}`} role="status" aria-live="polite">
            <div className="mc3-exec-empty__signal" aria-hidden="true">
                <span />
                <span />
                <span />
            </div>
            <div className="mc3-exec-empty__body">
                <div className="mc3-exec-empty__head">
                    <Badge appearance="tint" color={copy.active ? "brand" : "subtle"}>{copy.badge}</Badge>
                    {copy.active && <span className="mc3-exec-empty__pulse">listening</span>}
                </div>
                <strong>{copy.headline}</strong>
                <p>{copy.detail}</p>
                <div className="mc3-exec-empty__chips" aria-label="Stream checks">
                    {copy.chips.map((chip) => <span key={chip}>{chip}</span>)}
                </div>
            </div>
        </div>
    );
}

function AgentExecutionLanes({ state, slots, streamStatus }: { state: MissionState; slots: RuntimeSlotView[]; streamStatus?: string | null }) {
    const lanes = useMemo(() => {
        const live = slots.filter((slot) => slot.lifecycle !== "planned");
        if (live.length > 0) return live.slice(0, 6);
        return slots.slice(0, 4);
    }, [slots]);
    const generalistActivity = useMemo(() => {
        const entry = [...state.logs].reverse().find((log) => isInternalAgentRef(log.agentId) || isInternalAgentRef(log.agentName) || log.agentName === "Generalist" || !log.agentId);
        if (entry) return visibleRuntimeText(entry.message).slice(0, 140);
        if (streamStatus) return missionStreamStatusText(state, streamStatus).slice(0, 140);
        if (state.jobStatus === "planned") return "Planning the route";
        if (state.terminalType === "job_failed") return latestFailureMessage(state)?.slice(0, 140) || "Mission failed";
        if (state.terminalType === "job_cancelled") return "Mission cancelled";
        if (state.terminalType) return "Run completed";
        return "Coordinating specialists";
    }, [state, streamStatus]);
    const generalistFailed = state.terminalType === "job_failed" || state.jobStatus === "failed";
    const generalistComplete = !!state.terminalType && !generalistFailed;
    const generalistStateClass = generalistFailed ? " is-failed" : generalistComplete ? " is-done" : " is-running";
    const generalistLabel = generalistFailed ? "failed" : generalistComplete ? "done" : "routing";

    return (
        <div className="mc3-agent-lanes" aria-label="Active agent execution">
            <article className={`mc3-agent-lane mc3-agent-lane--generalist${generalistStateClass}`}>
                <div className="mc3-agent-lane__head">
                    <span className="mc3-agent-lane__dot" />
                    <strong>System</strong>
                    <span>{generalistLabel}</span>
                </div>
                <p>{generalistActivity}</p>
            </article>
            {lanes.map((slot) => (
                <article key={slot.slotId} className={`mc3-agent-lane mc3-agent-lane--${slot.lifecycle}${slot.isActive ? " is-active" : ""}`}>
                    <div className="mc3-agent-lane__head">
                        <span className="mc3-agent-lane__dot" />
                        <strong>{readableSlotLabel(slot)}</strong>
                        <span>{lifecycleLabel(slot.lifecycle)}</span>
                    </div>
                    <p>{slotLatestActivity(state, slot)}</p>
                </article>
            ))}
            {streamStatus && !state.terminalType && (
                <div className="mc3-agent-lanes__status" role="status">{missionStreamStatusText(state, streamStatus)}</div>
            )}
        </div>
    );
}

function MissionOutcomeBanner({ state }: { state: MissionState }) {
    const failure = latestFailureMessage(state);
    const verifierIssue = latestVerifierIssueMessage(state);
    if ((state.terminalType === "job_complete" || state.jobStatus === "completed") && verifierIssue) {
        return (
            <section className="mc3-outcome-banner mc3-outcome-banner--attention" role="alert" aria-label="Mission verifier issue summary">
                <div>
                    <strong>Verifier rejected</strong>
                    <p>Review the evidence before treating this mission as successful: {verifierIssue}</p>
                </div>
                <span>review evidence</span>
            </section>
        );
    }
    if (state.terminalType !== "job_failed" && state.jobStatus !== "failed") return null;
    return (
        <section className="mc3-outcome-banner mc3-outcome-banner--failed" role="alert" aria-label="Mission failure summary">
            <div>
                <strong>Mission failed</strong>
                <p>{failure || "AgentHub stopped this mission before execution telemetry was published. This usually points to a backend/runtime startup failure, not a problem with the mission prompt."}</p>
            </div>
            <span>backend/runtime</span>
        </section>
    );
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
        return { label: "System", kind: "generalist" };
    }

    return {
        label: visibleAgentName(agentRef) || "Runtime",
        kind: kindForWithProgress(log.agentId || agentRef, state.composition, state.slotProgress),
    };
}

function canvasLogAgentTitle(log: LogEntry, slots: RuntimeSlotView[], state: MissionState): string {
    const agent = canvasLogAgent(log, slots, state);
    const slot = slots.find((candidate) => logBelongsToSlot(log, candidate));
    const role = slot?.role || log.payloadSummary?.role || log.payloadSummary?.agentRole;
    const task = slot ? slotLatestActivity(state, slot) : log.payloadSummary?.taskDescription || log.message;
    return [
        `Source: ${agent.label}`,
        role ? `Task: ${visibleRuntimeText(String(role)).slice(0, 140)}` : null,
        task ? `Latest: ${visibleRuntimeText(String(task)).slice(0, 180)}` : null,
    ].filter(Boolean).join("\n");
}

function CanvasLogStream({ state, slots, streamStatus }: { state: MissionState; slots: RuntimeSlotView[]; streamStatus?: string | null }) {
    const [expanded, setExpanded] = useState<Set<number>>(() => new Set());
    const logs = useMemo(
        () => state.logs.slice(-192),
        [state.logs],
    );
    const rows = useMemo(() => buildExecutionTranscriptRows(logs, state, state.logs), [logs, state]);

    const toggleExpanded = useCallback((seq: number) => {
        setExpanded((current) => {
            const next = new Set(current);
            if (next.has(seq)) next.delete(seq);
            else next.add(seq);
            return next;
        });
    }, []);

    return (
        <div className="canvas-log-stream mc3-log" role="log" aria-label="Mission log stream" data-pi-live-log="true">
            {rows.length === 0 ? (
                <EmptyTranscriptState state={state} streamStatus={streamStatus} />
            ) : rows.map((row) => {
                const log = row.entry;
                const agent = canvasLogAgent(log, slots, state);
                const rowState = row.state;
                const isExpanded = expanded.has(log.seq);
                return (
                    <article
                        key={row.key}
                        className={`canvas-log-row mc3-transcript-row mc3-exec-row canvas-log-row--${agent.kind} mc3-transcript-row--${rowState}${row.isLive ? " mc3-exec-row--live" : ""}${row.isAttention ? " mc3-exec-row--attention" : ""}`}
                        data-kind={log.kind}
                        data-state={rowState}
                        data-pi-live-log-row="true"
                        data-pi-log-seq={log.seq}
                        data-pi-log-kind={log.kind}
                        data-pi-log-level={resolvedLogLevel(log)}
                        data-pi-log-collapse-state={row.isReceipt ? "collapsed" : row.isLive ? "recent" : "current"}
                    >
                        <div className="mc3-transcript-row__gutter" aria-hidden="true">
                            <span className={`mc3-transcript-row__dot mc3-transcript-row__dot--${rowState}`}>{transcriptDot(rowState)}</span>
                        </div>
                        <div className="mc3-transcript-row__content">
                            <header className="mc3-transcript-row__head">
                                <span className={`canvas-log-row__agent canvas-log-row__agent--${agent.kind}`}>{agent.label}</span>
                                {row.isLive && <span className="mc3-transcript-row__running">Streaming</span>}
                                <time className="canvas-log-row__time">{fmtLogTs(log.ts)}</time>
                            </header>
                            {row.isTextStream ? (
                                <div className={`mc3-stream-block mc3-stream-block--${row.streamKind || "assistant"}${row.isLive ? " is-live" : ""}`}>
                                    <p className="mc3-stream-block__label">{row.streamKind === "thinking" ? "Thinking" : "Assistant"}</p>
                                    <p className="mc3-stream-block__text">{visibleRuntimeText(row.streamText || log.message)}{row.isLive && <span className="mc3-stream-cursor" aria-hidden="true" />}</p>
                                </div>
                            ) : row.isLive ? (
                                <>
                                    <div className={`mc3-exec-current mc3-exec-current--${row.progress.semanticClass}`} data-spinner-mode={row.progress.mode} aria-label="Current execution spinner">
                                        <span className="mc3-exec-spinner" aria-hidden="true" />
                                        <span className="mc3-exec-glimmer">{visibleRuntimeText(row.progress.spinnerMessage)}</span>
                                    </div>
                                    <div className="mc3-agent-progress-line" aria-label="Current execution step">
                                        <span className="mc3-agent-progress-line__marker" aria-hidden="true">⎿</span>
                                        <span className="mc3-agent-progress-line__text">{visibleRuntimeText(row.progress.statusText)}</span>
                                    </div>
                                </>
                            ) : (
                                <p className="mc3-exec-headline">{visibleRuntimeText(row.headline)}</p>
                            )}
                            <div className="mc3-transcript-row__meta">
                                {row.meta.map((part) => <span key={part}>{part}</span>)}
                            </div>
                            {row.isLive && row.activities.length > 0 && (
                                <div className="mc3-exec-activity-list" aria-label="Current task details">
                                    {row.activities.map((activity) => (
                                        <div key={activity.seq} className={`mc3-exec-activity${activity.current ? " is-current" : ""}${activity.muted ? " is-muted" : ""}`}>
                                            <span className="mc3-exec-activity__marker" data-category={activity.category}>{activity.badge}</span>
                                            <span className="mc3-exec-activity__text">{visibleRuntimeText(activity.text)}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                            {(row.hiddenCount > 0 || row.children.length > 0) && (
                                <Button appearance="transparent" size="small" className="mc3-transcript-row__expand" onClick={() => toggleExpanded(log.seq)} aria-expanded={isExpanded}>
                                    {isExpanded ? "Hide details" : `Show details (+${row.hiddenCount || row.children.length})`}
                                </Button>
                            )}
                            {isExpanded && row.children.length > 0 && (
                                <div className="mc3-transcript-row__children">
                                    {row.children.map((child) => (
                                        <div key={child.seq} className={`mc3-transcript-child mc3-transcript-child--${child.kind}`}>
                                            <span className="mc3-transcript-child__marker">⎿</span>
                                            <span className="mc3-transcript-child__time">{fmtLogTs(child.ts)}</span>
                                            <span className="mc3-transcript-child__text">{visibleRuntimeText(child.message)}</span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </article>
                );
            })}
        </div>
    );
}

function transcriptDot(state: ExecutionRowState): string {
    if (state === "done") return "✓";
    if (state === "error") return "×";
    if (state === "warn") return "!";
    if (state === "queued") return "•";
    return "";
}

function DynamicMissionCanvas({
    state,
    runtimeSlots,
    streamStatus,
}: {
    state: MissionState;
    runtimeSlots: RuntimeSlotView[];
    streamStatus?: string | null;
}) {
    const slots = runtimeSlots.length > 0 ? runtimeSlots : [];

    return (
        <section className="agent-canvas agent-canvas--dense mc3-dmc-canvas" aria-label="Mission logs">
            <CanvasLogStream state={state} slots={slots} streamStatus={streamStatus} />
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
    const errorCount = useMemo(
        () => state.logs.filter((entry) => resolvedLogLevel(entry) === "error" || entry.kind === "error").length,
        [state.logs],
    );
    const warningCount = useMemo(
        () => state.logs.filter((entry) => resolvedLogLevel(entry) === "warn" && !/approval required/i.test(entry.message)).length,
        [state.logs],
    );
    const lastRollup = useMemo(
        () => [...state.logs].reverse().find((entry) => entry.kind === "rollup"),
        [state.logs],
    );
    const latestIssue = useMemo(
        () => [...state.logs].reverse().find((entry) => resolvedLogLevel(entry) !== "info" || entry.kind === "error"),
        [state.logs],
    );
    const activeAgent = activeAgentNameFor(state, runtimeSlots);
    const badge = state.terminalType
        ? `${changes.filter((change) => change.status === "applied").length || changes.length} applied`
        : pendingApprovals.length > 0
            ? "Approval needed"
            : `${changes.length} tracked`;

    return (
        <aside className="right-rail" aria-label="Change overview">
            <section className="mission-pulse" aria-label="Mission pulse">
                <header className="mission-pulse__header">
                    <h2>Mission pulse</h2>
                    <span>{state.jobStatus}</span>
                </header>
                <div className="mission-pulse__rows">
                    <div className="mission-pulse__row">
                        <span>Active</span>
                        <strong>{visibleAgentName(activeAgent) || (state.terminalType ? "Completed" : "Waiting")}</strong>
                    </div>
                    <div className="mission-pulse__row">
                        <span>Latest update</span>
                        <strong>{lastRollup ? visibleRuntimeText(lastRollup.message).slice(0, 120) : "No updates yet"}</strong>
                    </div>
                    <div className="mission-pulse__row">
                        <span>Needs attention</span>
                        <strong>{pendingApprovals[0]?.summary || (latestIssue ? visibleRuntimeText(latestIssue.message).slice(0, 120) : "None")}</strong>
                    </div>
                </div>
            </section>
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

function missionSummaryLine(job: MissionControlPageProps["initialJob"] | null | undefined, state: MissionState): string {
    const task = job?.task_description || (state.composition as any)?.task || "Mission running";
    const workspace = job?.workspace_name || job?.workspace_id || "Fabric workspace";
    return `${workspace} · ${task}`;
}

function plainRecord(value: unknown): Record<string, any> {
    return value && typeof value === "object" ? value as Record<string, any> : {};
}

function isNativePiSurfaceValue(value: unknown): boolean {
    const normalized = String(value || "").trim().toLowerCase();
    return normalized === "native-pi-web-ui"
        || normalized === "native-web-ui"
        || normalized === "pi-web-ui-full-page"
        || normalized === "full-page";
}

function isNativePiSurfaceRequested(job: MissionControlPageProps["initialJob"] | null | undefined, jobContext: Record<string, any>): boolean {
    const piOrchestration = plainRecord(jobContext.pi_orchestration);
    return String(job?.runtime || "").toLowerCase() === "pi"
        || isNativePiSurfaceValue(jobContext.execution_surface_mode)
        || isNativePiSurfaceValue(jobContext.pi_execution_surface)
        || isNativePiSurfaceValue(piOrchestration.execution_surface_mode)
        || isNativePiSurfaceValue(piOrchestration.execution_surface)
        || isNativePiSurfaceValue(piOrchestration.execution_surface_extension_mode);
}

function hasPiHarnessMetadata(jobContext: Record<string, any>, state: MissionState): boolean {
    const piOrchestration = plainRecord(jobContext.pi_orchestration);
    return String(jobContext.runtime || "").toLowerCase() === "pi"
        || String(jobContext.orchestration_runtime || "").toLowerCase() === "pi"
        || String(piOrchestration.runtime || "").toLowerCase() === "pi"
        || String(jobContext.subagent_runtime || "").toLowerCase() === "pi-subagents"
        || String(piOrchestration.subagent_runtime || "").toLowerCase() === "pi-subagents"
        || state.piEvents.some((event) => event.type.startsWith("pi."));
}

function piBridgeSeq(value: unknown, fallback: number): number {
    const seq = Number(value);
    return Number.isFinite(seq) && seq > 0 ? seq : Math.max(1, fallback);
}

function piBridgeText(value: unknown, fallback: string, max = 190): string {
    const raw = String(value || fallback || "").trim();
    const visible = visibleRuntimeText(raw || fallback);
    return visible.length > max ? `${visible.slice(0, max - 1)}…` : visible;
}

function buildPiSubagentsBridgeRows(state: MissionState, runtimeSlots: RuntimeSlotView[]): PiSubagentsBridgeRow[] {
    const rows: PiSubagentsBridgeRow[] = [];
    for (const event of state.piEvents) {
        const fallbackSeq = rows.length + state.lastSeq + 1;
        if (event.type === "pi.subagents.status") {
            rows.push({
                key: `status-${event.runId}-${event.seq}`,
                kind: "status",
                seq: piBridgeSeq(event.seq, fallbackSeq),
                runId: event.runId,
                title: piBridgeText(event.agentName || event.agent || event.agentId, "subagent", 80),
                state: String(event.state || "running"),
                summary: piBridgeText(event.summary || event.task || event.currentTool, "subagent activity"),
                meta: [event.currentTool && `tool ${event.currentTool}`, event.turnCount != null && `turns ${event.turnCount}`, event.toolCount != null && `tools ${event.toolCount}`].filter(Boolean) as string[],
            });
        } else if (event.type === "pi.subagents.control") {
            rows.push({
                key: `control-${event.runId}-${event.seq}`,
                kind: "control",
                seq: piBridgeSeq(event.seq, fallbackSeq),
                runId: event.runId,
                title: piBridgeText(event.agentName || event.agent || event.agentId, "control", 80),
                state: String(event.controlType || "control"),
                summary: piBridgeText(event.message || event.reason, "subagent control signal"),
                meta: [event.currentTool, event.turnCount != null && `turns ${event.turnCount}`, event.toolCount != null && `tools ${event.toolCount}`].filter(Boolean) as string[],
            });
        } else if (event.type === "pi.subagents.result") {
            rows.push({
                key: `result-${event.runId}-${event.seq}`,
                kind: "result",
                seq: piBridgeSeq(event.seq, fallbackSeq),
                runId: event.runId,
                title: piBridgeText(event.agentName || event.agent || event.agentId, "result", 80),
                state: String(event.status || "completed"),
                summary: piBridgeText(event.summary, "subagent result"),
                meta: [event.sessionFile && "session", (event.artifactPath || event.artifactPaths) && "artifacts"].filter(Boolean) as string[],
            });
        } else if (event.type === "pi.subagents.async") {
            rows.push({
                key: `async-${event.asyncId}-${event.seq}`,
                kind: "async",
                seq: piBridgeSeq(event.seq, fallbackSeq),
                runId: event.runId || event.asyncId,
                title: event.asyncId,
                state: String(event.state || "running"),
                summary: piBridgeText(event.summary || event.agents?.join(", "), "async subagent run"),
                meta: [event.mode, event.outputFile && "output"].filter(Boolean) as string[],
            });
        } else if (event.type === "pi.subagent.update") {
            rows.push({
                key: `update-${event.agentId}-${event.seq}`,
                kind: "status",
                seq: piBridgeSeq(event.seq, fallbackSeq),
                runId: event.agentId || `subagent-${event.seq}`,
                title: piBridgeText(event.agentName || event.agentId, "subagent", 80),
                state: String(event.state || "running"),
                summary: piBridgeText(event.task || event.summary || event.role, "context-window fork activity"),
                meta: [event.role].filter(Boolean) as string[],
            });
        }
    }

    if (rows.length > 0) return summarizePiSubagentBridgeRows(rows).slice(-6);

    const activeSlots = runtimeSlots
        .filter((slot) => slot.lifecycle !== "planned")
        .slice(0, 6);
    if (activeSlots.length > 0) {
        return activeSlots.map((slot, index) => ({
            key: `runtime-${slot.slotId}-${index}`,
            kind: "status" as const,
            seq: Math.max(1, state.lastSeq + index + 1),
            runId: slot.slotId || slot.agentId || `runtime-${index}`,
            title: visibleAgentName(slot.agentName || slot.agentId) || "subagent",
            state: slot.status || slot.lifecycle,
            summary: piBridgeText(slot.reason || slot.role, "context-window fork active"),
            meta: [slot.role, slot.lifecycle].filter(Boolean),
        }));
    }

    const latestLog = state.logs[state.logs.length - 1];
    if (latestLog) {
        return [{
            key: `runtime-log-${latestLog.seq}`,
            kind: "status",
            seq: Math.max(1, latestLog.seq),
            runId: latestLog.agentId || "agenthub-pi-bridge",
            title: visibleAgentName(latestLog.agentName || latestLog.agentId) || "Mission control",
            state: latestLog.kind === "error" ? "needs_attention" : "running",
            summary: piBridgeText(latestLog.message, "mission activity"),
            meta: [latestLog.logCategory, latestLog.kind].filter(Boolean),
        }];
    }

    return [];
}

function friendlyPiBridgeState(state: string): string {
    const normalized = String(state || "running").toLowerCase().replace(/[_-]/g, " ");
    if (normalized === "done" || normalized === "complete" || normalized === "completed" || normalized === "ok") return "done";
    if (normalized === "needs attention" || normalized === "blocked" || normalized === "paused") return "needs review";
    if (normalized === "failed" || normalized === "error") return "failed";
    if (normalized === "queued" || normalized === "pending") return "queued";
    if (normalized === "running" || normalized === "active long running") return "running";
    return normalized || "running";
}

function piBridgeRowPriority(row: PiSubagentsBridgeRow): number {
    if (row.kind === "control" || /needs|blocked|failed|error/i.test(row.state)) return 4;
    if (row.kind === "result") return 3;
    if (row.kind === "async") return 2;
    return 1;
}

function summarizePiSubagentBridgeRows(rows: PiSubagentsBridgeRow[]): PiSubagentsBridgeRow[] {
    const byAgent = new Map<string, PiSubagentsBridgeRow>();
    for (const row of [...rows].sort((a, b) => a.seq - b.seq)) {
        const key = canonicalAgentLabel(row.title) || row.runId;
        const current = byAgent.get(key);
        if (!current) {
            byAgent.set(key, { ...row, state: friendlyPiBridgeState(row.state) });
            continue;
        }
        const rowPriority = piBridgeRowPriority(row);
        const currentPriority = piBridgeRowPriority(current);
        const shouldPromote = rowPriority > currentPriority || (rowPriority === currentPriority && row.seq >= current.seq);
        const promoted = shouldPromote ? row : current;
        const supporting = shouldPromote ? current : row;
        byAgent.set(key, {
            ...promoted,
            state: friendlyPiBridgeState(promoted.state),
            summary: piBridgeText(promoted.summary || supporting.summary, "delegated work update"),
            meta: Array.from(new Set([...promoted.meta, ...supporting.meta].map((item) => piBridgeText(item, "detail", 80)))).slice(0, 3),
        });
    }
    return [...byAgent.values()].sort((a, b) => a.seq - b.seq);
}

function piSubagentsRowAttributes(row: PiSubagentsBridgeRow): Record<string, string> {
    const attrs: Record<string, string> = {
        "data-pi-run-id": row.runId,
        "data-pi-seq": String(row.seq),
    };
    if (row.kind === "status") {
        attrs["data-pi-subagents-status-row"] = "true";
        attrs["data-pi-state"] = row.state;
    } else if (row.kind === "control") {
        attrs["data-pi-subagents-control-row"] = "true";
        attrs["data-pi-control"] = row.state;
    } else if (row.kind === "result") {
        attrs["data-pi-subagents-result-row"] = "true";
        attrs["data-pi-result-status"] = row.state;
    } else {
        attrs["data-pi-subagents-async-row"] = "true";
        attrs["data-pi-state"] = row.state;
    }
    return attrs;
}

function focusMissionLogSeq(seq?: number | null): void {
    if (!seq || typeof document === "undefined") return;
    const selector = `[data-pi-log-seq="${String(seq)}"]`;
    const node = document.querySelector<HTMLElement>(selector);
    if (!node) return;
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    node.classList.add("is-focus-pulse");
    window.setTimeout(() => node.classList.remove("is-focus-pulse"), 1800);
}

function PiSubagentsObservabilityBridge({ state, runtimeSlots }: { state: MissionState; runtimeSlots: RuntimeSlotView[] }) {
    const rows = useMemo(() => buildPiSubagentsBridgeRows(state, runtimeSlots), [state.piEvents, state.lastSeq, state.logs, runtimeSlots]);
    if (rows.length === 0) return null;
    const statusCount = rows.filter((row) => row.kind === "status" && row.state === "running").length;
    const controlCount = rows.filter((row) => row.kind === "control" || /review|failed|error/i.test(row.state)).length;
    const resultCount = rows.filter((row) => row.kind === "result" || row.state === "done").length;

    return (
        <section
            className="mc3-pi-subagents-bridge"
            aria-label="Pi subagents observability"
            data-pi-subagents-observability="true"
            data-pi-subagents-runtime="pi-subagents"
            data-pi-subagents-package={PI_SUBAGENTS_PACKAGE.packageName}
            data-pi-subagents-status-count={statusCount}
            data-pi-subagents-control-count={controlCount}
            data-pi-subagents-result-count={resultCount}
            data-pi-orchestration-package={PI_ORCHESTRATION_RUNTIME_PACKAGE.packageName}
            data-pi-extension-surface={PI_MISSION_UI_EXTENSION.packageName}
            data-pi-log-compaction-package={PI_LOG_COMPACTION_EXTENSION.packageName}
        >
            <header className="mc3-pi-subagents-bridge__header">
                <strong>Delegated work</strong>
                <span>{statusCount} active · {controlCount} needs review · {resultCount} done</span>
            </header>
            <div className="mc3-pi-subagents-bridge__rows">
                {rows.map((row) => (
                    <Button key={row.key} appearance="transparent" className={`mc3-pi-subagents-bridge__row mc3-pi-subagents-bridge__row--${row.kind}`} onClick={() => focusMissionLogSeq(row.seq)} aria-label={`Show ${row.title} activity in the mission log`} {...piSubagentsRowAttributes(row)}>
                        <div>
                            <strong>{row.title}</strong>
                            <span>{row.state}</span>
                        </div>
                        <p>{row.summary}</p>
                        {row.meta.length > 0 && (
                            <footer>{row.meta.slice(0, 3).map((item) => <span key={item}>{piBridgeText(item, "detail", 80)}</span>)}</footer>
                        )}
                    </Button>
                ))}
            </div>
        </section>
    );
}

type MissionContextChipVariant = "workspace" | "lakehouse" | "warehouse" | "item" | "pdf" | "image" | "attachment";

interface MissionContextChip {
    key: string;
    label: string;
    meta: string;
    variant: MissionContextChipVariant;
}

function missionWorkspaceName(job: MissionControlPageProps["initialJob"] | null | undefined): string {
    return job?.workspace_name || String((job?.context as any)?.workspace_name || job?.workspace_id || "Fabric workspace");
}

function chipVariantForContextType(typeValue: unknown): MissionContextChipVariant {
    const type = String(typeValue || "").toLowerCase();
    if (type === "workspace") return "workspace";
    if (type === "lakehouse") return "lakehouse";
    if (type === "warehouse") return "warehouse";
    return "item";
}

function chipVariantForAttachment(attachment: Record<string, any>): MissionContextChipVariant {
    const kind = String(attachment.kind || attachment.type || attachment.mime || "").toLowerCase();
    if (kind.includes("pdf")) return "pdf";
    if (kind.includes("image") || /\.(png|jpe?g|gif|webp)$/i.test(String(attachment.name || ""))) return "image";
    return "attachment";
}

function missionContextChips(job: MissionControlPageProps["initialJob"] | null | undefined): MissionContextChip[] {
    const context = plainRecord(job?.context);
    const rawItems = Array.isArray(context.context_items) ? context.context_items : [];
    const rawAttachments = Array.isArray(context.prompt_attachments)
        ? context.prompt_attachments
        : Array.isArray(context.attachments)
            ? context.attachments
            : [];
    const chips: MissionContextChip[] = [];
    const seen = new Set<string>();

    const push = (chip: MissionContextChip) => {
        const key = chip.key || `${chip.variant}:${chip.label}`;
        if (!chip.label || seen.has(key)) return;
        seen.add(key);
        chips.push({ ...chip, key });
    };

    for (const item of rawItems) {
        const record = plainRecord(item);
        const label = String(record.name || record.displayName || record.id || "").trim();
        const variant = chipVariantForContextType(record.type || record.itemType);
        const meta = variant === "workspace" ? "Workspace" : String(record.type || record.itemType || "Fabric item");
        push({ key: `${variant}:${record.id || label}`, label, meta, variant });
    }

    if (job?.workspace_id || job?.workspace_name || context.workspace_name) {
        push({
            key: `workspace:${job?.workspace_id || context.workspace_id || context.workspace_name || job?.workspace_name}`,
            label: missionWorkspaceName(job),
            meta: "Workspace",
            variant: "workspace",
        });
    }

    for (const attachment of rawAttachments) {
        const record = plainRecord(attachment);
        const label = String(record.name || record.file_name || record.filename || "Attached file").trim();
        const variant = chipVariantForAttachment(record);
        const size = Number(record.size || record.byteLength || 0);
        const sizeLabel = Number.isFinite(size) && size > 0
            ? size > 1024 * 1024
                ? `${(size / (1024 * 1024)).toFixed(1)} MB`
                : `${Math.max(1, Math.round(size / 1024))} KB`
            : variant === "pdf"
                ? "PDF"
                : variant === "image"
                    ? "Image"
                    : "File";
        push({ key: `file:${record.id || label}`, label, meta: sizeLabel, variant });
    }

    return chips.slice(0, 18);
}

function MissionContextChipIcon({ variant }: { variant: MissionContextChipVariant }) {
    if (variant === "workspace") return <PeopleTeam20Regular />;
    if (variant === "warehouse") return <BuildingFactory20Regular />;
    if (variant === "lakehouse" || variant === "item") return <Database20Regular />;
    if (variant === "pdf") return <DocumentPdf20Regular />;
    if (variant === "image") return <Image20Regular />;
    return <Document16Regular />;
}

function MissionPromptMessage({ job, state }: { job: MissionControlPageProps["initialJob"] | null | undefined; state: MissionState }) {
    const task = job?.task_description || (state.composition as any)?.task || "Mission started.";
    const chips = useMemo(() => missionContextChips(job), [job]);
    return (
        <article className="mc3-chat-message mc3-chat-message--user mc3-task-context-message" data-mission-prompt-message="true">
            <div className="mc3-chat-avatar" aria-hidden="true">You</div>
            <div className="mc3-chat-bubble mc3-chat-bubble--prompt">
                <div className="mc3-chat-bubble__meta">
                    <span>Task prompt</span>
                    <span>{missionWorkspaceName(job)}</span>
                </div>
                <p className="mc3-task-context-message__text">{task}</p>
                {chips.length > 0 && (
                    <div className="composer-pills mc3-context-pills" aria-label="Attached mission context">
                        {chips.map((chip) => (
                            <span key={chip.key} className={`ctx-pill ctx-pill--${chip.variant}`} title={`${chip.label} · ${chip.meta}`}>
                                <MissionContextChipIcon variant={chip.variant} />
                                <span className="ctx-pill-name">{chip.label}</span>
                            </span>
                        ))}
                    </div>
                )}
            </div>
        </article>
    );
}

function MissionOutcomeChatNotice({ state }: { state: MissionState }) {
    const failure = latestFailureMessage(state);
    const verifierIssue = latestVerifierIssueMessage(state);
    if ((state.terminalType === "job_complete" || state.jobStatus === "completed") && verifierIssue) {
        return (
            <article className="mc3-chat-message mc3-chat-message--system mc3-chat-notice mc3-chat-notice--attention" role="alert" aria-label="Mission verifier issue summary">
                <div className="mc3-chat-avatar" aria-hidden="true">!</div>
                <div className="mc3-chat-bubble">
                    <div className="mc3-chat-bubble__meta"><span>Verifier</span><span>Review evidence</span></div>
                    <p>{verifierIssue}</p>
                </div>
            </article>
        );
    }
    if (state.terminalType !== "job_failed" && state.jobStatus !== "failed") return null;
    return (
        <article className="mc3-chat-message mc3-chat-message--system mc3-chat-notice mc3-chat-notice--failed" role="alert" aria-label="Mission failure summary">
            <div className="mc3-chat-avatar" aria-hidden="true">!</div>
            <div className="mc3-chat-bubble">
                <div className="mc3-chat-bubble__meta"><span>Runtime</span><span>Mission failed</span></div>
                <p>{failure || "AgentHub stopped this mission before execution telemetry was published. This usually points to a backend/runtime startup failure, not a problem with the mission prompt."}</p>
            </div>
        </article>
    );
}

function MissionChatEventRow({ row, state, slots }: { row: ReturnType<typeof buildExecutionTranscriptRows>[number]; state: MissionState; slots: RuntimeSlotView[] }) {
    const [expanded, setExpanded] = useState(false);
    const log = row.entry;
    const agent = canvasLogAgent(log, slots, state);
    const agentTitle = canvasLogAgentTitle(log, slots, state);
    const rowState = row.state;
    const level = resolvedLogLevel(log);
    const messageTone = level === "error" || rowState === "error" ? "failed" : level === "warn" || rowState === "warn" ? "attention" : row.isLive ? "live" : "normal";
    return (
        <article
            className={`mc3-chat-message mc3-chat-message--assistant mc3-chat-event mc3-chat-event--${messageTone} canvas-log-row mc3-transcript-row mc3-exec-row canvas-log-row--${agent.kind} mc3-transcript-row--${rowState}${row.isLive ? " mc3-exec-row--live" : ""}${row.isAttention ? " mc3-exec-row--attention" : ""}`}
            data-kind={log.kind}
            data-state={rowState}
            data-pi-live-log-row="true"
            data-pi-log-seq={log.seq}
            data-pi-log-kind={log.kind}
            data-pi-log-level={level}
            data-pi-log-collapse-state={row.isReceipt ? "collapsed" : row.isLive ? "recent" : "current"}
        >
            <div className="mc3-chat-avatar" aria-hidden="true" title={agentTitle}>{agent.label.slice(0, 2).toUpperCase()}</div>
            <div className="mc3-chat-bubble">
                <header className="mc3-transcript-row__head mc3-chat-event__head">
                    <span className={`canvas-log-row__agent canvas-log-row__agent--${agent.kind}`} title={agentTitle}>{agent.label}</span>
                    {row.isLive && <span className="mc3-transcript-row__running">Streaming</span>}
                    <time className="canvas-log-row__time">{fmtLogTs(log.ts)}</time>
                </header>
                {row.isTextStream ? (
                    <div className={`mc3-stream-block mc3-stream-block--${row.streamKind || "assistant"}${row.isLive ? " is-live" : ""}`}>
                        <p className="mc3-stream-block__label">{row.streamKind === "thinking" ? "Thinking" : "Assistant"}</p>
                        <p className="mc3-stream-block__text">{visibleRuntimeText(row.streamText || log.message)}{row.isLive && <span className="mc3-stream-cursor" aria-hidden="true" />}</p>
                    </div>
                ) : row.isLive ? (
                    <>
                        <div className={`mc3-exec-current mc3-exec-current--${row.progress.semanticClass}`} data-spinner-mode={row.progress.mode} aria-label="Current execution spinner">
                            <span className="mc3-exec-spinner" aria-hidden="true" />
                            <span className="mc3-exec-glimmer">{visibleRuntimeText(row.progress.spinnerMessage)}</span>
                        </div>
                        <div className="mc3-agent-progress-line" aria-label="Current execution step">
                            <span className="mc3-agent-progress-line__marker" aria-hidden="true">⎿</span>
                            <span className="mc3-agent-progress-line__text">{visibleRuntimeText(row.progress.statusText)}</span>
                        </div>
                    </>
                ) : (
                    <p className="mc3-exec-headline">{visibleRuntimeText(row.headline)}</p>
                )}
                {row.meta.length > 0 && (
                    <div className="mc3-transcript-row__meta">
                        {row.meta.map((part) => <span key={part}>{part}</span>)}
                    </div>
                )}
                {row.isLive && row.activities.length > 0 && (
                    <div className="mc3-exec-activity-list" aria-label="Current task details">
                        {row.activities.map((activity) => (
                            <div key={activity.seq} className={`mc3-exec-activity${activity.current ? " is-current" : ""}${activity.muted ? " is-muted" : ""}`}>
                                <span className="mc3-exec-activity__marker" data-category={activity.category}>{activity.badge}</span>
                                <span className="mc3-exec-activity__text">{visibleRuntimeText(activity.text)}</span>
                            </div>
                        ))}
                    </div>
                )}
                {(row.hiddenCount > 0 || row.children.length > 0) && (
                    <Button appearance="transparent" size="small" className="mc3-transcript-row__expand" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
                        {expanded ? "Hide details" : `Show details (+${row.hiddenCount || row.children.length})`}
                    </Button>
                )}
                {expanded && row.children.length > 0 && (
                    <div className="mc3-transcript-row__children">
                        {row.children.map((child) => (
                            <div key={child.seq} className={`mc3-transcript-child mc3-transcript-child--${child.kind}`}>
                                <span className="mc3-transcript-child__marker">⎿</span>
                                <span className="mc3-transcript-child__time">{fmtLogTs(child.ts)}</span>
                                <span className="mc3-transcript-child__text">{visibleRuntimeText(child.message)}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </article>
    );
}

function MissionChatHistory({
    job,
    state,
    runtimeSlots,
    streamStatus,
    connectionLabel,
    connectionState,
    connected,
    patternLabel,
    visibleAgentTotal,
    sessionId,
    githubToken,
    fabricToken,
}: {
    job: MissionControlPageProps["initialJob"] | null | undefined;
    state: MissionState;
    runtimeSlots: RuntimeSlotView[];
    streamStatus?: string | null;
    connectionLabel: string;
    connectionState: string;
    connected: boolean;
    patternLabel: string;
    visibleAgentTotal: number;
    sessionId: string;
    githubToken: string;
    fabricToken?: string;
}) {
    const scrollRef = useRef<HTMLDivElement>(null);
    const followRef = useRef(true);
    const rows = useMemo(() => buildExecutionTranscriptRows(state.logs.slice(-192), state, state.logs), [state.logs, state]);
    const terminal = !!state.terminalType;

    const scrollToLatest = useCallback((behavior: ScrollBehavior = "auto") => {
        const element = scrollRef.current;
        if (!element) return;
        element.scrollTo({ top: element.scrollHeight, behavior });
    }, []);

    const handleScroll = useCallback(() => {
        const element = scrollRef.current;
        if (!element) return;
        const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
        followRef.current = distanceFromBottom < 140;
    }, []);

    useEffect(() => {
        const frame = window.requestAnimationFrame(() => scrollToLatest("auto"));
        return () => window.cancelAnimationFrame(frame);
    }, [scrollToLatest]);

    useEffect(() => {
        if (!followRef.current) return;
        const frame = window.requestAnimationFrame(() => scrollToLatest("auto"));
        return () => window.cancelAnimationFrame(frame);
    }, [rows.length, state.logs.length, state.lastSeq, state.jobStatus, scrollToLatest]);

    return (
        <section className="mc3-chat-history" aria-label="Mission conversation">
            <div ref={scrollRef} className="mc3-chat-scroll canvas-log-stream mc3-log" role="log" aria-label="Mission log stream" data-pi-live-log="true" onScroll={handleScroll} data-auto-follow="true">
                <MissionChatStatusBar
                    state={state}
                    streamStatus={streamStatus}
                    connectionLabel={connectionLabel}
                    connectionState={connectionState}
                    connected={connected}
                    patternLabel={patternLabel}
                    visibleAgentTotal={visibleAgentTotal}
                />
                <MissionPromptMessage job={job} state={state} />
                <MissionOutcomeChatNotice state={state} />
                {rows.length === 0 ? (
                    <div className="mc3-chat-message mc3-chat-message--assistant">
                        <div className="mc3-chat-avatar" aria-hidden="true">AI</div>
                        <div className="mc3-chat-bubble">
                            <EmptyTranscriptState state={state} streamStatus={streamStatus} />
                        </div>
                    </div>
                ) : rows.map((row) => (
                    <MissionChatEventRow key={row.key} row={row} state={state} slots={runtimeSlots} />
                ))}
            </div>
            <MissionSteeringComposer
                sessionId={sessionId}
                githubToken={githubToken}
                fabricToken={fabricToken}
                state={state}
                runtimeSlots={runtimeSlots}
                terminal={terminal}
            />
        </section>
    );
}

function MissionOutputNodeRow({ node, workloadClient, depth = 0 }: { node: MissionOutputNode; workloadClient: WorkloadClientAPI; depth?: number }) {
    const icon = node.isFolder ? <Folder20Regular /> : <Document16Regular />;
    return (
        <article className={`mc3-summary-output ${node.isFolder ? "mc3-summary-output--folder" : "mc3-summary-output--item"}`} style={{ ["--output-depth" as string]: depth }}>
            <div className="mc3-summary-output__row">
                <span className="mc3-summary-output__icon">{icon}</span>
                <div className="mc3-summary-output__body">
                    <strong>{node.name}</strong>
                    <span className="mc3-summary-output__meta">
                        <Badge appearance="tint" color={node.isFolder ? "informative" : "subtle"} className="mc3-summary-output__badge">{outputTypeLabel(node.type)}</Badge>
                        <Badge appearance="tint" color={outputModeBadgeColor(node.mode)} className="mc3-summary-output__mode">{outputModeLabel(node.mode)}</Badge>
                        {node.status && <Badge appearance="tint" color={changeBadgeColor(node.status)}>{changeStatusLabel(node.status)}</Badge>}
                    </span>
                    {node.summary && <p>{visibleRuntimeText(node.summary)}</p>}
                </div>
                {node.webUrl && (
                    <a href={node.webUrl} target="_blank" rel="noopener noreferrer" aria-label={`Open ${node.name}`} onClick={externalLinkOnClick(workloadClient, node.webUrl)}>
                        <Open20Regular />
                    </a>
                )}
            </div>
            {node.children.length > 0 && (
                <div className="mc3-summary-output__children">
                    {node.children.map((child) => <MissionOutputNodeRow key={child.key} node={child} workloadClient={workloadClient} depth={depth + 1} />)}
                </div>
            )}
        </article>
    );
}

function MissionSummaryPanel({ state, runtimeSlots, workloadClient }: { state: MissionState; runtimeSlots: RuntimeSlotView[]; workloadClient: WorkloadClientAPI }) {
    const changes = useMemo(() => state.changeOrder.map((id) => state.changes[id]).filter(Boolean), [state.changeOrder, state.changes]);
    const artifacts = useMemo(() => state.artifactOrder.map((id) => state.artifacts[id]).filter((artifact): artifact is Artifact => Boolean(artifact) && !isHiddenOutputType(artifact.kind)), [state.artifactOrder, state.artifacts]);
    const outputNodes = useMemo(() => buildMissionOutputTree(changes, artifacts), [changes, artifacts]);
    const outputCount = useMemo(() => countOutputNodes(outputNodes), [outputNodes]);
    const pendingApprovals = useMemo(() => Object.values(state.approvals).filter((approval) => !approval.resolved), [state.approvals]);
    const errorCount = useMemo(() => state.logs.filter((entry) => resolvedLogLevel(entry) === "error" || entry.kind === "error").length, [state.logs]);
    const warningCount = useMemo(() => state.logs.filter((entry) => resolvedLogLevel(entry) === "warn" && !/approval required/i.test(entry.message)).length, [state.logs]);
    const latestIssue = useMemo(() => [...state.logs].reverse().find((entry) => resolvedLogLevel(entry) !== "info" || entry.kind === "error"), [state.logs]);
    const lastRollup = useMemo(() => [...state.logs].reverse().find((entry) => entry.kind === "rollup"), [state.logs]);
    const activeAgent = activeAgentNameFor(state, runtimeSlots);
    const activeRuntimeCount = runtimeSlots.filter((slot) => slot.lifecycle === "running" || slot.lifecycle === "waiting" || slot.lifecycle === "spinning_up" || slot.isActive).length;
    const applied = changes.filter((change) => change.status === "applied" || change.status === "completed").length;
    const visibleChanges = changes.slice(0, 8);
    const terminal = !!state.terminalType;
    const status = missionHeaderStatus(state);
    const issueCount = errorCount + warningCount + pendingApprovals.length;
    const statusColor = status.tone === "complete" ? "success" : status.tone === "failed" || status.tone === "cancelled" ? "danger" : status.tone === "waiting" ? "warning" : "brand";
    const latestText = pendingApprovals[0]?.summary
        || latestIssue && visibleRuntimeText(latestIssue.message).slice(0, 220)
        || lastRollup && visibleRuntimeText(lastRollup.message).slice(0, 220)
        || (terminal ? "Run settled." : "Waiting for the next mission update.");
    const latestSeq = pendingApprovals[0] ? null : latestIssue?.seq ?? lastRollup?.seq ?? null;
    const attentionSeq = latestIssue?.seq ?? null;
    const attentionText = pendingApprovals.length > 0
        ? "Needs approval"
        : errorCount > 0
            ? `${errorCount} error${errorCount === 1 ? "" : "s"}`
            : warningCount > 0
                ? `${warningCount} warning${warningCount === 1 ? "" : "s"}`
                : "None";

    return (
        <aside
            className={`mc3-intel mc3-summary-rail mc3-rail mc3-summary-rail--${status.tone}`}
            aria-label="Mission intelligence"
            data-design-intent="chat-execution-summary-output-pane"
        >
            <header className="mc3-summary-rail__header">
                <span className="mc3-summary-rail__pulse" data-state={status.tone} aria-hidden="true" />
                <div>
                    <span>Summary</span>
                    <strong>{status.tone === "failed" ? "Needs attention" : terminal ? "Run settled" : "Mission running"}</strong>
                </div>
                <Badge appearance="tint" color={statusColor} className="mc3-summary-status" data-state={status.tone}>{status.statusLabel}</Badge>
            </header>
            <div className="mc3-summary-metrics" aria-label="Mission health metrics">
                <article className="mc3-summary-metric">
                    <span>Outputs</span>
                    <strong>{outputCount}</strong>
                </article>
                <article className="mc3-summary-metric">
                    <span>Changes</span>
                    <strong>{applied || changes.length}</strong>
                </article>
                <article className={`mc3-summary-metric${issueCount > 0 ? " mc3-summary-metric--attention" : ""}`}>
                    <span>Attention</span>
                    <strong>{issueCount}</strong>
                </article>
                <article className="mc3-summary-metric">
                    <span>Active</span>
                    <strong>{activeRuntimeCount || (terminal ? 0 : Math.min(runtimeSlots.length || 1, 1))}</strong>
                </article>
            </div>
            <section className="mc3-summary-section mc3-summary-section--current">
                <h2>Current</h2>
                <dl className="mc3-summary-facts">
                    <div><dt>Active</dt><dd>{visibleAgentName(activeAgent) || (terminal ? "Complete" : "System routing")}</dd></div>
                    <div><dt>Latest update</dt><dd>{latestSeq ? <Button appearance="transparent" className="mc3-summary-focus" onClick={() => focusMissionLogSeq(latestSeq)}>{latestText}</Button> : latestText}</dd></div>
                    <div><dt>Open attention</dt><dd>{attentionSeq ? <Button appearance="transparent" className="mc3-summary-focus mc3-summary-focus--attention" onClick={() => focusMissionLogSeq(attentionSeq)}>{attentionText}</Button> : attentionText}</dd></div>
                </dl>
            </section>
            <section className="mc3-summary-section" aria-label="Mission outputs and changes">
                <div className="mc3-summary-section__head">
                    <h2>Outputs</h2>
                    <span>{outputCount}</span>
                </div>
                <div className="mc3-summary-list mc3-summary-output-tree">
                    {outputNodes.length === 0 ? (
                        <p className="mc3-summary-empty">Outputs appear here as Fabric items and files are published.</p>
                    ) : outputNodes.map((node) => <MissionOutputNodeRow key={node.key} node={node} workloadClient={workloadClient} />)}
                </div>
            </section>
            <section className="mc3-summary-section">
                <div className="mc3-summary-section__head">
                    <h2>Changes</h2>
                    <span>{applied || changes.length}</span>
                </div>
                <div className="mc3-summary-list">
                    {visibleChanges.length === 0 ? (
                        <p className="mc3-summary-empty">No workspace changes have been published yet.</p>
                    ) : visibleChanges.map((change) => (
                        <article key={change.recordId} className={`mc3-intel-change mc3-summary-change mc3-summary-change--${change.kind}`}>
                            <span className="mc3-summary-change__icon">{changeIcon(change.kind, change.targetScope)}</span>
                            <div>
                                <div className="mc3-summary-change__meta">
                                    <Badge appearance="tint" color="subtle">{visibleAgentName(change.agentName || change.agentId) || "runtime"}</Badge>
                                    <Badge appearance="tint" color={changeBadgeColor(change.status)}>{changeStatusLabel(change.status)}</Badge>
                                </div>
                                <strong>{change.targetName}</strong>
                                <p>{visibleRuntimeText(change.summary)}</p>
                            </div>
                            {change.webUrl && (
                                <a href={change.webUrl} target="_blank" rel="noopener noreferrer" aria-label={`Open ${change.targetName}`} onClick={externalLinkOnClick(workloadClient, change.webUrl)}>
                                    <Open20Regular />
                                </a>
                            )}
                        </article>
                    ))}
                </div>
            </section>
            {pendingApprovals.length > 0 && (
                <section className="mc3-summary-section mc3-summary-section--approval">
                    <h2>Waiting For You</h2>
                    {pendingApprovals.map((approval) => (
                        <article key={approval.approvalId} className="mc3-summary-approval">
                            <Badge appearance="tint" color="warning" className="mc3-summary-approval__badge">Approval required</Badge>
                            <p>{approval.summary}</p>
                        </article>
                    ))}
                </section>
            )}
        </aside>
    );
}

function MissionIntelligencePanel({ state, runtimeSlots, workloadClient }: { state: MissionState; runtimeSlots: RuntimeSlotView[]; workloadClient: WorkloadClientAPI }) {
    const changes = useMemo(
        () => state.changeOrder.map((id) => state.changes[id]).filter(Boolean),
        [state.changeOrder, state.changes],
    );
    const artifacts = useMemo(
        () => state.artifactOrder.map((id) => state.artifacts[id]).filter((artifact): artifact is Artifact => Boolean(artifact) && !isHiddenOutputType(artifact.kind)),
        [state.artifactOrder, state.artifacts],
    );
    const pendingApprovals = useMemo(
        () => Object.values(state.approvals).filter((approval) => !approval.resolved),
        [state.approvals],
    );
    const errorCount = useMemo(
        () => state.logs.filter((entry) => resolvedLogLevel(entry) === "error" || entry.kind === "error").length,
        [state.logs],
    );
    const warningCount = useMemo(
        () => state.logs.filter((entry) => resolvedLogLevel(entry) === "warn" && !/approval required/i.test(entry.message)).length,
        [state.logs],
    );
    const lastRollup = useMemo(
        () => [...state.logs].reverse().find((entry) => entry.kind === "rollup"),
        [state.logs],
    );
    const latestIssue = useMemo(
        () => [...state.logs].reverse().find((entry) => resolvedLogLevel(entry) !== "info" || entry.kind === "error"),
        [state.logs],
    );
    const rows = changes.slice(0, 5);
    const applied = changes.filter((change) => change.status === "applied" || change.status === "completed").length || changes.length;
    const activeAgent = activeAgentNameFor(state, runtimeSlots);
    const terminalLatest = state.terminalType === "job_failed"
        ? (latestFailureMessage(state) || "Run failed before public evidence was published.")
        : state.terminalType === "job_cancelled"
            ? "Run cancelled."
            : state.terminalType
                ? "Run completed."
                : "Waiting for the first mission update.";
    const attentionText = pendingApprovals[0]?.summary || (latestIssue ? visibleRuntimeText(latestIssue.message).slice(0, 180) : "No open issues");
    const panelTone = pendingApprovals.length > 0 || latestIssue
        ? "attention"
        : state.terminalType === "job_failed"
            ? "failed"
            : state.terminalType
                ? "settled"
                : "live";
    const headline = pendingApprovals.length > 0
        ? "Decision needed"
        : state.terminalType
            ? "Run settled"
            : "Live execution";
    const badge = pendingApprovals.length > 0
        ? "approval needed"
        : `${applied} applied`;

    return (
        <aside
            className={`mc3-intel mc3-rail mc3-intel--${panelTone}`}
            aria-label="Mission intelligence"
            data-design-intent="agentic-run-intelligence"
            data-design-palette="fabric-blue-cyan-purple-magenta-amber"
        >
            <header className="mc3-intel__header">
                <div>
                    <span className="mc3-intel__eyebrow">Run intelligence</span>
                    <strong>{headline}</strong>
                </div>
                <span className="mc3-intel__badge">{badge}</span>
            </header>
            <div className="mc3-intel__signals" aria-label="Mission signals">
                <article className="mc3-intel-signal mc3-intel-signal--latest">
                    <span>Latest update</span>
                    <strong>{lastRollup ? visibleRuntimeText(lastRollup.message).slice(0, 180) : terminalLatest}</strong>
                </article>
                <article className="mc3-intel-signal mc3-intel-signal--agent">
                    <span>Active lane</span>
                    <strong>{visibleAgentName(activeAgent) || (state.terminalType ? "Complete" : "System routing")}</strong>
                </article>
                <article className="mc3-intel-signal mc3-intel-signal--attention">
                    <span>{pendingApprovals[0] ? "Needs approval" : latestIssue ? "Needs review" : "Attention"}</span>
                    <strong>{attentionText}</strong>
                </article>
            </div>
            <section className="mc3-intel__changes" aria-label="Mission outputs and changes">
                <div className="mc3-intel__section-head">
                    <span>Outputs and changes</span>
                    <strong>{changes.length}</strong>
                </div>
                {rows.length === 0 ? (
                    <p className="mc3-intel__empty">No published workspace changes yet.</p>
                ) : rows.map((change) => (
                    <article key={change.recordId} className={`mc3-intel-change mc3-intel-change--${String(change.status || "tracked").toLowerCase()}`}>
                        <span className="mc3-intel-change__icon">{changeIcon(change.kind, change.targetScope)}</span>
                        <div>
                            <div className="mc3-intel-change__meta">
                                <span>{visibleAgentName(change.agentName || change.agentId) || "runtime"}</span>
                                <span>{changeStatusLabel(change.status)}</span>
                            </div>
                            <strong>{change.targetName}</strong>
                            <small>{visibleRuntimeText(change.summary)}</small>
                        </div>
                        {change.webUrl && (
                            <a
                                href={change.webUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="mc3-intel-change__link"
                                aria-label={`Open ${change.targetName}`}
                                onClick={externalLinkOnClick(workloadClient, change.webUrl)}
                            >
                                <Open20Regular />
                            </a>
                        )}
                    </article>
                ))}
            </section>
            {artifacts.length > 0 && (
                <section className="dmc-live__outputs" aria-label="Mission output inventory">
                    {artifacts.map((artifact) => (
                        <Card key={artifact.artifactId} className="ledger-row ledger-row--created">
                            <span className="ledger-row__fluent-icon">{changeIcon("created", artifact.kind)}</span>
                            <div className="ledger-row__body">
                                <div className="ledger-row__meta">
                                    <Badge appearance="outline" color="subtle" className="ledger-agent">{visibleAgentName(artifact.agentId) || "runtime"}</Badge>
                                    <Badge appearance="tint" color={artifact.state === "written" ? "success" : "warning"} className={`ledger-status ledger-status--${artifact.state}`}>{artifact.state === "written" ? "Written" : "Draft"}</Badge>
                                </div>
                                <strong>{artifact.name}</strong>
                                <small>{visibleRuntimeText(artifact.summary)}</small>
                            </div>
                            {artifact.webUrl && (
                                <a
                                    href={artifact.webUrl}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="mc3-change-card__link"
                                    aria-label={`Open ${artifact.name}`}
                                    onClick={externalLinkOnClick(workloadClient, artifact.webUrl)}
                                >
                                    <Open20Regular />
                                </a>
                            )}
                        </Card>
                    ))}
                </section>
            )}
        </aside>
    );
}

function MissionSteeringComposer({
    sessionId,
    githubToken,
    fabricToken,
    state,
    runtimeSlots,
    terminal,
}: {
    sessionId: string;
    githubToken: string;
    fabricToken?: string;
    state: MissionState;
    runtimeSlots: RuntimeSlotView[];
    terminal: boolean;
}) {
    const activeTargets = useMemo(
        () => runtimeSlots.filter((slot) => ["running", "waiting", "spinning_up"].includes(slot.lifecycle)),
        [runtimeSlots],
    );
    const preferredTarget = activeTargets.length === 1 ? activeTargets[0].slotId : "broadcast";
    const [message, setMessage] = useState("");
    const [target, setTarget] = useState(preferredTarget);
    const [busyMode, setBusyMode] = useState<"queue" | "interrupt" | null>(null);
    const [status, setStatus] = useState<string | null>(null);
    const targetSlot = runtimeSlots.find((slot) => slot.slotId === target);
    const targetLabel = target === "broadcast"
        ? `All active agents${activeTargets.length > 0 ? ` (${activeTargets.length})` : ""}`
        : targetSlot ? readableSlotLabel(targetSlot) : "Selected agent";
    const pendingSteering = useMemo(() => {
        const latestById = new Map<string, LogEntry>();
        for (const entry of state.logs) {
            if (entry.kind !== "steering" || !entry.steeringId) continue;
            latestById.set(entry.steeringId, entry);
        }
        return [...latestById.values()]
            .filter((entry) => /queued|requested|deferred/i.test(entry.message) && !/delivered|failed|broadcast/i.test(entry.message))
            .slice(-3)
            .reverse();
    }, [state.logs]);

    useEffect(() => {
        const validTargets = new Set(["broadcast", ...runtimeSlots.map((slot) => slot.slotId)]);
        if (!validTargets.has(target)) setTarget(preferredTarget);
    }, [preferredTarget, runtimeSlots, target]);

    const submit = useCallback(async (mode: "queue" | "interrupt") => {
        const trimmed = message.trim();
        if (!trimmed || busyMode || terminal) return;
        setBusyMode(mode);
        setStatus(mode === "interrupt" ? "Interrupt request queued…" : "Directive queued…");
        try {
            const res = await api.sendMessage(
                sessionId,
                trimmed,
                target === "broadcast" ? null : target,
                { githubToken, fabricToken, agentHubSessionId: sessionId },
                mode,
            );
            setMessage("");
            const count = res.targetCount ?? res.targetAgentSessionIds?.length ?? 1;
            setStatus(`${mode === "interrupt" ? "Interrupt" : "Directive"} accepted · ${count} target${count === 1 ? "" : "s"}`);
        } catch (err) {
            setStatus(err instanceof Error ? err.message.slice(0, 240) : "Unable to send directive");
        } finally {
            setBusyMode(null);
        }
    }, [busyMode, fabricToken, githubToken, message, sessionId, target, terminal]);

    return (
        <section className={`mc3-steering${terminal ? " mc3-steering--terminal" : ""}`} aria-label="Mission steering" data-design-intent="agentic-steering-composer">
            {pendingSteering.length > 0 && (
                <div className="mc3-steering__queue" aria-label="Queued steering messages">
                    <Badge appearance="tint" color="warning" className="mc3-steering__queue-count">{pendingSteering.length} queued</Badge>
                    {pendingSteering.map((entry) => (
                        <Tooltip key={entry.steeringId} content={visibleRuntimeText(entry.message)} relationship="label">
                            <Button
                                appearance="subtle"
                                size="small"
                                className="mc3-steering-queued"
                                onClick={() => focusMissionLogSeq(entry.seq)}
                                icon={<span className={`mc3-steering-queued__dot mc3-steering-queued__dot--${entry.deliveryMode === "interrupt" ? "interrupt" : "queue"}`} />}
                            >
                                <span>{visibleRuntimeText(entry.message.replace(/^.*?:\s*/, "")).slice(0, 120)}</span>
                            </Button>
                        </Tooltip>
                    ))}
                </div>
            )}
            <div className="mc3-steering__composer">
                <Textarea
                    id="mc3-steering-message"
                    className="mc3-steering__input"
                    aria-label="Steer mission"
                    value={message}
                    onChange={(_, data) => setMessage(data.value)}
                    onKeyDown={(event) => {
                        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                            event.preventDefault();
                            void submit("queue");
                        }
                    }}
                    disabled={terminal}
                    resize="vertical"
                    textarea={{ rows: 2, maxLength: 8000 }}
                    maxLength={8000}
                    placeholder={terminal ? "Run is complete" : "Message the active agents"}
                />
                <div className="mc3-steering__dock">
                    <div className="mc3-steering__target-wrap">
                        <Target20Regular />
                        <Dropdown
                            id="mc3-steering-target"
                            className="mc3-steering__target"
                            value={targetLabel}
                            selectedOptions={[target]}
                            onOptionSelect={(_, data) => data.optionValue && setTarget(data.optionValue)}
                            disabled={terminal}
                            aria-label="Message target"
                            size="small"
                        >
                            <Option value="broadcast" text="All active agents">All active agents</Option>
                            {runtimeSlots.map((slot) => (
                                <Option key={slot.slotId} value={slot.slotId} text={readableSlotLabel(slot)}>{readableSlotLabel(slot)}</Option>
                            ))}
                        </Dropdown>
                    </div>
                    {status && <div className="mc3-steering__status" role="status">{status}</div>}
                    <div className="mc3-steering__actions">
                        <Tooltip content="Interrupt active work" relationship="label">
                        <Button
                            appearance="secondary"
                            size="small"
                            className="mc3-steering__btn mc3-steering__btn--interrupt"
                            disabled={!message.trim() || !!busyMode || terminal}
                            onClick={() => void submit("interrupt")}
                            aria-label="Interrupt active work"
                            icon={busyMode === "interrupt" ? <Spinner size="tiny" /> : <Stop20Regular />}
                        >
                            <span>Interrupt</span>
                        </Button>
                        </Tooltip>
                        <Tooltip content="Send message" relationship="label">
                        <Button
                            appearance="primary"
                            size="small"
                            className="mc3-steering__btn mc3-steering__btn--primary"
                            disabled={!message.trim() || !!busyMode || terminal}
                            onClick={() => void submit("queue")}
                            aria-label="Send message"
                            icon={busyMode === "queue" ? <Spinner size="tiny" /> : <Send20Regular />}
                        >
                            <span>Send</span>
                        </Button>
                        </Tooltip>
                    </div>
                </div>
            </div>
        </section>
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

    const { state, error: streamError, isConnected, reconnectCount } = useMissionStream(sessionId, {
        getSessionOpts: { githubToken, fabricToken },
        initialJob,
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
                    runtime: j.runtime ?? null,
                    started_at: j.started_at ?? null, status: j.status,
                    context: j.context ?? null,
                    composition: j.composition ?? null,
                });
            } catch { /* best-effort */ }
        })();
        return () => { c = true; };
    }, [initialJob, sessionId, fabricToken, githubToken]);
    const job = initialJob ?? fetchedJob;

    const runtimeSlots = useMemo(
        () => buildRuntimeSlotViews(state),
        [state.composition, state.slotProgress, state.agentStatus, state.activeAgentId, state.approvals, state.logs.length, state.jobStatus],
    );
    const terminal = !!state.terminalType;
    const streamStatus = terminal
        ? null
        : streamError || (isConnected ? null : reconnectCount > 0 ? "Reconnecting event stream; latest session status remains visible." : null);
    const headerStatus = missionHeaderStatus(state);
    const statusLabel = headerStatus.statusLabel;
    const pendingApproval = hasPendingApproval(state);
    const connectionLabel = terminal
        ? headerStatus.connectionLabel
        : pendingApproval
            ? headerStatus.connectionLabel
        : isConnected
            ? "streaming"
            : streamError
                ? "quiet"
                : reconnectCount > 0
                    ? "reconnecting"
                    : headerStatus.connectionLabel;
    const comp: any = state.composition;
    const patternLabel = comp?.architecture || "dynamic";
    const visibleCompositionSlotCount = Array.isArray(comp?.slots)
        ? comp.slots.filter((slot: any) => !isInternalRuntimeSlot({
            id: String(slot.id || ""),
            agentId: String(slot.agentId || ""),
            role: String(slot.role || ""),
        })).length
        : 0;
    const visibleAgentTotal = Math.max(visibleCompositionSlotCount, runtimeSlots.length);
    const missionBrief = missionSummaryLine(job, state);
    const connectionState = headerStatus.connectionLabel === "complete" || terminal
        ? headerStatus.connectionLabel
        : connectionLabel;
    const jobContext = (job?.context || {}) as Record<string, any>;
    const nativePiSession = isNativePiSurfaceRequested(job, jobContext);

    if (nativePiSession) {
        return (
            <div
                className={`mc3 mc3-pi-web-ui-page mc3-pi-web-ui-page--${headerStatus.tone}${terminal ? " mc3--terminal" : ""}`}
                data-design-intent="native-pi-web-ui-mission-page"
                data-mission-status={statusLabel}
                data-stream-status={connectionLabel}
                data-mission-summary={missionBrief}
            >
                <MissionPiSurface
                    state={state}
                    runtimeSlots={runtimeSlots}
                    streamStatus={streamStatus}
                    sessionId={sessionId}
                    githubToken={githubToken}
                    fabricToken={fabricToken}
                    workloadClient={workloadClient}
                    variant="web-ui"
                />
            </div>
        );
    }

    return (
        <div
            className={`mc3 mc3-dmc-live mc3-dmc-live--pi-augmented${terminal ? " mc3--terminal" : ""}`}
            data-design-intent="agenthub-mission-page-with-pi-runtime"
            data-mission-status={statusLabel}
            data-stream-status={connectionLabel}
            data-mission-summary={missionBrief}
        >
            <section className="mc3-terminal-shell mc3-chat-shell" role="region" aria-label="Mission execution" data-design-intent="chat-first-mission-execution">
                <div className="mission-grid mission-grid--right mc3-dmc-grid mc3-chat-layout">
                    <main className="mc3-chat-main" aria-label="Mission logs">
                        <MissionChatHistory
                            job={job}
                            state={state}
                            runtimeSlots={runtimeSlots}
                            streamStatus={streamStatus}
                            connectionLabel={connectionLabel}
                            connectionState={connectionState}
                            connected={isConnected && !terminal}
                            patternLabel={patternLabel}
                            visibleAgentTotal={visibleAgentTotal}
                            sessionId={sessionId}
                            githubToken={githubToken}
                            fabricToken={fabricToken}
                        />
                    </main>
                    <MissionSummaryPanel
                        state={state}
                        runtimeSlots={runtimeSlots}
                        workloadClient={workloadClient}
                    />
                </div>
            </section>
        </div>
    );
}

// ════════════════════════════════════════════════════════════════════
// LIVE LOG
// ════════════════════════════════════════════════════════════════════

function LiveLog({ state, logFocusMode, onToggleLogFocus, onApproval, approvalBusy, mode, onModeChange }: {
    state: MissionState;
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
    const logs = useMemo(() => state.logs, [state.logs]);
    const pending = Object.values(state.approvals).filter((a) => !a.resolved);

    // Determine which entries are "major" (get a step-connector dot)
    // vs "minor" (tool calls, detail lines — rendered inline)
    const entries = useMemo(() => {
        const MAJOR_KINDS = new Set(["phase", "decision", "log", "action", "error"]);
        const result: Array<{ entry: LogEntry; children: LogEntry[]; major: boolean }> = [];
        for (const e of logs) {
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
    }, [logs, mode]);

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
                        {logs.length} shown · {state.logs.length} total{state.totalDuration ? ` · ${state.totalDuration}` : ""}
                    </span>
                )}
                <div className="mc3-log__bar-spacer" />
                <div className="mc3-log__bar-actions">
                    <div className="mc3-inline-toggle" role="tablist" aria-label="Live log density">
                        <Button
                            appearance="transparent"
                            role="tab"
                            className={`mc3-inline-toggle__btn${mode === "overview" ? " is-active" : ""}`}
                            aria-selected={mode === "overview"}
                            onClick={() => onModeChange("overview")}
                        >
                            Condensed
                        </Button>
                        <Button
                            appearance="transparent"
                            role="tab"
                            className={`mc3-inline-toggle__btn${mode === "detail" ? " is-active" : ""}`}
                            aria-selected={mode === "detail"}
                            onClick={() => onModeChange("detail")}
                        >
                            Expanded
                        </Button>
                    </div>
                    <Button
                        appearance="transparent"
                        className={`mc3-log__bar-action${followStream ? " is-active" : ""}`}
                        title={followStream ? "Following latest output" : "Resume following stream"}
                        onClick={handleJumpToLatest}
                    >
                        {followStream ? "Following" : "Follow"}
                    </Button>
                    <div className="mc3-log-layout-toggle" role="tablist" aria-label="Log layout">
                        <Button
                            appearance="transparent"
                            role="tab"
                            className={`mc3-log-layout-toggle__btn${!logFocusMode ? " is-active" : ""}`}
                            aria-selected={!logFocusMode}
                            onClick={() => logFocusMode && onToggleLogFocus()}
                        >
                            Standard
                        </Button>
                        <Button
                            appearance="transparent"
                            role="tab"
                            className={`mc3-log-layout-toggle__btn${logFocusMode ? " is-active" : ""}`}
                            aria-selected={logFocusMode}
                            onClick={() => !logFocusMode && onToggleLogFocus()}
                        >
                            Expanded log
                        </Button>
                    </div>
                    <Button appearance="transparent" className="mc3-log__bar-action" title="Copy log" onClick={handleCopyLog}>
                        <Copy20Regular /> Copy
                    </Button>
                </div>
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
                        Waiting for the first agent to come online…
                    </p>
                )}
                {!followStream && logs.length > 0 && (
                    <div className="mc3-log__tail">
                        <span className="mc3-log__tail-note">New output available</span>
                        <Button appearance="transparent" className="mc3-log__tail-btn" onClick={handleJumpToLatest}>
                            Jump to latest
                        </Button>
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
    const latencyBreakdown = formatLatencyBreakdownMs(end?.latencyBreakdownMs);
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
                <Button appearance="transparent" size="small" className="mc3-tool-block__copy" onClick={copyCommand} title="Copy command">
                    Copy
                </Button>
            </div>
            <div className="mc3-tool-block__cmd">$ {command}</div>
            {argsSummary && <div className="mc3-tool-block__meta">args: {argsSummary}</div>}
            {latencyBreakdown && <div className="mc3-tool-block__meta">latency: {latencyBreakdown}</div>}
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

    const artifacts = state.artifactOrder.map((id) => state.artifacts[id]).filter((artifact): artifact is Artifact => Boolean(artifact) && !isHiddenOutputType(artifact.kind));
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
                <Button
                    appearance="transparent"
                    role="tab"
                    className={`mc3-rail__tab${tab === "overview" ? " is-active" : ""}`}
                    aria-selected={tab === "overview"}
                    onClick={() => onTabChange("overview")}
                >
                    Overview
                </Button>
                <Button
                    appearance="transparent"
                    role="tab"
                    className={`mc3-rail__tab${tab === "diagnostics" ? " is-active" : ""}`}
                    aria-selected={tab === "diagnostics"}
                    onClick={() => onTabChange("diagnostics")}
                >
                    Diagnostics
                    {issuesCount > 0 && <span className="mc3-rail__tab-count">{issuesCount}</span>}
                </Button>
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

const legacyMissionSurfaceReferences = [
    TeamPanel,
    applyRuntimeTeam,
    CanvasLogStream,
    DynamicMissionCanvas,
    MissionOutcomeBanner,
    ChangeLedgerRail,
    MissionIntelligencePanel,
    LiveLog,
    RunOverviewRail,
    DiagnosticsPanel,
];
void legacyMissionSurfaceReferences;
