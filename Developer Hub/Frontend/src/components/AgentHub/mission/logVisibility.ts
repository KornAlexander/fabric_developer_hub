import type { PublicLogCategory } from "./events";
import type { LogEntry } from "./missionReducer";

const MAJOR_ERROR_PATTERNS: RegExp[] = [
    /capacitynotactive/i,
    /capacity\s+not\s+active/i,
    /capacity\s+is\s+inactive/i,
    /workspace\s+capacity\s+is\s+inactive/i,
    /cannot\s+proceed\s+until\s+capacity/i,
];

const MAJOR_WARNING_PATTERNS: RegExp[] = [
    /capacity\s+issue/i,
    /capacity\s+constraint/i,
    /throttl(ed|ing)?/i,
    /quota\s+exceeded/i,
];

export function inferIssueSeverity(message: string): "error" | "warn" | null {
    if (!message) return null;
    if (MAJOR_ERROR_PATTERNS.some((re) => re.test(message))) return "error";
    if (MAJOR_WARNING_PATTERNS.some((re) => re.test(message))) return "warn";
    return null;
}

export function resolvedLogLevel(entry: Pick<LogEntry, "level" | "message">): "info" | "warn" | "error" {
    if (entry.level === "error") return "error";
    const inferred = inferIssueSeverity(entry.message);
    if (inferred === "error") return "error";
    if (entry.level === "warn" || inferred === "warn") return "warn";
    return "info";
}

export function isHighSignalLog(entry: LogEntry): boolean {
    const level = resolvedLogLevel(entry);
    if (entry.logCategory === "high_level") return true;
    if (level === "error" || level === "warn" || entry.kind === "error") return true;
    if (entry.kind === "action" || entry.kind === "decision") return true;
    if (entry.kind === "rollup" || entry.kind === "steering") return true;
    if (entry.kind === "phase" && /complete|failed|approval|start/i.test(entry.message)) return true;
    return false;
}

export function logCategoryIncludedInView(_entryCategory: PublicLogCategory, _selectedCategory: PublicLogCategory): boolean {
    return true;
}

export function logVisibleInCategory(_entry: LogEntry, _selectedCategory: PublicLogCategory): boolean {
    return true;
}