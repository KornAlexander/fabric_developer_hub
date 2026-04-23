// WS-M — Prototype page.
//
// Click-to-add visual palette + free-positioned canvas (drag to move,
// handle to resize). Select a visual to bind fields from the loaded
// semantic model. Export the whole thing as a PBIR-lite JSON skeleton.
// Uploading as a real PBIR report is stubbed pending backend bridge.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Dropdown,
  Input,
  Option,
  Spinner,
  Text,
  Title3,
  Badge,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  makeStyles,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import {
  ArrowDownload20Regular,
  CloudArrowUp20Regular,
  Delete20Regular,
  Add20Regular,
  ArrowClockwise20Regular,
} from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import type { ModelData } from "../../types";
import { loadModelData } from "../../services/fabricApi";
import {
  exportPrototypeToPbir,
  downloadJson,
  uploadPrototypeAsReport,
  type PrototypeDocument,
  type PrototypePage,
  type PrototypeVisual,
  type VisualType,
  type FieldRef,
} from "../../services/prototypeApi";

const useStyles = makeStyles({
  root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0, ...shorthands.gap("8px") },
  toolbar: {
    display: "flex", alignItems: "center", ...shorthands.gap("10px"),
    flexWrap: "wrap", ...shorthands.padding("4px"),
  },
  grow: { flex: 1 },
  body: {
    flex: 1, minHeight: 0, display: "flex", ...shorthands.gap("10px"),
  },
  palette: {
    width: "180px", flexShrink: 0,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
    ...shorthands.padding("10px"),
    display: "flex", flexDirection: "column", ...shorthands.gap("6px"),
    overflowY: "auto",
  },
  paletteHead: {
    fontSize: tokens.fontSizeBase200, fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorNeutralForeground2,
    marginBottom: "4px",
  },
  canvasWrap: {
    flex: 1, minWidth: 0, minHeight: 0,
    overflow: "auto",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground2,
  },
  canvas: {
    position: "relative",
    backgroundColor: "#fff",
    backgroundImage:
      "linear-gradient(to right, #eee 1px, transparent 1px)," +
      "linear-gradient(to bottom, #eee 1px, transparent 1px)",
    backgroundSize: "40px 40px",
    margin: "16px",
    boxShadow: tokens.shadow4,
  },
  visualBox: {
    position: "absolute",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke1),
    backgroundColor: tokens.colorNeutralBackground1,
    borderRadius: tokens.borderRadiusSmall,
    boxShadow: tokens.shadow2,
    cursor: "move",
    overflow: "hidden",
    userSelect: "none",
  },
  visualSelected: {
    ...shorthands.border("2px", "solid", tokens.colorBrandBackground),
  },
  visualHeader: {
    ...shorthands.padding("3px", "6px"),
    backgroundColor: tokens.colorNeutralBackground3,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    fontSize: "11px", fontWeight: tokens.fontWeightSemibold,
    display: "flex", alignItems: "center", justifyContent: "space-between",
  },
  visualBody: {
    ...shorthands.padding("6px"),
    fontSize: "11px",
    color: tokens.colorNeutralForeground2,
    height: "calc(100% - 22px)",
    overflow: "hidden",
  },
  fieldChip: {
    display: "inline-block",
    ...shorthands.padding("1px", "4px"),
    marginRight: "3px", marginBottom: "2px",
    backgroundColor: tokens.colorBrandBackground2,
    color: tokens.colorBrandForeground2,
    borderRadius: tokens.borderRadiusSmall,
    fontSize: "10px",
  },
  resizeHandle: {
    position: "absolute", right: 0, bottom: 0,
    width: "10px", height: "10px",
    backgroundColor: tokens.colorNeutralStroke1,
    cursor: "nwse-resize",
  },
  inspector: {
    width: "280px", flexShrink: 0,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
    ...shorthands.padding("10px"),
    display: "flex", flexDirection: "column", ...shorthands.gap("8px"),
    overflowY: "auto",
  },
  fieldRow: {
    display: "flex", alignItems: "center", ...shorthands.gap("6px"),
    ...shorthands.padding("4px", "6px"),
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke3),
    borderRadius: tokens.borderRadiusSmall,
    fontSize: tokens.fontSizeBase200,
  },
  stats: {
    display: "flex", ...shorthands.gap("12px"), alignItems: "center",
    ...shorthands.padding("4px"),
    color: tokens.colorNeutralForeground2, fontSize: tokens.fontSizeBase200,
  },
  empty: {
    display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
    height: "100%", ...shorthands.padding("32px"), ...shorthands.gap("8px"),
    color: tokens.colorNeutralForeground3, textAlign: "center",
  },
});

const VISUAL_TYPES: { kind: VisualType; label: string; icon: string }[] = [
  { kind: "card", label: "Card", icon: "🔢" },
  { kind: "table", label: "Table", icon: "🔳" },
  { kind: "matrix", label: "Matrix", icon: "▦" },
  { kind: "barChart", label: "Bar chart", icon: "▬" },
  { kind: "columnChart", label: "Column chart", icon: "▮" },
  { kind: "lineChart", label: "Line chart", icon: "📈" },
  { kind: "pieChart", label: "Pie chart", icon: "◔" },
  { kind: "slicer", label: "Slicer", icon: "≡" },
];

function uid(): string {
  return Math.random().toString(36).slice(2, 10);
}

function makeVisual(type: VisualType): PrototypeVisual {
  return {
    id: uid(), type,
    title: VISUAL_TYPES.find((v) => v.kind === type)?.label ?? type,
    x: 40, y: 40, width: 320, height: 200, fields: [],
  };
}

function makePage(name: string): PrototypePage {
  return { id: uid(), name, width: 1280, height: 720, visuals: [] };
}

export const PrototypePage: React.FC<PageProps> = ({ auth, workspaceId, datasetId, datasetName }) => {
  const styles = useStyles();

  const [model, setModel] = useState<ModelData | null>(null);
  const [loadingModel, setLoadingModel] = useState(false);
  const [modelErr, setModelErr] = useState("");

  const [reportName, setReportName] = useState("Prototype report");
  const [pages, setPages] = useState<PrototypePage[]>([makePage("Page 1")]);
  const [activePageId, setActivePageId] = useState(pages[0].id);
  const [selectedVisualId, setSelectedVisualId] = useState<string | null>(null);
  const [status, setStatus] = useState("");

  const canvasRef = useRef<HTMLDivElement>(null);

  const activePage = useMemo(
    () => pages.find((p) => p.id === activePageId) ?? pages[0],
    [pages, activePageId],
  );
  const selectedVisual = useMemo(
    () => activePage.visuals.find((v) => v.id === selectedVisualId) ?? null,
    [activePage, selectedVisualId],
  );

  const loadModel = useCallback(async () => {
    if (!workspaceId || !datasetId) return;
    setLoadingModel(true); setModelErr("");
    try {
      const m = await loadModelData(auth, workspaceId, datasetId, datasetName ?? "");
      setModel(m);
    } catch (e) {
      setModelErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingModel(false);
    }
  }, [auth, workspaceId, datasetId, datasetName]);

  useEffect(() => { void loadModel(); }, [loadModel]);

  const updateActivePage = useCallback((updater: (p: PrototypePage) => PrototypePage) => {
    setPages((prev) => prev.map((p) => (p.id === activePageId ? updater(p) : p)));
  }, [activePageId]);

  const addVisual = useCallback((type: VisualType) => {
    const v = makeVisual(type);
    updateActivePage((p) => ({ ...p, visuals: [...p.visuals, v] }));
    setSelectedVisualId(v.id);
  }, [updateActivePage]);

  const updateVisual = useCallback((id: string, patch: Partial<PrototypeVisual>) => {
    updateActivePage((p) => ({
      ...p,
      visuals: p.visuals.map((v) => (v.id === id ? { ...v, ...patch } : v)),
    }));
  }, [updateActivePage]);

  const deleteVisual = useCallback((id: string) => {
    updateActivePage((p) => ({ ...p, visuals: p.visuals.filter((v) => v.id !== id) }));
    if (selectedVisualId === id) setSelectedVisualId(null);
  }, [updateActivePage, selectedVisualId]);

  /* ------------------------------------------------------------------ */
  /* Drag + resize                                                       */
  /* ------------------------------------------------------------------ */
  const dragState = useRef<null | {
    id: string;
    mode: "move" | "resize";
    startX: number; startY: number;
    origX: number; origY: number;
    origW: number; origH: number;
  }>(null);

  const onMouseDownVisual = (e: React.MouseEvent, v: PrototypeVisual, mode: "move" | "resize") => {
    e.stopPropagation();
    setSelectedVisualId(v.id);
    dragState.current = {
      id: v.id, mode,
      startX: e.clientX, startY: e.clientY,
      origX: v.x, origY: v.y, origW: v.width, origH: v.height,
    };
    const onMove = (ev: MouseEvent) => {
      const s = dragState.current;
      if (!s) return;
      const dx = ev.clientX - s.startX;
      const dy = ev.clientY - s.startY;
      if (s.mode === "move") {
        updateVisual(s.id, {
          x: Math.max(0, s.origX + dx),
          y: Math.max(0, s.origY + dy),
        });
      } else {
        updateVisual(s.id, {
          width: Math.max(80, s.origW + dx),
          height: Math.max(60, s.origH + dy),
        });
      }
    };
    const onUp = () => {
      dragState.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  /* ------------------------------------------------------------------ */
  /* Field options from the loaded model                                 */
  /* ------------------------------------------------------------------ */
  const fieldOptions = useMemo(() => {
    if (!model) return [] as FieldRef[];
    const out: FieldRef[] = [];
    for (const [tName, t] of Object.entries(model.tables)) {
      for (const cName of Object.keys(t.columns)) {
        out.push({ role: "Category", tableName: tName, propertyName: cName, kind: "column" });
      }
      for (const mName of Object.keys(t.measures)) {
        out.push({ role: "Values", tableName: tName, propertyName: mName, kind: "measure" });
      }
    }
    return out;
  }, [model]);

  const addFieldToSelected = useCallback((key: string) => {
    if (!selectedVisual) return;
    const opt = fieldOptions.find((f) => `${f.kind}|${f.tableName}|${f.propertyName}` === key);
    if (!opt) return;
    if (selectedVisual.fields.some((f) => f.tableName === opt.tableName && f.propertyName === opt.propertyName)) return;
    updateVisual(selectedVisual.id, { fields: [...selectedVisual.fields, opt] });
  }, [selectedVisual, fieldOptions, updateVisual]);

  const removeFieldFromSelected = useCallback((idx: number) => {
    if (!selectedVisual) return;
    const next = selectedVisual.fields.slice();
    next.splice(idx, 1);
    updateVisual(selectedVisual.id, { fields: next });
  }, [selectedVisual, updateVisual]);

  /* ------------------------------------------------------------------ */
  /* Export                                                              */
  /* ------------------------------------------------------------------ */
  const buildDocument = useCallback((): PrototypeDocument => ({
    version: "pbir-skeleton/1.0",
    reportName,
    datasetName, datasetId, workspaceId,
    pages,
  }), [reportName, datasetName, datasetId, workspaceId, pages]);

  const onExport = () => {
    const doc = buildDocument();
    const json = exportPrototypeToPbir(doc);
    const safe = reportName.replace(/[^A-Za-z0-9._-]+/g, "_");
    downloadJson(`${safe || "prototype"}.pbir-skeleton.json`, json);
    setStatus(`Exported ${safe}.pbir-skeleton.json (${json.length.toLocaleString()} chars).`);
  };

  const onUpload = async () => {
    if (!workspaceId) return;
    const res = await uploadPrototypeAsReport(auth, workspaceId, buildDocument());
    setStatus(res.message);
  };

  /* ------------------------------------------------------------------ */
  /* Render                                                              */
  /* ------------------------------------------------------------------ */
  if (!workspaceId || !datasetId) {
    return (
      <div className={styles.empty}>
        <Title3>Prototype</Title3>
        <Text>Select a workspace and a semantic model above to start prototyping.</Text>
      </div>
    );
  }

  const visualCount = pages.reduce((n, p) => n + p.visuals.length, 0);

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        <Input
          value={reportName}
          onChange={(_, d) => setReportName(d.value)}
          style={{ minWidth: "220px" }}
          placeholder="Report name…"
        />
        <Button icon={<ArrowClockwise20Regular />} onClick={loadModel} disabled={loadingModel}>
          Reload model
        </Button>
        <div className={styles.grow} />
        <Button icon={<ArrowDownload20Regular />} appearance="primary" onClick={onExport}>
          Export JSON
        </Button>
        <Button icon={<CloudArrowUp20Regular />} onClick={onUpload}>
          Upload as report
        </Button>
      </div>

      <div className={styles.stats}>
        {loadingModel && <><Spinner size="tiny" /><span>Loading model…</span></>}
        {!loadingModel && model && (
          <>
            <span>{Object.keys(model.tables).length} tables</span>
            <span>·</span>
            <span>{fieldOptions.length} bindable fields</span>
            <span>·</span>
            <Badge appearance="tint" color="informative">
              {pages.length} page{pages.length === 1 ? "" : "s"} / {visualCount} visual{visualCount === 1 ? "" : "s"}
            </Badge>
            <span style={{ marginLeft: "auto", color: tokens.colorNeutralForeground3 }}>{datasetName}</span>
          </>
        )}
      </div>

      {modelErr && (
        <MessageBar intent="error">
          <MessageBarBody><MessageBarTitle>Load failed</MessageBarTitle> {modelErr}</MessageBarBody>
        </MessageBar>
      )}
      {status && (
        <MessageBar intent="info">
          <MessageBarBody><MessageBarTitle>Prototype</MessageBarTitle> {status}</MessageBarBody>
        </MessageBar>
      )}

      <div className={styles.body}>
        {/* Palette */}
        <div className={styles.palette}>
          <div className={styles.paletteHead}>Visuals</div>
          {VISUAL_TYPES.map((v) => (
            <Button
              key={v.kind}
              icon={<Add20Regular />}
              onClick={() => addVisual(v.kind)}
              appearance="secondary"
            >
              <span style={{ marginRight: 6 }}>{v.icon}</span>{v.label}
            </Button>
          ))}
        </div>

        {/* Canvas */}
        <div className={styles.canvasWrap}>
          <div
            ref={canvasRef}
            className={styles.canvas}
            style={{ width: activePage.width, height: activePage.height }}
            onMouseDown={() => setSelectedVisualId(null)}
          >
            {activePage.visuals.map((v) => {
              const isSel = v.id === selectedVisualId;
              const cls = `${styles.visualBox} ${isSel ? styles.visualSelected : ""}`;
              return (
                <div
                  key={v.id}
                  className={cls}
                  style={{ left: v.x, top: v.y, width: v.width, height: v.height }}
                  onMouseDown={(e) => onMouseDownVisual(e, v, "move")}
                >
                  <div className={styles.visualHeader}>
                    <span>{VISUAL_TYPES.find((t) => t.kind === v.type)?.icon} {v.title}</span>
                    <Button
                      size="small"
                      appearance="subtle"
                      icon={<Delete20Regular />}
                      onClick={(e) => { e.stopPropagation(); deleteVisual(v.id); }}
                    />
                  </div>
                  <div className={styles.visualBody}>
                    {v.fields.length === 0 ? <em>No fields bound</em> : v.fields.map((f, i) => (
                      <span key={i} className={styles.fieldChip}>{f.tableName}.{f.propertyName}</span>
                    ))}
                  </div>
                  <div
                    className={styles.resizeHandle}
                    onMouseDown={(e) => onMouseDownVisual(e, v, "resize")}
                  />
                </div>
              );
            })}
          </div>
        </div>

        {/* Inspector */}
        <div className={styles.inspector}>
          <div className={styles.paletteHead}>Inspector</div>
          {!selectedVisual && <Text size={200}>Select a visual to bind fields.</Text>}
          {selectedVisual && (
            <>
              <Input
                value={selectedVisual.title}
                onChange={(_, d) => updateVisual(selectedVisual.id, { title: d.value })}
                placeholder="Visual title"
              />
              <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>
                {selectedVisual.type} · {selectedVisual.width}×{selectedVisual.height}
              </Text>

              <Text size={200} weight="semibold">Bind a field</Text>
              <Dropdown
                placeholder="Pick a column or measure…"
                onOptionSelect={(_, d) => d.optionValue && addFieldToSelected(d.optionValue)}
              >
                {fieldOptions.map((f) => {
                  const key = `${f.kind}|${f.tableName}|${f.propertyName}`;
                  return (
                    <Option key={key} value={key} text={`${f.tableName}.${f.propertyName}`}>
                      <span style={{ fontFamily: "monospace" }}>
                        {f.kind === "measure" ? "ƒ" : "#"} {f.tableName}[{f.propertyName}]
                      </span>
                    </Option>
                  );
                })}
              </Dropdown>

              <Text size={200} weight="semibold">Bound fields</Text>
              {selectedVisual.fields.length === 0 && <Text size={200}>None</Text>}
              {selectedVisual.fields.map((f, i) => (
                <div key={i} className={styles.fieldRow}>
                  <span style={{ flex: 1 }}>
                    <Badge appearance="tint" color={f.kind === "measure" ? "brand" : "informative"}>{f.role}</Badge>
                    &nbsp;{f.tableName}.{f.propertyName}
                  </span>
                  <Button
                    size="small" appearance="subtle"
                    icon={<Delete20Regular />}
                    onClick={() => removeFieldFromSelected(i)}
                  />
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
