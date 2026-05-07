export const PI_LOG_COMPACTION_RECENT_WINDOW_MS = 8000;
export const PI_LOG_COMPACTION_REFRESH_MS = 1500;
export const PI_LOG_COMPACTION_MAX_RECENT_ROWS = 8;
export const PI_LOG_COMPACTION_MAX_SOURCE_ROWS = 80;

export interface PiLiveLogDetailRow {
    type: "detail";
    key: string;
    seq: number;
    ts: string;
    level: "info" | "warn" | "error";
    agent: string;
    message: string;
    kind: string;
    ageState?: "recent" | "older";
}

export interface PiLiveLogRollupRow {
    type: "rollup";
    key: string;
    seq: number;
    ts: string;
    level: "info" | "warn" | "error";
    agent: string;
    message: string;
    kind: "collapsed-rollup";
    detailCount: number;
    coveredSeqStart: number;
    coveredSeqEnd: number;
    details: PiLiveLogDetailRow[];
}

export type PiSelfCollapsingLiveLogRow = PiLiveLogDetailRow | PiLiveLogRollupRow;

interface PiLogCompactionOptions {
    nowMs?: number;
    recentWindowMs?: number;
    maxRecentRows?: number;
}

function rowTimeMs(row: PiLiveLogDetailRow): number | null {
    const value = new Date(row.ts).getTime();
    return Number.isFinite(value) ? value : null;
}

function logGroup(row: PiLiveLogDetailRow): string {
    if (row.kind === "tool_start" || row.kind === "tool_end" || row.kind === "tool") return "tool";
    if (row.kind === "turn" || row.kind === "decision") return "reasoning";
    if (row.kind === "phase" || row.kind === "log" || row.kind === "orchestration") return "runtime";
    if (row.kind === "subagent") return "subagent";
    if (row.kind === "approval") return "approval";
    if (row.kind === "artifact") return "artifact";
    if (row.kind === "error") return "error";
    return row.kind || "activity";
}

function strongestLevel(rows: PiLiveLogDetailRow[]): PiLiveLogDetailRow["level"] {
    if (rows.some((row) => row.level === "error")) return "error";
    if (rows.some((row) => row.level === "warn")) return "warn";
    return "info";
}

function compactMessage(value: string, maxLength = 120): string {
    const normalized = value.replace(/\s+/g, " ").trim();
    if (!normalized) return "activity";
    return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}...` : normalized;
}

function stripStatusPrefix(value: string): string {
    return value
        .replace(/^(completed|running|failed|waiting):\s*/i, "")
        .replace(/^artifact ready:\s*/i, "")
        .replace(/^waiting for approval:\s*/i, "Approval needed: ")
        .trim();
}

function meaningfulRollupMessage(rows: PiLiveLogDetailRow[]): string {
    const last = rows[rows.length - 1];
    const important = [...rows].reverse().find((row) => row.level !== "info" || row.kind === "approval" || row.kind === "artifact" || row.kind === "tool");
    const lead = important || last;
    const cleanedLead = compactMessage(stripStatusPrefix(lead.message), 150);

    if (lead.level === "error") return `Needs review: ${cleanedLead}`;
    if (lead.level === "warn" || lead.kind === "approval") return cleanedLead.startsWith("Approval needed") ? cleanedLead : `Needs review: ${cleanedLead}`;
    if (lead.kind === "artifact") return `Output ready: ${cleanedLead}`;
    if (lead.kind === "tool") return cleanedLead;

    const first = compactMessage(stripStatusPrefix(rows[0].message), 82);
    const tail = compactMessage(stripStatusPrefix(last.message), 120);
    if (first && tail && first !== tail) return `${first} to ${tail}`;
    return tail || cleanedLead;
}

function rollupForRows(rows: PiLiveLogDetailRow[]): PiLiveLogRollupRow | PiLiveLogDetailRow {
    const first = rows[0];
    const last = rows[rows.length - 1];
    const group = logGroup(first);
    return {
        type: "rollup",
        key: `rollup-${first.seq}-${last.seq}-${first.agent}-${group}`,
        seq: last.seq,
        ts: last.ts,
        level: strongestLevel(rows),
        agent: first.agent,
        message: meaningfulRollupMessage(rows),
        kind: "collapsed-rollup",
        detailCount: rows.length,
        coveredSeqStart: first.seq,
        coveredSeqEnd: last.seq,
        details: rows.map((row) => ({ ...row, ageState: "older" })),
    };
}

export function applyPiLogCompactionExtension(
    sourceRows: PiLiveLogDetailRow[],
    options: PiLogCompactionOptions = {},
): PiSelfCollapsingLiveLogRow[] {
    const nowMs = options.nowMs ?? Date.now();
    const recentWindowMs = options.recentWindowMs ?? PI_LOG_COMPACTION_RECENT_WINDOW_MS;
    const maxRecentRows = options.maxRecentRows ?? PI_LOG_COMPACTION_MAX_RECENT_ROWS;
    const rows = [...sourceRows].sort((a, b) => a.seq - b.seq).slice(-PI_LOG_COMPACTION_MAX_SOURCE_ROWS);
    const newestKeys = new Set(rows.slice(-maxRecentRows).map((row) => row.key));

    const isRecent = (row: PiLiveLogDetailRow) => {
        if (newestKeys.has(row.key)) return true;
        const timeMs = rowTimeMs(row);
        return timeMs != null && nowMs - timeMs <= recentWindowMs;
    };

    const entries: PiSelfCollapsingLiveLogRow[] = [];
    let pending: PiLiveLogDetailRow[] = [];
    let pendingKey: string | null = null;

    const flushPending = () => {
        if (pending.length === 0) return;
        entries.push(rollupForRows(pending));
        pending = [];
        pendingKey = null;
    };

    for (const row of rows) {
        if (isRecent(row)) {
            flushPending();
            entries.push({ ...row, ageState: "recent" });
            continue;
        }

        const key = `${row.agent}:${logGroup(row)}:${row.level}`;
        if (pendingKey && pendingKey !== key) {
            flushPending();
        }
        pendingKey = key;
        pending.push(row);
    }

    flushPending();
    return entries;
}