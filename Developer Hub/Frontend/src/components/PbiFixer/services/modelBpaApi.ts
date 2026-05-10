// Public Model BPA surface — wraps the local `bpa/` engine which is a
// faithful TypeScript port of `sempy_labs.run_model_bpa` (54 rules).
//
// Replaces the original 10-rule client-side stub. The previous public
// shape (BpaSeverity / BpaRule / BpaFinding / BPA_RULES / runModelBpa)
// is preserved so consumers (`ModelBpaPage`, `FixerPage` dispatcher)
// keep compiling unchanged. `objectType` is widened from a closed
// 5-value enum to `string` because the engine emits richer scopes
// (Partition, Hierarchy, Calculation Item, Row Level Security, …).

import type { ModelData } from "../types";
import { runBpa, ruleSlug } from "../bpa/engine";
import { MODEL_BPA_RULES } from "../bpa/rules";
import type { BpaRule as EngineRule } from "../bpa/types";

export type BpaSeverity = "Error" | "Warning" | "Info";

export interface BpaRule {
  id: string;
  category: string;
  severity: BpaSeverity;
  name: string;
  description: string;
  url?: string;
  /** Fixer kind: only the small subset of rules wired to a backend fixer
   *  carries this. The 54 ported rules are read-only for now. */
  fixKind?: string;
}

export interface BpaFinding {
  rule: BpaRule;
  objectType: string;
  objectPath: string;
  detail?: string;
}

// Map sempy_labs rule names → fixKind values that the existing Fixer
// dispatcher already knows how to apply. Names that don't appear here
// stay read-only (no Fix It button).
const FIX_KINDS: Record<string, string> = {
  "Provide format string for measures": "SetMeasureFormat",
  "Visible objects with no description": "SetMeasureDescription",
  "Use the DIVIDE function for division": "RewriteDivideToDIVIDE",
  "Hide foreign keys": "HideColumn",
  "Do not summarize numeric columns": "SetSummarizeByNone",
  "First letter of objects must be capitalized": "CapitalizeMeasure",
};

function publicRule(r: EngineRule): BpaRule {
  return {
    id: ruleSlug(r.name),
    category: r.category,
    severity: r.severity,
    name: r.name,
    description: r.description,
    url: r.url,
    fixKind: FIX_KINDS[r.name],
  };
}

const RULES: BpaRule[] = MODEL_BPA_RULES.map(publicRule);
const RULE_BY_NAME = new Map<string, BpaRule>(RULES.map((r) => [r.name, r]));

export const BPA_RULES: ReadonlyArray<BpaRule> = RULES;

/** Run the full 54-rule BPA against `model` and return findings.
 *  This is a synchronous, in-browser pass against TMDL-derived model
 *  data. Rules that depend on DAX-only extras (calc dependencies, RLS
 *  filter expressions, row counts) degrade silently to no-ops. */
export function runModelBpa(model: ModelData): BpaFinding[] {
  const violations = runBpa(model, MODEL_BPA_RULES);
  const out: BpaFinding[] = [];
  for (const v of violations) {
    const rule = RULE_BY_NAME.get(v.ruleName);
    if (!rule) continue;
    out.push({
      rule,
      objectType: v.objectType,
      objectPath: v.objectName,
    });
  }
  return out;
}
