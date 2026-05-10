// BPA runner — builds the typed `ModelObj` once from a `ModelSnapshot` then
// iterates every rule × every in-scope object, calling each rule's
// predicate. The TomContext cache (set on the snapshot via `__modelObj`)
// avoids rebuilding scope objects on every predicate invocation.

import type { ModelData } from "../types";
import { buildModelObj, buildRlsObjs, resolveRelationshipDataTypes } from "./helpers";
import { MODEL_BPA_RULES } from "./rules";
import type {
  BpaRule,
  BpaScope,
  BpaViolation,
  CalcItemObj,
  ColumnObj,
  HierarchyObj,
  MeasureObj,
  ModelObj,
  ModelSnapshot,
  PartitionObj,
  RelationshipObj,
  RlsObj,
  ScopeObj,
  TableObj,
} from "./types";

function asArray<T>(v: T | T[]): T[] {
  return Array.isArray(v) ? v : [v];
}

function objectsForScope(scope: BpaScope, model: ModelObj, rls: RlsObj[]): ScopeObj[] {
  switch (scope) {
    case "Model":
      return [model];
    case "Table":
      return model.Tables.filter((t) => t.Type !== "CalculationGroup");
    case "Calculated Table":
      return model.Tables.filter((t) =>
        t.Partitions.some((p) => p.SourceType === "Calculated"),
      );
    case "Column":
      return model.Tables.flatMap((t) =>
        t.Columns.filter((c) => c.__kind === "Column"),
      );
    case "Calculated Column":
      return model.Tables.flatMap((t) =>
        t.Columns.filter((c) => c.__kind === "Calculated Column"),
      );
    case "Measure":
      return model.Tables.flatMap((t) => t.Measures);
    case "Hierarchy":
      return model.Tables.flatMap((t) => t.Hierarchies);
    case "Partition":
      return model.Tables.flatMap((t) => t.Partitions);
    case "Relationship":
      return model.Relationships;
    case "Calculation Item":
      return model.Tables.flatMap((t) => t.CalculationItems);
    case "Row Level Security":
      return rls;
    case "Role":
      return []; // No rules currently target Role scope directly.
  }
}

/** Stable, slug-style id derived from rule name + category — used by the
 *  existing `BpaFinding.rule.id` consumers (Fixer page dispatcher etc.). */
export function ruleId(rule: BpaRule): string {
  const slug = rule.name
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
  const cat = rule.category.replace(/\s+/g, "");
  return `${cat}.${slug}`;
}

function objectPathOf(scope: string, obj: ScopeObj): string {
  switch (obj.__kind) {
    case "Model":
      return (obj as ModelObj).Name;
    case "Table":
      return (obj as TableObj).Name;
    case "Column":
    case "Calculated Column":
      return `${(obj as ColumnObj).Table.Name}[${(obj as ColumnObj).Name}]`;
    case "Measure":
      return `${(obj as MeasureObj).Table.Name}[${(obj as MeasureObj).Name}]`;
    case "Hierarchy":
      return `${(obj as HierarchyObj).Table.Name}.${(obj as HierarchyObj).Name}`;
    case "Partition":
      return `${(obj as PartitionObj).Table.Name}.${(obj as PartitionObj).Name}`;
    case "Relationship":
      return (obj as RelationshipObj).Name;
    case "Calculation Item":
      return `${(obj as CalcItemObj).Table.Name}.${(obj as CalcItemObj).Name}`;
    case "Row Level Security":
      return (obj as RlsObj).Name;
    default:
      return scope;
  }
}

/** Run BPA against a `ModelSnapshot`. Returns raw `BpaViolation[]` — the
 *  `modelBpaApi` shim wraps these into the legacy `BpaFinding` shape. */
export function runBpa(snapshot: ModelSnapshot, rules: BpaRule[] = MODEL_BPA_RULES): BpaViolation[] {
  const model = buildModelObj(snapshot);
  resolveRelationshipDataTypes(model);
  // Cache on snapshot so rule predicates that call `__model__(ctx)` reuse it.
  (snapshot as unknown as { __modelObj?: ModelObj }).__modelObj = model;
  const rls = buildRlsObjs(snapshot);

  const out: BpaViolation[] = [];
  for (const rule of rules) {
    const scopes = asArray(rule.scope);
    const seen = new Set<string>();
    for (const scope of scopes) {
      const objs = objectsForScope(scope, model, rls);
      for (const obj of objs) {
        let hit = false;
        try {
          hit = rule.predicate(obj, snapshot);
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn(`[bpa] rule '${rule.name}' threw on ${objectPathOf(scope, obj)}`, err);
          continue;
        }
        if (!hit) continue;
        const path = objectPathOf(scope, obj);
        // Multi-scope rules (e.g. "Objects should not start or end with a
        // space") can match the same object from two scope buckets when scope
        // categories overlap; dedupe by (rule, objectPath).
        const key = `${rule.name}::${path}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({
          category: rule.category,
          ruleName: rule.name,
          severity: rule.severity,
          objectType: scope,
          objectName: path,
          description: rule.description,
          url: rule.url,
        });
      }
    }
  }
  return out;
}

/** Convenience wrapper: build snapshot bridge then evaluate. */
export async function runBpaFromModelData(
  buildSnapshot: () => Promise<ModelSnapshot>,
): Promise<BpaViolation[]> {
  const snap = await buildSnapshot();
  return runBpa(snap);
}

/** Build a thin `ModelSnapshot` from a {@link ModelData} alone, with all
 *  `INFO.*`-derived extras blank. Used as a fallback when DAX calls fail
 *  outright. */
export function snapshotFromModelData(model: ModelData): ModelSnapshot {
  return {
    model,
    tableDataCategory: {},
    roles: [],
    calcItems: [],
    rowCounts: {},
    isDirectLake: false,
    isDirectLakeUsingView: false,
    dependencies: [],
  };
}
