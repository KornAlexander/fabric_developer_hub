// WS-D — Report BPA rule engine (client-side).
//
// Mirrors `modelBpaApi.ts` for the Report side. Operates on a loaded
// `ReportData` (pages + visuals + visualObjects). The `sempy_labs.report.run_report_bpa`
// backend bridge will replace this once the Python dep lands; the
// finding shape is kept compatible so the swap-in is zero-touch.
//
// Coverage tracks `sempy_labs.report._report_bpa_rules.report_bpa_rules()`
// — every official rule that can be evaluated from PBIR alone has a
// matching entry below. Rules requiring a live semantic-model cross-
// check (e.g. "Valid Semantic Model Object") are intentionally skipped
// until the backend bridge lands.

import type {
  ReportData,
  PageInfo,
  VisualInfo,
  CustomVisualInfo,
} from "../types/report";

export type BpaSeverity = "Error" | "Warning" | "Info";
export type BpaFixKind =
  | "FixPieChart"
  | "FixPageSize"
  | "FixHiddenPage"
  | "FixVisualTitle"
  | "FixOffCanvasVisual"
  | "FixOverlappingVisuals"
  | "FixDisableShowItemsNoData"
  | "FixRemoveUnusedCustomVisuals"
  | "FixMigrateReportLevelMeasures";

export interface BpaRule {
  id: string;
  category: string;
  severity: BpaSeverity;
  name: string;
  description: string;
  fixKind?: BpaFixKind;
}

export type BpaObjectType =
  | "Report"
  | "Page"
  | "Visual"
  | "Custom Visual"
  | "Report Filter"
  | "Page Filter"
  | "Visual Filter"
  | "Report Level Measure";

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

// Thresholds aligned with the official sempy_labs report BPA rules.
const MAX_VISIBLE_VISUALS_PER_PAGE = 15;
const MAX_OBJECTS_PER_VISUAL = 5;
const MAX_PAGE_HEIGHT = 720; // Anything taller scrolls vertically.

export const BPA_RULES: readonly BpaRule[] = Object.freeze([
  // -------------------------------------------------------------------------
  // Custom client-side rules (kept on top of the official set).
  // -------------------------------------------------------------------------
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

  // -------------------------------------------------------------------------
  // Mirror of sempy_labs.report.report_bpa_rules() — Performance category.
  // -------------------------------------------------------------------------
  {
    id: "Report.TooManyVisuals",
    category: "Performance",
    severity: "Warning",
    name: "Reduce the number of visible visuals on the page",
    description:
      `Reducing the number of visible visuals on a page leads to faster report performance. ` +
      `Flagged when a page has more than ${MAX_VISIBLE_VISUALS_PER_PAGE} visible visuals.`,
  },
  {
    id: "Report.VisualTooManyObjects",
    category: "Performance",
    severity: "Warning",
    name: "Reduce the number of objects within visuals",
    description:
      "Reducing the number of objects (measures, columns) used in a visual leads to faster report performance.",
  },
  {
    id: "Report.FilterOnMeasure",
    category: "Performance",
    severity: "Warning",
    name: "Reduce usage of filters on measures",
    description:
      "Measure filters may cause performance degradation, especially against a large semantic model.",
  },
  {
    id: "Report.ShowItemsWithNoData",
    category: "Performance",
    severity: "Warning",
    name: "Avoid setting 'Show items with no data' on columns",
    description:
      "This setting will show all column values for all columns in the visual which may lead to performance degradation. " +
      "See https://learn.microsoft.com/power-bi/create-reports/desktop-show-items-no-data",
    fixKind: "FixDisableShowItemsNoData",
  },
  {
    id: "Report.PageTallScrolling",
    category: "Performance",
    severity: "Warning",
    name: "Avoid tall report pages with vertical scrolling",
    description:
      `Report pages are designed to be in a single view and not scroll. ` +
      `Pages taller than ${MAX_PAGE_HEIGHT}px scroll vertically.`,
    fixKind: "FixPageSize",
  },
  {
    id: "Report.UnusedCustomVisual",
    category: "Performance",
    severity: "Warning",
    name: "Remove custom visuals which are not used in the report",
    description:
      "Removing unused custom visuals from a report may lead to faster report performance.",
    fixKind: "FixRemoveUnusedCustomVisuals",
  },
  {
    id: "Report.AnyCustomVisual",
    category: "Performance",
    severity: "Info",
    name: "Reduce usage of custom visuals",
    description: "Using custom visuals may lead to performance degradation.",
  },
  {
    id: "Report.TopNFilter",
    category: "Performance",
    severity: "Info",
    name: "Reduce usage of TopN filtering within visuals",
    description:
      "TopN filtering may cause performance degradation, especially against a high cardinality column.",
  },

  // -------------------------------------------------------------------------
  // Mirror of sempy_labs.report.report_bpa_rules() — Maintenance category.
  // -------------------------------------------------------------------------
  {
    id: "Report.ReportLevelMeasure",
    category: "Maintenance",
    severity: "Info",
    name: "Move report-level measures into the semantic model",
    description:
      "It is a best practice to keep measures defined in the semantic model and not in the report.",
    fixKind: "FixMigrateReportLevelMeasures",
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

// ---------------------------------------------------------------------------
// PBIR JSON helpers — narrow shape inspection of the cached rawJson payloads.
// ---------------------------------------------------------------------------

interface FilterEntry {
  name?: string;
  type?: string;
  field?: Record<string, unknown>;
  filter?: Record<string, unknown>;
}

function getFilters(raw: unknown): FilterEntry[] {
  if (!raw || typeof raw !== "object") return [];
  const fc = (raw as { filterConfig?: { filters?: unknown } }).filterConfig;
  const arr = fc?.filters;
  return Array.isArray(arr) ? (arr as FilterEntry[]) : [];
}

/**
 * Top-level field-kind detection — matches sempy's behaviour of treating
 * anything wrapped in `Measure`/`Aggregation` as measure-like, vs `Column`
 * / `HierarchyLevel` as column-like.
 */
function isMeasureField(f: FilterEntry): boolean {
  const field = f.field;
  if (!field || typeof field !== "object") return false;
  return "Measure" in field || "Aggregation" in field;
}

function isTopNFilter(f: FilterEntry): boolean {
  // Modern PBIR uses `type: "TopN"`, the visual variant is `VisualTopN`.
  const t = (f.type ?? "").toString();
  return t === "TopN" || t === "VisualTopN";
}

function describeFilterField(f: FilterEntry): string {
  const field = f.field as Record<string, unknown> | undefined;
  if (!field) return f.name ?? "filter";
  for (const key of ["Measure", "Aggregation", "Column", "HierarchyLevel"]) {
    const inner = field[key] as { Property?: string; Expression?: { SourceRef?: { Source?: string; Entity?: string } } } | undefined;
    if (inner) {
      const tbl = inner.Expression?.SourceRef?.Entity ?? inner.Expression?.SourceRef?.Source ?? "";
      const prop = inner.Property ?? "";
      return tbl && prop ? `${tbl}[${prop}]` : prop || tbl || (f.name ?? "filter");
    }
  }
  return f.name ?? "filter";
}

/** Counts query projections (data fields) bound to a visual. */
function countVisualObjects(raw: unknown): number {
  if (!raw || typeof raw !== "object") return 0;
  const visual = (raw as { visual?: { query?: { queryState?: Record<string, unknown> } } }).visual;
  const queryState = visual?.query?.queryState;
  if (!queryState || typeof queryState !== "object") return 0;
  let n = 0;
  for (const v of Object.values(queryState)) {
    const projections = (v as { projections?: unknown[] })?.projections;
    if (Array.isArray(projections)) n += projections.length;
  }
  return n;
}

/** True if any projection on the visual has `showAll: true`. */
function visualHasShowItemsWithNoData(raw: unknown): boolean {
  if (!raw || typeof raw !== "object") return false;
  const visual = (raw as { visual?: { query?: { queryState?: Record<string, unknown> } } }).visual;
  const queryState = visual?.query?.queryState;
  if (!queryState || typeof queryState !== "object") return false;
  for (const v of Object.values(queryState)) {
    const projections = (v as { projections?: { showAll?: boolean }[] })?.projections;
    if (Array.isArray(projections)) {
      for (const p of projections) {
        if (p?.showAll === true) return true;
      }
    }
  }
  return false;
}

function visualFilters(raw: unknown): FilterEntry[] {
  if (!raw || typeof raw !== "object") return [];
  // Visual-level filters live on `visual.filterConfig.filters` in PBIR.
  const visual = (raw as { visual?: unknown }).visual;
  return getFilters(visual);
}

// ---------------------------------------------------------------------------
// Main analyzer.
// ---------------------------------------------------------------------------

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

    if (page.height > MAX_PAGE_HEIGHT) {
      findings.push({
        rule: ruleById("Report.PageTallScrolling"),
        objectType: "Page",
        objectPath: pagePath,
        detail: `${page.height}px tall`,
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

    const visibleVisualsCount = visuals.filter(([, v]) => !v.hidden).length;
    if (visibleVisualsCount > MAX_VISIBLE_VISUALS_PER_PAGE) {
      findings.push({
        rule: ruleById("Report.TooManyVisuals"),
        objectType: "Page",
        objectPath: pagePath,
        detail: `${visibleVisualsCount} visible visuals`,
      });
    }

    // Page-level filter rules (Filter on Measure / TopN).
    for (const flt of getFilters(page.rawJson)) {
      const label = describeFilterField(flt);
      if (isMeasureField(flt)) {
        findings.push({
          rule: ruleById("Report.FilterOnMeasure"),
          objectType: "Page Filter",
          objectPath: `${pagePath} : ${label}`,
        });
      }
      if (isTopNFilter(flt)) {
        findings.push({
          rule: ruleById("Report.TopNFilter"),
          objectType: "Page Filter",
          objectPath: `${pagePath} : ${label}`,
        });
      }
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

      // Visual object count (>5 projections).
      const objCount = countVisualObjects(v.rawJson);
      if (objCount > MAX_OBJECTS_PER_VISUAL) {
        findings.push({
          rule: ruleById("Report.VisualTooManyObjects"),
          objectType: "Visual",
          objectPath: vPath,
          detail: `${objCount} objects`,
        });
      }

      // Show items with no data.
      if (visualHasShowItemsWithNoData(v.rawJson)) {
        findings.push({
          rule: ruleById("Report.ShowItemsWithNoData"),
          objectType: "Visual",
          objectPath: vPath,
        });
      }

      // Visual-level filters.
      for (const flt of visualFilters(v.rawJson)) {
        const label = describeFilterField(flt);
        if (isMeasureField(flt)) {
          findings.push({
            rule: ruleById("Report.FilterOnMeasure"),
            objectType: "Visual Filter",
            objectPath: `${vPath} : ${label}`,
          });
        }
        if (isTopNFilter(flt)) {
          findings.push({
            rule: ruleById("Report.TopNFilter"),
            objectType: "Visual Filter",
            objectPath: `${vPath} : ${label}`,
          });
        }
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

  // Custom visual rules (Performance · Custom Visual).
  const customVisuals: CustomVisualInfo[] = report.customVisuals ?? [];
  for (const cv of customVisuals) {
    findings.push({
      rule: ruleById("Report.AnyCustomVisual"),
      objectType: "Custom Visual",
      objectPath: cv.displayName || cv.name,
      detail: cv.isPublic ? "public" : "organization",
    });
    if (!cv.usedInReport) {
      findings.push({
        rule: ruleById("Report.UnusedCustomVisual"),
        objectType: "Custom Visual",
        objectPath: cv.displayName || cv.name,
      });
    }
  }

  // Report-level measures (Maintenance · Report Level Measure).
  for (const m of report.reportLevelMeasures ?? []) {
    findings.push({
      rule: ruleById("Report.ReportLevelMeasure"),
      objectType: "Report Level Measure",
      objectPath: m.table ? `${m.table}[${m.name}]` : m.name,
    });
  }

  return findings;
}
