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
}

export interface PageInfo {
  displayName: string;
  width: number;
  height: number;
  hidden: boolean;
  visualCount: number;
  ordinal: number;
  visuals: Record<string, VisualInfo>;
}

export interface VisualObjectRef {
  table: string;
  object: string;
  type: "Measure" | "Column";
}

export interface ReportData {
  pages: Record<string, PageInfo>;
  format: string;
  reportId: string;
  workspaceId: string;
  visualObjects?: Record<string, VisualObjectRef[]>;
}

export type ReportNodeType = "report" | "page" | "visual";
