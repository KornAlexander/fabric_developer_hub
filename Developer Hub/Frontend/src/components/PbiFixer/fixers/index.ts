// WS-E v0.41 — TS fixer registry, backend-driven.
//
// Each fixer's ``scan`` / ``apply`` delegates to the backend
// ``/api/pbi-fixer/fixers/apply`` endpoint, which round-trips the
// model TMDL or report PBIR JSON via Fabric REST. The frontend
// registry just describes the catalog (id / title / scope / BPA
// rule mapping) — the heavy lifting lives in
// ``Backend/src/services/agenthub/pbi_fixer_handlers.py``.
//
// This replaces the v0.14 TS-native scan-only registry. The historical
// ``ReportData`` / ``ModelData`` arguments are still accepted on the
// context but unused for backend fixers.

import type { ReportData } from "../types/report";
import type { ModelData } from "../types";
import type { PbiAuth } from "../services/fabricApi";
import { runFixer } from "../services/fixersApi";

export type FixerScope = "report" | "sm";
export type FixerMode = "backend" | "stub";

export interface FixerFinding {
  objectPath: string;
  detail?: string;
  before?: string;
  after?: string;
}

export interface FixerResult {
  findings: FixerFinding[];
  diff?: string;
  applied: boolean;
  log: string[];
}

export interface FixerContext {
  auth?: PbiAuth;
  workspaceId?: string;
  datasetId?: string;
  reportId?: string;
  /** Kept for backward-compat with WS-E v0.14 callers; unused for
   *  backend-mode fixers. */
  report?: ReportData;
  model?: ModelData;
}

export interface Fixer {
  id: string;
  title: string;
  scope: FixerScope;
  mode: FixerMode;
  bpaRuleIds?: string[];
  scan(ctx: FixerContext): Promise<FixerResult>;
  apply(ctx: FixerContext): Promise<FixerResult>;
}

function emptyResult(log: string[]): FixerResult {
  return { findings: [], applied: false, log };
}

function buildDiff(findings: FixerFinding[]): string {
  return findings
    .filter((f) => f.before || f.after)
    .map((f) => `- ${f.objectPath}\n    - ${f.before ?? ""}\n    + ${f.after ?? ""}`)
    .join("\n");
}

/** Build a backend-delegating Fixer from minimal metadata. */
function backendFixer(meta: {
  id: string;
  title: string;
  scope: FixerScope;
  bpaRuleIds?: string[];
}): Fixer {
  const call = async (ctx: FixerContext, scanOnly: boolean): Promise<FixerResult> => {
    if (!ctx.auth || !ctx.workspaceId) return emptyResult(["No auth / workspace."]);
    if (meta.scope === "sm" && !ctx.datasetId) return emptyResult(["Pick a semantic model first."]);
    if (meta.scope === "report" && !ctx.reportId) return emptyResult(["Pick a report first."]);
    try {
      const res = await runFixer(ctx.auth, {
        workspaceId: ctx.workspaceId,
        fixerId: meta.id,
        scanOnly,
        datasetId: ctx.datasetId,
        reportId: ctx.reportId,
      });
      const findings: FixerFinding[] = res.findings.map((f) => ({
        objectPath: f.objectPath,
        detail: f.detail ?? undefined,
        before: f.before ?? undefined,
        after: f.after ?? undefined,
      }));
      const log = [
        `${scanOnly ? "Scan" : "Apply"} OK — ${findings.length} finding(s)${res.applied ? ", written back to Fabric." : "."}`,
        ...res.log,
      ];
      return {
        findings,
        diff: buildDiff(findings),
        applied: res.applied,
        log,
      };
    } catch (e) {
      return emptyResult([`ERROR: ${e instanceof Error ? e.message : String(e)}`]);
    }
  };
  return {
    id: meta.id,
    title: meta.title,
    scope: meta.scope,
    mode: "backend",
    bpaRuleIds: meta.bpaRuleIds,
    scan: (ctx) => call(ctx, true),
    apply: (ctx) => call(ctx, false),
  };
}

/* ------------------------------------------------------------------ */
/* Catalog                                                             */
/* ------------------------------------------------------------------ */

export const fixPieChart = backendFixer({
  id: "Fix_PieChart",
  title: "Replace pie / donut / funnel charts with bar charts",
  scope: "report",
  bpaRuleIds: ["Report.PieOrDonut"],
});

export const fixPageSize = backendFixer({
  id: "Fix_PageSize",
  title: "Set page size to Full HD 16:9 (1280×720)",
  scope: "report",
  bpaRuleIds: ["Report.PageSizeNonStandard"],
});

export const fixHideVisualFilters = backendFixer({
  id: "Fix_HideVisualFilters",
  title: "Hide every visual-level filter from the filter pane",
  scope: "report",
});

export const fixDisableShowItemsNoData = backendFixer({
  id: "Fix_DisableShowItemsNoData",
  title: "Disable 'Show items with no data' on visual projections",
  scope: "report",
});

export const fixRemoveUnusedCustomVisuals = backendFixer({
  id: "Fix_RemoveUnusedCustomVisuals",
  title: "Remove declared custom visuals that aren't used on any page",
  scope: "report",
});

export const fixUpgradeToPbir: Fixer = {
  id: "Fix_UpgradeToPbir",
  title: "Upgrade report from PBIRLegacy to PBIR",
  scope: "report",
  mode: "stub",
  async scan({ report }) {
    const findings: FixerFinding[] = [];
    if (report && report.format && report.format !== "PBIR") {
      findings.push({ objectPath: `Report ${report.reportId}`, detail: `format: ${report.format}` });
    }
    return {
      findings,
      applied: false,
      log: findings.length
        ? [`Report is in ${report?.format} format — upgrade requires the sempy-labs backend bridge.`]
        : ["Report already in PBIR format — nothing to do."],
    };
  },
  async apply(ctx) {
    return this.scan(ctx);
  },
};

export const fixDiscourageImplicitMeasures = backendFixer({
  id: "Fix_DiscourageImplicitMeasures",
  title: "Discourage implicit measures (set summarizeBy: none on numeric columns)",
  scope: "sm",
});

export const fixDoNotSummarize = backendFixer({
  id: "Fix_DoNotSummarize",
  title: "Do not summarize numeric columns (alias of DiscourageImplicitMeasures)",
  scope: "sm",
});

export const fixFloatingPointDataType = backendFixer({
  id: "Fix_FloatingPointDataType",
  title: "Replace Double data type with Decimal on columns",
  scope: "sm",
});

export const fixHideForeignKeys = backendFixer({
  id: "Fix_HideForeignKeys",
  title: "Hide foreign-key columns (relationship 'from' side)",
  scope: "sm",
});

export const fixIsAvailableInMdxFalse = backendFixer({
  id: "Fix_IsAvailableInMdxFalse",
  title: "Set isAvailableInMdx: false on hidden columns",
  scope: "sm",
});

export const fixMeasureFormat = backendFixer({
  id: "Fix_MeasureFormat",
  title: "Default measure format to '#,0' when missing",
  scope: "sm",
});

export const fixPercentageFormat = backendFixer({
  id: "Fix_PercentageFormat",
  title: "Apply percentage format to measures whose name contains '%'",
  scope: "sm",
});

export const fixWholeNumberFormat = backendFixer({
  id: "Fix_WholeNumberFormat",
  title: "Default Int64 column format to '#,0' when missing",
  scope: "sm",
});

export const FIXERS: readonly Fixer[] = Object.freeze([
  // Report (backend)
  fixPieChart,
  fixPageSize,
  fixHideVisualFilters,
  fixDisableShowItemsNoData,
  fixRemoveUnusedCustomVisuals,
  fixUpgradeToPbir,
  // Semantic model (backend)
  fixDiscourageImplicitMeasures,
  fixDoNotSummarize,
  fixFloatingPointDataType,
  fixHideForeignKeys,
  fixIsAvailableInMdxFalse,
  fixMeasureFormat,
  fixPercentageFormat,
  fixWholeNumberFormat,
]);

export function findFixerForBpaRule(ruleId: string): Fixer | undefined {
  return FIXERS.find((f) => f.bpaRuleIds?.includes(ruleId));
}
