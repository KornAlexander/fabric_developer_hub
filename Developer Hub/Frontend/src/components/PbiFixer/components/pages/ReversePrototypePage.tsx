// WS-N — Reverse Prototype page.
//
// Mirrors the Python ``_report_prototype.generate_report_prototype``
// flow: load an existing PBI report (PBIR), extract its pages and
// visuals (position / size / type / title) and render a read-only
// wireframe gallery. The same payload is then exportable as PBIR-lite
// JSON, an Excalidraw scene, or an SVG that drag-drops into Figma —
// reusing the exporters shipped in WS-M (Prototype).
//
// Field bindings are intentionally NOT extracted: the per-visual
// query.json contains projections in DAX form and mapping them back
// to a stable table[column] reference requires resolving against the
// model. Out of scope for v1; the user can re-bind in the canvas.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Button,
  Spinner,
  Switch,
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
  ArrowClockwise20Regular,
  ArrowDownload20Regular,
} from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import type { ReportData } from "../../types/report";
import { loadReportDefinition } from "../../services/fabricApi";
import {
  reportToPrototypeDocument,
  exportPrototypeToPbir,
  exportPrototypeToExcalidraw,
  exportPrototypeToSvg,
  downloadJson,
  downloadText,
  type PrototypeDocument,
} from "../../services/prototypeApi";

const useStyles = makeStyles({
  root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0, ...shorthands.gap("8px") },
  toolbar: {
    display: "flex", alignItems: "center", ...shorthands.gap("10px"),
    flexWrap: "wrap", ...shorthands.padding("4px"),
  },
  grow: { flex: 1 },
  stats: {
    display: "flex", alignItems: "center", ...shorthands.gap("12px"),
    ...shorthands.padding("4px"),
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
  },
  body: {
    flex: 1, minHeight: 0, overflow: "auto",
    ...shorthands.padding("8px"),
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground2,
    display: "flex", flexDirection: "column", ...shorthands.gap("16px"),
  },
  pageCard: {
    backgroundColor: tokens.colorNeutralBackground1,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    ...shorthands.padding("12px"),
  },
  pageHeader: {
    display: "flex", alignItems: "baseline", ...shorthands.gap("8px"),
    marginBottom: "8px",
  },
  pageTitle: { fontWeight: tokens.fontWeightSemibold, fontSize: tokens.fontSizeBase400 },
  pageMeta: { color: tokens.colorNeutralForeground3, fontSize: tokens.fontSizeBase200 },
  canvasWrap: {
    width: "100%",
    overflow: "auto",
  },
  canvas: {
    position: "relative",
    backgroundColor: "#ffffff",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke3),
    borderRadius: tokens.borderRadiusSmall,
  },
  visual: {
    position: "absolute",
    boxSizing: "border-box",
    ...shorthands.border("1px", "solid", "#475569"),
    borderRadius: "4px",
    ...shorthands.padding("6px", "8px"),
    fontSize: "11px",
    color: "#0f172a",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    ...shorthands.gap("2px"),
  },
  visualTitle: { fontWeight: tokens.fontWeightSemibold, fontSize: "12px", lineHeight: "14px" },
  visualType: { fontSize: "10px", color: "#475569", lineHeight: "12px" },
  empty: {
    display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
    height: "100%", ...shorthands.padding("32px"), ...shorthands.gap("8px"),
    color: tokens.colorNeutralForeground3, textAlign: "center",
  },
});

/** Same palette as the Prototype canvas / exporters. */
const VISUAL_FILL: Record<string, string> = {
  card:        "#dbeafe",
  table:       "#fef3c7",
  matrix:      "#fde68a",
  barChart:    "#bbf7d0",
  columnChart: "#a7f3d0",
  lineChart:   "#bae6fd",
  pieChart:    "#fbcfe8",
  slicer:      "#e9d5ff",
};

/** Cap the on-screen canvas width so very wide reports don't blow out
 *  the layout. The exports always use the original page dimensions. */
const PREVIEW_MAX_W = 720;

export const ReversePrototypePage: React.FC<PageProps> = ({
  auth, workspaceId, reportId, reportName,
}) => {
  const styles = useStyles();
  const [report, setReport] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [includeHidden, setIncludeHidden] = useState(false);
  const [status, setStatus] = useState("");

  const loadReport = useCallback(async () => {
    if (!workspaceId || !reportId) return;
    setLoading(true);
    setErr("");
    setStatus("");
    try {
      const r = await loadReportDefinition(auth, workspaceId, reportId, reportName ?? "");
      setReport(r);
      const pageCount = Object.keys(r.pages).length;
      const visualCount = Object.values(r.pages).reduce(
        (n, p) => n + Object.keys(p.visuals).length, 0,
      );
      setStatus(`Loaded ${pageCount} page${pageCount === 1 ? "" : "s"} / ${visualCount} visual${visualCount === 1 ? "" : "s"}.`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth, workspaceId, reportId, reportName]);

  useEffect(() => { void loadReport(); }, [loadReport]);

  const doc: PrototypeDocument | null = useMemo(() => {
    if (!report) return null;
    return reportToPrototypeDocument(report, reportName ?? "Reverse-prototype", { includeHidden });
  }, [report, reportName, includeHidden]);

  const pageCount = doc?.pages.length ?? 0;
  const visualCount = useMemo(
    () => doc?.pages.reduce((n, p) => n + p.visuals.length, 0) ?? 0,
    [doc],
  );

  const safeName = (reportName ?? "report").replace(/[^A-Za-z0-9._-]+/g, "_") || "report";

  const onExportJson = () => {
    if (!doc) return;
    const json = exportPrototypeToPbir(doc);
    downloadJson(`${safeName}.reverse-prototype.json`, json);
    setStatus(`Exported ${safeName}.reverse-prototype.json (${json.length.toLocaleString()} chars).`);
  };
  const onExportExcalidraw = () => {
    if (!doc) return;
    const scene = exportPrototypeToExcalidraw(doc);
    downloadText(`${safeName}.reverse.excalidraw`, scene, "application/json");
    setStatus(`Exported ${safeName}.reverse.excalidraw — open at excalidraw.com (File ▸ Open).`);
  };
  const onExportSvg = () => {
    if (!doc) return;
    const svg = exportPrototypeToSvg(doc);
    downloadText(`${safeName}.reverse.svg`, svg, "image/svg+xml");
    setStatus(`Exported ${safeName}.reverse.svg — drag onto a Figma canvas to import.`);
  };

  if (!workspaceId || !reportId) {
    return (
      <div className={styles.empty}>
        <Title3>Reverse Prototype</Title3>
        <Text>Select a workspace and a report above to extract its layout.</Text>
      </div>
    );
  }

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        <Button icon={<ArrowClockwise20Regular />} onClick={loadReport} disabled={loading}>
          {loading ? "Loading…" : "Reload report"}
        </Button>
        <Switch
          checked={includeHidden}
          onChange={(_, d) => setIncludeHidden(!!d.checked)}
          label="Include hidden"
          disabled={loading}
        />
        <div className={styles.grow} />
        <Button
          icon={<ArrowDownload20Regular />}
          appearance="primary"
          onClick={onExportJson}
          disabled={!doc || pageCount === 0}
        >
          Export JSON
        </Button>
        <Button
          icon={<ArrowDownload20Regular />}
          onClick={onExportExcalidraw}
          disabled={!doc || pageCount === 0}
          title="Excalidraw scene (.excalidraw)"
        >
          Export Excalidraw
        </Button>
        <Button
          icon={<ArrowDownload20Regular />}
          onClick={onExportSvg}
          disabled={!doc || pageCount === 0}
          title="SVG — drag onto a Figma canvas"
        >
          Export SVG (Figma)
        </Button>
      </div>

      <div className={styles.stats}>
        {loading && <><Spinner size="tiny" /><span>Loading report definition…</span></>}
        {!loading && doc && (
          <>
            <Badge appearance="tint" color="informative">
              {pageCount} page{pageCount === 1 ? "" : "s"} / {visualCount} visual{visualCount === 1 ? "" : "s"}
            </Badge>
            <span>·</span>
            <span>{reportName}</span>
            {status && (
              <>
                <span>·</span>
                <span style={{ color: tokens.colorNeutralForeground3 }}>{status}</span>
              </>
            )}
          </>
        )}
      </div>

      {err && (
        <MessageBar intent="error">
          <MessageBarBody>
            <MessageBarTitle>Failed to load report</MessageBarTitle>
            {err}
          </MessageBarBody>
        </MessageBar>
      )}

      <div className={styles.body}>
        {!loading && doc && pageCount === 0 && (
          <Text>No pages found in the report definition.</Text>
        )}
        {doc?.pages.map((pg) => {
          const scale = Math.min(1, PREVIEW_MAX_W / Math.max(pg.width, 1));
          return (
            <div key={pg.id} className={styles.pageCard}>
              <div className={styles.pageHeader}>
                <span className={styles.pageTitle}>{pg.name}</span>
                <span className={styles.pageMeta}>
                  {pg.width} × {pg.height} · {pg.visuals.length} visual{pg.visuals.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className={styles.canvasWrap}>
                <div
                  className={styles.canvas}
                  style={{
                    width: pg.width * scale,
                    height: pg.height * scale,
                  }}
                >
                  {pg.visuals.map((v) => (
                    <div
                      key={v.id}
                      className={styles.visual}
                      style={{
                        left: v.x * scale,
                        top: v.y * scale,
                        width: v.width * scale,
                        height: v.height * scale,
                        backgroundColor: VISUAL_FILL[v.type] ?? "#e5e7eb",
                      }}
                      title={`${v.title} (${v.type})`}
                    >
                      <div className={styles.visualTitle}>{v.title}</div>
                      <div className={styles.visualType}>{v.type}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
