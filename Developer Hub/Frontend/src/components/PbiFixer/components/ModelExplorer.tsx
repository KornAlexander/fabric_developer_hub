// ModelExplorer — React component (FluentUI)
// Mirrors model_explorer_tab() from _sm_explorer.py

import React, { useState, useCallback, useMemo } from "react";
import {
  Button,
  Input,
  Textarea,
  Spinner,
  Switch,
  makeStyles,
  shorthands,
  Tooltip,
  tokens,
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
  BORDER_COLOR,
  GRAY_COLOR,
  ICON_ACCENT,
  SECTION_BG,
} from "../utils";
import {
  listSemanticModels,
  loadModelData,
  resolveWorkspaceId,
  updateMeasureProperties,
  MeasureEdit,
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
});

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface ModelExplorerProps {
  auth: PbiAuth;
  workspace: string;
  datasetName: string;
  /** Optional resolved Fabric/PBI dataset id. When set, skips the
   *  name-based lookup against /groups/{ws}/datasets (which 404s for
   *  Fabric-native semantic models that aren't indexed in PBI). */
  datasetId?: string;
}

export const ModelExplorer: React.FC<ModelExplorerProps> = ({
  auth,
  workspace,
  datasetName,
  datasetId,
}) => {
  const styles = useStyles();

  const [modelData, setModelData] = useState<ModelData | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [status, setStatus] = useState<{ msg: string; color: string }>({ msg: "", color: GRAY_COLOR });
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  // Per-measure pending edits keyed by `${table}::${measure}`.
  // Each value is a partial overlay (only the fields the user changed).
  type MeasurePatch = Partial<Pick<MeasureEdit, "expression" | "formatString" | "description" | "displayFolder" | "isHidden">>;
  const [pendingMeasureEdits, setPendingMeasureEdits] = useState<Record<string, MeasurePatch>>({});
  const [saving, setSaving] = useState(false);
  const [resolvedIds, setResolvedIds] = useState<{ wsId: string; datasetId: string } | null>(null);
  // Tree highlight set: just the measure keys with edits.
  const pendingChanges = useMemo<Set<string>>(
    () => new Set(Object.keys(pendingMeasureEdits).map((k) => {
      const [t, m] = k.split("::");
      return `measure:${t}:${m}`;
    })),
    [pendingMeasureEdits]
  );
  const [previewText, setPreviewText] = useState("");
  const [daxRef, setDaxRef] = useState("");

  // Identify selected measure (table, name) so the Expression textarea can
  // become editable and bind to pendingMeasureEdits[].expression.
  const selectedMeasure = useMemo<{ table: string; measure: string } | null>(() => {
    if (!selectedKey) return null;
    const parts = selectedKey.split(":");
    if (parts[0] !== "measure" || parts.length < 3) return null;
    return { table: parts[1], measure: parts.slice(2).join(":") };
  }, [selectedKey]);

  // Effective expression value shown in the textarea: pending edit overlay
  // takes precedence over the model's stored expression.
  const expressionValue = useMemo(() => {
    if (!selectedMeasure || !modelData) return previewText;
    const editKey = `${selectedMeasure.table}::${selectedMeasure.measure}`;
    const patch = pendingMeasureEdits[editKey];
    if (patch?.expression !== undefined) return patch.expression;
    const m = modelData.tables[selectedMeasure.table]?.measures[selectedMeasure.measure];
    return m?.expression ?? previewText;
  }, [selectedMeasure, modelData, pendingMeasureEdits, previewText]);

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
    if (!auth.fabricToken || !workspace || !datasetName) {
      setStatus({ msg: "Workspace and dataset name required", color: "#ff3b30" });
      return;
    }
    setLoading(true);
    setStatus({ msg: "Loading model...", color: GRAY_COLOR });
    try {
      const wsId = await resolveWorkspaceId(auth, workspace);
      // Use the id from the picker when available so we don't depend on
      // the PBI groups/datasets index (which omits Fabric-native models).
      let resolvedId = datasetId;
      let resolvedName = datasetName;
      if (!resolvedId) {
        const models = await listSemanticModels(auth, wsId);
        const match = models.find(
          (m) => m.name.toLowerCase() === datasetName.toLowerCase()
        );
        if (!match) {
          setStatus({ msg: `Dataset '${datasetName}' not found`, color: "#ff3b30" });
          setLoading(false);
          return;
        }
        resolvedId = match.id;
        resolvedName = match.name;
      }
      const data = await loadModelData(auth, wsId, resolvedId, resolvedName);
      setModelData(data);
      setResolvedIds({ wsId, datasetId: resolvedId });
      setPendingMeasureEdits({});
      setExpanded(new Set([resolvedName]));
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
  }, [auth, workspace, datasetName, datasetId]);

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
      const t = modelData.tables[tName];
      // Measure display folders — must mirror buildMeasuresWithFolders' key
      // shape (`folder:<table>:<ancestor>`) and include every nested ancestor.
      for (const m of Object.values(t.measures ?? {})) {
        const df = m.displayFolder;
        if (!df) continue;
        const parts = df.replace(/\//g, "\\").split("\\");
        for (let d = 0; d < parts.length; d++) {
          all.add(`folder:${tName}:${parts.slice(0, d + 1).join("\\")}`);
        }
      }
      // Column display folders — `colfolder:<table>:<ancestor>`. Columns may
      // carry multiple folder paths separated by `;` — only the first is used
      // by buildColumnsWithFolders, so mirror that here.
      for (const c of Object.values(t.columns ?? {})) {
        const df = c.displayFolder;
        if (!df) continue;
        const firstFolder = df.split(";")[0].trim();
        if (!firstFolder) continue;
        const parts = firstFolder.replace(/\//g, "\\").split("\\");
        for (let d = 0; d < parts.length; d++) {
          all.add(`colfolder:${tName}:${parts.slice(0, d + 1).join("\\")}`);
        }
      }
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
  // Editable measure handlers
  // ---------------------------------------------------------------------------

  const setMeasureEdit = useCallback(
    (table: string, measure: string, field: keyof Pick<MeasureEdit, "expression" | "formatString" | "description" | "displayFolder" | "isHidden">, value: string | boolean) => {
      const key = `${table}::${measure}`;
      setPendingMeasureEdits((prev) => {
        const cur = { ...(prev[key] ?? {}) } as Record<string, unknown>;
        cur[field] = value;
        return { ...prev, [key]: cur as MeasurePatch };
      });
    },
    []
  );

  const handleDiscardEdits = useCallback(() => {
    setPendingMeasureEdits({});
    setStatus({ msg: "Discarded pending changes", color: GRAY_COLOR });
  }, []);

  const handleSaveEdits = useCallback(async () => {
    if (!resolvedIds || !modelData) return;
    const editsArr: MeasureEdit[] = Object.entries(pendingMeasureEdits).map(([k, patch]) => {
      const [table, measure] = k.split("::");
      return { table, measure, ...patch };
    });
    if (editsArr.length === 0) return;
    setSaving(true);
    setStatus({ msg: `Saving ${editsArr.length} measure change(s)...`, color: GRAY_COLOR });
    try {
      const res = await updateMeasureProperties(auth, resolvedIds.wsId, resolvedIds.datasetId, editsArr);
      // Apply edits to local model data so the UI reflects the saved state.
      setModelData((prev) => {
        if (!prev) return prev;
        const next = { ...prev, tables: { ...prev.tables } };
        for (const e of editsArr) {
          const tbl = next.tables[e.table];
          if (!tbl) continue;
          const m = tbl.measures[e.measure];
          if (!m) continue;
          tbl.measures = {
            ...tbl.measures,
            [e.measure]: {
              ...m,
              ...(e.expression !== undefined ? { expression: e.expression } : {}),
              ...(e.formatString !== undefined ? { formatString: e.formatString } : {}),
              ...(e.description !== undefined ? { description: e.description } : {}),
              ...(e.displayFolder !== undefined ? { displayFolder: e.displayFolder } : {}),
              ...(e.isHidden !== undefined ? { isHidden: e.isHidden } : {}),
            },
          };
        }
        return next;
      });
      setPendingMeasureEdits({});
      const msg = res.errors.length > 0
        ? `Saved ${res.updated}; ${res.errors.length} warning(s): ${res.errors.join("; ")}`
        : `Saved ${res.updated} measure change(s)`;
      setStatus({ msg, color: res.errors.length ? "#ff9500" : "#34c759" });
    } catch (err) {
      setStatus({
        msg: `Save failed: ${err instanceof Error ? err.message : String(err)}`,
        color: "#ff3b30",
      });
    } finally {
      setSaving(false);
    }
  }, [auth, resolvedIds, modelData, pendingMeasureEdits]);

  // ---------------------------------------------------------------------------
  // Properties panel content
  // ---------------------------------------------------------------------------

  const propertiesContent = useMemo(() => {
    if (!selectedKey || !modelData) return null;
    const parts = selectedKey.split(":");
    const nodeType = parts[0];

    if (nodeType === "measure") {
      const tableName = parts[1];
      const measureName = parts[2];
      const t = modelData.tables[tableName];
      const m = t?.measures[measureName];
      if (!m) return null;
      const editKey = `${tableName}::${measureName}`;
      const patch = pendingMeasureEdits[editKey] ?? {};
      const cur = {
        formatString: patch.formatString ?? m.formatString,
        description: patch.description ?? m.description,
        displayFolder: patch.displayFolder ?? m.displayFolder,
        isHidden: patch.isHidden ?? m.isHidden,
      };
      return (
        <>
          <PropRow label="Table" value={tableName} />
          <PropRow label="Name" value={measureName} />
          <PropEditRow label="Format">
            <Input
              size="small"
              value={cur.formatString}
              onChange={(_, d) => setMeasureEdit(tableName, measureName, "formatString", d.value)}
            />
          </PropEditRow>
          <PropEditRow label="Description">
            <Textarea
              size="small"
              value={cur.description}
              resize="vertical"
              style={{ width: "100%", minHeight: "48px" }}
              onChange={(_, d) => setMeasureEdit(tableName, measureName, "description", d.value)}
            />
          </PropEditRow>
          <PropEditRow label="Display Folder">
            <Input
              size="small"
              value={cur.displayFolder}
              onChange={(_, d) => setMeasureEdit(tableName, measureName, "displayFolder", d.value)}
            />
          </PropEditRow>
          <PropEditRow label="Hidden">
            <Switch
              checked={cur.isHidden}
              onChange={(_, d) => setMeasureEdit(tableName, measureName, "isHidden", d.checked)}
            />
          </PropEditRow>
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
  }, [selectedKey, modelData, pendingMeasureEdits, setMeasureEdit]);

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
              <div style={{ padding: "20px", color: GRAY_COLOR, textAlign: "center", fontStyle: "italic" }}>
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
                <code style={{ fontSize: "12px", color: tokens.colorNeutralForeground2 }}>{daxRef}</code>
                <Tooltip content="Copy DAX reference" relationship="label">
                  <Button appearance="subtle" size="small" icon={<Copy20Regular />} onClick={handleCopyRef} />
                </Tooltip>
              </div>
            )}
            <Textarea
              value={selectedMeasure ? expressionValue : previewText}
              readOnly={!selectedMeasure}
              resize="vertical"
              style={{ width: "100%", minHeight: "120px", fontFamily: "monospace", fontSize: "12px" }}
              placeholder="Select a measure to view its DAX expression."
              onChange={selectedMeasure ? (_, d) => setMeasureEdit(selectedMeasure.table, selectedMeasure.measure, "expression", d.value) : undefined}
            />
          </div>

          <div className={styles.propertiesPanel}>
            <div className={styles.sectionLabel}>Properties</div>
            {propertiesContent ?? (
              <div style={{ padding: "12px", color: GRAY_COLOR, fontSize: "13px", fontStyle: "italic" }}>
                Select an object to view properties
              </div>
            )}
            {Object.keys(pendingMeasureEdits).length > 0 && (
              <div style={{ display: "flex", gap: "8px", marginTop: "12px", paddingTop: "8px", borderTop: `1px solid ${BORDER_COLOR}` }}>
                <Button
                  appearance="primary"
                  size="small"
                  onClick={handleSaveEdits}
                  disabled={saving || !resolvedIds}
                  icon={saving ? <Spinner size="tiny" /> : undefined}
                >
                  Save {Object.keys(pendingMeasureEdits).length} change{Object.keys(pendingMeasureEdits).length === 1 ? "" : "s"}
                </Button>
                <Button appearance="secondary" size="small" onClick={handleDiscardEdits} disabled={saving}>
                  Discard
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

const PropEditRow: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => {
  const styles = useStyles();
  return (
    <div className={styles.propRow} style={{ alignItems: "center" }}>
      <span className={styles.propLabel}>{label}</span>
      <span className={styles.propValue} style={{ flex: 1 }}>{children}</span>
    </div>
  );
};
