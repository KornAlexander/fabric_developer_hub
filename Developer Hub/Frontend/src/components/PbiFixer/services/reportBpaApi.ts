// WS-D — Report BPA rule engine (client-side).
//
// Mirrors `modelBpaApi.ts` for the Report side. Operates on a loaded
// `ReportData` (pages + visuals + visualObjects). The `sempy_labs.report.run_report_bpa`
// backend bridge will replace this once the Python dep lands; the
// finding shape is kept compatible so the swap-in is zero-touch.

import type { ReportData, PageInfo, VisualInfo } from "../types/report";

export type BpaSeverity = "Error" | "Warning" | "Info";
export type BpaFixKind =
  | "FixPieChart"
  | "FixPageSize"
  | "FixHiddenPage"
  | "FixVisualTitle"
  | "FixOffCanvasVisual"
  | "FixOverlappingVisuals";

export interface BpaRule {
  id: string;
  category: string;
  severity: BpaSeverity;
  name: string;
  description: string;
  fixKind?: BpaFixKind;
}

export type BpaObjectType = "Report" | "Page" | "Visual";

export interface BpaFinding {
  rule: BpaRule;
  objectType: BpaObjectType;
  objectPath: string;
  detail?: string;
}

// Visual types that should be avoided (pie/donut/funnel).
const DISCOURAGED_VISUALS = new Set(["pieChart", "donutChart", "funnel"]);

// Full HD target (Fix_PageSize).
const TARGET_W = 1280;
const TARGET_H = 720;

export const BPA_RULES: readonly BpaRule[] = Object.freeze([
  {
    id: "Report.PieOrDonut",
    category: "Visualization",
    severity: "Warning",
    name: "Avoid pie / donut / funnel charts",
    description: "Pie, donut and funnel charts are hard to read. Prefer bar or column charts.",
    fixKind: "FixPieChart",
  },
  {
    id: "Report.PageSizeNonStandard",
    category: "Layout",
    severity: "Info",
    name: "Non-standard page size",
    description: `Page size should be Full HD 16:9 (${TARGET_W}×${TARGET_H}).`,
    fixKind: "FixPageSize",
  },
  {
    id: "Report.HiddenPage",
    category: "Layout",
    severity: "Info",
    name: "Hidden page",
    description: "The page is hidden. Confirm it should remain hidden from end users.",
    fixKind: "FixHiddenPage",
  },
  {
    id: "Report.EmptyPage",
    category: "Layout",
    severity: "Warning",
    name: "Empty page",
    description: "The page contains no visuals.",
  },
  {
    id: "Report.VisualHidden",
    category: "Layout",
    severity: "Info",
    name: "Hidden visual",
    description: "Visual is hidden. Ensure it's intentional (e.g. used for bookmarks).",
  },
  {
    id: "Report.VisualNoTitle",
    category: "Accessibility",
    severity: "Info",
    name: "Visual without a title",
    description: "Visuals should have a descriptive title for accessibility.",
    fixKind: "FixVisualTitle",
  },
  {
    id: "Report.VisualOffCanvas",
    category: "Layout",
    severity: "Warning",
    name: "Visual placed off-canvas",
    description: "Visual sits partially or fully outside the page boundary.",
    fixKind: "FixOffCanvasVisual",
  },
  {
    id: "Report.TooManyVisuals",
    category: "Performance",
    severity: "Warning",
    name: "Too many visuals on a page",
    description: "More than 15 visuals on one page hurts rendering performance.",
  },
  {
    id: "Report.VisualTooSmall",
    category: "Accessibility",
    severity: "Info",
    name: "Visual too small",
    description: "Visuals smaller than 80×60 px are hard to read.",
  },
  {
    id: "Report.OverlappingVisuals",
    category: "Layout",
    severity: "Warning",
    name: "Overlapping visuals",
    description: "Two or more visuals overlap on the same page.",
    fixKind: "FixOverlappingVisuals",
  },
]);

const ruleById = (id: string): BpaRule => BPA_RULES.find((r) => r.id === id)!;

function rectsOverlap(a: VisualInfo, b: VisualInfo): boolean {
  return !(
    a.x + a.width <= b.x ||
    b.x + b.width <= a.x ||
    a.y + a.height <= b.y ||
    b.y + b.height <= a.y
  );
}

export function runReportBpa(report: ReportData): BpaFinding[] {
  const findings: BpaFinding[] = [];
  const pageNames = Object.keys(report.pages ?? {});

  for (const pName of pageNames) {
    const page: PageInfo = report.pages[pName];
    const pagePath = page.displayName || pName;

    if ((page.width !== TARGET_W || page.height !== TARGET_H) && page.width > 0 && page.height > 0) {
      findings.push({
        rule: ruleById("Report.PageSizeNonStandard"),
        objectType: "Page",
        objectPath: pagePath,
        detail: `${page.width}×${page.height}`,
      });
    }

    if (page.hidden) {
      findings.push({
        rule: ruleById("Report.HiddenPage"),
        objectType: "Page",
        objectPath: pagePath,
      });
    }

    const visuals = Object.entries(page.visuals ?? {});
    if (visuals.length === 0) {
      findings.push({
        rule: ruleById("Report.EmptyPage"),
        objectType: "Page",
        objectPath: pagePath,
      });
    }

    if (visuals.length > 15) {
      findings.push({
        rule: ruleById("Report.TooManyVisuals"),
        objectType: "Page",
        objectPath: pagePath,
        detail: `${visuals.length} visuals`,
      });
    }

    for (const [vKey, v] of visuals) {
      const vPath = `${pagePath} › ${v.title || vKey}`;

      if (DISCOURAGED_VISUALS.has(v.type)) {
        findings.push({
          rule: ruleById("Report.PieOrDonut"),
          objectType: "Visual",
          objectPath: vPath,
          detail: v.displayType || v.type,
        });
      }

      if (v.hidden) {
        findings.push({
          rule: ruleById("Report.VisualHidden"),
          objectType: "Visual",
          objectPath: vPath,
        });
      }

      if (!v.title || v.title.trim().length === 0) {
        findings.push({
          rule: ruleById("Report.VisualNoTitle"),
          objectType: "Visual",
          objectPath: vPath,
          detail: v.displayType || v.type,
        });
      }

      const pw = page.width || TARGET_W;
      const ph = page.height || TARGET_H;
      if (v.x < 0 || v.y < 0 || v.x + v.width > pw || v.y + v.height > ph) {
        findings.push({
          rule: ruleById("Report.VisualOffCanvas"),
          objectType: "Visual",
          objectPath: vPath,
          detail: `(${Math.round(v.x)},${Math.round(v.y)}) ${Math.round(v.width)}×${Math.round(v.height)}`,
        });
      }

      if (v.width > 0 && v.height > 0 && (v.width < 80 || v.height < 60)) {
        findings.push({
          rule: ruleById("Report.VisualTooSmall"),
          objectType: "Visual",
          objectPath: vPath,
          detail: `${Math.round(v.width)}×${Math.round(v.height)}`,
        });
      }
    }

    // Overlap detection — pairwise, skipping hidden visuals.
    const visibleVisuals = visuals.filter(([, v]) => !v.hidden && v.width > 0 && v.height > 0);
    for (let i = 0; i < visibleVisuals.length; i += 1) {
      for (let j = i + 1; j < visibleVisuals.length; j += 1) {
        const [ak, a] = visibleVisuals[i];
        const [bk, b] = visibleVisuals[j];
        if (rectsOverlap(a, b)) {
          findings.push({
            rule: ruleById("Report.OverlappingVisuals"),
            objectType: "Page",
            objectPath: pagePath,
            detail: `${a.title || ak} ↔ ${b.title || bk}`,
          });
        }
      }
    }
  }

  return findings;
}
