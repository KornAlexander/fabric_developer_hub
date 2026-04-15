// ReportExplorer — React component (FluentUI)
// Mirrors report_explorer_tab() from _report_explorer.py

import React, { useState, useCallback, useMemo } from "react";
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
  FONT_FAMILY,
  BORDER_COLOR,
  GRAY_COLOR,
  ICON_ACCENT,
  SECTION_BG,
} from "../utils";
import {
  listReports,
  loadReportDefinition,
  resolveWorkspaceId,
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
    fontFamily: "monospace",
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

export interface ReportExplorerProps {
  accessToken: string;
  workspace: string;
  reportName: string;
  onNavigateToModel?: (key: string) => void;
}

export const ReportExplorer: React.FC<ReportExplorerProps> = ({
  accessToken,
  workspace,
  reportName,
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
    if (!accessToken || !workspace || !reportName) {
      setStatus({ msg: "Workspace and report name required", color: "#ff3b30" });
      return;
    }
    setLoading(true);
    setStatus({ msg: "Loading report...", color: GRAY_COLOR });
    try {
      const wsId = await resolveWorkspaceId(accessToken, workspace);
      const reports = await listReports(accessToken, wsId);
      const match = reports.find(
        (r) => r.name.toLowerCase() === reportName.toLowerCase()
      );
      if (!match) {
        setStatus({ msg: `Report '${reportName}' not found`, color: "#ff3b30" });
        setLoading(false);
        return;
      }
      const data = await loadReportDefinition(accessToken, wsId, match.id, match.name);
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
  }, [accessToken, workspace, reportName]);

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
              <div style={{ padding: "20px", color: GRAY_COLOR, textAlign: "center", fontStyle: "italic", fontFamily: FONT_FAMILY }}>
                {reportData ? "No matching items" : "Click Load Report to explore"}
              </div>
            )}
          </div>
        </div>

        <div className={styles.rightPanel}>
          <div className={styles.previewPanel}>
            <div className={styles.sectionLabel}>Preview</div>
            <div style={{ padding: "16px", color: GRAY_COLOR, fontSize: "13px", fontStyle: "italic" }}>
              {reportData ? "Select a page or visual to see details" : "Load a report to see the live preview"}
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
