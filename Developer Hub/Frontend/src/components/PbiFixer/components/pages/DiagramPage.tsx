// WS-J — Diagram page (Power BI Desktop-style relationship view).
//
// Pure SVG canvas (no `reactflow` dep — that install is owned by WS-N).
// Features:
//   • Auto-layout grid seeded by relationship degree (fact tables centre-ish)
//   • Drag table cards (mouse). Layout persisted per-dataset in sessionStorage.
//   • Pan canvas (drag empty background). Zoom (mouse wheel).
//   • Collapse / expand cards (header → just header; body → header + columns).
//   • Relationship edges:
//       – cardinality glyph (1 / *) on each endpoint
//       – diamond mid-line for filter direction (◇ single, ◈ both)
//       – dashed line + dimmed colour for inactive relationships
//   • Reset Layout button.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Spinner,
  Title3,
  Text,
  Badge,
  Switch,
  MessageBar,
  MessageBarBody,
  makeStyles,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import {
  ArrowClockwise20Regular,
  ArrowExpand20Regular,
} from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import type { RelationshipInfo } from "../../types/model";
import {
  loadDiagramData,
  loadLayout,
  saveLayout,
  clearLayout,
  autoLayout,
  type DiagramData,
  type DiagramTable,
  type LayoutMap,
} from "../../services/diagramApi";

const CARD_WIDTH = 220;
const CARD_HEADER_HEIGHT = 32;
const CARD_ROW_HEIGHT = 18;
const CARD_PADDING = 8;
const MAX_COLUMNS_RENDERED = 12;

const useStyles = makeStyles({
  root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0, ...shorthands.gap("8px") },
  toolbar: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("10px"),
    flexWrap: "wrap",
    ...shorthands.padding("4px"),
  },
  grow: { flex: 1 },
  canvasWrap: {
    position: "relative",
    flex: 1,
    minHeight: 0,
    overflow: "hidden",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground2,
    cursor: "grab",
  },
  canvasGrabbing: { cursor: "grabbing" },
  empty: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    ...shorthands.gap("8px"),
    color: tokens.colorNeutralForeground3,
    textAlign: "center",
    ...shorthands.padding("48px", "24px"),
  },
  legend: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("16px"),
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground3,
  },
  legendItem: { display: "inline-flex", alignItems: "center", ...shorthands.gap("4px") },
});

interface DragState {
  type: "node" | "pan" | null;
  nodeName?: string;
  startX: number;
  startY: number;
  origX: number;
  origY: number;
}

function cardHeight(t: DiagramTable, layout: LayoutMap): number {
  const collapsed = layout[t.name]?.collapsed ?? false;
  if (collapsed) return CARD_HEADER_HEIGHT;
  const visible = t.columns.filter((c) => !c.isHidden).slice(0, MAX_COLUMNS_RENDERED);
  return CARD_HEADER_HEIGHT + visible.length * CARD_ROW_HEIGHT + CARD_PADDING;
}

/** Anchor point on a card border closest to a target point. */
function anchor(
  cardX: number,
  cardY: number,
  cardW: number,
  cardH: number,
  targetX: number,
  targetY: number
): { x: number; y: number; side: "left" | "right" | "top" | "bottom" } {
  const cx = cardX + cardW / 2;
  const cy = cardY + cardH / 2;
  const dx = targetX - cx;
  const dy = targetY - cy;
  // Pick side based on dominant axis.
  if (Math.abs(dx) * cardH > Math.abs(dy) * cardW) {
    // Left or right.
    const side = dx >= 0 ? "right" : "left";
    return {
      x: side === "right" ? cardX + cardW : cardX,
      y: cy,
      side,
    };
  } else {
    const side = dy >= 0 ? "bottom" : "top";
    return {
      x: cx,
      y: side === "bottom" ? cardY + cardH : cardY,
      side,
    };
  }
}

function multiplicityEnd(m: string): { from: "1" | "*"; to: "1" | "*" } {
  // "OneToMany" → from=1, to=*; "ManyToOne" → from=*, to=1; "OneToOne" / "ManyToMany"
  const s = (m || "").toLowerCase();
  if (s.includes("onetomany")) return { from: "1", to: "*" };
  if (s.includes("manytoone")) return { from: "*", to: "1" };
  if (s.includes("manytomany")) return { from: "*", to: "*" };
  return { from: "1", to: "1" };
}

export const DiagramPage: React.FC<PageProps> = ({ auth, workspaceId, datasetId, datasetName }) => {
  const styles = useStyles();
  const [data, setData] = useState<DiagramData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState<LayoutMap>({});
  const [showHidden, setShowHidden] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 40, y: 40 });

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState>({ type: null, startX: 0, startY: 0, origX: 0, origY: 0 });

  // ── load ────────────────────────────────────────────────────────────────
  const reload = useCallback(async () => {
    if (!workspaceId || !datasetId) return;
    setLoading(true);
    setError(null);
    try {
      const d = await loadDiagramData(auth, workspaceId, datasetId);
      setData(d);
      const stored = loadLayout(datasetId);
      const tablesNeedingLayout = d.tables.filter((t) => !stored[t.name]);
      if (tablesNeedingLayout.length > 0) {
        const seeded = autoLayout(tablesNeedingLayout, d.relationships);
        const merged = { ...stored, ...seeded };
        setLayout(merged);
        saveLayout(datasetId, merged);
      } else {
        setLayout(stored);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth, workspaceId, datasetId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // ── layout helpers ─────────────────────────────────────────────────────
  const persist = useCallback(
    (next: LayoutMap) => {
      setLayout(next);
      saveLayout(datasetId, next);
    },
    [datasetId]
  );

  const handleResetLayout = useCallback(() => {
    if (!data) return;
    clearLayout(datasetId);
    const fresh = autoLayout(data.tables, data.relationships);
    persist(fresh);
    setPan({ x: 40, y: 40 });
    setZoom(1);
  }, [data, datasetId, persist]);

  const handleFit = useCallback(() => {
    if (!data || !wrapRef.current) return;
    const wrap = wrapRef.current.getBoundingClientRect();
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const t of data.tables) {
      const l = layout[t.name];
      if (!l) continue;
      const h = cardHeight(t, layout);
      minX = Math.min(minX, l.x);
      minY = Math.min(minY, l.y);
      maxX = Math.max(maxX, l.x + CARD_WIDTH);
      maxY = Math.max(maxY, l.y + h);
    }
    if (!isFinite(minX)) return;
    const contentW = maxX - minX + 80;
    const contentH = maxY - minY + 80;
    const z = Math.min(1, Math.min(wrap.width / contentW, wrap.height / contentH));
    setZoom(z);
    setPan({ x: 40 - minX * z, y: 40 - minY * z });
  }, [data, layout]);

  const toggleCollapsed = useCallback(
    (name: string) => {
      const cur = layout[name];
      if (!cur) return;
      persist({ ...layout, [name]: { ...cur, collapsed: !cur.collapsed } });
    },
    [layout, persist]
  );

  // ── pointer handlers ───────────────────────────────────────────────────
  const onPointerDownNode = (e: React.PointerEvent, name: string) => {
    e.stopPropagation();
    const cur = layout[name];
    if (!cur) return;
    (e.target as Element).setPointerCapture(e.pointerId);
    dragRef.current = {
      type: "node",
      nodeName: name,
      startX: e.clientX,
      startY: e.clientY,
      origX: cur.x,
      origY: cur.y,
    };
  };

  const onPointerDownCanvas = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    dragRef.current = {
      type: "pan",
      startX: e.clientX,
      startY: e.clientY,
      origX: pan.x,
      origY: pan.y,
    };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d.type) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (d.type === "node" && d.nodeName) {
      const cur = layout[d.nodeName];
      if (!cur) return;
      setLayout({
        ...layout,
        [d.nodeName]: { ...cur, x: d.origX + dx / zoom, y: d.origY + dy / zoom },
      });
    } else if (d.type === "pan") {
      setPan({ x: d.origX + dx, y: d.origY + dy });
    }
  };

  const onPointerUp = () => {
    const d = dragRef.current;
    if (d.type === "node") {
      saveLayout(datasetId, layout);
    }
    dragRef.current = { type: null, startX: 0, startY: 0, origX: 0, origY: 0 };
  };

  const onWheel = (e: React.WheelEvent) => {
    if (!wrapRef.current) return;
    const rect = wrapRef.current.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const newZoom = Math.min(2.5, Math.max(0.2, zoom * factor));
    // Keep cursor world-position stable.
    const wx = (cx - pan.x) / zoom;
    const wy = (cy - pan.y) / zoom;
    setZoom(newZoom);
    setPan({ x: cx - wx * newZoom, y: cy - wy * newZoom });
  };

  // ── derived ────────────────────────────────────────────────────────────
  const visibleTables = useMemo(() => {
    if (!data) return [];
    return showHidden ? data.tables : data.tables.filter((t) => !t.isHidden);
  }, [data, showHidden]);

  const visibleNames = useMemo(() => new Set(visibleTables.map((t) => t.name)), [visibleTables]);

  const visibleRels = useMemo(() => {
    if (!data) return [];
    return data.relationships.filter(
      (r) => visibleNames.has(r.fromTable) && visibleNames.has(r.toTable)
    );
  }, [data, visibleNames]);

  // ── render gating ──────────────────────────────────────────────────────
  if (!workspaceId || !datasetId) {
    return (
      <div className={styles.empty}>
        <Title3>Diagram</Title3>
        <Text>Select a workspace and a semantic model in the connection bar above to begin.</Text>
      </div>
    );
  }

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        <Title3>Diagram {datasetName ? <Text size={300}> · {datasetName}</Text> : null}</Title3>
        <Badge appearance="outline">{visibleTables.length} tables</Badge>
        <Badge appearance="outline">{visibleRels.length} relationships</Badge>
        <div className={styles.grow} />
        <Switch label="Show hidden" checked={showHidden} onChange={(_, d) => setShowHidden(d.checked)} />
        <Button icon={<ArrowExpand20Regular />} onClick={handleFit} disabled={!data}>Fit</Button>
        <Button onClick={handleResetLayout} disabled={!data}>Reset Layout</Button>
        <Button icon={<ArrowClockwise20Regular />} onClick={() => void reload()} disabled={loading}>Refresh</Button>
      </div>

      <div className={styles.legend}>
        <span className={styles.legendItem}><strong>1</strong> / <strong>*</strong> = cardinality</span>
        <span className={styles.legendItem}>◇ single filter · ◈ both</span>
        <span className={styles.legendItem}>solid = active · dashed = inactive</span>
      </div>

      {error && (
        <MessageBar intent="error">
          <MessageBarBody>{error}</MessageBarBody>
        </MessageBar>
      )}

      <div
        ref={wrapRef}
        className={`${styles.canvasWrap} ${dragRef.current.type === "pan" ? styles.canvasGrabbing : ""}`}
        onPointerDown={onPointerDownCanvas}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onWheel={onWheel}
      >
        {loading && (
          <div style={{ position: "absolute", top: 12, left: 12, zIndex: 5 }}>
            <Spinner size="small" label="Loading model…" />
          </div>
        )}
        {!loading && data && visibleTables.length === 0 && (
          <div className={styles.empty}>
            <Text>No tables to display.</Text>
          </div>
        )}
        <DiagramSvg
          tables={visibleTables}
          relationships={visibleRels}
          layout={layout}
          pan={pan}
          zoom={zoom}
          onPointerDownNode={onPointerDownNode}
          onToggleCollapsed={toggleCollapsed}
        />
      </div>
    </div>
  );
};

// ── Inner SVG renderer ─────────────────────────────────────────────────────

interface SvgProps {
  tables: DiagramTable[];
  relationships: RelationshipInfo[];
  layout: LayoutMap;
  pan: { x: number; y: number };
  zoom: number;
  onPointerDownNode: (e: React.PointerEvent, name: string) => void;
  onToggleCollapsed: (name: string) => void;
}

const DiagramSvg: React.FC<SvgProps> = (props) => {
  const { tables, relationships, layout, pan, zoom, onPointerDownNode, onToggleCollapsed } = props;

  // Quick lookup for endpoint anchors.
  const cardBox = (name: string): { x: number; y: number; w: number; h: number } | null => {
    const l = layout[name];
    if (!l) return null;
    const t = tables.find((x) => x.name === name);
    if (!t) return null;
    return { x: l.x, y: l.y, w: CARD_WIDTH, h: cardHeight(t, layout) };
  };

  const edges = relationships.map((r, idx) => {
    const a = cardBox(r.fromTable);
    const b = cardBox(r.toTable);
    if (!a || !b) return null;
    const aCx = a.x + a.w / 2;
    const aCy = a.y + a.h / 2;
    const bCx = b.x + b.w / 2;
    const bCy = b.y + b.h / 2;
    const pa = anchor(a.x, a.y, a.w, a.h, bCx, bCy);
    const pb = anchor(b.x, b.y, b.w, b.h, aCx, aCy);
    const mx = (pa.x + pb.x) / 2;
    const my = (pa.y + pb.y) / 2;
    const mult = multiplicityEnd(r.multiplicity);
    const both = (r.crossFilter || "").toLowerCase().includes("both");
    return { idx, r, pa, pb, mx, my, mult, both, active: r.isActive };
  }).filter(Boolean) as Array<{
    idx: number;
    r: RelationshipInfo;
    pa: { x: number; y: number };
    pb: { x: number; y: number };
    mx: number;
    my: number;
    mult: { from: "1" | "*"; to: "1" | "*" };
    both: boolean;
    active: boolean;
  }>;

  return (
    <svg
      width="100%"
      height="100%"
      style={{ display: "block" }}
    >
      <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
        {/* Edges first so cards render above. */}
        {edges.map((e) => (
          <g key={e.idx} opacity={e.active ? 0.95 : 0.55}>
            <line
              x1={e.pa.x}
              y1={e.pa.y}
              x2={e.pb.x}
              y2={e.pb.y}
              stroke={e.active ? tokens.colorBrandStroke1 : tokens.colorNeutralStroke2}
              strokeWidth={1.5}
              strokeDasharray={e.active ? undefined : "5 3"}
            />
            {/* Endpoint cardinality glyphs. */}
            <CardinalityGlyph cx={e.pa.x} cy={e.pa.y} label={e.mult.from} />
            <CardinalityGlyph cx={e.pb.x} cy={e.pb.y} label={e.mult.to} />
            {/* Mid-line filter direction diamond. */}
            <FilterDiamond cx={e.mx} cy={e.my} both={e.both} />
          </g>
        ))}

        {/* Cards. */}
        {tables.map((t) => {
          const l = layout[t.name];
          if (!l) return null;
          const h = cardHeight(t, layout);
          const visibleCols = t.columns.filter((c) => !c.isHidden).slice(0, MAX_COLUMNS_RENDERED);
          const isCalc = t.type === "CalculationGroup" || t.type === "CalculatedTable";
          return (
            <g key={t.name} transform={`translate(${l.x} ${l.y})`}>
              {/* Card body. */}
              <rect
                width={CARD_WIDTH}
                height={h}
                rx={6}
                ry={6}
                fill={tokens.colorNeutralBackground1}
                stroke={t.isHidden ? tokens.colorNeutralStroke2 : tokens.colorBrandStroke1}
                strokeWidth={1}
              />
              {/* Header (drag handle + collapse toggle). */}
              <rect
                width={CARD_WIDTH}
                height={CARD_HEADER_HEIGHT}
                rx={6}
                ry={6}
                fill={isCalc ? tokens.colorPaletteBerryBackground2 : tokens.colorBrandBackground2}
                onPointerDown={(e) => onPointerDownNode(e, t.name)}
                style={{ cursor: "move" }}
              />
              <text
                x={10}
                y={CARD_HEADER_HEIGHT / 2 + 4}
                fontSize={12}
                fontWeight={600}
                fill={tokens.colorNeutralForeground1}
                pointerEvents="none"
              >
                {truncate(t.name, 24)}
              </text>
              <g
                transform={`translate(${CARD_WIDTH - 24} ${CARD_HEADER_HEIGHT / 2 - 6})`}
                style={{ cursor: "pointer" }}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => onToggleCollapsed(t.name)}
              >
                <rect width={16} height={12} fill="transparent" />
                <text x={8} y={10} fontSize={11} textAnchor="middle" fill={tokens.colorNeutralForeground2}>
                  {l.collapsed ? "▾" : "▴"}
                </text>
              </g>
              {/* Columns. */}
              {!l.collapsed && visibleCols.map((c, i) => (
                <g key={c.name} transform={`translate(0 ${CARD_HEADER_HEIGHT + i * CARD_ROW_HEIGHT})`}>
                  <text x={10} y={CARD_ROW_HEIGHT - 5} fontSize={11} fill={tokens.colorNeutralForeground1}>
                    {c.isKey ? "🔑 " : ""}{truncate(c.name, 22)}
                  </text>
                  <text
                    x={CARD_WIDTH - 10}
                    y={CARD_ROW_HEIGHT - 5}
                    fontSize={10}
                    textAnchor="end"
                    fill={tokens.colorNeutralForeground3}
                  >
                    {shortType(c.dataType)}
                  </text>
                </g>
              ))}
              {!l.collapsed && t.columns.filter((c) => !c.isHidden).length > MAX_COLUMNS_RENDERED && (
                <text
                  x={10}
                  y={CARD_HEADER_HEIGHT + MAX_COLUMNS_RENDERED * CARD_ROW_HEIGHT + 12}
                  fontSize={10}
                  fill={tokens.colorNeutralForeground3}
                >
                  + {t.columns.filter((c) => !c.isHidden).length - MAX_COLUMNS_RENDERED} more…
                </text>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
};

const CardinalityGlyph: React.FC<{ cx: number; cy: number; label: "1" | "*" }> = ({ cx, cy, label }) => (
  <g pointerEvents="none">
    <circle cx={cx} cy={cy} r={9} fill={tokens.colorNeutralBackground1} stroke={tokens.colorBrandStroke1} strokeWidth={1} />
    <text x={cx} y={cy + 4} fontSize={11} fontWeight={700} textAnchor="middle" fill={tokens.colorBrandForeground1}>
      {label}
    </text>
  </g>
);

const FilterDiamond: React.FC<{ cx: number; cy: number; both: boolean }> = ({ cx, cy, both }) => {
  const r = 6;
  const points = `${cx},${cy - r} ${cx + r},${cy} ${cx},${cy + r} ${cx - r},${cy}`;
  return (
    <g pointerEvents="none">
      <polygon
        points={points}
        fill={both ? tokens.colorBrandStroke1 : tokens.colorNeutralBackground1}
        stroke={tokens.colorBrandStroke1}
        strokeWidth={1}
      />
      {both && (
        <polygon
          points={`${cx},${cy - 2} ${cx + 2},${cy} ${cx},${cy + 2} ${cx - 2},${cy}`}
          fill={tokens.colorNeutralBackground1}
        />
      )}
    </g>
  );
};

function truncate(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}

function shortType(t: string): string {
  if (!t) return "";
  const s = t.toLowerCase();
  if (s.includes("int")) return "int";
  if (s.includes("decimal") || s.includes("double") || s.includes("number")) return "num";
  if (s.includes("date") || s.includes("time")) return "date";
  if (s.includes("string") || s.includes("text")) return "str";
  if (s.includes("bool")) return "bool";
  return t.length > 6 ? t.slice(0, 6) : t;
}
