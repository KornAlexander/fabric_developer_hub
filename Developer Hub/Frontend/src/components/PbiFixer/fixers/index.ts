// WS-E — TS-native fixer registry.
//
// Each fixer implements a `scan` over already-loaded `ReportData` /
// `ModelData` and returns findings + an optional textual diff preview.
// `apply` is intentionally stubbed for the first cut — writing back via
// `fabric_updateDefinition` lands after the safety-rail UX (Apply switch
// + diff review + confirm dialog) ships in v0.14. Backend-bridge fixers
// (`Fix_UpgradeToPbir`, `Fix_DiscourageImplicitMeasures`) land with the
// Python sempy-labs bridge.

import type { ReportData } from "../types/report";
import type { ModelData } from "../types";

export type FixerScope = "report" | "sm";
export type FixerMode = "ts" | "backend";

export interface FixerFinding {
  /** Short label used in the scan log + preselection matching. */
  objectPath: string;
  detail?: string;
  /** Optional before/after snippet rendered in the diff preview. */
  before?: string;
  after?: string;
}

export interface FixerResult {
  findings: FixerFinding[];
  /** Combined diff text — shown in the collapsible preview panel. */
  diff?: string;
  /** True only when `apply=true` was requested AND the write-back
   *  actually executed. For v0.14 this stays false for every fixer. */
  applied: boolean;
  /** Extra human-readable log lines emitted during scan/apply. */
  log: string[];
}

export interface FixerContext {
  report?: ReportData;
  model?: ModelData;
}

export interface Fixer {
  id: string;
  title: string;
  scope: FixerScope;
  mode: FixerMode;
  /** Rule ids from Model/Report BPA that should preselect this fixer
   *  when the user clicks "Fix it". */
  bpaRuleIds?: string[];
  scan(ctx: FixerContext): FixerResult;
  apply(ctx: FixerContext): Promise<FixerResult>;
}

/* ------------------------------------------------------------------ */
/* Report-side fixers                                                  */
/* ------------------------------------------------------------------ */

const PIE_TYPES = new Set(["pieChart", "donutChart", "funnel"]);

export const fixPieChart: Fixer = {
  id: "Fix_PieChart",
  title: "Replace pie / donut / funnel charts with bar charts",
  scope: "report",
  mode: "ts",
  bpaRuleIds: ["Report.PieOrDonut"],
  scan({ report }) {
    const findings: FixerFinding[] = [];
    const diffs: string[] = [];
    if (!report) {
      return { findings, applied: false, log: ["No report loaded."] };
    }
    for (const [pName, p] of Object.entries(report.pages)) {
      for (const [vKey, v] of Object.entries(p.visuals)) {
        if (PIE_TYPES.has(v.type)) {
          findings.push({
            objectPath: `${p.displayName || pName} › ${v.title || vKey}`,
            detail: v.type,
            before: `"visualType": "${v.type}"`,
            after:  `"visualType": "barChart"`,
          });
          diffs.push(`- pages/${pName}/visuals/${vKey}/visual.json\n    - "visualType": "${v.type}"\n    + "visualType": "barChart"`);
        }
      }
    }
    return {
      findings,
      diff: diffs.join("\n"),
      applied: false,
      log: [`Scanned ${Object.keys(report.pages).length} page(s) · ${findings.length} pie/donut/funnel visual(s) to replace.`],
    };
  },
  async apply(ctx) {
    const r = this.scan(ctx);
    r.log.push("Apply not yet wired — TS write-back via fabric_updateDefinition lands next.");
    return r;
  },
};

export const fixBarChart: Fixer = {
  id: "Fix_BarChart",
  title: "Standardize bar-chart formatting",
  scope: "report",
  mode: "ts",
  scan({ report }) {
    const findings: FixerFinding[] = [];
    if (!report) return { findings, applied: false, log: ["No report loaded."] };
    for (const [pName, p] of Object.entries(report.pages)) {
      for (const [vKey, v] of Object.entries(p.visuals)) {
        if (v.type === "barChart" || v.type === "clusteredBarChart") {
          findings.push({
            objectPath: `${p.displayName || pName} › ${v.title || vKey}`,
            detail: v.type,
            before: "dataLabels: unset · legend: default",
            after:  "dataLabels: on · legend: hidden (single series)",
          });
        }
      }
    }
    return {
      findings,
      applied: false,
      log: [`Found ${findings.length} bar chart(s) that would be standardized.`],
    };
  },
  async apply(ctx) {
    const r = this.scan(ctx);
    r.log.push("Apply not yet wired — TS write-back via fabric_updateDefinition lands next.");
    return r;
  },
};

export const fixColumnChart: Fixer = {
  id: "Fix_ColumnChart",
  title: "Standardize column-chart formatting",
  scope: "report",
  mode: "ts",
  scan({ report }) {
    const findings: FixerFinding[] = [];
    if (!report) return { findings, applied: false, log: ["No report loaded."] };
    for (const [pName, p] of Object.entries(report.pages)) {
      for (const [vKey, v] of Object.entries(p.visuals)) {
        if (v.type === "columnChart" || v.type === "clusteredColumnChart") {
          findings.push({
            objectPath: `${p.displayName || pName} › ${v.title || vKey}`,
            detail: v.type,
            before: "dataLabels: unset · legend: default",
            after:  "dataLabels: on · legend: hidden (single series)",
          });
        }
      }
    }
    return {
      findings,
      applied: false,
      log: [`Found ${findings.length} column chart(s) that would be standardized.`],
    };
  },
  async apply(ctx) {
    const r = this.scan(ctx);
    r.log.push("Apply not yet wired — TS write-back via fabric_updateDefinition lands next.");
    return r;
  },
};

const TARGET_W = 1280;
const TARGET_H = 720;

export const fixPageSize: Fixer = {
  id: "Fix_PageSize",
  title: `Set page size to Full HD 16:9 (${TARGET_W}×${TARGET_H})`,
  scope: "report",
  mode: "ts",
  bpaRuleIds: ["Report.PageSizeNonStandard"],
  scan({ report }) {
    const findings: FixerFinding[] = [];
    const diffs: string[] = [];
    if (!report) return { findings, applied: false, log: ["No report loaded."] };
    for (const [pName, p] of Object.entries(report.pages)) {
      if ((p.width !== TARGET_W || p.height !== TARGET_H) && p.width > 0 && p.height > 0) {
        findings.push({
          objectPath: p.displayName || pName,
          detail: `${p.width}×${p.height} → ${TARGET_W}×${TARGET_H}`,
          before: `"width": ${p.width}, "height": ${p.height}`,
          after:  `"width": ${TARGET_W}, "height": ${TARGET_H}`,
        });
        diffs.push(`- pages/${pName}/page.json\n    - ${p.width}×${p.height}\n    + ${TARGET_W}×${TARGET_H}`);
      }
    }
    return {
      findings,
      diff: diffs.join("\n"),
      applied: false,
      log: [`${findings.length} page(s) not at ${TARGET_W}×${TARGET_H}.`],
    };
  },
  async apply(ctx) {
    const r = this.scan(ctx);
    r.log.push("Apply not yet wired — TS write-back via fabric_updateDefinition lands next.");
    return r;
  },
};

/* ------------------------------------------------------------------ */
/* Semantic-model fixers (backend-bridge placeholders)                 */
/* ------------------------------------------------------------------ */

export const fixUpgradeToPbir: Fixer = {
  id: "Fix_UpgradeToPbir",
  title: "Upgrade report from PBIRLegacy to PBIR",
  scope: "report",
  mode: "backend",
  scan({ report }) {
    const findings: FixerFinding[] = [];
    if (report && report.format && report.format !== "PBIR") {
      findings.push({
        objectPath: `Report ${report.reportId}`,
        detail: `format: ${report.format}`,
      });
    }
    return {
      findings,
      applied: false,
      log: findings.length
        ? [`Report is in ${report?.format} format — upgrade available.`]
        : ["Report already in PBIR format — nothing to do."],
    };
  },
  async apply(ctx) {
    const r = this.scan(ctx);
    r.log.push("Backend bridge (sempy-labs) not yet wired — Apply deferred.");
    return r;
  },
};

export const fixDiscourageImplicitMeasures: Fixer = {
  id: "Fix_DiscourageImplicitMeasures",
  title: "Discourage implicit measures on the semantic model",
  scope: "sm",
  mode: "backend",
  scan({ model }) {
    const findings: FixerFinding[] = [];
    if (!model) return { findings, applied: false, log: ["No model loaded."] };
    const numericTypes = new Set(["Int64", "Double", "Decimal", "Currency"]);
    for (const [tName, t] of Object.entries(model.tables ?? {})) {
      for (const [cName, c] of Object.entries(t.columns ?? {})) {
        if (numericTypes.has(c.dataType) && c.summarizeBy && c.summarizeBy !== "None") {
          findings.push({
            objectPath: `${tName}[${cName}]`,
            detail: `summarizeBy=${c.summarizeBy}`,
          });
        }
      }
    }
    return {
      findings,
      applied: false,
      log: [`${findings.length} numeric column(s) would be flagged.`],
    };
  },
  async apply(ctx) {
    const r = this.scan(ctx);
    r.log.push("Backend bridge (sempy-labs TOM write) not yet wired — Apply deferred.");
    return r;
  },
};

export const FIXERS: readonly Fixer[] = Object.freeze([
  fixPieChart,
  fixBarChart,
  fixColumnChart,
  fixPageSize,
  fixUpgradeToPbir,
  fixDiscourageImplicitMeasures,
]);

export function findFixerForBpaRule(ruleId: string): Fixer | undefined {
  return FIXERS.find((f) => f.bpaRuleIds?.includes(ruleId));
}
