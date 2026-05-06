// WS-C — Model BPA rule engine.
// A minimal TypeScript implementation of the most impactful rules from
// the official Best-Practice-Analyzer ruleset (subset of what
// `sempy_labs.run_model_bpa()` ships). Implemented client-side against
// the already-loaded `ModelData` so the page works without waiting for
// the backend sempy-labs bridge to land (separate backend workstream).
//
// When the backend bridge ships, `ModelBpaPage` can swap to the remote
// result and leave this file in place for offline demos / tests — the
// finding shape is stable.

import type { ModelData, MeasureInfo, ColumnInfo, TableInfo, RelationshipInfo } from "../types";

export type BpaSeverity = "Error" | "Warning" | "Info";

export interface BpaRule {
  id: string;
  category: string;
  severity: BpaSeverity;
  name: string;
  description: string;
  /** Stable kind so the Fixer page knows which automated fix applies.
   *  Backend TOM-write actions are a separate future workstream. */
  fixKind?: string;
}

export interface BpaFinding {
  rule: BpaRule;
  objectType: "Table" | "Column" | "Measure" | "Relationship" | "Model";
  objectPath: string;
  detail?: string;
}

const RULES: BpaRule[] = [
  {
    id: "DAX.MeasureNoFormat",
    category: "DAX",
    severity: "Warning",
    name: "Measures without a format string",
    description: "Measures should declare an explicit format string so client tools render values consistently.",
    fixKind: "SetMeasureFormat",
  },
  {
    id: "DAX.MeasureNoDescription",
    category: "Documentation",
    severity: "Info",
    name: "Measures without a description",
    description: "Measures should have a description explaining their business meaning.",
    fixKind: "SetMeasureDescription",
  },
  {
    id: "DAX.MeasureDivideAnti",
    category: "DAX",
    severity: "Warning",
    name: "Avoid division with '/' operator",
    description: "Use DIVIDE() to safely handle division-by-zero instead of the '/' operator.",
    fixKind: "RewriteDivideToDIVIDE",
  },
  {
    id: "Model.ColumnKeyHidden",
    category: "Modeling",
    severity: "Warning",
    name: "Key columns should be hidden",
    description: "Primary key columns used in relationships should be hidden from report authors.",
    fixKind: "HideColumn",
  },
  {
    id: "Model.ColumnSummarizeBySum",
    category: "Modeling",
    severity: "Warning",
    name: "Numeric columns with implicit SUM",
    description: "Numeric columns default to SummarizeBy=Sum; set it to None unless the column is meant to aggregate automatically.",
    fixKind: "SetSummarizeByNone",
  },
  {
    id: "Perf.TooManyColumns",
    category: "Performance",
    severity: "Info",
    name: "Tables with many columns",
    description: "Tables with more than 50 columns are harder to maintain and may hurt Vertipaq compression.",
  },
  {
    id: "Model.RelationshipInactive",
    category: "Modeling",
    severity: "Info",
    name: "Inactive relationships",
    description: "Inactive relationships need USERELATIONSHIP() in DAX; confirm each one is intentional.",
  },
  {
    id: "Model.RelationshipManyToMany",
    category: "Modeling",
    severity: "Warning",
    name: "Many-to-many relationships",
    description: "Many-to-many relationships can be slow; prefer a bridge table or a single-direction relationship.",
  },
  {
    id: "Naming.CamelCaseMeasure",
    category: "Naming",
    severity: "Info",
    name: "Measure names should not start with a lowercase letter",
    description: "Measure names are user-facing; start each name with an uppercase letter.",
    fixKind: "CapitalizeMeasure",
  },
  {
    id: "Naming.TableSpace",
    category: "Naming",
    severity: "Info",
    name: "Avoid spaces in technical table names",
    description: "Tables exposed to tooling and DAX benefit from no embedded spaces; hidden tables can use them freely.",
  },
];

export const BPA_RULES: ReadonlyArray<BpaRule> = RULES;

/** Run the built-in rule set against `model` and return findings. */
export function runModelBpa(model: ModelData): BpaFinding[] {
  const findings: BpaFinding[] = [];
  const rule = (id: string) => RULES.find((r) => r.id === id)!;

  // Pre-compute the set of columns participating in relationships — used
  // by several modelling rules below.
  const relCols = new Set<string>();
  for (const r of model.relationships) {
    relCols.add(`${r.fromTable}[${r.fromColumn}]`);
    relCols.add(`${r.toTable}[${r.toColumn}]`);
  }

  for (const [tName, t] of Object.entries(model.tables)) {
    // Column-level rules
    for (const [cName, c] of Object.entries(t.columns)) {
      const path = `${tName}[${cName}]`;
      if (relCols.has(path) && !c.isHidden) {
        findings.push({ rule: rule("Model.ColumnKeyHidden"), objectType: "Column", objectPath: path });
      }
      const numeric = ["Int64", "Double", "Decimal", "Currency"].includes(c.dataType);
      if (numeric && (c.summarizeBy || "").toLowerCase() === "sum" && !c.isHidden) {
        findings.push({
          rule: rule("Model.ColumnSummarizeBySum"),
          objectType: "Column",
          objectPath: path,
          detail: `SummarizeBy = ${c.summarizeBy}`,
        });
      }
    }

    // Measure-level rules
    for (const [mName, m] of Object.entries(t.measures)) {
      const path = `${tName}[${mName}]`;
      if (!m.formatString || m.formatString.trim().length === 0) {
        findings.push({ rule: rule("DAX.MeasureNoFormat"), objectType: "Measure", objectPath: path });
      }
      if (!m.description || m.description.trim().length === 0) {
        findings.push({ rule: rule("DAX.MeasureNoDescription"), objectType: "Measure", objectPath: path });
      }
      if (/[^\w]\/[^\w]/.test(m.expression || "") && !/DIVIDE\s*\(/i.test(m.expression || "")) {
        findings.push({
          rule: rule("DAX.MeasureDivideAnti"),
          objectType: "Measure",
          objectPath: path,
          detail: "Contains '/' without DIVIDE()",
        });
      }
      if (mName && mName[0] !== mName[0].toUpperCase() && /^[a-z]/.test(mName)) {
        findings.push({ rule: rule("Naming.CamelCaseMeasure"), objectType: "Measure", objectPath: path });
      }
    }

    // Table-level rules
    const colCount = Object.keys(t.columns).length;
    if (colCount > 50) {
      findings.push({
        rule: rule("Perf.TooManyColumns"),
        objectType: "Table",
        objectPath: tName,
        detail: `${colCount} columns`,
      });
    }
    if (!t.isHidden && /\s/.test(tName)) {
      findings.push({ rule: rule("Naming.TableSpace"), objectType: "Table", objectPath: tName });
    }
  }

  // Relationship rules
  for (const r of model.relationships) {
    const path = `${r.fromTable}[${r.fromColumn}] -> ${r.toTable}[${r.toColumn}]`;
    if (r.isActive === false) {
      findings.push({ rule: rule("Model.RelationshipInactive"), objectType: "Relationship", objectPath: path });
    }
    if ((r.multiplicity || "").toLowerCase().includes("many_to_many") || r.multiplicity === "m:m") {
      findings.push({ rule: rule("Model.RelationshipManyToMany"), objectType: "Relationship", objectPath: path });
    }
  }

  return findings;
}
