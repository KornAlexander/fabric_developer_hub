// ReportExplorer — React component (FluentUI)
// Mirrors report_explorer_tab() from _report_explorer.py

import React, { useState, useCallback, useMemo, useEffect, useRef } from "react";
import {
  Button,
  Input,
  Spinner,
  makeStyles,
  shorthands,
} from "@fluentui/react-components";
import {
  Search20Regular,
  ArrowExpand20Regular,
  ArrowCollapseAll20Regular,
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
  SECTION_BG,
} from "../utils";
import {
  listReports,
  loadReportDefinition,
  resolveWorkspaceId,
  getReportEmbedToken,
  PbiAuth,
} from "../services";

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const useStyles = makeStyles({
  root: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    ...shorthands.gap("8px"),
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
    backgroundColor: SECTION_BG,
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
  },
  previewPanel: {
    ...shorthands.border("1px", "solid", BORDER_COLOR),
    ...shorthands.borderRadius("8px"),
    ...shorthands.padding("8px"),
    backgroundColor: SECTION_BG,
    minHeight: "400px",
    flex: 1,
    display: "flex",
    flexDirection: "column",
  },
  previewSurface: {
    flex: 1,
    minHeight: "480px",
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
  },
  propertiesPanel: {
    ...shorthands.border("1px", "solid", BORDER_COLOR),
    ...shorthands.borderRadius("8px"),
    ...shorthands.padding("8px"),
    backgroundColor: SECTION_BG,
    flex: 1,
    overflowY: "auto",
  },
  sectionLabel: {
    fontSize: "12px",
    fontWeight: "600",
    color: ICON_ACCENT,
    textTransform: "uppercase" as const,
    letterSpacing: "0.5px",
    marginBottom: "4px",
  },
  propRow: {
    display: "flex",
    ...shorthands.padding("3px", "0"),
    fontSize: "13px",
  },
  propLabel: {
    fontWeight: "600",
    color: "#555",
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
              pageNavigation: { visible: true },
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
  }, [reportData?.reportId, reportData?.workspaceId, auth.fabricToken]);

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
    <div style={{ position: "relative", width: "100%", height: "100%", minHeight: 480 }}>
      <div
        ref={containerRef}
        style={{ width: "100%", height: "100%", minHeight: 480 }}
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
}

export const ReportExplorer: React.FC<ReportExplorerProps> = ({
  auth,
  workspace,
  reportName,
  reportId,
  onNavigateToModel,
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

  // Build tree
  const treeResult = useMemo<TreeBuildResult>(() => {
    if (!reportData) return { options: [], keyMap: {} };
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
      <div className={styles.toolbar}>
        <Button
          appearance="primary"
          onClick={handleLoad}
          disabled={loading}
          icon={loading ? <Spinner size="tiny" /> : undefined}
        >
          Load Report
        </Button>
        <Button
          appearance="subtle"
          icon={<ArrowExpand20Regular />}
          onClick={handleExpandAll}
          disabled={!reportData}
        >
          Expand All
        </Button>
        <Button
          appearance="subtle"
          icon={<ArrowCollapseAll20Regular />}
          onClick={handleCollapseAll}
          disabled={!reportData}
        >
          Collapse All
        </Button>
        {status.msg && (
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
              const isSelected = key === selectedKey;
              return (
                <div
                  key={option}
                  className={`${styles.treeItem} ${isSelected ? styles.treeItemSelected : ""}`}
                  onClick={() => handleSelect(option)}
                >
                  {option}
                </div>
              );
            })}
            {filteredOptions.length === 0 && !loading && (
              <div style={{ padding: "20px", color: GRAY_COLOR, textAlign: "center", fontStyle: "italic" }}>
                {reportData ? "No matching items" : "Click Load Report to explore"}
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
                <PropRow label="Dimensions" value={`${pageProps.width} \u00d7 ${pageProps.height}`} />
                <PropRow label="Hidden" value={String(pageProps.hidden)} />
              </>
            )}

            {visualProps && (
              <>
                <PropRow label="Type" value={visualProps.displayType} />
                <PropRow label="Internal Name" value={visualProps.internalName} />
                <PropRow label="Page" value={visualProps.pageName} />
                <PropRow label="Title" value={visualProps.title} />
                <PropRow label="Position" value={`X: ${visualProps.x}, Y: ${visualProps.y}`} />
                <PropRow label="Size" value={`${visualProps.width} \u00d7 ${visualProps.height}`} />
                <PropRow label="Hidden" value={String(visualProps.hidden)} />
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
          </div>
        </div>
      </div>
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
