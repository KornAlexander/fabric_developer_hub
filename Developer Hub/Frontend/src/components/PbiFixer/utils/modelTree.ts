// Model tree builder — mirrors _build_tree, _build_measures_with_folders,
// _build_columns_with_folders from _sm_explorer.py

import {
  ModelData,
  TreeItem,
  TreeBuildResult,
  ScanResult,
} from "../types";
import { EXPANDED, COLLAPSED } from "./theme";
import { buildTreeItems, tableSummary } from "./treeUtils";

// ---------------------------------------------------------------------------
// Folder grouping helpers
// ---------------------------------------------------------------------------

function countUnder(
  folders: Record<string, string[]>,
  prefix: string
): number {
  let total = 0;
  for (const [fp, items] of Object.entries(folders)) {
    const fpNorm = fp.replace(/\//g, "\\");
    if (fpNorm === prefix || fpNorm.startsWith(prefix + "\\")) {
      total += items.length;
    }
  }
  return total;
}

function buildMeasuresWithFolders(
  measures: Record<string, { displayFolder?: string; expression?: string }>,
  tableKey: string,
  baseIndent: number,
  expanded: Set<string>,
  pendingChanges: Set<string>
): TreeItem[] {
  const items: TreeItem[] = [];
  const folders: Record<string, string[]> = {};
  const noFolder: string[] = [];

  for (const mn of Object.keys(measures).sort()) {
    const df = measures[mn].displayFolder ?? "";
    if (df) {
      (folders[df] = folders[df] || []).push(mn);
    } else {
      noFolder.push(mn);
    }
  }

  for (const mn of noFolder) {
    const mk = `measure:${tableKey}:${mn}`;
    const pfx = pendingChanges.has(mk) ? "\u270f " : "";
    items.push({ indent: baseIndent, icon: "measure", label: `${pfx}${mn}`, key: mk });
  }

  const emittedFolders = new Set<string>();

  for (const folderPath of Object.keys(folders).sort()) {
    const parts = folderPath.replace(/\//g, "\\").split("\\");

    for (let depth = 0; depth < parts.length; depth++) {
      const ancestor = parts.slice(0, depth + 1).join("\\");
      if (!emittedFolders.has(ancestor)) {
        emittedFolders.add(ancestor);
        const folderKey = `folder:${tableKey}:${ancestor}`;
        const isExp = expanded.has(folderKey);
        const marker = isExp ? EXPANDED : COLLAPSED;
        const count = countUnder(folders, ancestor);
        items.push({
          indent: baseIndent + depth,
          icon: "folder",
          label: `${marker} ${parts[depth]}  [${count}]`,
          key: folderKey,
        });
      }
    }

    // Check all ancestors expanded
    let allExpanded = true;
    for (let depth = 0; depth < parts.length; depth++) {
      const ancestor = parts.slice(0, depth + 1).join("\\");
      if (!expanded.has(`folder:${tableKey}:${ancestor}`)) {
        allExpanded = false;
        break;
      }
    }

    if (allExpanded) {
      for (const mn of folders[folderPath].sort()) {
        const mk = `measure:${tableKey}:${mn}`;
        const pfx = pendingChanges.has(mk) ? "\u270f " : "";
        items.push({
          indent: baseIndent + parts.length,
          icon: "measure",
          label: `${pfx}${mn}`,
          key: mk,
        });
      }
    }
  }

  return items;
}

function buildColumnsWithFolders(
  columns: Record<string, { displayFolder?: string; dataType?: string; isHidden?: boolean }>,
  tableKey: string,
  baseIndent: number,
  expanded: Set<string>,
  pendingChanges: Set<string>
): TreeItem[] {
  const items: TreeItem[] = [];
  const folders: Record<string, string[]> = {};
  const noFolder: string[] = [];

  for (const cn of Object.keys(columns).sort()) {
    const df = columns[cn].displayFolder ?? "";
    if (df) {
      const firstFolder = df.split(";")[0].trim();
      (folders[firstFolder] = folders[firstFolder] || []).push(cn);
    } else {
      noFolder.push(cn);
    }
  }

  for (const cn of noFolder) {
    const c = columns[cn];
    const hidden = c.isHidden ? " (hidden)" : "";
    const ck = `column:${tableKey}:${cn}`;
    const pfx = pendingChanges.has(ck) ? "\u270f " : "";
    items.push({
      indent: baseIndent,
      icon: "column",
      label: `${pfx}${cn} [${c.dataType ?? ""}]${hidden}`,
      key: ck,
    });
  }

  const emittedFolders = new Set<string>();
  for (const folderPath of Object.keys(folders).sort()) {
    const parts = folderPath.replace(/\//g, "\\").split("\\");

    for (let depth = 0; depth < parts.length; depth++) {
      const ancestor = parts.slice(0, depth + 1).join("\\");
      if (!emittedFolders.has(ancestor)) {
        emittedFolders.add(ancestor);
        const folderKey = `colfolder:${tableKey}:${ancestor}`;
        const isExp = expanded.has(folderKey);
        const marker = isExp ? EXPANDED : COLLAPSED;
        const count = countUnder(folders, ancestor);
        items.push({
          indent: baseIndent + depth,
          icon: "folder",
          label: `${marker} ${parts[depth]}  [${count}]`,
          key: folderKey,
        });
      }
    }

    let allExpanded = true;
    for (let depth = 0; depth < parts.length; depth++) {
      const ancestor = parts.slice(0, depth + 1).join("\\");
      if (!expanded.has(`colfolder:${tableKey}:${ancestor}`)) {
        allExpanded = false;
        break;
      }
    }

    if (allExpanded) {
      for (const cn of folders[folderPath].sort()) {
        const c = columns[cn];
        const hidden = c.isHidden ? " (hidden)" : "";
        const ck = `column:${tableKey}:${cn}`;
        const pfx = pendingChanges.has(ck) ? "\u270f " : "";
        items.push({
          indent: baseIndent + parts.length,
          icon: "column",
          label: `${pfx}${cn} [${c.dataType ?? ""}]${hidden}`,
          key: ck,
        });
      }
    }
  }

  return items;
}

// ---------------------------------------------------------------------------
// Main tree builder
// ---------------------------------------------------------------------------

export function buildModelTree(
  modelData: ModelData,
  expandedNodes: Set<string>,
  _scanResults: ScanResult = {},
  pendingChanges: Set<string> = new Set()
): TreeBuildResult {
  const items: TreeItem[] = [];

  const dsName = modelData.datasetName ?? "Model";
  const compat = modelData.modelProperties.compatibilityLevel ?? "";
  const mode = modelData.modelProperties.defaultMode ?? "";
  const propStr = compat ? ` (${mode}, CL ${compat})` : "";
  const tCount = Object.keys(modelData.tables ?? {}).length;
  const isModelExp = expandedNodes.has(dsName);
  const marker = isModelExp ? EXPANDED : COLLAPSED;

  items.push({
    indent: 0,
    icon: "model",
    label: `${marker} ${dsName}${propStr}  [${tCount} tables]`,
    key: `model:${dsName}`,
  });

  if (isModelExp) {
    for (const tName of Object.keys(modelData.tables).sort()) {
      const t = modelData.tables[tName];
      const icon = t.type === "CalculationGroup" ? "calc_group" : "table";
      const isExpanded = expandedNodes.has(tName);
      const tMarker = isExpanded ? EXPANDED : COLLAPSED;
      const suffix = t.isHidden ? " (hidden)" : "";
      const summary = tableSummary(t);

      items.push({
        indent: 1,
        icon,
        label: `${tMarker} ${tName}${suffix}  [${summary}]`,
        key: `table:${tName}`,
      });

      if (!isExpanded) continue;

      items.push(
        ...buildMeasuresWithFolders(
          t.measures,
          tName,
          2,
          expandedNodes,
          pendingChanges
        )
      );
      items.push(
        ...buildColumnsWithFolders(
          t.columns,
          tName,
          2,
          expandedNodes,
          pendingChanges
        )
      );

      for (const hn of Object.keys(t.hierarchies).sort()) {
        const lvls = t.hierarchies[hn].levels ?? [];
        const lvlStr = lvls.join(" \u2192 ");
        const hKey = `hierarchy:${tName}:${hn}`;
        const isHExp = expandedNodes.has(hKey);
        const hMarker = lvls.length > 0 ? (isHExp ? EXPANDED : COLLAPSED) + " " : "";
        items.push({
          indent: 2,
          icon: "hierarchy",
          label: `${hMarker}${hn}  (${lvlStr})`,
          key: hKey,
        });
        if (isHExp) {
          lvls.forEach((lvl, i) => {
            items.push({
              indent: 3,
              icon: "column",
              label: `${i + 1}. ${lvl}`,
              key: `level:${tName}:${hn}:${i}`,
            });
          });
        }
      }

      for (const ciName of Object.keys(t.calcItems).sort(
        (a, b) => (t.calcItems[a].ordinal ?? 0) - (t.calcItems[b].ordinal ?? 0)
      )) {
        items.push({
          indent: 2,
          icon: "calc_item",
          label: ciName,
          key: `calc_item:${tName}:${ciName}`,
        });
      }

      for (const pt of t.partitions ?? []) {
        items.push({
          indent: 2,
          icon: "partition",
          label: `${pt.name} (${pt.sourceType})`,
          key: `partition:${tName}:${pt.name}`,
        });
      }
    }

    // Relationships
    const rels = modelData.relationships ?? [];
    if (rels.length > 0) {
      const relKey = "rels:_single";
      const isRelsExp = expandedNodes.has(relKey);
      const rMarker = isRelsExp ? EXPANDED : COLLAPSED;
      items.push({
        indent: 1,
        icon: "relationship",
        label: `${rMarker} Relationships  [${rels.length}]`,
        key: relKey,
      });
      if (isRelsExp) {
        rels.forEach((rel, i) => {
          const active = rel.isActive ? "" : " (inactive)";
          items.push({
            indent: 2,
            icon: "relationship",
            label: `${rel.fromTable}[${rel.fromColumn}] \u2194 ${rel.toTable}[${rel.toColumn}]${active}`,
            key: `rel:_single:${i}`,
          });
        });
      }
    }

    // Perspectives
    const persps = modelData.perspectives ?? [];
    if (persps.length > 0) {
      items.push({
        indent: 1,
        icon: "folder",
        label: `Perspectives  [${persps.length}]`,
        key: "persps:_single",
      });
      for (const pname of persps.sort()) {
        items.push({
          indent: 2,
          icon: "calc_item",
          label: pname,
          key: `persp:_single:${pname}`,
        });
      }
    }
  }

  return buildTreeItems(items);
}

// ---------------------------------------------------------------------------
// Preview text resolver
// ---------------------------------------------------------------------------

export function getModelPreviewText(
  modelData: ModelData,
  key: string
): string {
  const parts = key.split(":");
  const nodeType = parts[0];

  if (nodeType === "rels") return "";

  if (nodeType === "model") {
    const p = modelData.modelProperties;
    return Object.entries(p)
      .map(([k, v]) => `${k}: ${v}`)
      .join("\n");
  }

  if (nodeType === "partition") {
    const tableName = parts[1] ?? "";
    const pName = parts[2] ?? "";
    const t = modelData.tables[tableName];
    if (t) {
      const pt = t.partitions?.find((p) => p.name === pName);
      return pt?.expression ?? "";
    }
    return "";
  }

  if (nodeType === "rel") {
    const idx = parseInt(parts[2] ?? "-1", 10);
    const rels = modelData.relationships ?? [];
    if (idx >= 0 && idx < rels.length) {
      const r = rels[idx];
      return [
        `From: '${r.fromTable}'[${r.fromColumn}]`,
        `To:   '${r.toTable}'[${r.toColumn}]`,
        `Multiplicity: ${r.multiplicity}`,
        `Cross-filter: ${r.crossFilter}`,
        `Security filtering: ${r.securityFiltering}`,
        `Active: ${r.isActive}`,
        `Rely on RRI: ${r.relyOnRri}`,
      ].join("\n");
    }
    return "";
  }

  if (nodeType === "measure") {
    const t = modelData.tables[parts[1]];
    return t?.measures[parts[2]]?.expression ?? "";
  }

  if (nodeType === "column") {
    const t = modelData.tables[parts[1]];
    return t?.columns[parts[2]]?.expression ?? "";
  }

  if (nodeType === "calc_item") {
    const t = modelData.tables[parts[1]];
    return t?.calcItems[parts[2]]?.expression ?? "";
  }

  return "";
}

/**
 * Get a DAX reference string for a measure or column.
 */
export function getDaxReference(key: string): string {
  const parts = key.split(":");
  const nodeType = parts[0];
  if (nodeType === "measure") {
    return `[${parts[2]}]`;
  }
  if (nodeType === "column") {
    return `'${parts[1]}'[${parts[2]}]`;
  }
  return "";
}
