// Vertipaq Analyzer client — calls the new
// ``/api/pbi-fixer/memory/vertipaq`` backend endpoint that runs DMV
// Discover SOAP calls against the Power BI XMLA-over-HTTPS gateway
// using the user's OBO ``PBI_API_TOKEN``. Replaces the old SLL
// sidecar's ``runSllVertipaq`` for the Memory page.

import { runVertipaqAnalyzer as backendRunVertipaq } from "../../../controller/AgentHubApi";
import type { PbiAuth } from "./fabricApi";

// ── Result shapes (must match Backend/services/agenthub/pbi_fixer_vertipaq.py) ──

export interface VertipaqModelRow {
    datasetName: string;
    compatibilityLevel: string;
    defaultMode: string;
    totalSize: number;
    tableCount: number;
    columnCount: number;
    partitionCount: number;
    hierarchyCount: number;
    relationshipCount: number;
    totalRows: number;
}

export interface VertipaqTableRow {
    table: string;
    rows: number;
    totalSize: number;
    dataSize: number;
    dictionarySize: number;
    hierarchySize: number;
    userHierarchySize: number;
    relationshipSize: number;
    partitionsCount: number;
    columnsCount: number;
    segmentsCount: number;
    mode: string;
    pctDb: number;
}

export interface VertipaqColumnRow {
    table: string;
    column: string;
    totalSize: number;
    dataSize: number;
    dictionarySize: number;
    hierarchySize: number;
    encoding: string;
    isResident: boolean;
    temperature: number;
    lastAccessed: string;
    records: number;
    segments: number;
    dataType: string;
    pctDb: number;
    pctTable: number;
}

export interface VertipaqHierarchyRow {
    table: string;
    hierarchy: string;
    usedSize: number;
    rowsCount: number;
}

export interface VertipaqRelationshipRow {
    fromTable: string;
    fromColumn: string;
    toTable: string;
    toColumn: string;
    usedSize: number;
    maxFromCardinality: number;
    maxToCardinality: number;
    missingKeys: number;
}

export interface VertipaqPartitionRow {
    table: string;
    partition: string;
    mode: string;
    dataSourceType: string;
    modifiedTime: string;
    refreshedTime: string;
}

export interface VertipaqAnalyzerResult {
    sections: {
        model: VertipaqModelRow[];
        tables: VertipaqTableRow[];
        partitions: VertipaqPartitionRow[];
        columns: VertipaqColumnRow[];
        hierarchies: VertipaqHierarchyRow[];
        relationships: VertipaqRelationshipRow[];
    };
    meta: {
        workspaceId: string;
        workspaceName: string;
        datasetId: string;
        datasetName: string;
    };
}

export async function runVertipaqAnalyzer(
    auth: PbiAuth,
    workspaceId: string,
    datasetId: string,
    workspaceName?: string,
    datasetName?: string,
): Promise<VertipaqAnalyzerResult> {
    return backendRunVertipaq<VertipaqAnalyzerResult>(
        { workspaceId, datasetId, workspaceName, datasetName },
        auth,
    );
}

// ── Display helpers ──

const _UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

export function formatBytes(bytes: number | null | undefined): string {
    if (!bytes || bytes <= 0) return "—";
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < _UNITS.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${value.toFixed(value >= 100 || unit === 0 ? 0 : value >= 10 ? 1 : 2)} ${_UNITS[unit]}`;
}

export function formatNumber(value: number | null | undefined): string {
    if (value == null || Number.isNaN(value)) return "—";
    return value.toLocaleString();
}

export function formatPct(pct: number | null | undefined): string {
    if (pct == null || Number.isNaN(pct)) return "—";
    return `${pct.toFixed(pct < 1 ? 2 : 1)}%`;
}
