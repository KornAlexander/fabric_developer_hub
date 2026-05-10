// Build a typed ModelSnapshot from a `ModelData` (already loaded via the
// existing `loadModelData` Fabric REST helper) plus best-effort `INFO.*`
// DAX queries for things `loadModelData` doesn't expose: per-table data
// category, RLS roles + permissions, and calc-dependency graph.
//
// Every DAX call is wrapped in `safeDax` — when the tenant rejects the
// `INFO.*` family (some Fabric capacities do), the snapshot still loads
// with empty extras and rules that depend on those extras simply produce
// no findings instead of failing.

import type { PbiAuth } from "../services/fabricApi";
import { executeDax } from "../services/fabricApi";
import type { ModelData } from "../types";
import type { CalcDependency, ModelSnapshot } from "./types";

function cell<T = unknown>(row: Record<string, unknown>, ...names: string[]): T | undefined {
  for (const k of names) {
    if (k in row) return row[k] as T;
    const bracket = `[${k}]`;
    if (bracket in row) return row[bracket] as T;
  }
  const lower = names.map((n) => n.toLowerCase());
  for (const k of Object.keys(row)) {
    const stripped = k.replace(/^\[|\]$/g, "").toLowerCase();
    if (lower.includes(stripped)) return row[k] as T;
  }
  return undefined;
}

const toStr = (v: unknown): string => (v == null ? "" : String(v));
const toNum = (v: unknown): number => {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
};

async function safeDax(
  auth: PbiAuth,
  ws: string,
  ds: string,
  query: string,
): Promise<Record<string, unknown>[]> {
  try {
    return await executeDax(auth, ws, ds, query);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn(`[bpa/snapshot] DAX query failed (returning empty):`, query, err);
    return [];
  }
}

export async function buildModelSnapshot(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
  model: ModelData,
): Promise<ModelSnapshot> {
  const [infoTables, infoRoles, infoTablePerms, infoCalcItems, infoCalcDeps] = await Promise.all([
    safeDax(auth, workspaceId, datasetId, "EVALUATE INFO.TABLES()"),
    safeDax(auth, workspaceId, datasetId, "EVALUATE INFO.ROLES()"),
    safeDax(auth, workspaceId, datasetId, "EVALUATE INFO.TABLEPERMISSIONS()"),
    safeDax(auth, workspaceId, datasetId, "EVALUATE INFO.CALCULATIONITEMS()"),
    safeDax(auth, workspaceId, datasetId, "EVALUATE INFO.CALCDEPENDENCY()"),
  ]);

  // ── Build TableID → Name lookup for cross-referencing INFO.* tables ──
  const tableIdToName: Record<string, string> = {};
  const tableDataCategory: Record<string, string> = {};
  for (const row of infoTables) {
    const id = toStr(cell(row, "ID"));
    const name = toStr(cell(row, "Name"));
    if (id && name) tableIdToName[id] = name;
    const cat = toStr(cell(row, "DataCategory"));
    if (name && cat) tableDataCategory[name] = cat;
  }

  // ── Roles ──
  const roleIdToName: Record<string, { name: string; modelPermission: string }> = {};
  const roles: ModelSnapshot["roles"] = infoRoles.map((row) => {
    const id = toStr(cell(row, "ID"));
    const name = toStr(cell(row, "Name"));
    const mp = toStr(cell(row, "ModelPermission"));
    if (id) roleIdToName[id] = { name, modelPermission: mp };
    return { name, modelPermission: mp, filters: [] as { table: string; expression: string }[] };
  });
  for (const row of infoTablePerms) {
    const roleId = toStr(cell(row, "RoleID"));
    const tableId = toStr(cell(row, "TableID"));
    const expr = toStr(cell(row, "FilterExpression", "Expression"));
    if (!expr) continue;
    const roleEntry = roles.find((r) => r.name === roleIdToName[roleId]?.name);
    if (roleEntry) {
      roleEntry.filters.push({
        table: tableIdToName[tableId] ?? tableId,
        expression: expr,
      });
    }
  }

  // ── Calculation items ──
  const calcItems: ModelSnapshot["calcItems"] = [];
  for (const row of infoCalcItems) {
    const expr = toStr(cell(row, "Expression"));
    const name = toStr(cell(row, "Name"));
    // Calc items belong to a CalculationGroup — relationship via TableID.
    const tableId = toStr(cell(row, "TableID"));
    const tableName = tableIdToName[tableId] ?? "";
    calcItems.push({ table: tableName, name, expression: expr });
  }

  // ── Calc dependencies (best-effort) ──
  const dependencies: CalcDependency[] = infoCalcDeps.map((row) => ({
    tableName: toStr(cell(row, "TABLE", "Table")),
    objectName: toStr(cell(row, "OBJECT", "Object")),
    objectType: toStr(cell(row, "OBJECT_TYPE", "ObjectType")),
    expression: toStr(cell(row, "EXPRESSION", "Expression")),
    referencedTable: toStr(cell(row, "REFERENCED_TABLE", "ReferencedTable")),
    referencedObject: toStr(cell(row, "REFERENCED_OBJECT", "ReferencedObject")),
    referencedObjectType: toStr(cell(row, "REFERENCED_OBJECT_TYPE", "ReferencedObjectType")),
  }));

  // ── Direct Lake / hybrid heuristics from partition modes ──
  let isDirectLake = false;
  let isDirectLakeUsingView = false;
  for (const t of Object.values(model.tables)) {
    for (const p of t.partitions ?? []) {
      const st = (p.sourceType ?? "").toLowerCase();
      const mode = (p.sourceType ?? "").toLowerCase(); // TMDL exposes mode-as-sourceType
      if (st.includes("entity") || mode.includes("directlake") || mode === "entity") {
        isDirectLake = true;
      }
      if ((p.expression ?? "").toLowerCase().includes("createview")) {
        isDirectLakeUsingView = true;
      }
    }
  }

  // Row counts — best-effort COUNTROWS per table; cheap on Direct Lake but
  // potentially expensive on Import. Skip in v1; stats-based rules will
  // gracefully no-op when rowCounts is empty.
  const rowCounts: Record<string, number> = {};
  // Reserved for `extended=true` mode — left empty intentionally.
  void toNum; // silence unused-var lint when stats branch not taken

  return {
    model,
    tableDataCategory,
    roles,
    calcItems,
    rowCounts,
    isDirectLake,
    isDirectLakeUsingView,
    dependencies,
  };
}
