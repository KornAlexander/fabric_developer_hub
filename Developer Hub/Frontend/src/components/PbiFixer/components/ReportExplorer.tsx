// ReportExplorer — React component (FluentUI)
// Mirrors report_explorer_tab() from _report_explorer.py

import React, { useState, useCallback, useMemo, useEffect, useRef } from "react";
import {
  Button,
  Input,
  Dropdown,
  Option,
  Spinner,
  makeStyles,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import {
  Search20Regular,
  ArrowExpand20Regular,
  ArrowCollapseAll20Regular,
  Wrench20Regular,
  ChartMultiple20Regular,
} from "@fluentui/react-icons";
import {
  ReportData,
  TreeBuildResult,
  ScanResult,
} from "../types";
import {
  buildReportTree,
  getPageProperties,
  getVisualProperties,
  filterTreeOptions,
  BORDER_COLOR,
  GRAY_COLOR,
  ICON_ACCENT,
} from "../utils";
// Hero design ported from AgentHub Sessions tab.
import {
  listReports,
  loadReportDefinition,
  resolveWorkspaceId,
  getReportEmbedToken,
  PbiAuth,
} from "../services";
import { updateVisualProperties } from "../services/fixersApi";
import { FIXERS, type Fixer, type FixerContext, type FixerResult } from "../fixers";
import { runReportBpa, type BpaFinding } from "../services/reportBpaApi";
import { ReportScanResults } from "./ReportScanResults";

// PBIR ``visualType`` catalogue (subset; covers the most common edits
// like Pie → Clustered Bar). Values are the internal strings the engine
// writes into ``visual.json``.
const VISUAL_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "barChart", label: "Stacked Bar" },
  { value: "clusteredBarChart", label: "Clustered Bar" },
  { value: "hundredPercentStackedBarChart", label: "100% Stacked Bar" },
  { value: "columnChart", label: "Stacked Column" },
  { value: "clusteredColumnChart", label: "Clustered Column" },
  { value: "hundredPercentStackedColumnChart", label: "100% Stacked Column" },
  { value: "lineChart", label: "Line" },
  { value: "areaChart", label: "Area" },
  { value: "stackedAreaChart", label: "Stacked Area" },
  { value: "lineStackedColumnComboChart", label: "Line + Stacked Column" },
  { value: "lineClusteredColumnComboChart", label: "Line + Clustered Column" },
  { value: "ribbonChart", label: "Ribbon" },
  { value: "waterfallChart", label: "Waterfall" },
  { value: "funnel", label: "Funnel" },
  { value: "scatterChart", label: "Scatter" },
  { value: "pieChart", label: "Pie" },
  { value: "donutChart", label: "Donut" },
  { value: "treemap", label: "Treemap" },
  { value: "map", label: "Map" },
  { value: "filledMap", label: "Filled Map" },
  { value: "shapeMap", label: "Shape Map" },
  { value: "tableEx", label: "Table" },
  { value: "pivotTable", label: "Matrix" },
  { value: "card", label: "Card" },
  { value: "multiRowCard", label: "Multi-row Card" },
  { value: "kpi", label: "KPI" },
  { value: "gauge", label: "Gauge" },
  { value: "slicer", label: "Slicer" },
  { value: "advancedSlicerVisual", label: "Slicer (new)" },
  { value: "textbox", label: "Text box" },
  { value: "image", label: "Image" },
  { value: "shape", label: "Shape" },
  { value: "actionButton", label: "Button" },
  { value: "decompositionTreeVisual", label: "Decomposition Tree" },
  { value: "qnaVisual", label: "Q&A" },
];

function visualTypeLabel(value: string): string {
  return VISUAL_TYPE_OPTIONS.find((o) => o.value === value)?.label ?? value;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const useStyles = makeStyles({
  root: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    ...shorthands.gap("12px"),
    backgroundColor: "#faf9f8",
    ...shorthands.padding("4px", "24px", "20px", "24px"),
    ...shorthands.margin("-8px", "-24px", "-24px", "-24px"),
    overflow: "hidden",
  },
  hero: {
    display: "flex",
    flexDirection: "column",
    ...shorthands.gap("4px"),
    marginBottom: "4px",
  },
  heroEyebrowRow: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("8px"),
  },
  heroEyebrow: {
    fontSize: "12px",
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "#0078d4",
  },
  heroVersion: {
    fontSize: "12px",
    color: tokens.colorNeutralForeground2,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    ...shorthands.padding("2px", "6px"),
    ...shorthands.border("1px", "solid", "rgba(192, 199, 212, 0.4)"),
    ...shorthands.borderRadius("4px"),
    backgroundColor: tokens.colorNeutralBackground3,
  },
  inlineConnection: {
    display: "flex",
    alignItems: "flex-end",
    ...shorthands.gap("12px"),
    flexWrap: "wrap",
    ...shorthands.margin("4px", "0", "8px", "0"),
  },
  heroTitle: {
    fontSize: "28px",
    fontWeight: 700,
    lineHeight: 1.15,
    margin: 0,
    backgroundImage: "linear-gradient(95deg, #1a1c1c 0%, #004883 72%, #0078d4 100%)",
    WebkitBackgroundClip: "text",
    backgroundClip: "text",
    color: "transparent",
  },
  heroSubtitle: {
    fontSize: "14px",
    color: "#5a5e62",
    margin: 0,
    maxWidth: "720px",
  },
  loadCta: {
    backgroundImage: "linear-gradient(135deg, #005faa 0%, #0078d4 100%)",
    backgroundColor: "#0078d4",
    color: "#ffffff",
    border: "none",
    "&:hover": {
      backgroundImage: "linear-gradient(135deg, #004883 0%, #0066b8 100%)",
      backgroundColor: "#0066b8",
      color: "#ffffff",
    },
    "&:active": {
      backgroundImage: "linear-gradient(135deg, #003a6b 0%, #005faa 100%)",
      color: "#ffffff",
    },
  },
  toolbar: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("8px"),
    flexWrap: "wrap",
  },
  mainLayout: {
    display: "flex",
    ...shorthands.gap("8px"),
    flex: 1,
    minHeight: 0,
  },
  treePanel: {
    display: "flex",
    flexDirection: "column",
    width: "320px",
    minWidth: "280px",
    ...shorthands.gap("4px"),
  },
  treeList: {
    flex: 1,
    minHeight: "300px",
    maxHeight: "520px",
    overflowY: "auto",
    overflowX: "hidden",
    ...shorthands.border("1px", "solid", BORDER_COLOR),
    ...shorthands.borderRadius("8px"),
    backgroundColor: "#ffffff",
    fontSize: "12px",
  },
  treeItem: {
    ...shorthands.padding("2px", "8px"),
    cursor: "pointer",
    whiteSpace: "nowrap",
    "&:hover": {
      backgroundColor: "#f0f0f0",
    },
  },
  treeItemSelected: {
    backgroundColor: `${ICON_ACCENT}22`,
    fontWeight: "600",
  },
  rightPanel: {
    display: "flex",
    flexDirection: "column",
    flex: 1,
    ...shorthands.gap("8px"),
    minWidth: 0,
    minHeight: 0,
    overflow: "hidden",
  },
  previewPanel: {
    ...shorthands.border("1px", "solid", BORDER_COLOR),
    ...shorthands.borderRadius("8px"),
    ...shorthands.padding("8px"),
    backgroundColor: "#ffffff",
    boxShadow: "0 1px 2px rgba(0, 0, 0, 0.04)",
    flex: 1,
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
    position: "relative",
    isolation: "isolate" as const,
    overflow: "hidden",
  },
  previewSurface: {
    flex: 1,
    minHeight: 0,
    display: "flex",
    alignItems: "stretch",
    justifyContent: "stretch",
    overflow: "hidden",
    backgroundColor: "#ffffff",
    ...shorthands.border("1px", "solid", BORDER_COLOR),
    ...shorthands.borderRadius("6px"),
  },
  previewEmpty: {
    color: GRAY_COLOR,
    fontSize: "13px",
    fontStyle: "italic",
    flex: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    textAlign: "center",
    ...shorthands.padding("24px"),
  },
  propertiesPanel: {
    ...shorthands.border("1px", "solid", BORDER_COLOR),
    ...shorthands.borderRadius("8px"),
    ...shorthands.padding("8px"),
    backgroundColor: "#ffffff",
    boxShadow: "0 1px 2px rgba(0, 0, 0, 0.04)",
    flex: "0 0 auto",
    minHeight: "200px",
    maxHeight: "360px",
    overflowY: "auto",
    position: "relative",
    zIndex: 1,
  },
  sectionLabel: {
    // v0.70 — mirror Fluent <Field label="…"> styling used by the
    // Workspace / Semantic Model toolbar fields (small neutral label,
    // no uppercase, no accent colour) instead of the previous
    // accented all-caps section header.
    fontSize: "14px",
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorNeutralForeground1,
    marginBottom: "4px",
  },
  propRow: {
    display: "flex",
    ...shorthands.padding("3px", "0"),
    fontSize: "13px",
  },
  propLabel: {
    fontWeight: "600",
    // WS-O #8: align with AgentHub neutral foreground tokens — was "#555"
    color: tokens.colorNeutralForeground2,
    whiteSpace: "nowrap",
    minWidth: "120px",
    paddingRight: "10px",
  },
  propValue: {
    wordBreak: "break-word",
  },
  statusBar: {
    fontSize: "13px",
    ...shorthands.padding("4px", "8px"),
    ...shorthands.borderRadius("6px"),
  },
  saveRow: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("4px"),
    marginTop: "4px",
  },
});

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface ReportPreviewProps {
  reportData: ReportData | null;
  selectedKey: string | null;
  auth: PbiAuth;
  /** Bumped by the parent to force a fresh embed (Load Report / post-Save). */
  refreshNonce?: number;
}

/**
 * Live, interactive Power BI report embed (mirrors the original Python
 * Fixer's `powerbiclient.Report` widget).
 *
 * Embedding strategy: we mint a short-lived **embed token** server-side
 * via `POST /groups/{ws}/reports/{id}/GenerateToken` (uses our existing
 * OBO PBI token in the proxy) and hand it to the official
 * `powerbi-client` SDK. The SDK is loaded once from a CDN to avoid an
 * npm dep. This bypasses the third-party-cookie sign-in prompt that
 * `autoAuth=true` would otherwise show inside Fabric's iframe-in-iframe
 * context.
 *
 * When the user picks a different page in the tree we don't re-embed —
 * we call `report.setPage(pageName)` for a smooth swap.
 */

// ── powerbi-client SDK loader ────────────────────────────────────────
const POWERBI_SDK_URL =
  "https://cdn.jsdelivr.net/npm/powerbi-client@2.23.1/dist/powerbi.min.js";
let pbiSdkPromise: Promise<any> | null = null;
function loadPowerBiSdk(): Promise<any> {
  const w = window as any;
  if (w["powerbi-client"]) return Promise.resolve(w["powerbi-client"]);
  if (w.powerbi && w["powerbi-client"] === undefined) {
    // Older bundle exposes only `window.powerbi`; that's the singleton service.
    return Promise.resolve({ service: w.powerbi, models: w["powerbi-models"] });
  }
  if (pbiSdkPromise) return pbiSdkPromise;
  pbiSdkPromise = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = POWERBI_SDK_URL;
    s.async = true;
    s.onload = () => {
      const win = window as any;
      // The CDN bundle exposes `powerbi` (service singleton) and
      // `powerbi-client` (namespace with `models`, `Report`, …).
      const ns = win["powerbi-client"] ?? win["powerbi-client"];
      if (!win.powerbi) {
        reject(new Error("powerbi-client loaded but window.powerbi missing"));
        return;
      }
      resolve({
        service: win.powerbi,
        models: ns?.models ?? win["powerbi-client"]?.models ?? win.models,
        namespace: ns,
      });
    };
    s.onerror = () => reject(new Error(`Failed to load Power BI SDK from ${POWERBI_SDK_URL}`));
    document.head.appendChild(s);
  });
  return pbiSdkPromise;
}

const ReportPreview: React.FC<ReportPreviewProps> = ({
  reportData,
  selectedKey,
  auth,
  refreshNonce,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const reportRef = useRef<any>(null);
  const sdkRef = useRef<any>(null);
  const [embedError, setEmbedError] = useState<string | null>(null);
  const [embedLoading, setEmbedLoading] = useState(false);

  // Resolve which page to focus from the current selection.
  const focusedPage = useMemo(() => {
    if (!reportData) return null;
    if (selectedKey?.startsWith("page:")) {
      return selectedKey.slice("page:".length);
    }
    if (selectedKey?.startsWith("visual:")) {
      const parts = selectedKey.slice("visual:".length).split(":");
      return parts[0] ?? null;
    }
    return null;
  }, [reportData, selectedKey]);

  // Embed (or re-embed) the report whenever the report id / workspace changes.
  useEffect(() => {
    if (!reportData?.reportId || !reportData?.workspaceId) return;
    let cancelled = false;
    (async () => {
      setEmbedError(null);
      setEmbedLoading(true);
      try {
        const sdk = await loadPowerBiSdk();
        if (cancelled) return;
        sdkRef.current = sdk;
        const tokenInfo = await getReportEmbedToken(
          auth,
          reportData.workspaceId,
          reportData.reportId,
        );
        if (cancelled || !containerRef.current) return;
        const models = sdk.models;
        const config: any = {
          type: "report",
          id: reportData.reportId,
          embedUrl: tokenInfo.embedUrl,
          accessToken: tokenInfo.token,
          tokenType: models?.TokenType?.Embed ?? 1, // 1 = Embed
          permissions: models?.Permissions?.Read ?? 0,
          settings: {
            panes: {
              filters: { visible: false },
              // The left-hand tree handles page navigation; hiding the
              // in-iframe page-tab strip avoids it overlapping the
              // Properties panel below the preview.
              pageNavigation: { visible: false },
            },
            background: models?.BackgroundType?.Transparent ?? 1,
          },
        };
        if (focusedPage) config.pageName = focusedPage;
        // Reset any previous embed in this container.
        try { sdk.service.reset(containerRef.current); } catch { /* ignore */ }
        const report = sdk.service.embed(containerRef.current, config);
        reportRef.current = report;
        report.off("loaded");
        report.on("loaded", () => { if (!cancelled) setEmbedLoading(false); });
        report.off("error");
        report.on("error", (evt: any) => {
          if (cancelled) return;
          setEmbedError(evt?.detail?.message || "Embed error");
          setEmbedLoading(false);
        });
      } catch (e: any) {
        if (!cancelled) {
          setEmbedError(e?.message || String(e));
          setEmbedLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
      try {
        if (sdkRef.current && containerRef.current) {
          sdkRef.current.service.reset(containerRef.current);
        }
      } catch { /* ignore */ }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportData?.reportId, reportData?.workspaceId, auth.fabricToken, refreshNonce]);

  // When the user picks a different page, swap pages on the existing embed
  // (no re-mint of the token).
  useEffect(() => {
    const report = reportRef.current;
    if (!report || !focusedPage) return;
    try {
      const maybe = report.setPage(focusedPage);
      if (maybe && typeof maybe.catch === "function") {
        maybe.catch(() => { /* page may not exist yet */ });
      }
    } catch { /* ignore */ }
  }, [focusedPage]);

  if (!reportData) {
    return <span>Load a report to see the live preview</span>;
  }
  if (!reportData.reportId || !reportData.workspaceId) {
    return <span>Report metadata missing — cannot embed</span>;
  }

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <div
        ref={containerRef}
        style={{ width: "100%", height: "100%" }}
      />
      {embedLoading && (
        <div style={{
          position: "absolute", inset: 0, display: "flex", alignItems: "center",
          justifyContent: "center", background: "rgba(255,255,255,0.6)", pointerEvents: "none",
        }}>
          <Spinner size="medium" label="Embedding report…" />
        </div>
      )}
      {embedError && (
        <div style={{
          position: "absolute", left: 12, right: 12, bottom: 12,
          padding: "8px 12px", background: "#fde7e9", color: "#a4262c",
          border: "1px solid #f1bbbf", borderRadius: 4, fontSize: 12,
        }}>
          Embed failed: {embedError}
        </div>
      )}
    </div>
  );
};

export interface ReportExplorerProps {
  auth: PbiAuth;
  workspace: string;
  reportName: string;
  /** Optional resolved Fabric report id. Skips name-based lookup. */
  reportId?: string;
  onNavigateToModel?: (key: string) => void;
  /** Inline connection picker (Workspace + Report) injected by
   *  PbiFixerPage so it sits between the description and the Load Report
   *  toolbar instead of in the page-level chrome. */
  connectionSlot?: React.ReactNode;
  /** Version badge shown next to the eyebrow ("POWER BI FIXER"). */
  version?: string;
  /** Imperative nav request — used by the BPA "Fix it" button to jump
   *  to the Fixer page. Wired by PbiFixerPage. */
  onNavigate?: (key: string) => void;
}

export const ReportExplorer: React.FC<ReportExplorerProps> = ({
  auth,
  workspace,
  reportName,
  reportId,
  onNavigateToModel,
  connectionSlot,
  version,
  onNavigate,
}) => {
  const styles = useStyles();

  const [reportData, setReportData] = useState<ReportData | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [status, setStatus] = useState<{ msg: string; color: string }>({ msg: "", color: GRAY_COLOR });
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [scanResults] = useState<ScanResult>({});

  const [pendingChanges, setPendingChanges] = useState<Record<string, Record<string, unknown>>>({});

  // Bumped on Load Report and after a successful Save — forces ReportPreview
  // to mint a fresh embed token + reset the iframe so PBIR changes show up.
  const [previewNonce, setPreviewNonce] = useState(0);

  // v0.61 — per-visual quick-fix run state.
  const [runningFixerId, setRunningFixerId] = useState<string | null>(null);
  // v0.73 — report-wide fixer scan state. Keyed by fixer id; only
  // entries with findings.length > 0 are surfaced. Independent from
  // ``scanResults`` (tree-node violation counter, Record<string, number>).
  const [fixerScanResults, setFixerScanResults] = useState<Record<string, FixerResult>>({});
  const [fixerScanning, setFixerScanning] = useState(false);
  const [fixerScanRanOnce, setFixerScanRanOnce] = useState(false);
  // v0.102 — unified Scan Report results. handleScanReport runs report
  // fixers (scan_only) followed by Best Practice Analyzer; results are
  // rendered by <ReportScanResults> below mainLayout.
  const [reportBpaFindings, setReportBpaFindings] = useState<BpaFinding[] | null>(null);
  const [scanStep, setScanStep] = useState<"" | "fixers" | "bpa">("");
  const resultsRef = useRef<HTMLDivElement | null>(null);
  // v0.61–0.65 attempted drag-and-drop page reorder; removed in v0.66 because
  // the Fabric workload-iframe host intercepts/breaks both HTML5 drag and
  // pointer-event-based drag. Backend endpoint kept; UI parked in PLAN.md.

  // Build tree
  const treeResult = useMemo<TreeBuildResult>(() => {
    if (!reportData) return { options: [], keyMap: {}, iconMap: {} };
    return buildReportTree(reportData, expanded, scanResults);
  }, [reportData, expanded, scanResults]);

  const filteredOptions = useMemo(
    () => filterTreeOptions(treeResult.options, searchQuery),
    [treeResult.options, searchQuery]
  );

  const pageProps = useMemo(() => {
    if (!selectedKey || !reportData || !selectedKey.startsWith("page:")) return null;
    return getPageProperties(reportData, selectedKey);
  }, [selectedKey, reportData]);

  const visualProps = useMemo(() => {
    if (!selectedKey || !reportData || !selectedKey.startsWith("visual:")) return null;
    return getVisualProperties(reportData, selectedKey);
  }, [selectedKey, reportData]);

  // ── WS-Q v0.42 — editable visual / page properties ─────────────────
  type VisualEdit = { visualType?: string; x?: number; y?: number; width?: number; height?: number };
  type PageEdit = { pageWidth?: number; pageHeight?: number };
  const [visualEdit, setVisualEdit] = useState<VisualEdit>({});
  const [pageEdit, setPageEdit] = useState<PageEdit>({});
  const [savingProps, setSavingProps] = useState(false);

  // Reset staged edits whenever the user picks a different node.
  useEffect(() => {
    setVisualEdit({});
    setPageEdit({});
  }, [selectedKey]);

  const visualEditDirty =
    (visualEdit.visualType !== undefined && visualEdit.visualType !== visualProps?.type) ||
    (visualEdit.x !== undefined && visualEdit.x !== visualProps?.x) ||
    (visualEdit.y !== undefined && visualEdit.y !== visualProps?.y) ||
    (visualEdit.width !== undefined && visualEdit.width !== visualProps?.width) ||
    (visualEdit.height !== undefined && visualEdit.height !== visualProps?.height);

  const pageEditDirty =
    (pageEdit.pageWidth !== undefined && pageEdit.pageWidth !== pageProps?.width) ||
    (pageEdit.pageHeight !== undefined && pageEdit.pageHeight !== pageProps?.height);

  const handleSaveProps = useCallback(async () => {
    if (!reportData?.workspaceId || !reportData?.reportId) return;
    setSavingProps(true);
    try {
      if (visualProps && visualEditDirty) {
        await updateVisualProperties(auth, {
          workspaceId: reportData.workspaceId,
          reportId: reportData.reportId,
          page: visualProps.pageName,
          visual: visualProps.internalName,
          visualType: visualEdit.visualType,
          x: visualEdit.x,
          y: visualEdit.y,
          width: visualEdit.width,
          height: visualEdit.height,
        });
      } else if (pageProps && pageEditDirty) {
        await updateVisualProperties(auth, {
          workspaceId: reportData.workspaceId,
          reportId: reportData.reportId,
          page: pageProps.internalName,
          visual: "*",
          pageWidth: pageEdit.pageWidth,
          pageHeight: pageEdit.pageHeight,
        });
      } else {
        return;
      }
      // Reload the definition so the tree + preview reflect the change.
      const data = await loadReportDefinition(
        auth,
        reportData.workspaceId,
        reportData.reportId,
        reportData.reportId,
      );
      setReportData(data);
      setVisualEdit({});
      setPageEdit({});
      setPreviewNonce((n) => n + 1);
      setStatus({ msg: "Saved", color: "#34c759" });
    } catch (err) {
      setStatus({
        msg: `Save failed: ${err instanceof Error ? err.message : String(err)}`,
        color: "#ff3b30",
      });
    } finally {
      setSavingProps(false);
    }
  }, [auth, reportData, visualProps, pageProps, visualEdit, pageEdit, visualEditDirty, pageEditDirty]);

  // ── v0.61 — per-visual quick-fix runner ──────────────────────────────
  // Calls the existing report-wide apply flow; the report-scoped fixers
  // currently scan the entire report (no per-visual scoping in the apply
  // endpoint yet), so this is just a discoverable shortcut from the
  // visual props pane. Reloads the definition + bumps the embed nonce on
  // success so the live preview reflects the change immediately.
  const runQuickFixer = useCallback(
    async (fixer: Fixer) => {
      if (!reportData?.workspaceId || !reportData?.reportId) return;
      setRunningFixerId(fixer.id);
      setStatus({ msg: `Running ${fixer.title}…`, color: GRAY_COLOR });
      try {
        const ctx: FixerContext = {
          auth,
          workspaceId: reportData.workspaceId,
          reportId: reportData.reportId,
          report: reportData,
        };
        const result = await fixer.apply(ctx);
        if (result.applied) {
          const data = await loadReportDefinition(
            auth,
            reportData.workspaceId,
            reportData.reportId,
            reportData.reportId,
          );
          setReportData(data);
          setPreviewNonce((n) => n + 1);
          setStatus({
            msg: `Applied ${fixer.title} — ${result.findings.length} change(s) report-wide`,
            color: "#34c759",
          });
        } else {
          setStatus({
            msg: `${fixer.title}: ${result.findings.length} finding(s), nothing applied`,
            color: result.findings.length ? "#ff9f0a" : GRAY_COLOR,
          });
        }
      } catch (err) {
        setStatus({
          msg: `Quick fix failed: ${err instanceof Error ? err.message : String(err)}`,
          color: "#ff3b30",
        });
      } finally {
        setRunningFixerId(null);
      }
    },
    [auth, reportData],
  );

  // Filter the global fixer registry down to the ones whose `appliesTo`
  // includes the currently selected visual's type (or that have no
  // `appliesTo` and are therefore generic to all visuals).
  const applicableFixers = useMemo<Fixer[]>(() => {
    if (!visualProps) return [];
    const vt = visualProps.type;
    return FIXERS.filter((f) => {
      if (f.scope !== "report") return false;
      if (f.mode !== "backend") return false;
      if (!f.appliesTo || f.appliesTo.length === 0) return true;
      return f.appliesTo.includes(vt);
    });
  }, [visualProps]);

  // v0.73 \u2014 Report-wide fixer scan. Runs every backend report-scoped
  // fixer in scan-only mode and surfaces only those reporting findings.
  const reportFixers = useMemo<Fixer[]>(
    () => FIXERS.filter((f) => f.scope === "report" && f.mode === "backend"),
    []
  );

  const buildReportFixerCtx = useCallback((): FixerContext | null => {
    if (!reportData?.workspaceId || !reportData?.reportId) return null;
    return {
      auth,
      workspaceId: reportData.workspaceId,
      reportId: reportData.reportId,
      report: reportData,
    };
  }, [auth, reportData]);

  const handleScanReport = useCallback(async () => {
    const ctx = buildReportFixerCtx();
    if (!ctx) {
      setStatus({ msg: "Load a report first", color: "#ff3b30" });
      return;
    }
    setFixerScanning(true);

    // Phase 1 — backend report fixers (scan-only).
    setScanStep("fixers");
    setStatus({ msg: `Scanning ${reportFixers.length} report fixer(s)\u2026`, color: GRAY_COLOR });
    let withFindings = 0;
    try {
      const entries = await Promise.all(
        reportFixers.map(async (fx) => {
          try {
            const res = await fx.scan(ctx);
            return [fx.id, res] as const;
          } catch (err) {
            const fail: FixerResult = { findings: [], applied: false, log: [`scan error: ${err instanceof Error ? err.message : String(err)}`] };
            return [fx.id, fail] as const;
          }
        })
      );
      const next: Record<string, FixerResult> = {};
      for (const [id, res] of entries) next[id] = res;
      setFixerScanResults(next);
      setFixerScanRanOnce(true);
      withFindings = entries.filter(([, r]) => r.findings.length > 0).length;
    } catch (err) {
      setStatus({
        msg: `Fixer scan failed: ${err instanceof Error ? err.message : String(err)}`,
        color: "#ff3b30",
      });
      setFixerScanning(false);
      setScanStep("");
      return;
    }

    // Phase 2 — Report BPA (synchronous, runs on already-loaded reportData).
    setScanStep("bpa");
    setStatus({ msg: "Running Best Practice Analyzer\u2026", color: GRAY_COLOR });
    let bpaCount = 0;
    try {
      if (reportData) {
        const findings = runReportBpa(reportData);
        setReportBpaFindings(findings);
        bpaCount = findings.length;
      } else {
        setReportBpaFindings([]);
      }
    } catch (err) {
      setReportBpaFindings([]);
      setStatus({
        msg: `BPA failed: ${err instanceof Error ? err.message : String(err)}`,
        color: "#ff9500",
      });
    }

    setStatus({
      msg: withFindings === 0 && bpaCount === 0
        ? "Scan complete \u2014 no fixer or BPA issues"
        : `Scan complete \u2014 ${withFindings} fixer issue(s), ${bpaCount} BPA finding(s)`,
      color: (withFindings === 0 && bpaCount === 0) ? "#34c759" : "#ff9500",
    });
    setFixerScanning(false);
    setScanStep("");
  }, [buildReportFixerCtx, reportFixers, reportData]);

  // Smooth-scroll to the new results panel as soon as a scan completes.
  useEffect(() => {
    if (fixerScanRanOnce && !fixerScanning && resultsRef.current) {
      requestAnimationFrame(() => {
        resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  }, [fixerScanRanOnce, fixerScanning]);

  const handleApplyReportFixer = useCallback(async (fx: Fixer) => {
    const ctx = buildReportFixerCtx();
    if (!ctx) return;
    setRunningFixerId(fx.id);
    setStatus({ msg: `Applying ${fx.title}\u2026`, color: GRAY_COLOR });
    try {
      const res = await fx.apply(ctx);
      let rescan: FixerResult | null = null;
      try {
        rescan = await fx.scan(ctx);
      } catch { /* ignore */ }
      setFixerScanResults((prev) => ({ ...prev, [fx.id]: rescan ?? { ...res, applied: false } }));
      if (res.applied && reportData?.workspaceId && reportData?.reportId) {
        try {
          const data = await loadReportDefinition(
            auth,
            reportData.workspaceId,
            reportData.reportId,
            reportData.reportId,
          );
          setReportData(data);
          setPreviewNonce((n) => n + 1);
        } catch { /* keep existing report on reload failure */ }
      }
      setStatus({
        msg: res.applied
          ? `Applied ${fx.title} \u2014 ${res.findings.length} change(s)`
          : `${fx.title}: ${res.findings.length} finding(s), nothing applied`,
        color: res.applied ? "#34c759" : (res.findings.length ? "#ff9500" : GRAY_COLOR),
      });
    } catch (err) {
      setStatus({ msg: `Apply failed: ${err instanceof Error ? err.message : String(err)}`, color: "#ff3b30" });
    } finally {
      setRunningFixerId(null);
    }
  }, [auth, buildReportFixerCtx, reportData]);

  const reportFixersWithFindings = useMemo(
    () => reportFixers.filter((f) => (fixerScanResults[f.id]?.findings.length ?? 0) > 0),
    [reportFixers, fixerScanResults]
  );

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleLoad = useCallback(async () => {
    if (!auth.fabricToken || !workspace || !reportName) {
      setStatus({ msg: "Workspace and report name required", color: "#ff3b30" });
      return;
    }
    setLoading(true);
    setStatus({ msg: "Loading report...", color: GRAY_COLOR });
    try {
      const wsId = await resolveWorkspaceId(auth, workspace);
      let resolvedId = reportId;
      let resolvedName = reportName;
      if (!resolvedId) {
        const reports = await listReports(auth, wsId);
        const match = reports.find(
          (r) => r.name.toLowerCase() === reportName.toLowerCase()
        );
        if (!match) {
          setStatus({ msg: `Report '${reportName}' not found`, color: "#ff3b30" });
          setLoading(false);
          return;
        }
        resolvedId = match.id;
        resolvedName = match.name;
      }
      const data = await loadReportDefinition(auth, wsId, resolvedId, resolvedName);
      setReportData(data);
      setExpanded(new Set(Object.keys(data.pages)));
      setPreviewNonce((n) => n + 1);
      setStatus({
        msg: `Loaded ${Object.keys(data.pages).length} pages`,
        color: "#34c759",
      });
    } catch (err) {
      setStatus({
        msg: `Error: ${err instanceof Error ? err.message : String(err)}`,
        color: "#ff3b30",
      });
    } finally {
      setLoading(false);
    }
  }, [auth, workspace, reportName, reportId]);

  // v0.91 — Auto-load on selection (mirrors Memory Analyzer / Model BPA pattern).
  // The Load Report button has been removed; selecting a workspace + report in
  // the shared picker triggers handleLoad automatically.
  useEffect(() => { void handleLoad(); }, [handleLoad]);

  const handleToggleNode = useCallback((key: string) => {
    const parts = key.split(":");
    const nodeType = parts[0];
    let toggleKey: string;

    if (nodeType === "report") {
      toggleKey = parts[1];
    } else if (nodeType === "page") {
      toggleKey = parts[1];
    } else {
      return;
    }

    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(toggleKey)) {
        next.delete(toggleKey);
      } else {
        next.add(toggleKey);
      }
      return next;
    });
  }, []);

  const handleSelect = useCallback(
    (option: string) => {
      const key = treeResult.keyMap[option];
      if (!key) return;
      setSelectedKey(key);
      handleToggleNode(key);
    },
    [treeResult.keyMap, handleToggleNode]
  );

  const handleExpandAll = useCallback(() => {
    if (!reportData) return;
    setExpanded(new Set(Object.keys(reportData.pages)));
  }, [reportData]);

  const handleCollapseAll = useCallback(() => {
    setExpanded(new Set());
  }, []);

  const hasPendingChanges = Object.keys(pendingChanges).length > 0;
  const pendingCount = Object.keys(pendingChanges).length;

  const handleDiscard = useCallback(() => {
    setPendingChanges({});
  }, []);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className={styles.root}>
      <div className={styles.hero}>
        <div className={styles.heroEyebrowRow}>
          <div className={styles.heroEyebrow}>Power BI Fixer</div>
          {version && <span className={styles.heroVersion}>{version}</span>}
        </div>
        <h1 className={styles.heroTitle}>Report Explorer</h1>
        <p className={styles.heroSubtitle}>
          Browse pages and visuals of the loaded report. Inspect properties,
          edit visual settings, and run report fixes.
        </p>
      </div>
      {connectionSlot && (
        <div className={styles.inlineConnection}>{connectionSlot}</div>
      )}
      <div className={styles.toolbar}>
        <Button
          appearance="primary"
          className={styles.loadCta}
          icon={<ArrowExpand20Regular />}
          onClick={handleExpandAll}
          disabled={!reportData}
        >
          Expand All
        </Button>
        <Button
          appearance="primary"
          className={styles.loadCta}
          icon={<ArrowCollapseAll20Regular />}
          onClick={handleCollapseAll}
          disabled={!reportData}
        >
          Collapse All
        </Button>
        <Button
          appearance="primary"
          className={styles.loadCta}
          icon={fixerScanning ? <Spinner size="tiny" /> : <Wrench20Regular />}
          onClick={handleScanReport}
          disabled={!reportData || fixerScanning}
        >
          {fixerScanning
            ? scanStep === "fixers"
              ? "Scanning fixers\u2026"
              : scanStep === "bpa"
                ? "Running BPA\u2026"
                : "Scanning\u2026"
            : "Scan Report"}
        </Button>
        {loading && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: GRAY_COLOR, fontSize: 12 }}>
            <Spinner size="tiny" /> Loading report...
          </span>
        )}
        {/* v0.69 — toolbar status pill is reserved for ERRORS only.
            Loading state is conveyed by the primary button's spinner + label;
            success/info messages would just clutter the toolbar. */}
        {status.msg && (status.color === "#ff3b30" || status.color === "#a4262c") && (
          <span
            className={styles.statusBar}
            style={{ background: `${status.color}1a`, color: status.color }}
          >
            {status.msg}
          </span>
        )}
      </div>

      <div className={styles.mainLayout}>
        <div className={styles.treePanel}>
          <Input
            placeholder="Filter tree..."
            value={searchQuery}
            onChange={(_, data) => setSearchQuery(data.value)}
            contentBefore={<Search20Regular />}
          />
          <div className={styles.treeList}>
            {filteredOptions.map((option) => {
              const key = treeResult.keyMap[option];
              const iconKey = treeResult.iconMap[option];
              const isSelected = key === selectedKey;
              const indentMatch = option.match(/^[\u00A0]*/);
              const indent = indentMatch ? indentMatch[0] : "";
              const labelText = option.slice(indent.length);
              return (
                <div
                  key={option}
                  className={`${styles.treeItem} ${isSelected ? styles.treeItemSelected : ""}`}
                  onClick={() => handleSelect(option)}
                  style={{ display: "flex", alignItems: "center", gap: "4px" }}
                >
                  {iconKey === "page" ? (
                    <>
                      <span style={{ whiteSpace: "pre" }}>{indent}</span>
                      <ChartMultiple20Regular
                        primaryFill={ICON_ACCENT}
                        style={{ flexShrink: 0 }}
                      />
                      <span>{labelText}</span>
                    </>
                  ) : (
                    option
                  )}
                </div>
              );
            })}
            {filteredOptions.length === 0 && !loading && (
              <div style={{ padding: "20px", color: GRAY_COLOR, textAlign: "center", fontStyle: "italic" }}>
                {reportData ? "No matching items" : "Pick a workspace and report to load"}
              </div>
            )}
          </div>
        </div>

        <div className={styles.rightPanel}>
          <div className={styles.previewPanel}>
            <div className={styles.sectionLabel}>Preview</div>
            <div className={styles.previewSurface}>
              {reportData ? (
                <ReportPreview
                  reportData={reportData}
                  selectedKey={selectedKey}
                  auth={auth}
                  refreshNonce={previewNonce}
                />
              ) : (
                <span className={styles.previewEmpty}>
                  Load a report to see the live preview
                </span>
              )}
            </div>
          </div>

          <div className={styles.propertiesPanel}>
            <div className={styles.sectionLabel}>Properties</div>

            {pageProps && (
              <>
                <PropRow label="Internal Name" value={pageProps.internalName} />
                <PropRow label="Display Name" value={pageProps.displayName} />
                <PropRow label="Visual Count" value={String(pageProps.visualCount)} />
                <PropRow label="Visual Types" value={pageProps.visualTypeSummary} />
                <EditableNumberRow
                  label="Width"
                  current={pageProps.width}
                  pending={pageEdit.pageWidth}
                  onChange={(v) => setPageEdit((e) => ({ ...e, pageWidth: v }))}
                />
                <EditableNumberRow
                  label="Height"
                  current={pageProps.height}
                  pending={pageEdit.pageHeight}
                  onChange={(v) => setPageEdit((e) => ({ ...e, pageHeight: v }))}
                />
                <PropRow label="Hidden" value={String(pageProps.hidden)} />
                {pageEditDirty && (
                  <div className={styles.saveRow}>
                    <Button
                      appearance="primary"
                      size="small"
                      onClick={handleSaveProps}
                      disabled={savingProps}
                      icon={savingProps ? <Spinner size="tiny" /> : undefined}
                    >
                      Save changes
                    </Button>
                    <Button
                      appearance="secondary"
                      size="small"
                      onClick={() => setPageEdit({})}
                      disabled={savingProps}
                    >
                      Discard
                    </Button>
                  </div>
                )}
                <JsonPreview label="Page JSON" data={pageProps.rawJson} />
              </>
            )}

            {visualProps && (
              <>
                <div className={styles.propRow}>
                  <span className={styles.propLabel}>Type</span>
                  <span className={styles.propValue} style={{ flex: 1 }}>
                    <Dropdown
                      size="small"
                      value={visualTypeLabel(visualEdit.visualType ?? visualProps.type)}
                      selectedOptions={[visualEdit.visualType ?? visualProps.type]}
                      onOptionSelect={(_, data) => {
                        if (data.optionValue) {
                          setVisualEdit((e) => ({ ...e, visualType: data.optionValue }));
                        }
                      }}
                      style={{ minWidth: 220 }}
                    >
                      {VISUAL_TYPE_OPTIONS.map((o) => (
                        <Option key={o.value} value={o.value} text={o.label}>
                          {o.label}
                        </Option>
                      ))}
                    </Dropdown>
                  </span>
                </div>
                <PropRow label="Internal Name" value={visualProps.internalName} />
                <PropRow label="Page" value={visualProps.pageName} />
                <PropRow label="Title" value={visualProps.title} />
                <EditableNumberRow
                  label="X"
                  current={visualProps.x}
                  pending={visualEdit.x}
                  onChange={(v) => setVisualEdit((e) => ({ ...e, x: v }))}
                />
                <EditableNumberRow
                  label="Y"
                  current={visualProps.y}
                  pending={visualEdit.y}
                  onChange={(v) => setVisualEdit((e) => ({ ...e, y: v }))}
                />
                <EditableNumberRow
                  label="Width"
                  current={visualProps.width}
                  pending={visualEdit.width}
                  onChange={(v) => setVisualEdit((e) => ({ ...e, width: v }))}
                />
                <EditableNumberRow
                  label="Height"
                  current={visualProps.height}
                  pending={visualEdit.height}
                  onChange={(v) => setVisualEdit((e) => ({ ...e, height: v }))}
                />
                <PropRow label="Hidden" value={String(visualProps.hidden)} />
                {visualEditDirty && (
                  <div className={styles.saveRow}>
                    <Button
                      appearance="primary"
                      size="small"
                      onClick={handleSaveProps}
                      disabled={savingProps}
                      icon={savingProps ? <Spinner size="tiny" /> : undefined}
                    >
                      Save changes
                    </Button>
                    <Button
                      appearance="secondary"
                      size="small"
                      onClick={() => setVisualEdit({})}
                      disabled={savingProps}
                    >
                      Discard
                    </Button>
                  </div>
                )}
                {visualProps.usedObjects.length > 0 && (
                  <div style={{ marginTop: "8px" }}>
                    <div className={styles.sectionLabel} style={{ marginBottom: "4px" }}>Used Objects</div>
                    {visualProps.usedObjects.map((obj, i) => (
                      <div
                        key={i}
                        style={{
                          fontSize: "12px",
                          padding: "2px 0",
                          cursor: onNavigateToModel ? "pointer" : "default",
                          color: onNavigateToModel ? ICON_ACCENT : "inherit",
                        }}
                        onClick={() => {
                          if (onNavigateToModel) {
                            const navKey = obj.type === "Measure"
                              ? `measure:${obj.table}:${obj.object}`
                              : `column:${obj.table}:${obj.object}`;
                            onNavigateToModel(navKey);
                          }
                        }}
                      >
                        {obj.icon} {obj.table}[{obj.object}] ({obj.type})
                      </div>
                    ))}
                  </div>
                )}
                {(() => {
                  // v0.73 — visual-scoped quick fixes are now scan-gated:
                  // only fixers that returned findings on the most recent
                  // report-wide scan AND apply to the selected visual's
                  // type are surfaced here.
                  const visualFixers = fixerScanRanOnce
                    ? applicableFixers.filter((fx) => (fixerScanResults[fx.id]?.findings.length ?? 0) > 0)
                    : [];
                  if (visualFixers.length === 0) return null;
                  return (
                    <div style={{ marginTop: "8px" }}>
                      <div className={styles.sectionLabel} style={{ marginBottom: "4px" }}>
                        Quick fixes for this visual
                      </div>
                      <div style={{ fontSize: 11, color: GRAY_COLOR, marginBottom: 6, fontStyle: "italic" }}>
                        Runs the fixer report-wide (per-visual scoping pending — see PLAN.md).
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        {visualFixers.map((fx) => (
                          <Button
                            key={fx.id}
                            appearance="secondary"
                            size="small"
                            disabled={runningFixerId !== null}
                            icon={runningFixerId === fx.id ? <Spinner size="tiny" /> : undefined}
                            onClick={() => void runQuickFixer(fx)}
                            style={{ justifyContent: "flex-start", textAlign: "left" }}
                          >
                            {fx.title} ({fixerScanResults[fx.id]?.findings.length ?? 0})
                          </Button>
                        ))}
                      </div>
                    </div>
                  );
                })()}
                <JsonPreview label="Visual JSON" data={visualProps.rawJson} />
              </>
            )}

            {!pageProps && !visualProps && (
              <div style={{ padding: "12px", color: GRAY_COLOR, fontSize: "13px", fontStyle: "italic" }}>
                Select an object to view properties
              </div>
            )}

            {hasPendingChanges && (
              <div className={styles.saveRow}>
                <Button appearance="primary" size="small">
                  {"\u26a0\ufe0f"} {pendingCount} unsaved change(s)
                </Button>
                <Button appearance="secondary" size="small" onClick={handleDiscard}>
                  {"\u2718"} Discard
                </Button>
              </div>
            )}
            {/* v0.102 — Quick fixes panel relocated to the unified
                <ReportScanResults> section below mainLayout. */}
          </div>
        </div>
      </div>

      <ReportScanResults
        ref={resultsRef}
        scanRanOnce={fixerScanRanOnce}
        fixersWithFindings={reportFixersWithFindings}
        scanResults={fixerScanResults}
        runningFixerId={runningFixerId}
        onApplyFixer={(fx) => void handleApplyReportFixer(fx)}
        bpaFindings={reportBpaFindings}
        onNavigate={onNavigate}
      />
    </div>
  );
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const PropRow: React.FC<{ label: string; value: string }> = ({ label, value }) => {
  const styles = useStyles();
  if (!value) return null;
  return (
    <div className={styles.propRow}>
      <span className={styles.propLabel}>{label}</span>
      <span className={styles.propValue}>{value}</span>
    </div>
  );
};

interface EditableNumberRowProps {
  label: string;
  current: number;
  pending: number | undefined;
  onChange: (v: number | undefined) => void;
}

const EditableNumberRow: React.FC<EditableNumberRowProps> = ({
  label,
  current,
  pending,
  onChange,
}) => {
  const styles = useStyles();
  const value = pending ?? current;
  return (
    <div className={styles.propRow}>
      <span className={styles.propLabel}>{label}</span>
      <span className={styles.propValue} style={{ flex: 1 }}>
        <Input
          size="small"
          type="number"
          value={String(Math.round(value))}
          onChange={(_, data) => {
            const n = data.value === "" ? undefined : Number(data.value);
            if (n === undefined || Number.isNaN(n)) {
              onChange(undefined);
            } else {
              onChange(n);
            }
          }}
          style={{ width: 110 }}
        />
      </span>
    </div>
  );
};

const JsonPreview: React.FC<{ label: string; data: unknown }> = ({ label, data }) => {
  const [open, setOpen] = useState(false);
  if (data === undefined || data === null) return null;
  let pretty = "";
  try { pretty = JSON.stringify(data, null, 2); } catch { pretty = String(data); }
  return (
    <div style={{ marginTop: 8 }}>
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          cursor: "pointer",
          padding: "4px 0",
          color: GRAY_COLOR,
          userSelect: "none",
        }}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "\u25bc" : "\u25b6"} {label}
        {open && (
          <Button
            appearance="subtle"
            size="small"
            style={{ marginLeft: 8 }}
            onClick={(e) => {
              e.stopPropagation();
              navigator.clipboard.writeText(pretty);
            }}
          >
            Copy
          </Button>
        )}
      </div>
      {open && (
        <pre
          style={{
            margin: 0,
            padding: 8,
            background: "#f6f8fa",
            border: "1px solid #d0d7de",
            borderRadius: 4,
            fontSize: 11,
            fontFamily: "monospace",
            maxHeight: 280,
            overflow: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {pretty}
        </pre>
      )}
    </div>
  );
};
