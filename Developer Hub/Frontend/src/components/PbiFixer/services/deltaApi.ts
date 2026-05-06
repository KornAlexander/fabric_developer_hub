// WS-I — Delta Analyzer service.
//
// Captures **model snapshots** (a flat structural fingerprint of tables /
// columns / measures / relationships) into `sessionStorage`, then diffs any
// two snapshots into Added / Removed / Changed rows grouped by object type.
//
// Phase parity note (matches WS-B / WS-C / WS-D): the snapshot reads via the
// friendly `INFO.VIEW.*` DAX family through the existing `executeDax` proxy.
// The eventual `sempy_labs.delta_analyzer()` backend bridge will only need to
// replace `takeSnapshot` — the diff engine and UI are source-agnostic.

import type { PbiAuth } from "./fabricApi";
import { loadVertipaqData, type VertipaqData } from "./memoryApi";

const STORAGE_KEY = "pbiFixer.deltaSnapshots.v1";
const MAX_SNAPSHOTS = 20;

// ── Snapshot shape ─────────────────────────────────────────────────

export interface SnapshotMeta {
  /** Stable ID, ULID-ish (timestamp-prefixed). */
  id: string;
  label: string;
  workspaceId: string;
  workspaceName?: string;
  datasetId: string;
  datasetName?: string;
  /** ISO timestamp. */
  takenAt: string;
}

export interface ModelSnapshot extends SnapshotMeta {
  data: VertipaqData;
}

// ── Diff shape ─────────────────────────────────────────────────────

export type DeltaCategory = "tables" | "columns" | "measures" | "relationships";
export type DeltaKind = "added" | "removed" | "changed";

export interface DeltaRow {
  category: DeltaCategory;
  kind: DeltaKind;
  /** Stable identity within its category (e.g. `Table[Column]`). */
  key: string;
  /** Human label for the grid. */
  label: string;
  /** When `kind === "changed"`, the per-property before/after values. */
  changes?: { property: string; before: string; after: string }[];
}

export interface DeltaSummary {
  added: number;
  removed: number;
  changed: number;
  unchanged: number;
}

export interface DeltaResult {
  base: SnapshotMeta;
  compare: SnapshotMeta;
  byCategory: Record<DeltaCategory, DeltaRow[]>;
  totals: Record<DeltaCategory, DeltaSummary>;
}

// ── Storage ────────────────────────────────────────────────────────

function safeParse<T>(json: string | null, fallback: T): T {
  if (!json) return fallback;
  try { return JSON.parse(json) as T; } catch { return fallback; }
}

export function listSnapshots(): SnapshotMeta[] {
  if (typeof sessionStorage === "undefined") return [];
  const all = safeParse<ModelSnapshot[]>(sessionStorage.getItem(STORAGE_KEY), []);
  return all.map(({ data: _data, ...meta }) => meta).sort((a, b) => b.takenAt.localeCompare(a.takenAt));
}

export function getSnapshot(id: string): ModelSnapshot | null {
  if (typeof sessionStorage === "undefined") return null;
  const all = safeParse<ModelSnapshot[]>(sessionStorage.getItem(STORAGE_KEY), []);
  return all.find((s) => s.id === id) ?? null;
}

export function deleteSnapshot(id: string): void {
  if (typeof sessionStorage === "undefined") return;
  const all = safeParse<ModelSnapshot[]>(sessionStorage.getItem(STORAGE_KEY), []);
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(all.filter((s) => s.id !== id)));
}

export function clearSnapshots(): void {
  if (typeof sessionStorage === "undefined") return;
  sessionStorage.removeItem(STORAGE_KEY);
}

function persistSnapshot(snap: ModelSnapshot): void {
  if (typeof sessionStorage === "undefined") return;
  const all = safeParse<ModelSnapshot[]>(sessionStorage.getItem(STORAGE_KEY), []);
  all.unshift(snap);
  // Trim to MAX_SNAPSHOTS most recent (sessionStorage quota is small).
  while (all.length > MAX_SNAPSHOTS) all.pop();
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch (err) {
    // Quota exceeded — drop oldest until it fits.
    while (all.length > 1) {
      all.pop();
      try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(all)); return; } catch { /* keep trimming */ }
    }
    // eslint-disable-next-line no-console
    console.warn("[deltaApi] failed to persist snapshot:", err);
  }
}

// ── Capture ────────────────────────────────────────────────────────

function newId(): string {
  // Sortable timestamp prefix + small random suffix.
  const ts = Date.now().toString(36).padStart(9, "0");
  const rnd = Math.random().toString(36).slice(2, 8);
  return `${ts}-${rnd}`;
}

export interface TakeSnapshotOpts {
  auth: PbiAuth;
  workspaceId: string;
  workspaceName?: string;
  datasetId: string;
  datasetName?: string;
  label?: string;
}

export async function takeSnapshot(opts: TakeSnapshotOpts): Promise<ModelSnapshot> {
  const data = await loadVertipaqData(opts.auth, opts.workspaceId, opts.datasetId);
  const takenAt = new Date().toISOString();
  const defaultLabel = `${opts.datasetName ?? opts.datasetId} @ ${takenAt.slice(11, 19)}`;
  const snap: ModelSnapshot = {
    id: newId(),
    label: opts.label?.trim() || defaultLabel,
    workspaceId: opts.workspaceId,
    workspaceName: opts.workspaceName,
    datasetId: opts.datasetId,
    datasetName: opts.datasetName,
    takenAt,
    data,
  };
  persistSnapshot(snap);
  return snap;
}

// ── Diff engine ────────────────────────────────────────────────────

interface IndexedItem<T> {
  key: string;
  label: string;
  /** Properties used for change detection (stringified). */
  props: Record<string, string>;
  raw: T;
}

function asProps(obj: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v == null) { out[k] = ""; continue; }
    if (typeof v === "string") { out[k] = v; continue; }
    if (typeof v === "number" || typeof v === "boolean") { out[k] = String(v); continue; }
    out[k] = JSON.stringify(v);
  }
  return out;
}

function diffIndexed<T>(
  category: DeltaCategory,
  base: IndexedItem<T>[],
  compare: IndexedItem<T>[],
  trackedProps: string[],
): { rows: DeltaRow[]; summary: DeltaSummary } {
  const baseByKey = new Map(base.map((i) => [i.key, i]));
  const compareByKey = new Map(compare.map((i) => [i.key, i]));
  const rows: DeltaRow[] = [];
  let added = 0, removed = 0, changed = 0, unchanged = 0;

  for (const c of compare) {
    const b = baseByKey.get(c.key);
    if (!b) {
      rows.push({ category, kind: "added", key: c.key, label: c.label });
      added++;
      continue;
    }
    const propDiffs: { property: string; before: string; after: string }[] = [];
    for (const p of trackedProps) {
      const before = b.props[p] ?? "";
      const after = c.props[p] ?? "";
      if (before !== after) propDiffs.push({ property: p, before, after });
    }
    if (propDiffs.length > 0) {
      rows.push({ category, kind: "changed", key: c.key, label: c.label, changes: propDiffs });
      changed++;
    } else {
      unchanged++;
    }
  }
  for (const b of base) {
    if (!compareByKey.has(b.key)) {
      rows.push({ category, kind: "removed", key: b.key, label: b.label });
      removed++;
    }
  }
  return { rows, summary: { added, removed, changed, unchanged } };
}

function indexTables(d: VertipaqData): IndexedItem<unknown>[] {
  return d.tables.map((t) => ({
    key: t.table,
    label: t.table,
    props: asProps({ rows: t.rows, columns: t.columns, mode: t.mode, isHidden: t.isHidden, description: t.description }),
    raw: t,
  }));
}

function indexColumns(d: VertipaqData): IndexedItem<unknown>[] {
  return d.columns.map((c) => ({
    key: `${c.table}[${c.column}]`,
    label: `${c.table}[${c.column}]`,
    props: asProps({ dataType: c.dataType, isHidden: c.isHidden, isKey: c.isKey, formatString: c.formatString, folder: c.folder, description: c.description }),
    raw: c,
  }));
}

function indexMeasures(d: VertipaqData): IndexedItem<unknown>[] {
  return d.measures.map((m) => ({
    key: `${m.table}[${m.measure}]`,
    label: `${m.table}[${m.measure}]`,
    props: asProps({ dataType: m.dataType, formatString: m.formatString, folder: m.folder, isHidden: m.isHidden, expression: m.expression }),
    raw: m,
  }));
}

function indexRelationships(d: VertipaqData): IndexedItem<unknown>[] {
  return d.relationships.map((r) => ({
    key: `${r.fromTable}[${r.fromColumn}] → ${r.toTable}[${r.toColumn}]`,
    label: `${r.fromTable}[${r.fromColumn}] → ${r.toTable}[${r.toColumn}]`,
    props: asProps({ cardinality: r.cardinality, isActive: r.isActive, crossFilter: r.crossFilter }),
    raw: r,
  }));
}

const TRACKED: Record<DeltaCategory, string[]> = {
  tables: ["rows", "columns", "mode", "isHidden", "description"],
  columns: ["dataType", "isHidden", "isKey", "formatString", "folder", "description"],
  measures: ["dataType", "formatString", "folder", "isHidden", "expression"],
  relationships: ["cardinality", "isActive", "crossFilter"],
};

export function computeDelta(base: ModelSnapshot, compare: ModelSnapshot): DeltaResult {
  const { byCategory, totals } = (["tables", "columns", "measures", "relationships"] as DeltaCategory[]).reduce(
    (acc, cat) => {
      const baseIdx =
        cat === "tables" ? indexTables(base.data) :
        cat === "columns" ? indexColumns(base.data) :
        cat === "measures" ? indexMeasures(base.data) :
        indexRelationships(base.data);
      const compIdx =
        cat === "tables" ? indexTables(compare.data) :
        cat === "columns" ? indexColumns(compare.data) :
        cat === "measures" ? indexMeasures(compare.data) :
        indexRelationships(compare.data);
      const { rows, summary } = diffIndexed(cat, baseIdx, compIdx, TRACKED[cat]);
      acc.byCategory[cat] = rows;
      acc.totals[cat] = summary;
      return acc;
    },
    { byCategory: {} as Record<DeltaCategory, DeltaRow[]>, totals: {} as Record<DeltaCategory, DeltaSummary> },
  );

  return {
    base: stripData(base),
    compare: stripData(compare),
    byCategory,
    totals,
  };
}

function stripData(s: ModelSnapshot): SnapshotMeta {
  const { data: _data, ...meta } = s;
  return meta;
}

// ── CSV export ─────────────────────────────────────────────────────

export function exportDeltaToCsv(result: DeltaResult): string {
  const escape = (v: unknown): string => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines: string[] = [];
  lines.push(["Category", "Change", "Object", "Property", "Before", "After"].join(","));
  for (const cat of ["tables", "columns", "measures", "relationships"] as DeltaCategory[]) {
    for (const r of result.byCategory[cat]) {
      if (r.kind === "changed" && r.changes && r.changes.length > 0) {
        for (const c of r.changes) {
          lines.push([cat, r.kind, r.label, c.property, c.before, c.after].map(escape).join(","));
        }
      } else {
        lines.push([cat, r.kind, r.label, "", "", ""].map(escape).join(","));
      }
    }
  }
  return lines.join("\n");
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}
