// ModelExplorer — React component (FluentUI)
// Mirrors model_explorer_tab() from _sm_explorer.py

import React, { useState, useCallback, useMemo } from "react";
import {
  Button,
  Input,
  Textarea,
  Spinner,
  makeStyles,
  shorthands,
  Tooltip,
} from "@fluentui/react-components";
import {
  Search20Regular,
  ArrowExpand20Regular,
  ArrowCollapseAll20Regular,
  Copy20Regular,
} from "@fluentui/react-icons";
import {
  ModelData,
  TreeBuildResult,
} from "../types";
import {
  buildModelTree,
  getModelPreviewText,
  getDaxReference,
  filterTreeOptions,
  FONT_FAMILY,
  BORDER_COLOR,
  GRAY_COLOR,
  ICON_ACCENT,
  SECTION_BG,
} from "../utils";
import {
  listSemanticModels,
  loadModelData,
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
    minHeight: "160px",
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
});

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface ModelExplorerProps {
  accessToken: string;
  workspace: string;
  datasetName: string;
}

export const ModelExplorer: React.FC<ModelExplorerProps> = ({
  accessToken,
  workspace,
  datasetName,
}) => {
  const styles = useStyles();

  const [modelData, setModelData] = useState<ModelData | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [status, setStatus] = useState<{ msg: string; color: string }>({ msg: "", color: GRAY_COLOR });
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [pendingChanges] = useState<Set<string>>(new Set());
  const [previewText, setPreviewText] = useState("");
  const [daxRef, setDaxRef] = useState("");

  // Build tree
  const treeResult = useMemo<TreeBuildResult>(() => {
    if (!modelData) return { options: [], keyMap: {} };
    return buildModelTree(modelData, expanded, {}, pendingChanges);
  }, [modelData, expanded, pendingChanges]);

  const filteredOptions = useMemo(
    () => filterTreeOptions(treeResult.options, searchQuery),
    [treeResult.options, searchQuery]
  );

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleLoad = useCallback(async () => {
    if (!accessToken || !workspace || !datasetName) {
      setStatus({ msg: "Workspace and dataset name required", color: "#ff3b30" });
      return;
    }
    setLoading(true);
    setStatus({ msg: "Loading model...", color: GRAY_COLOR });
    try {
      const wsId = await resolveWorkspaceId(accessToken, workspace);
      const models = await listSemanticModels(accessToken, wsId);
      const match = models.find(
        (m) => m.name.toLowerCase() === datasetName.toLowerCase()
      );
      if (!match) {
        setStatus({ msg: `Dataset '${datasetName}' not found`, color: "#ff3b30" });
        setLoading(false);
        return;
      }
      const data = await loadModelData(accessToken, wsId, match.id, match.name);
      setModelData(data);
      setExpanded(new Set([match.name]));
      setStatus({
        msg: `Loaded ${Object.keys(data.tables).length} tables`,
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
  }, [accessToken, workspace, datasetName]);

  const handleToggleNode = useCallback(
    (key: string) => {
      const parts = key.split(":");
      const nodeType = parts[0];
      let toggleKey: string;

      if (nodeType === "model") {
        toggleKey = parts[1];
      } else if (nodeType === "table") {
        toggleKey = parts[1];
      } else if (nodeType === "folder" || nodeType === "colfolder") {
        toggleKey = key;
      } else if (key.startsWith("rels:")) {
        toggleKey = key;
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
    },
    []
  );

  const handleSelect = useCallback(
    (option: string) => {
      const key = treeResult.keyMap[option];
      if (!key) return;

      setSelectedKey(key);
      handleToggleNode(key);

      if (modelData) {
        setPreviewText(getModelPreviewText(modelData, key));
        setDaxRef(getDaxReference(key));
      }
    },
    [treeResult.keyMap, modelData, handleToggleNode]
  );

  const handleExpandAll = useCallback(() => {
    if (!modelData) return;
    const all = new Set<string>();
    const dsName = modelData.datasetName ?? "Model";
    all.add(dsName);
    for (const tName of Object.keys(modelData.tables)) {
      all.add(tName);
    }
    if (modelData.relationships?.length) all.add("rels:_single");
    setExpanded(all);
  }, [modelData]);

  const handleCollapseAll = useCallback(() => {
    setExpanded(new Set());
  }, []);

  const handleCopyRef = useCallback(() => {
    if (daxRef) {
      navigator.clipboard.writeText(daxRef);
    }
  }, [daxRef]);

  // ---------------------------------------------------------------------------
  // Properties panel content
  // ---------------------------------------------------------------------------

  const propertiesContent = useMemo(() => {
    if (!selectedKey || !modelData) return null;
    const parts = selectedKey.split(":");
    const nodeType = parts[0];

    if (nodeType === "measure") {
      const t = modelData.tables[parts[1]];
      const m = t?.measures[parts[2]];
      if (!m) return null;
      return (
        <>
          <PropRow label="Table" value={parts[1]} />
          <PropRow label="Name" value={parts[2]} />
          <PropRow label="Format" value={m.formatString} />
          <PropRow label="Description" value={m.description} />
          <PropRow label="Display Folder" value={m.displayFolder} />
          <PropRow label="Hidden" value={String(m.isHidden)} />
        </>
      );
    }

    if (nodeType === "column") {
      const t = modelData.tables[parts[1]];
      const c = t?.columns[parts[2]];
      if (!c) return null;
      return (
        <>
          <PropRow label="Table" value={parts[1]} />
          <PropRow label="Name" value={parts[2]} />
          <PropRow label="Data Type" value={c.dataType} />
          <PropRow label="Column Type" value={c.type} />
          <PropRow label="Summarize By" value={c.summarizeBy} />
          <PropRow label="Display Folder" value={c.displayFolder} />
          <PropRow label="Is Key" value={String(c.isKey)} />
          <PropRow label="Data Category" value={c.dataCategory} />
          <PropRow label="Sort By" value={c.sortByColumn} />
          <PropRow label="Encoding Hint" value={c.encodingHint} />
          <PropRow label="Nullable" value={String(c.isNullable)} />
          <PropRow label="Hidden" value={String(c.isHidden)} />
        </>
      );
    }

    if (nodeType === "table") {
      const t = modelData.tables[parts[1]];
      if (!t) return null;
      return (
        <>
          <PropRow label="Name" value={parts[1]} />
          <PropRow label="Type" value={t.type} />
          <PropRow label="Description" value={t.description} />
          <PropRow label="Hidden" value={String(t.isHidden)} />
          <PropRow label="Columns" value={String(Object.keys(t.columns).length)} />
          <PropRow label="Measures" value={String(Object.keys(t.measures).length)} />
          <PropRow label="Partitions" value={String(t.partitions?.length ?? 0)} />
        </>
      );
    }

    return null;
  }, [selectedKey, modelData]);

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
          Load Model
        </Button>
        <Button
          appearance="subtle"
          icon={<ArrowExpand20Regular />}
          onClick={handleExpandAll}
          disabled={!modelData}
        >
          Expand All
        </Button>
        <Button
          appearance="subtle"
          icon={<ArrowCollapseAll20Regular />}
          onClick={handleCollapseAll}
          disabled={!modelData}
        >
          Collapse All
        </Button>
        {status.msg && (
          <span
            className={styles.statusBar}
            style={{
              background: `${status.color}1a`,
              color: status.color,
            }}
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
                {modelData ? "No matching items" : "Click Load Model to explore"}
              </div>
            )}
          </div>
        </div>

        <div className={styles.rightPanel}>
          <div className={styles.previewPanel}>
            <div className={styles.sectionLabel}>Expression</div>
            {daxRef && (
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                <code style={{ fontSize: "12px", color: "#555" }}>{daxRef}</code>
                <Tooltip content="Copy DAX reference" relationship="label">
                  <Button appearance="subtle" size="small" icon={<Copy20Regular />} onClick={handleCopyRef} />
                </Tooltip>
              </div>
            )}
            <Textarea
              value={previewText}
              readOnly
              resize="vertical"
              style={{ width: "100%", minHeight: "120px", fontFamily: "monospace", fontSize: "12px" }}
              placeholder="Select a measure to view its DAX expression."
            />
          </div>

          <div className={styles.propertiesPanel}>
            <div className={styles.sectionLabel}>Properties</div>
            {propertiesContent ?? (
              <div style={{ padding: "12px", color: GRAY_COLOR, fontSize: "13px", fontStyle: "italic" }}>
                Select an object to view properties
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
