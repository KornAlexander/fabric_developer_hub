// Tree-building utilities — mirrors build_tree_items from _ui_components.py

import { TreeItem, TreeBuildResult } from "../types";
import { ICONS, INDENT } from "./theme";

/**
 * Build display options and a key lookup map from structured tree items.
 * Handles deduplication with zero-width spaces (matching Python behavior).
 */
export function buildTreeItems(items: TreeItem[]): TreeBuildResult {
  const options: string[] = [];
  const keyMap: Record<string, string> = {};
  const seen: Record<string, number> = {};

  for (const { indent, icon: iconKey, label, key } of items) {
    const icon = ICONS[iconKey] ?? iconKey;
    let formatted = `${INDENT.repeat(indent)}${icon} ${label}`;

    if (keyMap[formatted] !== undefined) {
      const count = (seen[formatted] ?? 1) + 1;
      seen[formatted] = count;
      formatted += "\u200b".repeat(count);
    }

    options.push(formatted);
    keyMap[formatted] = key;
  }

  return { options, keyMap };
}

/**
 * Filter tree options, keeping parent nodes above any match.
 */
export function filterTreeOptions(
  allOptions: string[],
  query: string
): string[] {
  const q = query.toLowerCase().trim();
  if (!q) return allOptions;

  const matched = new Set<number>();
  for (let i = 0; i < allOptions.length; i++) {
    if (allOptions[i].toLowerCase().includes(q)) {
      matched.add(i);
      // Walk backwards to include parent nodes (lower indent)
      let curIndent = allOptions[i].length - allOptions[i].replace(/^\s+/, "").length;
      for (let j = i - 1; j >= 0; j--) {
        const pIndent =
          allOptions[j].length - allOptions[j].replace(/^\s+/, "").length;
        if (pIndent < curIndent) {
          matched.add(j);
          curIndent = pIndent;
          if (pIndent === 0) break;
        }
      }
    }
  }

  return Array.from(matched)
    .sort((a, b) => a - b)
    .map((i) => allOptions[i]);
}

/**
 * Get the total child count for a table (columns + measures + hierarchies + calcItems).
 */
export function tableSummary(t: {
  columns?: Record<string, unknown>;
  measures?: Record<string, unknown>;
  hierarchies?: Record<string, unknown>;
  calcItems?: Record<string, unknown>;
}): number {
  return (
    Object.keys(t.columns ?? {}).length +
    Object.keys(t.measures ?? {}).length +
    Object.keys(t.hierarchies ?? {}).length +
    Object.keys(t.calcItems ?? {}).length
  );
}
