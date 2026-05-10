// TOM-equivalent helpers + scope-object factories.
//
// The original Python rules in `_model_bpa_rules.py` operate on TOM
// objects with property names like `obj.IsAvailableInMDX`, `obj.Parent.IsHidden`,
// `tom.used_in_relationships(object=obj)`. We mirror that surface here so
// the rule predicates can be ported almost verbatim.

import type {
  TableInfo,
  ColumnInfo,
  MeasureInfo,
  PartitionInfo,
  RelationshipInfo,
  HierarchyInfo,
} from "../types";
import type {
  ColumnObj,
  HierarchyObj,
  MeasureObj,
  ModelObj,
  ModelSnapshot,
  PartitionObj,
  RelationshipObj,
  RlsObj,
  RoleObj,
  TableObj,
  CalcItemObj,
} from "./types";

// ── Enum-like string maps ─────────────────────────────────────────

export const DataType = {
  Double: "Double",
  Decimal: "Decimal",
  Int64: "Int64",
  DateTime: "DateTime",
  String: "String",
  Boolean: "Boolean",
  Binary: "Binary",
  Unknown: "Unknown",
};

export const ColumnType = {
  Data: "Data",
  Calculated: "Calculated",
  CalculatedTableColumn: "CalculatedTableColumn",
  RowNumber: "RowNumber",
};

export const ModeType = {
  Import: "Import",
  DirectQuery: "DirectQuery",
  Dual: "Dual",
  Default: "Default",
};

export const PartitionSourceType = {
  M: "M",
  Calculated: "Calculated",
  Query: "Query",
  Entity: "Entity",
  PolicyRange: "PolicyRange",
  CalculationGroup: "CalculationGroup",
};

export const RelationshipEndCardinality = {
  Many: "Many",
  One: "One",
};

export const CrossFilteringBehavior = {
  OneDirection: "OneDirection",
  BothDirections: "BothDirections",
  Automatic: "Automatic",
};

// ── Normalization helpers ─────────────────────────────────────────

const TMDL_DATATYPE_MAP: Record<string, string> = {
  double: "Double",
  decimal: "Decimal",
  int64: "Int64",
  datetime: "DateTime",
  string: "String",
  boolean: "Boolean",
  binary: "Binary",
};

function normalizeDataType(raw: string): string {
  if (!raw) return "Unknown";
  return TMDL_DATATYPE_MAP[raw.toLowerCase()] ?? raw;
}

function normalizeColumnType(raw: string): string {
  if (!raw) return ColumnType.Data;
  const r = raw.toLowerCase();
  if (r === "calculated" || r === "calc") return ColumnType.Calculated;
  if (r === "calculatedtablecolumn") return ColumnType.CalculatedTableColumn;
  if (r === "rownumber") return ColumnType.RowNumber;
  if (r === "data") return ColumnType.Data;
  return raw;
}

function normalizeMode(raw: string): string {
  if (!raw) return ModeType.Default;
  const r = raw.toLowerCase();
  if (r === "import") return ModeType.Import;
  if (r === "directquery" || r === "directlake") return ModeType.DirectQuery;
  if (r === "dual") return ModeType.Dual;
  return raw;
}

function normalizeSourceType(raw: string): string {
  if (!raw) return "";
  const r = raw.toLowerCase();
  if (r === "m") return PartitionSourceType.M;
  if (r === "calculated") return PartitionSourceType.Calculated;
  if (r === "query") return PartitionSourceType.Query;
  if (r === "entity") return PartitionSourceType.Entity;
  if (r === "policyrange") return PartitionSourceType.PolicyRange;
  if (r === "calculationgroup") return PartitionSourceType.CalculationGroup;
  return raw;
}

function normalizeCardinality(raw: string): string {
  if (!raw) return "";
  const r = raw.toLowerCase();
  if (r === "many" || r === "*") return RelationshipEndCardinality.Many;
  if (r === "one" || r === "1") return RelationshipEndCardinality.One;
  return raw;
}

function normalizeCrossFilter(raw: string): string {
  if (!raw) return CrossFilteringBehavior.OneDirection;
  const r = raw.toLowerCase();
  if (r === "bothdirections" || r === "both") return CrossFilteringBehavior.BothDirections;
  if (r === "automatic") return CrossFilteringBehavior.Automatic;
  return CrossFilteringBehavior.OneDirection;
}

// ── Scope-object factories ────────────────────────────────────────

export function buildModelObj(snapshot: ModelSnapshot): ModelObj {
  const tables: TableObj[] = [];
  const tableMap: Record<string, TableObj> = {};

  for (const [tName, t] of Object.entries(snapshot.model.tables)) {
    const tObj = buildTableObjShell(tName, t, snapshot);
    tables.push(tObj);
    tableMap[tName] = tObj;
  }

  // Relationships need table refs — build after tables exist
  const rels: RelationshipObj[] = snapshot.model.relationships.map((r) =>
    buildRelationshipObj(r),
  );

  return {
    __kind: "Model",
    Name: snapshot.model.datasetName ?? "Model",
    Tables: tables,
    Relationships: rels,
  };
}

function buildTableObjShell(name: string, t: TableInfo, snapshot: ModelSnapshot): TableObj {
  const tObj: TableObj = {
    __kind: "Table",
    Name: name,
    IsHidden: t.isHidden,
    Description: t.description,
    DataCategory: snapshot.tableDataCategory[name] ?? "",
    Type: t.type,
    Partitions: [],
    Columns: [],
    Measures: [],
    Hierarchies: [],
    CalculationItems: [],
    raw: t,
  };
  // Children
  for (const [cName, c] of Object.entries(t.columns)) {
    tObj.Columns.push(buildColumnObj(cName, c, tObj));
  }
  for (const [mName, m] of Object.entries(t.measures)) {
    tObj.Measures.push(buildMeasureObj(mName, m, tObj));
  }
  for (const [hName, h] of Object.entries(t.hierarchies)) {
    tObj.Hierarchies.push(buildHierarchyObj(hName, h, tObj));
  }
  for (const p of t.partitions) {
    tObj.Partitions.push(buildPartitionObj(p, tObj));
  }
  // Calc items from snapshot (only set on CalculationGroup tables)
  for (const ci of snapshot.calcItems) {
    if (ci.table === name) {
      tObj.CalculationItems.push({
        __kind: "Calculation Item",
        Name: ci.name,
        Expression: ci.expression,
        Table: tObj,
      });
    }
  }
  return tObj;
}

function buildColumnObj(name: string, c: ColumnInfo, parent: TableObj): ColumnObj {
  const colType = normalizeColumnType(c.type);
  return {
    __kind: colType === ColumnType.Calculated ? "Calculated Column" : "Column",
    Name: name,
    Table: parent,
    Parent: parent,
    DataType: normalizeDataType(c.dataType),
    Type: colType,
    IsHidden: c.isHidden,
    IsKey: c.isKey,
    // TMDL doesn't expose IsAvailableInMDX; default to true (TOM default).
    IsAvailableInMDX: true,
    IsNullable: c.isNullable,
    DataCategory: c.dataCategory,
    DisplayFolder: c.displayFolder,
    FormatString: "",
    SortByColumn: c.sortByColumn || null,
    SummarizeBy: c.summarizeBy,
    EncodingHint: c.encodingHint,
    Expression: c.expression ?? "",
    SourceColumn: name,
    raw: c,
  };
}

function buildMeasureObj(name: string, m: MeasureInfo, parent: TableObj): MeasureObj {
  return {
    __kind: "Measure",
    Name: name,
    Table: parent,
    Parent: parent,
    Expression: m.expression,
    FormatString: m.formatString,
    FormatStringDefinition: null,
    Description: m.description,
    DisplayFolder: m.displayFolder,
    IsHidden: m.isHidden,
    raw: m,
  };
}

function buildHierarchyObj(name: string, h: HierarchyInfo, parent: TableObj): HierarchyObj {
  return {
    __kind: "Hierarchy",
    Name: name,
    Table: parent,
    Parent: parent,
    Levels: h.levels.map((l) => ({ Name: l, Column: { Name: l } })),
    IsHidden: false,
    Description: "",
  };
}

function buildPartitionObj(p: PartitionInfo, parent: TableObj): PartitionObj {
  // TMDL exposes mode-as-sourceType in our parser; the same string is both
  // the source-type (m / calculated / entity) AND mode (import / directlake).
  // We map both meanings from the same field.
  const raw = p.sourceType ?? "";
  const lower = raw.toLowerCase();
  let mode = ModeType.Import;
  let sourceType = PartitionSourceType.M;
  if (lower === "calculated") {
    mode = ModeType.Import;
    sourceType = PartitionSourceType.Calculated;
  } else if (lower === "entity" || lower === "directlake") {
    mode = ModeType.DirectQuery;
    sourceType = PartitionSourceType.Entity;
  } else if (lower === "directquery") {
    mode = ModeType.DirectQuery;
    sourceType = PartitionSourceType.M;
  } else if (lower === "dual") {
    mode = ModeType.Dual;
    sourceType = PartitionSourceType.M;
  } else if (lower === "import" || lower === "m" || lower === "") {
    mode = ModeType.Import;
    sourceType = PartitionSourceType.M;
  } else {
    mode = normalizeMode(raw);
    sourceType = normalizeSourceType(raw);
  }
  return {
    __kind: "Partition",
    Name: p.name,
    Table: parent,
    Parent: parent,
    Mode: mode,
    SourceType: sourceType,
    DataCoverageDefinition: null,
    Source: { Expression: p.expression ?? "" },
    raw: p,
  };
}

function buildRelationshipObj(r: RelationshipInfo): RelationshipObj {
  return {
    __kind: "Relationship",
    Name: `${r.fromTable}[${r.fromColumn}] -> ${r.toTable}[${r.toColumn}]`,
    FromTable: { Name: r.fromTable },
    ToTable: { Name: r.toTable },
    FromColumn: { Name: r.fromColumn, DataType: "" },
    ToColumn: { Name: r.toColumn, DataType: "" },
    FromCardinality: normalizeCardinality(r.multiplicity?.split(":")[0] ?? "Many"),
    ToCardinality: normalizeCardinality(r.multiplicity?.split(":")[1] ?? "One"),
    CrossFilteringBehavior: normalizeCrossFilter(r.crossFilter),
    IsActive: r.isActive,
    SecurityFilteringBehavior: r.securityFiltering,
    RelyOnReferentialIntegrity: r.relyOnRri,
    raw: r,
  };
}

/** Resolve relationship column data types from the model's column table once
 *  all tables/columns are built. Mutates rels in place. */
export function resolveRelationshipDataTypes(model: ModelObj): void {
  const tableByName: Record<string, TableObj> = {};
  for (const t of model.Tables) tableByName[t.Name] = t;
  for (const r of model.Relationships) {
    const fromT = tableByName[r.FromTable.Name];
    const toT = tableByName[r.ToTable.Name];
    if (fromT) {
      const fc = fromT.Columns.find((c) => c.Name === r.FromColumn.Name);
      if (fc) r.FromColumn.DataType = fc.DataType;
    }
    if (toT) {
      const tc = toT.Columns.find((c) => c.Name === r.ToColumn.Name);
      if (tc) r.ToColumn.DataType = tc.DataType;
    }
  }
}

// ── Roles / RLS scope objects ─────────────────────────────────────

export function buildRoleObjs(snapshot: ModelSnapshot): RoleObj[] {
  return snapshot.roles.map((r) => ({
    __kind: "Role",
    Name: r.name,
    ModelPermission: r.modelPermission,
    TablePermissions: r.filters,
  }));
}

export function buildRlsObjs(snapshot: ModelSnapshot): RlsObj[] {
  const out: RlsObj[] = [];
  for (const r of snapshot.roles) {
    for (const f of r.filters) {
      out.push({
        __kind: "Row Level Security",
        Name: `${r.name}::${f.table}`,
        FilterExpression: f.expression,
        Table: { Name: f.table },
        RoleName: r.name,
      });
    }
  }
  return out;
}

// ── TOM helpers ────────────────────────────────────────────────────
// Mirror the methods on the Python `tom` argument used inside rule lambdas.

export class TomContext {
  readonly model: ModelObj;
  readonly snapshot: ModelSnapshot;
  private readonly tableByName: Record<string, TableObj>;
  private readonly allMeasureCache: MeasureObj[];

  constructor(model: ModelObj, snapshot: ModelSnapshot) {
    this.model = model;
    this.snapshot = snapshot;
    this.tableByName = {};
    for (const t of model.Tables) this.tableByName[t.Name] = t;
    this.allMeasureCache = model.Tables.flatMap((t) => t.Measures);
  }

  is_direct_lake(): boolean {
    return this.snapshot.isDirectLake;
  }

  is_direct_lake_using_view(): boolean {
    return this.snapshot.isDirectLakeUsingView;
  }

  is_field_parameter(opts: { table_name: string }): boolean {
    const t = this.tableByName[opts.table_name];
    if (!t) return false;
    // Field parameter tables are calculated tables whose expression contains
    // NAMEOF(…); detect via partition expression.
    return t.Partitions.some(
      (p) =>
        p.SourceType === PartitionSourceType.Calculated &&
        /\bNAMEOF\s*\(/i.test(p.Source.Expression),
    );
  }

  is_calculated_table(opts: { table_name: string }): boolean {
    const t = this.tableByName[opts.table_name];
    if (!t) return false;
    return t.Partitions.some((p) => p.SourceType === PartitionSourceType.Calculated);
  }

  is_hybrid_table(opts: { table_name: string }): boolean {
    const t = this.tableByName[opts.table_name];
    if (!t) return false;
    const modes = new Set(t.Partitions.map((p) => p.Mode));
    return modes.has(ModeType.Import) && modes.has(ModeType.DirectQuery);
  }

  has_hybrid_table(): boolean {
    return this.model.Tables.some((t) => this.is_hybrid_table({ table_name: t.Name }));
  }

  all_partitions(): PartitionObj[] {
    return this.model.Tables.flatMap((t) => t.Partitions);
  }

  all_measures(): MeasureObj[] {
    return this.allMeasureCache;
  }

  all_columns(): ColumnObj[] {
    return this.model.Tables.flatMap((t) => t.Columns);
  }

  all_rls(): { Name: string; Table: { Name: string }; FilterExpression: string }[] {
    return buildRlsObjs(this.snapshot).map((r) => ({
      Name: r.Table.Name,
      Table: r.Table,
      FilterExpression: r.FilterExpression,
    }));
  }

  /** Iterates relationships involving a Table or Column. Object can be a
   *  TableObj or ColumnObj (matching original TOM signature). */
  used_in_relationships(opts: { object: TableObj | ColumnObj }): RelationshipObj[] {
    const obj = opts.object;
    if (obj.__kind === "Table") {
      return this.model.Relationships.filter(
        (r) => r.FromTable.Name === obj.Name || r.ToTable.Name === obj.Name,
      );
    }
    // Column
    const tableName = obj.Table.Name;
    const colName = obj.Name;
    return this.model.Relationships.filter(
      (r) =>
        (r.FromTable.Name === tableName && r.FromColumn.Name === colName) ||
        (r.ToTable.Name === tableName && r.ToColumn.Name === colName),
    );
  }

  used_in_sort_by(opts: { column: ColumnObj }): ColumnObj[] {
    const target = opts.column;
    return this.all_columns().filter(
      (c) =>
        c.Table.Name === target.Table.Name &&
        c.SortByColumn != null &&
        c.SortByColumn === target.Name,
    );
  }

  used_in_hierarchies(opts: { column: ColumnObj }): HierarchyObj[] {
    const target = opts.column;
    const out: HierarchyObj[] = [];
    for (const h of target.Table.Hierarchies) {
      if (h.Levels.some((l) => l.Column.Name === target.Name)) out.push(h);
    }
    return out;
  }

  row_count(opts: { object: TableObj | PartitionObj }): number {
    const obj = opts.object;
    const name = obj.__kind === "Table" ? obj.Name : obj.Table.Name;
    return this.snapshot.rowCounts[name] ?? 0;
  }

  // Calc-deps helpers (best-effort; empty deps → return empty arrays so rules
  // that rely on them simply don't fire instead of producing false positives).
  unqualified_columns(opts: { object: ColumnObj | MeasureObj | CalcItemObj | TableObj; dependencies?: unknown }): unknown[] {
    void opts;
    return [];
  }
  fully_qualified_measures(opts: { object: ColumnObj | MeasureObj | CalcItemObj | TableObj; dependencies?: unknown }): unknown[] {
    void opts;
    return [];
  }
  depends_on(opts: { object: ColumnObj | MeasureObj; dependencies?: unknown }): unknown[] {
    // Without calc-deps we conservatively assume "depends on something" so the
    // "Remove unnecessary columns" rule (which requires `any(depends_on)`)
    // doesn't false-positive on every hidden column.
    void opts;
    return [];
  }
  referenced_by(opts: { object: MeasureObj; dependencies?: unknown }): unknown[] {
    void opts;
    // Without calc-deps, we conservatively assume measures ARE referenced
    // (return non-empty) so we don't flag every hidden measure.
    return [{}];
  }
}
