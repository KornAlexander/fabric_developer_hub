import type { LogEntry, MissionState } from "./missionReducer";
import { formatDurationMs, formatOperationalMessage, formatToolName } from "./logPresentation";

export type ExecutionRowState = "running" | "done" | "queued" | "warn" | "error";
export type ExecutionProgressMode = "requesting" | "responding" | "thinking" | "tool-input" | "tool-use" | "idle";
export type ExecutionSemanticClass = "preparing" | "search-read" | "thinking" | "tool" | "waiting" | "completed" | "attention";

export interface ExecutionProgress {
    mode: ExecutionProgressMode;
    semanticClass: ExecutionSemanticClass;
    spinnerMessage: string;
    statusText: string;
    lastToolInfo?: string | null;
    toolUseCount: number;
    tokenCount: number | null;
    isResolved: boolean;
    isBackgrounded: boolean;
    isError: boolean;
    isIdle: boolean;
}

export interface ExecutionActivity {
    seq: number;
    ts: string;
    text: string;
    badge: string;
    current: boolean;
    muted: boolean;
    category: "search" | "read" | "write" | "validation" | "diagnostic" | "steering" | "message" | "tool";
}

export interface ExecutionTranscriptRow {
    key: string;
    entry: LogEntry;
    children: LogEntry[];
    hiddenCount: number;
    state: ExecutionRowState;
    headline: string;
    currentDetail?: string;
    activities: ExecutionActivity[];
    meta: string[];
    progress: ExecutionProgress;
    streamText?: string;
    streamKind?: LogEntry["streamKind"];
    isTextStream?: boolean;
    isLive: boolean;
    isAttention: boolean;
    isReceipt?: boolean;
}

const MAX_VISIBLE_LOGS = 192;

function text(value: unknown, max = 180): string {
    const out = String(value || "").replace(/\s+/g, " ").trim();
    if (!out) return "";
    return out.length > max ? `${out.slice(0, max - 1)}...` : out;
}

function logLevel(entry: Pick<LogEntry, "level" | "rollupStatus">): ExecutionRowState {
    if (entry.level === "error" || entry.rollupStatus === "failed") return "error";
    if (entry.level === "warn") return "warn";
    return "done";
}

function isSpecialRow(entry: LogEntry): boolean {
    return entry.kind === "rollup" || entry.kind === "steering" || entry.kind === "diagnostic";
}

function isStreamEntry(entry: LogEntry): boolean {
    return entry.streamKind === "assistant" || entry.streamKind === "thinking";
}

function isAttention(entry: LogEntry): boolean {
    if (entry.kind === "diagnostic" && entry.level !== "info") return true;
    return /approval|required|blocked|denial|fallback|issue|failed|error/i.test(entry.message);
}

function isCritical(entry: LogEntry): boolean {
    return entry.kind === "steering" || isAttention(entry) || entry.level === "warn" || entry.level === "error";
}

function operationText(entry: LogEntry): string {
    const op = String(entry.operationKind || "").replace(/_/g, " ").trim();
    if (op) return op;
    const tool = String(entry.toolName || "").toLowerCase();
    if (/grep|search|query|find|scan/.test(tool)) return "search";
    if (/read|get|fetch|load|inspect|list/.test(tool)) return "read";
    if (/create|write|update|edit|delete|deploy|apply|publish/.test(tool)) return "write";
    if (/validat|verify|test|check/.test(tool)) return "validation";
    return "work";
}

function categoryFor(entry: LogEntry): ExecutionActivity["category"] {
    const operation = operationText(entry);
    if (/search|query|scan|find/.test(operation)) return "search";
    if (/read|list|load|inspect/.test(operation)) return "read";
    if (/create|write|update|edit|delete|deploy|apply|publish/.test(operation)) return "write";
    if (/validat|verify|test|check/.test(operation)) return "validation";
    if (entry.kind === "diagnostic") return "diagnostic";
    if (entry.kind === "steering") return "steering";
    if (entry.kind === "tool_start" || entry.kind === "tool_end" || entry.toolName) return "tool";
    return "message";
}

function activityText(entry: LogEntry): string {
    if (isStreamEntry(entry)) return text(entry.streamText || entry.message, 180) || (entry.streamKind === "thinking" ? "Thinking" : "Assistant response");
    if (entry.kind === "tool_start") return text(entry.message, 130) || `Starting ${formatToolName(entry.toolName)}`;
    if (entry.kind === "tool_end") return `${entry.toolStatus === "error" ? "Failed" : "Finished"} ${formatToolName(entry.toolName)}`;
    if (entry.toolName && entry.kind === "log") return `${formatToolName(entry.toolName)} · ${text(entry.message, 130)}`;
    if (entry.kind === "diagnostic") return text(entry.message, 130) || "Checking diagnostics";
    if (entry.kind === "steering") return text(entry.message, 130) || "Steering update";
    return text(entry.message, 130) || "Working";
}

function activityBadge(entry: LogEntry): string {
    if (isStreamEntry(entry)) return entry.streamKind === "thinking" ? "Think" : "Text";
    if (entry.kind === "tool_end") return entry.toolStatus === "error" ? "Fail" : "Done";
    if (entry.kind === "action") return "Done";
    const category = categoryFor(entry);
    if (category === "search") return "Search";
    if (category === "read") return "Read";
    if (category === "write") return /delete/i.test(entry.message) ? "Delete" : /edit|write|update/i.test(entry.message) ? "Edit" : "Create";
    if (category === "validation") return "Check";
    if (category === "diagnostic") return "Diag";
    if (category === "steering") return "Note";
    if (category === "tool") return "Call";
    return "Info";
}

function semanticClassFor(entry: LogEntry | undefined, terminal: boolean): ExecutionSemanticClass {
    if (terminal) return "completed";
    if (!entry) return "preparing";
    if (entry.progressSemanticClass) return entry.progressSemanticClass;
    if (isStreamEntry(entry)) return "thinking";
    if (entry.kind === "steering" || /queued|waiting|approval|deferred/i.test(entry.message)) return "waiting";
    if (entry.kind === "diagnostic" || isAttention(entry)) return "attention";
    if (entry.kind === "decision" || /think|plan|review|decid|reason/i.test(entry.message)) return "thinking";
    const category = categoryFor(entry);
    if (category === "search" || category === "read") return "search-read";
    if (category === "write" || category === "validation" || category === "tool") return "tool";
    if (entry.kind === "phase" || /start|prepar|initial/i.test(entry.message)) return "preparing";
    return "thinking";
}

function spinnerModeFor(entry: LogEntry | undefined, semanticClass: ExecutionSemanticClass): ExecutionProgressMode {
    if (!entry) return "responding";
    if (entry.progressMode) return entry.progressMode;
    if (isStreamEntry(entry)) return entry.streamKind === "thinking" ? "thinking" : "responding";
    if (entry.kind === "tool_start" || entry.toolName) return "tool-use";
    if (semanticClass === "thinking") return "thinking";
    if (semanticClass === "waiting") return "requesting";
    return "responding";
}

function getSearchReadSummaryText(searchCount: number, readCount: number, active: boolean): string {
    const parts: string[] = [];
    if (searchCount > 0) {
        const verb = active ? (parts.length === 0 ? "Searching for" : "searching for") : (parts.length === 0 ? "Searched for" : "searched for");
        parts.push(`${verb} ${searchCount} ${searchCount === 1 ? "pattern" : "patterns"}`);
    }
    if (readCount > 0) {
        const verb = active ? (parts.length === 0 ? "Reading" : "reading") : (parts.length === 0 ? "Read" : "read");
        parts.push(`${verb} ${readCount} ${readCount === 1 ? "file" : "files"}`);
    }
    const summary = parts.join(", ");
    return active ? `${summary}...` : summary;
}

function summarizeClaudeRecentActivities(activities: ExecutionActivity[]): string | undefined {
    if (activities.length === 0) return undefined;
    let searchCount = 0;
    let readCount = 0;
    for (let i = activities.length - 1; i >= 0; i -= 1) {
        const activity = activities[i]!;
        if (activity.category === "search") searchCount += 1;
        else if (activity.category === "read") readCount += 1;
        else break;
    }
    if (searchCount + readCount >= 2) return getSearchReadSummaryText(searchCount, readCount, true);
    for (let i = activities.length - 1; i >= 0; i -= 1) {
        const description = text(activities[i]?.text, 160);
        if (description) return description;
    }
    return undefined;
}

function activeHeadline(entry: LogEntry | undefined, activities: ExecutionActivity[], semanticClass: ExecutionSemanticClass): string {
    if (!entry) return "Opening event channel...";
    if (isStreamEntry(entry)) return entry.streamKind === "thinking" ? "Thinking" : "Assistant response";
    const trailingSummary = summarizeClaudeRecentActivities(activities);
    if (trailingSummary) return trailingSummary;
    const detail = activityText(entry);
    if (semanticClass === "tool" && entry.toolName) return detail || formatToolName(entry.toolName);
    return detail;
}

function completedHeadline(entries: LogEntry[]): string {
    if (entries.length === 0) return "Completed activity";
    if (entries.length === 1 && isStreamEntry(entries[0]!)) {
        return entries[0]!.streamKind === "thinking" ? "Thinking complete" : "Assistant response";
    }
    const meaningful = entries
        .map((entry) => text(formatOperationalMessage(entry.message), 220))
        .filter((message) => message && !/^(working|done|completed|starting|initializing|activity|event \d+)$/i.test(message));
    const last = meaningful[meaningful.length - 1];
    if (!last) return text(entries[entries.length - 1]!.message, 220) || "Activity updated";
    if (entries.length === 1) return last;
    if (/\b(successfully|succeeded|failed|rejected|approved|completed|ready|created|updated|verified|proof|evidence)\b/i.test(last)) return last;

    const important = [...entries].reverse().find((entry) => {
        const category = categoryFor(entry);
        return entry.level !== "info" || category === "write" || category === "validation" || entry.kind === "tool_end" || entry.kind === "action";
    });
    if (important) {
        const importantText = text(formatOperationalMessage(important.message), 220);
        if (importantText) return importantText;
    }

    const first = meaningful[0];
    if (first && first !== last && `${first} to ${last}`.length <= 220) return `${first} to ${last}`;
    return last;
}

function activityList(entries: LogEntry[], currentSeq?: number): ExecutionActivity[] {
    return entries.map((entry, index) => ({
        seq: entry.seq,
        ts: entry.ts,
        text: activityText(entry),
        badge: activityBadge(entry),
        current: currentSeq != null ? entry.seq === currentSeq : index === entries.length - 1,
        muted: currentSeq != null ? entry.seq !== currentSeq : index !== entries.length - 1,
        category: categoryFor(entry),
    }));
}

function rollupChildren(entry: LogEntry, logs: LogEntry[]): LogEntry[] {
    const start = Number(entry.coveredSeqStart ?? -1);
    const end = Number(entry.coveredSeqEnd ?? -1);
    if (start < 0 || end < 0) return [];
    return logs.filter((candidate) => candidate.seq >= start && candidate.seq <= end && candidate.seq !== entry.seq && candidate.kind !== "rollup");
}

function metaFor(entry: LogEntry, _hiddenCount: number): string[] {
    const parts: string[] = [];
    if (entry.durationMs != null) parts.push(formatDurationMs(entry.durationMs));
    return parts;
}

function countToolUses(entries: LogEntry[]): number {
    const callIds = new Set<string>();
    let anonymousToolUses = 0;
    for (const entry of entries) {
        if (!(entry.kind === "tool_start" || entry.kind === "tool_end" || entry.toolName)) continue;
        if (entry.callId) callIds.add(entry.callId);
        else if (entry.kind === "tool_start" || entry.toolName) anonymousToolUses += 1;
    }
    return callIds.size + anonymousToolUses;
}

function tokenCountFor(entry: LogEntry): number | null {
    const candidates = [
        entry.counts?.tokens,
        entry.counts?.tokenCount,
        entry.payloadSummary?.tokens,
        entry.payloadSummary?.tokenCount,
    ];
    for (const candidate of candidates) {
        if (typeof candidate === "number" && Number.isFinite(candidate)) return candidate;
    }
    return null;
}

function makeStreamRow(entry: LogEntry, isLive: boolean): ExecutionTranscriptRow {
    const state: ExecutionRowState = entry.level === "error" ? "error" : entry.level === "warn" ? "warn" : isLive ? "running" : "done";
    const activities = activityList([entry], entry.seq);
    const progress = buildProgress(entry, [entry], activities, !isLive);
    return {
        key: `${entry.streamId || entry.streamKind || "stream"}-${entry.seq}`,
        entry,
        children: [],
        hiddenCount: 0,
        state,
        headline: entry.streamKind === "thinking" ? "Thinking" : "Assistant response",
        activities: [],
        meta: metaFor(entry, 0),
        progress,
        streamText: entry.streamText || entry.message,
        streamKind: entry.streamKind,
        isTextStream: true,
        isLive,
        isAttention: state === "error" || state === "warn",
    };
}

function taskDescriptionFor(entry: LogEntry): string | undefined {
    const raw = entry.payloadSummary?.taskDescription
        || entry.payloadSummary?.role
        || entry.payloadSummary?.agentRole;
    const cleaned = text(raw, 120);
    return cleaned || undefined;
}

function withClaudeEllipsis(value: string): string {
    const cleaned = value.trim();
    if (!cleaned) return "Working…";
    if (cleaned.endsWith("…") || cleaned.endsWith("...")) return cleaned.replace(/\.\.\.$/, "…");
    return `${cleaned}…`;
}

function spinnerMessageFor(entry: LogEntry | undefined, semanticClass: ExecutionSemanticClass): string {
    if (entry) {
        const taskDescription = taskDescriptionFor(entry);
        if (taskDescription) return withClaudeEllipsis(taskDescription);
    }
    if (semanticClass === "thinking") return "Thinking…";
    if (semanticClass === "waiting") return "Waiting…";
    return "Working…";
}

function statusTextFor(isResolved: boolean, isBackgrounded: boolean, lastToolInfo: string | undefined, taskDescription?: string): string {
    if (!isResolved) return lastToolInfo || "Waiting for the next public agent event";
    if (isBackgrounded) return taskDescription ?? "Running in the background";
    return "Done";
}

function buildProgress(entry: LogEntry | undefined, entries: LogEntry[], activities: ExecutionActivity[], isResolved: boolean): ExecutionProgress {
    const semanticClass = semanticClassFor(entry, isResolved);
    const lastToolInfo = !isResolved ? summarizeClaudeRecentActivities(activities) : undefined;
    const taskDescription = entry ? taskDescriptionFor(entry) : undefined;
    const isBackgrounded = false;
    const isError = entry?.level === "error" || semanticClass === "attention";
    return {
        mode: isResolved ? "idle" : spinnerModeFor(entry, semanticClass),
        semanticClass,
        spinnerMessage: spinnerMessageFor(entry, semanticClass),
        statusText: statusTextFor(isResolved, isBackgrounded, lastToolInfo, taskDescription),
        lastToolInfo,
        toolUseCount: countToolUses(entries),
        tokenCount: entry ? tokenCountFor(entry) : null,
        isResolved,
        isBackgrounded,
        isError,
        isIdle: isResolved,
    };
}

function makeCompletedRow(entries: LogEntry[]): ExecutionTranscriptRow | null {
    if (entries.length === 0) return null;
    if (entries.length === 1 && isStreamEntry(entries[0]!)) return makeStreamRow(entries[0]!, false);
    const entry = entries[entries.length - 1]!;
    const state = entries.some((candidate) => candidate.level === "error") ? "error" : entries.some((candidate) => candidate.level === "warn") ? "warn" : "done";
    const hiddenCount = entries.length;
    const activities = activityList(entries);
    return {
        key: `group-${entries[0]!.seq}-${entry.seq}`,
        entry,
        children: entries,
        hiddenCount,
        state,
        headline: completedHeadline(entries),
        activities,
        meta: metaFor(entry, hiddenCount),
        progress: buildProgress(entry, entries, activities, true),
        isLive: false,
        isAttention: state === "error" || state === "warn",
    };
}

function makeSpecialRow(entry: LogEntry, logs: LogEntry[]): ExecutionTranscriptRow {
    const children = entry.kind === "rollup" ? rollupChildren(entry, logs) : [];
    const hiddenCount = entry.kind === "rollup" ? (entry.detailCount || children.length) : children.length;
    const state = entry.kind === "steering" && /queued|requested|deferred/i.test(entry.message) ? "queued" : logLevel(entry);
    const activities = children.length ? activityList(children) : [];
    return {
        key: `${entry.kind}-${entry.seq}`,
        entry,
        children,
        hiddenCount,
        state,
        headline: text(entry.message, 260) || activityText(entry),
        activities,
        meta: metaFor(entry, hiddenCount),
        progress: buildProgress(entry, children.length ? children : [entry], activities, true),
        isLive: false,
        isAttention: isAttention(entry),
        isReceipt: entry.kind === "rollup",
    };
}

function coveredByRollup(logs: LogEntry[]): Set<number> {
    const covered = new Set<number>();
    const rollups = logs.filter((entry) => entry.kind === "rollup" && entry.coveredSeqStart != null && entry.coveredSeqEnd != null);
    for (const rollup of rollups) {
        const start = Number(rollup.coveredSeqStart);
        const end = Number(rollup.coveredSeqEnd);
        for (const entry of logs) {
            if (entry.seq >= start && entry.seq <= end && entry.seq !== rollup.seq && entry.kind !== "rollup" && !isCritical(entry)) {
                covered.add(entry.seq);
            }
        }
    }
    return covered;
}

function matchesActiveAgent(entry: LogEntry, activeAgentId: string | null): boolean {
    if (!activeAgentId) return false;
    const needle = activeAgentId.toLowerCase();
    return String(entry.agentId || "").toLowerCase() === needle || String(entry.agentName || "").toLowerCase() === needle;
}

function findActiveEntries(logs: LogEntry[], state: MissionState, covered: Set<number>): LogEntry[] {
    if (state.terminalType || state.jobStatus === "completed" || state.jobStatus === "failed" || state.jobStatus === "cancelled") return [];
    const candidates = logs.filter((entry) => !covered.has(entry.seq) && !isSpecialRow(entry) && entry.kind !== "error" && entry.streamStatus !== "finalized");
    if (candidates.length === 0) return [];
    const activeMatches = candidates.filter((entry) => matchesActiveAgent(entry, state.activeAgentId));
    const source = activeMatches.length > 0 ? activeMatches : candidates;
    const latest = source[source.length - 1]!;
    const agentKey = String(latest.agentId || latest.agentName || "");
    const matched = source.filter((entry) => {
        if (!agentKey) return true;
        return String(entry.agentId || entry.agentName || "") === agentKey;
    });
    return activeSubstepEntries(matched);
}

function activeToolStartIndex(entries: LogEntry[]): number {
    const completedCallIds = new Set<string>();
    for (const entry of entries) {
        if (entry.kind === "tool_end" && entry.callId) completedCallIds.add(entry.callId);
    }
    for (let index = entries.length - 1; index >= 0; index -= 1) {
        const entry = entries[index]!;
        if (entry.kind !== "tool_start") continue;
        if (!entry.callId || !completedCallIds.has(entry.callId)) return index;
    }
    return -1;
}

function activeSubstepEntries(entries: LogEntry[]): LogEntry[] {
    if (entries.length <= 1) return entries;
    const startIndex = activeToolStartIndex(entries);
    if (startIndex < 0) return entries;
    const startEntry = entries[startIndex]!;
    const callId = startEntry.callId;
    if (!callId) return entries.slice(startIndex);
    return entries.slice(startIndex).filter((entry) => !entry.callId || entry.callId === callId);
}

function liveSlotStatus(status?: string): boolean {
    return status === "running" || status === "waiting" || status === "approval_required" || status === "queued";
}

function slotMatchesActiveAgent(slot: any, activeAgentId: string | null): boolean {
    if (!activeAgentId) return false;
    const active = activeAgentId.toLowerCase();
    return [slot.slotId, slot.agentId, slot.agentName, slot.role]
        .filter(Boolean)
        .map((value) => String(value).toLowerCase())
        .some((value) => value === active || value.includes(active) || active.includes(value));
}

function liveSlotProgresses(state: MissionState): any[] {
    if (state.terminalType || state.jobStatus === "completed" || state.jobStatus === "failed" || state.jobStatus === "cancelled") return [];
    const slots = Object.values(state.slotProgress || {}) as any[];
    if (slots.length === 0) return [];
    return slots
        .filter((slot) => liveSlotStatus(String(slot.status || "")))
        .sort((a, b) => {
            const aActive = slotMatchesActiveAgent(a, state.activeAgentId) ? 0 : 1;
            const bActive = slotMatchesActiveAgent(b, state.activeAgentId) ? 0 : 1;
            if (aActive !== bActive) return aActive - bActive;
            const priority = (slot: any) => {
                const status = String(slot.status || "");
                if (status === "running") return 0;
                if (status === "waiting" || status === "approval_required") return 1;
                return 2;
            };
            return priority(a) - priority(b);
        });
}

function activeSlotProgress(state: MissionState): any | null {
    return liveSlotProgresses(state)[0] || null;
}

function toolNameFromStep(step: string): string | undefined {
    const calling = step.match(/^Calling\s+([a-z0-9_:-]+)\.{0,3}$/i);
    if (calling) return calling[1];
    const running = step.match(/^Running\s+([a-z0-9_:-]+)\.{0,3}$/i);
    if (running) return running[1];
    return undefined;
}

function isBlandRuntimeStep(value: string): boolean {
    return /^(starting|starting\.\.\.|preparing|preparing execution|initializing|initializing\.\.\.|working)$/i.test(value.trim());
}

function readableRole(value: unknown): string {
    return text(value, 120).replace(/\.$/, "");
}

function snapshotStepText(slot: any, formattedStep: string, status: string): string {
    if (formattedStep && !isBlandRuntimeStep(formattedStep)) return formattedStep;
    const role = readableRole(slot.role || slot.agentName || slot.agentId || "agent");
    if (status === "waiting" || status === "approval_required") return `Waiting on ${role || "the next guarded step"}`;
    if (status === "queued") return `Preparing ${role || "the next specialist"}`;
    return role ? `Working on ${role}` : "Working";
}

function entryMatchesSlot(entry: LogEntry, slot: any): boolean {
    const slotKeys = [slot.slotId, slot.agentId, slot.agentName, slot.role]
        .map((value) => String(value || "").toLowerCase())
        .filter(Boolean);
    const entryKeys = [entry.agentId, entry.agentName]
        .map((value) => String(value || "").toLowerCase())
        .filter(Boolean);
    if (slotKeys.length === 0 || entryKeys.length === 0) return false;
    return entryKeys.some((entryKey) => slotKeys.some((slotKey) => entryKey === slotKey || entryKey.includes(slotKey) || slotKey.includes(entryKey)));
}

function syntheticLiveEntryFromSlot(state: MissionState, logs: LogEntry[], slotOverride?: any, offset = 0): LogEntry | null {
    const slot = slotOverride || activeSlotProgress(state);
    if (!slot) return null;
    const rawStep = text(slot.currentStep || slot.reason || "", 220);
    const status = String(slot.status || "running").toLowerCase();
    const toolName = toolNameFromStep(rawStep);
    const formattedStep = rawStep ? formatOperationalMessage(rawStep) : "";
    const fallback = snapshotStepText(slot, formattedStep, status);
    const latestTs = logs.length > 0 ? logs[logs.length - 1]!.ts : new Date().toISOString();
    return {
        seq: Math.max(1, state.lastSeq + 1 + offset),
        ts: latestTs,
        agentId: String(slot.agentId || slot.slotId || ""),
        agentName: String(slot.agentName || slot.role || slot.agentId || "Agent"),
        level: status === "failed" ? "error" : status === "waiting" || status === "approval_required" ? "warn" : "info",
        message: fallback,
        logCategory: "high_level",
        kind: toolName ? "tool_start" : "log",
        toolName,
        payloadSummary: { taskDescription: String(slot.role || slot.agentName || "") },
        sourceEventType: "session_snapshot",
    };
}

function makeLiveRow(activeEntries: LogEntry[]): ExecutionTranscriptRow | null {
    if (activeEntries.length === 0) return null;
    const entry = activeEntries[activeEntries.length - 1]!;
    if (isStreamEntry(entry)) return makeStreamRow(entry, true);
    const semanticClass = semanticClassFor(entry, false);
    const activities = activityList(activeEntries, entry.seq);
    const progress = buildProgress(entry, activeEntries, activities, false);
    return {
        key: `live-${entry.seq}`,
        entry,
        children: [],
        hiddenCount: 0,
        state: "running",
        headline: activeHeadline(entry, activities, semanticClass),
        currentDetail: progress.statusText,
        activities,
        meta: metaFor(entry, 0),
        progress,
        isLive: true,
        isAttention: false,
    };
}

function findActiveEntryGroups(logs: LogEntry[], state: MissionState, covered: Set<number>): LogEntry[][] {
    const candidates = logs.filter((entry) => !covered.has(entry.seq) && !isSpecialRow(entry) && entry.kind !== "error" && entry.streamStatus !== "finalized");
    const liveSlots = liveSlotProgresses(state).slice(0, 6);
    if (liveSlots.length === 0) {
        const legacyActive = findActiveEntries(logs, state, covered);
        return legacyActive.length > 0 ? [legacyActive] : [];
    }

    const groups: LogEntry[][] = [];
    const claimedSeqs = new Set<number>();
    liveSlots.forEach((slot, index) => {
        const matched = candidates.filter((entry) => !claimedSeqs.has(entry.seq) && entryMatchesSlot(entry, slot));
        if (matched.length > 0) {
            const activeMatched = activeSubstepEntries(matched);
            activeMatched.forEach((entry) => claimedSeqs.add(entry.seq));
            groups.push(activeMatched);
            return;
        }
        const synthetic = syntheticLiveEntryFromSlot(state, logs, slot, index);
        if (synthetic) groups.push([synthetic]);
    });

    const activeMatches = activeSubstepEntries(
        candidates.filter((entry) => !claimedSeqs.has(entry.seq) && matchesActiveAgent(entry, state.activeAgentId)),
    );
    if (activeMatches.length > 0) groups.unshift(activeMatches);

    return groups;
}

export function buildExecutionTranscriptRows(logs: LogEntry[], state: MissionState, allLogs: LogEntry[] = logs): ExecutionTranscriptRow[] {
    const visibleLogs = logs.slice(-MAX_VISIBLE_LOGS);
    const activeSourceLogs = allLogs;
    const covered = coveredByRollup(visibleLogs);
    const activeCovered = coveredByRollup(activeSourceLogs);
    const activeGroups = findActiveEntryGroups(activeSourceLogs, state, activeCovered);
    const activeSeqs = new Set(activeGroups.flatMap((group) => group.map((entry) => entry.seq)));
    const liveRows: ExecutionTranscriptRow[] = [];
    for (const group of activeGroups) {
        const liveRow = makeLiveRow(group);
        if (liveRow) liveRows.push(liveRow);
    }

    const rows: ExecutionTranscriptRow[] = [];

    let pending: LogEntry[] = [];
    const flushPending = () => {
        const row = makeCompletedRow(pending);
        if (row) rows.push(row);
        pending = [];
    };

    for (const entry of visibleLogs) {
        if (activeSeqs.has(entry.seq) || covered.has(entry.seq)) continue;
        if (isStreamEntry(entry)) {
            flushPending();
            rows.push(makeStreamRow(entry, false));
            continue;
        }
        if (isSpecialRow(entry) || isCritical(entry)) {
            flushPending();
            rows.push(makeSpecialRow(entry, visibleLogs));
            continue;
        }
        const previous = pending[pending.length - 1];
        const sameAgent = !previous || String(previous.agentId || previous.agentName || "") === String(entry.agentId || entry.agentName || "");
        if (!sameAgent) {
            flushPending();
        }
        pending.push(entry);
    }
    flushPending();

    rows.push(...liveRows);

    return rows.slice(-64);
}