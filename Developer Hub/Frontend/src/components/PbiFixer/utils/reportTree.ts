// Report tree builder — mirrors _build_tree, _get_properties_html
// from _report_explorer.py

import {
  ReportData,
  VisualObjectRef,
  TreeItem,
  TreeBuildResult,
  ScanResult,
} from "../types";
import { EXPANDED, COLLAPSED } from "./theme";
import { buildTreeItems } from "./treeUtils";

export function buildReportTree(
  reportData: ReportData,
  expandedPages: Set<string>,
  scanResults: ScanResult = {}
): TreeBuildResult {
  const items: TreeItem[] = [];
  const pages = reportData.pages ?? {};

  // Sort pages by ordinal
  const sortedPages = Object.keys(pages).sort(
    (a, b) => (pages[a].ordinal ?? 9999) - (pages[b].ordinal ?? 9999)
  );

  for (const pName of sortedPages) {
    const p = pages[pName];
    const isExpanded = expandedPages.has(pName);
    const marker = isExpanded ? EXPANDED : COLLAPSED;
    const hiddenSuffix = p.hidden ? " (hidden)" : "";
    const vCount = Object.keys(p.visuals).length;
    const pageViolations = scanResults[`page:${pName}`] ?? 0;
    const badge = pageViolations > 0 ? ` \u26a0\ufe0f${pageViolations}` : "";

    items.push({
      indent: 0,
      icon: "page",
      label: `${marker} ${p.displayName}${hiddenSuffix}  [${vCount} visuals]${badge}`,
      key: `page:${pName}`,
    });

    if (!isExpanded) continue;

    for (const vName of Object.keys(p.visuals).sort()) {
      const v = p.visuals[vName];
      let label = v.displayType || v.type;
      if (v.title) label = `${label}: ${v.title}`;
      if (v.hidden) label += " (hidden)";
      const vKey = `visual:${pName}:${vName}`;
      if (scanResults[vKey]) {
        label += ` \u26a0\ufe0f${scanResults[vKey]}`;
      }
      items.push({ indent: 1, icon: "visual", label, key: vKey });
    }
  }

  return buildTreeItems(items);
}

// ---------------------------------------------------------------------------
// Properties resolver
// ---------------------------------------------------------------------------

export interface PageProperties {
  internalName: string;
  displayName: string;
  width: number;
  height: number;
  hidden: boolean;
  visualCount: number;
  visualTypeSummary: string;
  rawJson?: unknown;
}

export interface VisualProperties {
  type: string;
  displayType: string;
  internalName: string;
  pageName: string;
  title: string;
  x: number;
  y: number;
  width: number;
  height: number;
  hidden: boolean;
  usedObjects: { icon: string; table: string; object: string; type: string }[];
  rawJson?: unknown;
}

export function getPageProperties(
  reportData: ReportData,
  pageKey: string
): PageProperties | null {
  const pName = pageKey.replace(/^page:/, "");
  const p = reportData.pages[pName];
  if (!p) return null;

  const typeCounts: Record<string, number> = {};
  for (const v of Object.values(p.visuals)) {
    const dt = v.displayType || v.type || "unknown";
    typeCounts[dt] = (typeCounts[dt] ?? 0) + 1;
  }
  const summary = Object.entries(typeCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([t, c]) => `${c}\u00d7 ${t}`)
    .join(", ");

  return {
    internalName: pName,
    displayName: p.displayName,
    width: p.width,
    height: p.height,
    hidden: p.hidden,
    visualCount: Object.keys(p.visuals).length,
    visualTypeSummary: summary,
    rawJson: p.rawJson,
  };
}

export function getVisualProperties(
  reportData: ReportData,
  key: string
): VisualProperties | null {
  const parts = key.split(":");
  if (parts.length < 3) return null;
  const pName = parts[1];
  const vName = parts[2];
  const p = reportData.pages[pName];
  if (!p) return null;
  const v = p.visuals[vName];
  if (!v) return null;

  // Used objects
  const voKey = `${pName}:${vName}`;
  const objects = (reportData.visualObjects?.[voKey] ?? []).map((obj: VisualObjectRef) => ({
    icon: obj.type === "Measure" ? "\u{1F4D0}" : "\u{1F4CF}",
    table: obj.table,
    object: obj.object,
    type: obj.type,
  }));

  return {
    type: v.type,
    displayType: v.displayType,
    internalName: vName,
    pageName: pName,
    title: v.title,
    x: v.x,
    y: v.y,
    width: v.width,
    height: v.height,
    hidden: v.hidden,
    usedObjects: objects,
    rawJson: v.rawJson,
  };
}
