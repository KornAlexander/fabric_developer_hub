// WS-J — Diagram page API.
//
// Thin wrapper around `loadModelData`. Surfaces just the bits the diagram
// canvas needs: a flat list of tables (with their visible columns) and
// the relationship list. Also owns the session-storage layout cache so
// dragged positions survive nav-away/back without a backend round-trip.

import type { PbiAuth } from "./fabricApi";
import { loadModelData } from "./fabricApi";
import type { ModelData, RelationshipInfo } from "../types/model";

export interface DiagramTable {
  name: string;
  isHidden: boolean;
  type: "Table" | "CalculationGroup" | "CalculatedTable";
  columns: { name: string; dataType: string; isKey: boolean; isHidden: boolean }[];
  measureCount: number;
}

export interface DiagramData {
  tables: DiagramTable[];
  relationships: RelationshipInfo[];
}

export async function loadDiagramData(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string
): Promise<DiagramData> {
  const md: ModelData = await loadModelData(auth, workspaceId, datasetId);
  const tables: DiagramTable[] = Object.entries(md.tables).map(([name, t]) => ({
    name,
    isHidden: t.isHidden,
    type: t.type,
    columns: Object.entries(t.columns).map(([cname, c]) => ({
      name: cname,
      dataType: c.dataType,
      isKey: c.isKey,
      isHidden: c.isHidden,
    })),
    measureCount: Object.keys(t.measures).length,
  }));
  return { tables, relationships: md.relationships };
}

// ── Layout persistence ────────────────────────────────────────────────────

export interface NodeLayout { x: number; y: number; collapsed: boolean }
export type LayoutMap = Record<string, NodeLayout>;

const KEY_PREFIX = "pbiFixer.diagram.layout.";

export function loadLayout(datasetId: string | undefined): LayoutMap {
  if (!datasetId) return {};
  try {
    const raw = sessionStorage.getItem(KEY_PREFIX + datasetId);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? (parsed as LayoutMap) : {};
  } catch {
    return {};
  }
}

export function saveLayout(datasetId: string | undefined, layout: LayoutMap): void {
  if (!datasetId) return;
  try {
    sessionStorage.setItem(KEY_PREFIX + datasetId, JSON.stringify(layout));
  } catch {
    /* quota — ignore */
  }
}

export function clearLayout(datasetId: string | undefined): void {
  if (!datasetId) return;
  try {
    sessionStorage.removeItem(KEY_PREFIX + datasetId);
  } catch {
    /* ignore */
  }
}

// ── Auto-layout ──────────────────────────────────────────────────────────
// Simple grid placement seeded by table degree (most-connected tables placed
// first near the centre). Good enough for typical Power BI star/snowflake
// schemas; users drag from there to taste.

export interface AutoLayoutOptions {
  columnsPerRow?: number;
  cardWidth?: number;
  cardHeight?: number;
  gapX?: number;
  gapY?: number;
}

export function autoLayout(
  tables: DiagramTable[],
  relationships: RelationshipInfo[],
  opts: AutoLayoutOptions = {}
): LayoutMap {
  const cardWidth = opts.cardWidth ?? 220;
  const cardHeight = opts.cardHeight ?? 180;
  const gapX = opts.gapX ?? 60;
  const gapY = opts.gapY ?? 60;
  const cols = Math.max(1, opts.columnsPerRow ?? Math.ceil(Math.sqrt(tables.length)));

  // Degree (fact tables = highest degree → placed first).
  const degree = new Map<string, number>();
  for (const r of relationships) {
    degree.set(r.fromTable, (degree.get(r.fromTable) ?? 0) + 1);
    degree.set(r.toTable, (degree.get(r.toTable) ?? 0) + 1);
  }
  const sorted = [...tables].sort((a, b) => (degree.get(b.name) ?? 0) - (degree.get(a.name) ?? 0));

  const layout: LayoutMap = {};
  sorted.forEach((t, idx) => {
    const col = idx % cols;
    const row = Math.floor(idx / cols);
    layout[t.name] = {
      x: col * (cardWidth + gapX),
      y: row * (cardHeight + gapY),
      collapsed: false,
    };
  });
  return layout;
}
