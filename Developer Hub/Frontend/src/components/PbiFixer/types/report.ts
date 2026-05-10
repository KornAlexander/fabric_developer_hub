// Types for Report Explorer

export interface VisualInfo {
  type: string;
  displayType: string;
  x: number;
  y: number;
  width: number;
  height: number;
  hidden: boolean;
  title: string;
  /** Raw parsed visual.json payload (kept for the JSON preview pane). */
  rawJson?: unknown;
}

export interface PageInfo {
  displayName: string;
  width: number;
  height: number;
  hidden: boolean;
  visualCount: number;
  ordinal: number;
  visuals: Record<string, VisualInfo>;
  /** Raw parsed page.json payload (kept for the JSON preview pane). */
  rawJson?: unknown;
}

export interface VisualObjectRef {
  table: string;
  object: string;
  type: "Measure" | "Column";
}

export interface CustomVisualInfo {
  /** Internal visual type identifier (matches `visual.visualType` on usage). */
  name: string;
  /** Human-readable display name (falls back to `name`). */
  displayName: string;
  /** True when sourced from `publicCustomVisuals`, false for `resourcePackages` (organizational). */
  isPublic: boolean;
  /** True when at least one visual on any page uses this `visualType`. */
  usedInReport: boolean;
}

export interface ReportLevelMeasureInfo {
  name: string;
  table: string;
  expression?: string;
}

export interface ReportData {
  pages: Record<string, PageInfo>;
  format: string;
  reportId: string;
  workspaceId: string;
  visualObjects?: Record<string, VisualObjectRef[]>;
  /** Custom visuals declared in `definition/report.json` (publicCustomVisuals + resourcePackages). */
  customVisuals?: CustomVisualInfo[];
  /** Report-level measures parsed from `definition/reportExtensions.json`. */
  reportLevelMeasures?: ReportLevelMeasureInfo[];
}

export type ReportNodeType = "report" | "page" | "visual";
