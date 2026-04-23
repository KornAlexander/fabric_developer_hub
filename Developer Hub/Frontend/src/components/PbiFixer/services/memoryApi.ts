// WS-B — Memory Analyzer (a.k.a. Vertipaq Analyzer).
//
// Two-phase architecture per the WS-B PLAN:
//   • Phase 1 (planned): backend bridge to `sempy_labs.vertipaq_analyzer()` —
//     uses the XMLA endpoint, exposes full segment/dictionary telemetry.
//   • Phase 2 (THIS): client-side via the Power BI `executeQueries` REST API.
//     The REST proxy only permits the friendly `INFO.VIEW.*` family — raw
//     `INFO.STORAGETABLE*`, `INFO.PARTITIONS()`, `INFO.HIERARCHIES()` are
//     rejected by the service with `DatasetExecuteQueriesError`. We therefore
//     ship row counts + structural metadata now, and gate the per-column
//     storage breakdown behind the future backend bridge.
//
// Sources used (all run via the existing `executeDax` helper):
//   • INFO.VIEW.TABLES()        — name, rows count, columns count, mode
//   • INFO.VIEW.COLUMNS()       — column-level metadata (no segment sizes)
//   • INFO.VIEW.MEASURES()      — measures with expressions & folders
//   • INFO.VIEW.RELATIONSHIPS() — relationship graph

import type { PbiAuth } from "./fabricApi";
import { executeDax } from "./fabricApi";

// ── Row shapes ─────────────────────────────────────────────────────

export interface VertipaqSummary {
  tableCount: number;
  columnCount: number;
  measureCount: number;
  relationshipCount: number;
  /** Sum of `RowsCount` from `INFO.VIEW.TABLES()`. */
  totalRowCount: number;
  /** True when the friendly `INFO.VIEW.*` queries returned data. The
   *  per-column storage breakdown is still gated behind the backend
   *  bridge and is always empty in Phase 2. */
  hasViewData: boolean;
}

export interface VertipaqTableRow {
  table: string;
  rows: number;
  columns: number;
  mode: string;
  isHidden: boolean;
  description: string;
  modified: string;
}

export interface VertipaqColumnRow {
  table: string;
  column: string;
  dataType: string;
  isHidden: boolean;
  isKey: boolean;
  formatString: string;
  folder: string;
  description: string;
}

export interface VertipaqMeasureRow {
  table: string;
  measure: string;
  dataType: string;
  formatString: string;
  folder: string;
  isHidden: boolean;
  expression: string;
}

export interface VertipaqRelationshipRow {
  fromTable: string;
  fromColumn: string;
  toTable: string;
  toColumn: string;
  cardinality: string;
  isActive: boolean;
  crossFilter: string;
}

export interface VertipaqData {
  summary: VertipaqSummary;
  tables: VertipaqTableRow[];
  columns: VertipaqColumnRow[];
  measures: VertipaqMeasureRow[];
  relationships: VertipaqRelationshipRow[];
}

// ── Helpers ────────────────────────────────────────────────────────

function cell<T = unknown>(row: Record<string, unknown>, ...candidates: string[]): T | undefined {
  for (const k of candidates) {
    if (k in row) return row[k] as T;
    const bracket = `[${k}]`;
    if (bracket in row) return row[bracket] as T;
  }
  const lower = candidates.map((c) => c.toLowerCase());
  for (const k of Object.keys(row)) {
    const stripped = k.replace(/^\[|\]$/g, "").toLowerCase();
    if (lower.includes(stripped)) return row[k] as T;
  }
  return undefined;
}

function toNum(v: unknown): number {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

function toStr(v: unknown): string {
  return v == null ? "" : String(v);
}

function toBool(v: unknown): boolean {
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0;
  if (typeof v === "string") return v.toLowerCase() === "true" || v === "1";
  return false;
}

async function safeDax(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
  query: string,
): Promise<Record<string, unknown>[]> {
  try {
    return await executeDax(auth, workspaceId, datasetId, query);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn(`[memoryApi] DAX query failed (returning empty):`, query, err);
    return [];
  }
}

// ── Loader ────────────────────────────────────────────────────────

export async function loadVertipaqData(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
): Promise<VertipaqData> {
  const [tablesView, columnsView, measuresView, relsView] = await Promise.all([
    safeDax(auth, workspaceId, datasetId, "EVALUATE INFO.VIEW.TABLES()"),
    safeDax(auth, workspaceId, datasetId, "EVALUATE INFO.VIEW.COLUMNS()"),
    safeDax(auth, workspaceId, datasetId, "EVALUATE INFO.VIEW.MEASURES()"),
    safeDax(auth, workspaceId, datasetId, "EVALUATE INFO.VIEW.RELATIONSHIPS()"),
  ]);

  const tables: VertipaqTableRow[] = tablesView.map((row) => ({
    table: toStr(cell(row, "Name", "TableName", "Table")),
    rows: toNum(cell(row, "RowsCount", "Rows", "RowCount")),
    columns: toNum(cell(row, "ColumnsCount", "Columns")),
    mode: toStr(cell(row, "ImportMode", "Mode", "Type")),
    isHidden: toBool(cell(row, "IsHidden", "Hidden")),
    description: toStr(cell(row, "Description")),
    modified: toStr(cell(row, "ModifiedTime", "RefreshedTime")),
  }));

  const columns: VertipaqColumnRow[] = columnsView
    .map((row) => ({
      table: toStr(cell(row, "Table", "TableName")),
      column: toStr(cell(row, "Name", "ColumnName", "Column")),
      dataType: toStr(cell(row, "DataType")),
      isHidden: toBool(cell(row, "IsHidden", "Hidden")),
      isKey: toBool(cell(row, "IsKey")),
      formatString: toStr(cell(row, "FormatString")),
      folder: toStr(cell(row, "DisplayFolder", "Folder")),
      description: toStr(cell(row, "Description")),
    }))
    .filter((c) => !c.column.startsWith("RowNumber-"));

  const measures: VertipaqMeasureRow[] = measuresView.map((row) => ({
    table: toStr(cell(row, "Table", "TableName")),
    measure: toStr(cell(row, "Name", "MeasureName", "Measure")),
    dataType: toStr(cell(row, "DataType")),
    formatString: toStr(cell(row, "FormatString")),
    folder: toStr(cell(row, "DisplayFolder", "Folder")),
    isHidden: toBool(cell(row, "IsHidden", "Hidden")),
    expression: toStr(cell(row, "Expression", "MeasureExpression")),
  }));

  const relationships: VertipaqRelationshipRow[] = relsView.map((row) => ({
    fromTable: toStr(cell(row, "FromTable", "FromTableName")),
    fromColumn: toStr(cell(row, "FromColumn", "FromColumnName")),
    toTable: toStr(cell(row, "ToTable", "ToTableName")),
    toColumn: toStr(cell(row, "ToColumn", "ToColumnName")),
    cardinality: (() => {
      const fromCard = toStr(cell(row, "FromCardinality"));
      const toCard = toStr(cell(row, "ToCardinality"));
      if (fromCard && toCard) return `${fromCard} → ${toCard}`;
      return toStr(cell(row, "Cardinality", "RelationshipType")) || "—";
    })(),
    isActive: toBool(cell(row, "IsActive", "Active")),
    crossFilter: toStr(cell(row, "CrossFilteringBehavior", "CrossFilter")) || "OneDirection",
  }));

  const totalRowCount = tables.reduce((s, t) => s + t.rows, 0);

  const summary: VertipaqSummary = {
    tableCount: tables.length,
    columnCount: columns.length,
    measureCount: measures.length,
    relationshipCount: relationships.length,
    totalRowCount,
    hasViewData: tablesView.length > 0,
  };

  return { summary, tables, columns, measures, relationships };
}

// ── Formatters / CSV export ───────────────────────────────────────

export function formatNumber(n: number): string {
  return n.toLocaleString();
}

export function exportSectionToCsv(section: keyof VertipaqData, data: VertipaqData): string {
  const escape = (v: unknown): string => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines: string[] = [];
  if (section === "tables") {
    lines.push(["Table", "Rows", "Columns", "Mode", "Hidden", "Description", "Modified"].join(","));
    for (const r of data.tables) {
      lines.push([r.table, r.rows, r.columns, r.mode, r.isHidden, r.description, r.modified].map(escape).join(","));
    }
  } else if (section === "columns") {
    lines.push(["Table", "Column", "DataType", "Hidden", "IsKey", "Folder", "FormatString", "Description"].join(","));
    for (const r of data.columns) {
      lines.push([r.table, r.column, r.dataType, r.isHidden, r.isKey, r.folder, r.formatString, r.description].map(escape).join(","));
    }
  } else if (section === "measures") {
    lines.push(["Table", "Measure", "DataType", "FormatString", "Folder", "Hidden", "Expression"].join(","));
    for (const r of data.measures) {
      lines.push([r.table, r.measure, r.dataType, r.formatString, r.folder, r.isHidden, r.expression].map(escape).join(","));
    }
  } else if (section === "relationships") {
    lines.push(["From Table", "From Column", "To Table", "To Column", "Cardinality", "Active", "Cross Filter"].join(","));
    for (const r of data.relationships) {
      lines.push([r.fromTable, r.fromColumn, r.toTable, r.toColumn, r.cardinality, r.isActive, r.crossFilter].map(escape).join(","));
    }
  }
  return lines.join("\n");
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
