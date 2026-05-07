import type { LogEntry } from "./missionReducer";

type ToolArgsPreview = Record<string, unknown> | undefined;

const TOOL_LABELS: Record<string, string> = {
    fabric_create_folder: "Create folder",
    fabric_create_item: "Create Fabric item",
    fabric_create_directory: "Create directory",
    fabric_delete_item: "Delete Fabric item",
    fabric_list_items: "Read workspace inventory",
    fabric_read_file: "Read item file",
    fabric_write_file: "Write item file",
    fabric_list_files: "List item files",
    fabric_get_item_definition: "Read item definition",
};

function titleCaseWords(value: string): string {
    return value
        .replace(/^fabric_/, "")
        .replace(/_/g, " ")
        .replace(/\b\w/g, (ch) => ch.toUpperCase())
        .trim() || "Tool";
}

function cleanName(value: unknown): string | null {
    if (typeof value !== "string") return null;
    const trimmed = value.trim();
    return trimmed ? trimmed.slice(0, 140) : null;
}

function quotedName(args: ToolArgsPreview): string | null {
    const value = cleanName(args?.display_name)
        || cleanName(args?.displayName)
        || cleanName(args?.name)
        || cleanName(args?.path)
        || cleanName(args?.item_name)
        || cleanName(args?.itemName);
    return value ? `“${value}”` : null;
}

function itemType(args: ToolArgsPreview): string {
    return cleanName(args?.item_type)
        || cleanName(args?.itemType)
        || cleanName(args?.type)
        || "Fabric item";
}

export function formatToolName(toolName?: string | null): string {
    const raw = (toolName || "").trim();
    if (!raw) return "Tool";
    const key = raw.toLowerCase();
    return TOOL_LABELS[key] || titleCaseWords(raw);
}

export function formatDurationMs(durationMs?: number): string {
    if (durationMs == null || !Number.isFinite(durationMs)) return "";
    if (durationMs < 1000) return `${durationMs} ms`;
    const seconds = durationMs / 1000;
    return `${seconds >= 10 ? Math.round(seconds) : seconds.toFixed(1)} s`;
}

export function formatToolStartMessage(toolName: string, args: ToolArgsPreview): string {
    const name = quotedName(args);
    switch (toolName) {
        case "fabric_create_folder":
            return `Creating folder${name ? ` ${name}` : ""}`;
        case "fabric_create_item":
            return `Creating ${itemType(args)}${name ? ` ${name}` : ""}`;
        case "fabric_create_directory":
            return `Creating directory${name ? ` ${name}` : ""}`;
        case "fabric_delete_item":
            return `Deleting Fabric item${name ? ` ${name}` : ""}`;
        case "fabric_list_items":
            return "Reading workspace inventory";
        case "fabric_read_file":
        case "fabric_get_item_definition":
            return `Reading item details${name ? ` for ${name}` : ""}`;
        case "fabric_write_file":
            return `Writing item content${name ? ` to ${name}` : ""}`;
        default:
            return `Running ${formatToolName(toolName).toLowerCase()}${name ? ` for ${name}` : ""}`;
    }
}

export function formatToolEndMessage(toolName: string, status: "ok" | "error", durationMs?: number): string {
    const duration = formatDurationMs(durationMs);
    const label = formatToolName(toolName).toLowerCase();
    const prefix = status === "ok" ? "Completed" : "Failed";
    return `${prefix}: ${label}${duration ? ` · ${duration}` : ""}`;
}

export function formatLatencyBreakdownMs(breakdown?: Record<string, number>): string {
    if (!breakdown) return "";
    const parts = [
        ["policy", breakdown.backendPolicyMs],
        ["sidecar", breakdown.sidecarHttpMs],
        ["startup", breakdown.mcpProcessStartupMs],
        ["tool", breakdown.mcpToolExecutionMs],
    ]
        .filter((entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1]))
        .map(([label, value]) => `${label} ${formatDurationMs(value)}`);
    return parts.join(" · ");
}

function replaceRawToolNames(value: string): string {
    return value.replace(/\bfabric_[a-z0-9_]+\b/gi, (toolName) => formatToolName(toolName).toLowerCase());
}

function normalizeTraceTokens(value: string): string {
    return replaceRawToolNames(value)
        .replace(/\bRequesting\s+(?:model|assistant|[a-z0-9][a-z0-9._:-]*)\s+response\s+for\s+/gi, "Preparing ")
        .replace(/\s*\(\s*\d+\s+tool\s+calls?\s*(?:[·,]\s*\d+\s+chars?)?\s*\)/gi, "")
        .replace(/\s*\(\s*\d+\s+chars?\s*\)/gi, "")
        .replace(/\s*\(\s*\+\d+\s+detail\s+events?\s*\)/gi, "")
        .replace(/@fabric-clawhub\/pi-log-compactor/gi, "automatic log summary")
        .replace(/@fabric-clawhub\/pi-agentic-engineering/gi, "mission guidance")
        .replace(/@fabric-clawhub\/pi-mission-ui/gi, "mission UI")
        .replace(/@mariozechner\/pi-web-ui/gi, "mission UI")
        .replace(/@mariozechner\/pi-agent-core/gi, "agent runtime")
        .replace(/@mariozechner\/pi-[a-z0-9-]+/gi, "mission runtime")
        .replace(/\bpi-subagents\b/gi, "delegated agents")
        .replace(/\bpi\s+subagents\b/gi, "delegated agents")
        .replace(/\bPi\s+Web\s+UI\b/g, "Mission UI")
        .replace(/\bPi\s+runtime\b/gi, "mission runtime")
        .replace(/\bPi\s+agents?\b/gi, "delegated agents")
        .replace(/\bTOOL_ERROR\b/g, "Tool issue")
        .replace(/===HANDOFF/g, "handoff")
        .replace(/\[object Object\]/g, "details unavailable")
        .replace(/\bundefined\b/g, "details unavailable")
        .replace(/\bNaN\b/g, "not available")
        .replace(/→/g, " to ")
        .replace(/←/g, " from ")
        .replace(/\s{2,}/g, " ")
        .trim();
}

export function formatVisibleRuntimeText(message?: string | null): string {
    return normalizeTraceTokens(String(message || ""));
}

export function formatOperationalMessage(message?: string | null): string {
    const raw = cleanName(message) || "";
    if (!raw) return "";

    const calling = raw.match(/^Calling\s+([a-z0-9_:-]+)\.{0,3}$/i);
    if (calling) {
        return formatToolStartMessage(calling[1], undefined);
    }

    const completed = raw.match(/^Tool\s+([a-z0-9_:-]+)\s+completed\.?\s*(?:Thinking\.{0,3})?$/i);
    if (completed) {
        return `Completed: ${formatToolName(completed[1]).toLowerCase()}`;
    }

    const majorIssue = raw.match(/^Major issue detected while calling\s+([a-z0-9_:-]+):\s*(.*)$/i);
    if (majorIssue) {
        const detail = normalizeTraceTokens(majorIssue[2]);
        return `Issue while running ${formatToolName(majorIssue[1]).toLowerCase()}${detail ? `: ${detail}` : ""}`;
    }

    const workflowState = raw.match(/^workflow state:\s*(?:WorkflowRunState\.)?([A-Z_]+)$/i);
    if (workflowState) {
        const state = workflowState[1].toLowerCase().replace(/_/g, " ");
        return `Workflow ${state}`;
    }

    return normalizeTraceTokens(raw);
}

export function formatActionMessage(action: Record<string, unknown>): string {
    const rawVerb = cleanName(action.action_type) || "Updated";
    const entityType = normalizeTraceTokens(cleanName(action.entity_type) || "artifact");
    const entityName = normalizeTraceTokens(cleanName(action.entity_name) || "unnamed artifact");

    if (/^fabric_[a-z0-9_]+$/i.test(rawVerb)) {
        return `${formatToolName(rawVerb)}: ${entityName !== "unnamed artifact" ? entityName : entityType}`;
    }

    const verb = normalizeTraceTokens(rawVerb);
    return `${verb} ${entityType}: ${entityName}`;
}

export function formatToolLine(entry: LogEntry): string {
    if (entry.kind === "tool_start") {
        return entry.message;
    }
    if (entry.kind === "tool_end") {
        return entry.message;
    }
    return entry.message;
}

function stringifyArgValue(value: unknown): string {
    if (value == null) return "null";
    if (typeof value === "string") {
        const compact = normalizeTraceTokens(value.trim().replace(/\s+/g, " "));
        return compact.includes(" ") ? `"${compact}"` : compact;
    }
    if (typeof value === "number" || typeof value === "boolean") {
        return String(value);
    }
    if (Array.isArray(value)) {
        return `[${value.length} items]`;
    }
    if (typeof value === "object") {
        return "{...}";
    }
    return "<value>";
}

function formatArgKey(key: string): string {
    return key
        .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
        .replace(/_/g, " ")
        .toLowerCase();
}

function displayArgEntries(args?: Record<string, unknown>): Array<[string, unknown]> {
    if (!args) return [];
    return Object.entries(args).filter(([key]) => key !== "terminalLabel" && key !== "terminalLines");
}

export function formatToolCommand(toolName?: string | null, args?: Record<string, unknown>): string {
    const safeToolName = formatToolName(toolName);
    const argEntries = displayArgEntries(args);
    if (argEntries.length === 0) {
        return safeToolName;
    }

    const parts = argEntries
        .slice(0, 5)
        .map(([key, value]) => `${formatArgKey(key)}: ${stringifyArgValue(value)}`);
    const omitted = argEntries.length - parts.length;
    const suffix = omitted > 0 ? ` · ${omitted} more` : "";
    return `${safeToolName} · ${parts.join(" · ")}${suffix}`.trim();
}

export function formatToolArgsSummary(args?: Record<string, unknown>): string | null {
    const argEntries = displayArgEntries(args);
    if (argEntries.length === 0) return null;
    const firstKeys = argEntries.slice(0, 4).map(([key]) => formatArgKey(key));
    const summary = firstKeys.join(", ");
    const extra = argEntries.length - firstKeys.length;
    return extra > 0 ? `${summary} +${extra} more` : summary;
}
