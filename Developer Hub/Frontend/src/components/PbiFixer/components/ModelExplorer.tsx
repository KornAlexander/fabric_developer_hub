// ModelExplorer — React component (FluentUI)
// Mirrors model_explorer_tab() from _sm_explorer.py

import React, { useState, useCallback, useMemo, useEffect } from "react";
import {
  Button,
  Input,
  Textarea,
  Spinner,
  Switch,
  Dropdown,
  Option,
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
  Wrench20Regular,
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
// Hero design ported from AgentHub Sessions tab. Eyebrow + gradient title +
// blue CTA mirror .sessions-hero / .sessions-cta in src/styles.scss.
import {
  listSemanticModels,
  loadModelData,
  resolveWorkspaceId,
  updateMeasureProperties,
  updateColumnProperties,
  updateTableProperties,
  updateRelationshipProperties,
  updatePartitionExpressions,
  executeDax,
  formatDax,
  MeasureEdit,
  ColumnEdit,
  TableEdit,
  RelationshipEdit,
  PartitionEdit,
  PbiAuth,
} from "../services";
import {
  loadPerspectives,
  type PerspectiveMember,
} from "../services/perspectivesApi";
import { FIXERS, type Fixer, type FixerResult, type FixerContext } from "../fixers";

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
    ...shorthands.padding("20px", "24px"),
    ...shorthands.margin("-24px"),
    overflowY: "auto",
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
    backgroundColor: "#ffffff",
    boxShadow: "0 1px 2px rgba(0, 0, 0, 0.04)",
    minHeight: "160px",
  },
  propertiesPanel: {
    ...shorthands.border("1px", "solid", BORDER_COLOR),
    ...shorthands.borderRadius("8px"),
    ...shorthands.padding("8px"),
    backgroundColor: "#ffffff",
    boxShadow: "0 1px 2px rgba(0, 0, 0, 0.04)",
    flex: 1,
    minHeight: 0,
    overflowY: "auto",
    // v0.87 — column flex so child blocks (e.g. partition Expression
    // editor) can flex-grow to fill the panel instead of being capped
    // by their minHeight, leaving the bottom of the panel unused.
    display: "flex",
    flexDirection: "column",
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
  /** Inline connection picker (Workspace + Semantic Model) injected by
   *  PbiFixerPage so it sits between the description and the Load Model
   *  toolbar instead of in the page-level chrome. */
  connectionSlot?: React.ReactNode;
  /** Version badge shown next to the eyebrow ("POWER BI FIXER"). */
  version?: string;
}

export const ModelExplorer: React.FC<ModelExplorerProps> = ({
  auth,
  workspace,
  datasetName,
  datasetId,
  connectionSlot,
  version,
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
  type MeasurePatch = Partial<Pick<MeasureEdit, "newName" | "expression" | "formatString" | "description" | "displayFolder" | "isHidden">>;
  type ColumnPatch = Partial<Pick<ColumnEdit, "newName" | "description" | "displayFolder" | "isHidden" | "summarizeBy" | "dataCategory" | "formatString">>;
  type TablePatch = Partial<Pick<TableEdit, "description" | "isHidden">>;
  type RelPatch = Partial<Pick<RelationshipEdit, "isActive" | "crossFilteringBehavior">>;
  // v0.72 — partition edits keyed by `${table}::${partition}`. Only the
  // M / DAX expression body is editable; sourceType + name are fixed.
  type PartitionPatch = Partial<Pick<PartitionEdit, "expression">>;
  const [pendingMeasureEdits, setPendingMeasureEdits] = useState<Record<string, MeasurePatch>>({});
  // Pending edits per object kind, keyed by `${table}::${name}` (column),
  // `${table}` (table), or `${i}` (relationship index in modelData.relationships).
  const [pendingColumnEdits, setPendingColumnEdits] = useState<Record<string, ColumnPatch>>({});
  const [pendingTableEdits, setPendingTableEdits] = useState<Record<string, TablePatch>>({});
  const [pendingRelEdits, setPendingRelEdits] = useState<Record<string, RelPatch>>({});
  const [pendingPartitionEdits, setPendingPartitionEdits] = useState<Record<string, PartitionPatch>>({});
  const [formatting, setFormatting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [resolvedIds, setResolvedIds] = useState<{ wsId: string; datasetId: string } | null>(null);
  // v0.73 — Quick-fix scan state. `scanResults` is keyed by fixer id; only
  // entries with findings.length > 0 are surfaced in the UI. `scanRanOnce`
  // distinguishes "never scanned" from "scanned, no issues".
  const [scanResults, setScanResults] = useState<Record<string, FixerResult>>({});
  const [scanning, setScanning] = useState(false);
  const [scanRanOnce, setScanRanOnce] = useState(false);
  const [applyingFixerId, setApplyingFixerId] = useState<string | null>(null);
  // Total pending edit count across all object kinds.
  const totalPendingEdits =
    Object.keys(pendingMeasureEdits).length +
    Object.keys(pendingColumnEdits).length +
    Object.keys(pendingTableEdits).length +
    Object.keys(pendingRelEdits).length +
    Object.keys(pendingPartitionEdits).length;
  // Tree highlight set: union of all pending object keys.
  const pendingChanges = useMemo<Set<string>>(() => {
    const s = new Set<string>();
    for (const k of Object.keys(pendingMeasureEdits)) {
      const [t, m] = k.split("::");
      s.add(`measure:${t}:${m}`);
    }
    for (const k of Object.keys(pendingColumnEdits)) {
      const [t, c] = k.split("::");
      s.add(`column:${t}:${c}`);
    }
    for (const t of Object.keys(pendingTableEdits)) {
      s.add(`table:${t}`);
    }
    for (const i of Object.keys(pendingRelEdits)) {
      s.add(`rel:_single:${i}`);
    }
    for (const k of Object.keys(pendingPartitionEdits)) {
      const [t, p] = k.split("::");
      s.add(`partition:${t}:${p}`);
    }
    return s;
  }, [pendingMeasureEdits, pendingColumnEdits, pendingTableEdits, pendingRelEdits, pendingPartitionEdits]);
  const [previewText, setPreviewText] = useState("");
  const [daxRef, setDaxRef] = useState("");

  // Table data preview cache (TOPN(100, '<Table>')) keyed by table name.
  const [tablePreview, setTablePreview] = useState<Record<string, { loading: boolean; rows: Record<string, unknown>[]; error: string | null }>>({});

  // Perspective filter — when set, restrict tree to objects belonging to
  // that perspective (TMDL exposure of perspective membership is partial,
  // so this currently filters tables only when the perspective name
  // appears in any object's parsed perspectives list).
  const [perspectiveFilter, setPerspectiveFilter] = useState<string>("");
  const [perspectives, setPerspectives] = useState<{ name: string }[]>([]);
  const [perspectiveMembers, setPerspectiveMembers] = useState<PerspectiveMember[]>([]);

  // Right-click context menu state
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; key: string } | null>(null);

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

  // v0.77 — partitions render their (potentially long) M expression in the
  // editable Properties textarea below; hide the top read-only Expression
  // panel for them so the script isn't shown twice and truncated up top.
  const selectedNodeType = useMemo(() => {
    if (!selectedKey) return null;
    return selectedKey.split(":")[0];
  }, [selectedKey]);
  const showPreviewPanel = selectedNodeType !== "partition";

  // Build tree
  const treeResult = useMemo<TreeBuildResult>(() => {
    if (!modelData) return { options: [], keyMap: {} };
    return buildModelTree(modelData, expanded, {}, pendingChanges);
  }, [modelData, expanded, pendingChanges]);

  const filteredOptions = useMemo(
    () => filterTreeOptions(treeResult.options, searchQuery),
    [treeResult.options, searchQuery]
  );

  // Apply perspective filter on top of the search filter. Allowed object
  // keys are: any table-name in the perspective + its measures/columns/
  // hierarchies + display folders inside such a table + level rows.
  const perspectiveFilteredOptions = useMemo(() => {
    if (!perspectiveFilter || !modelData) return filteredOptions;
    const tablesInPersp = new Set<string>();
    const memberPaths = new Set<string>();
    for (const m of perspectiveMembers) {
      if (m.perspectiveName !== perspectiveFilter) continue;
      tablesInPersp.add(m.tableName);
      memberPaths.add(m.path);
    }
    return filteredOptions.filter((opt) => {
      const key = treeResult.keyMap[opt];
      if (!key) return true;
      const p = key.split(":");
      const t = p[0];
      if (t === "model") return true;
      if (t === "table") return tablesInPersp.has(p[1]);
      if (t === "folder" || t === "colfolder") return tablesInPersp.has(p[1]);
      if (t === "column") return memberPaths.has(`${p[1]}[${p[2]}]`);
      if (t === "measure") return memberPaths.has(`${p[1]}[${p[2]}]`);
      if (t === "hierarchy" || t === "level") return memberPaths.has(`${p[1]}::${p[2]}`);
      if (t === "calc_item") return tablesInPersp.has(p[1]);
      if (t === "partition") return tablesInPersp.has(p[1]);
      // Relationships and perspectives meta nodes always shown.
      return true;
    });
  }, [filteredOptions, treeResult.keyMap, perspectiveFilter, perspectiveMembers, modelData]);

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleLoad = useCallback(async () => {
    if (!auth.fabricToken || !workspace || !datasetName) {
      setStatus({ msg: "Workspace and semantic model required", color: "#ff3b30" });
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
      setPendingColumnEdits({});
      setPendingTableEdits({});
      setPendingRelEdits({});
      setExpanded(new Set([resolvedName]));
      setStatus({
        msg: `Loaded ${Object.keys(data.tables).length} tables`,
        color: "#34c759",
      });
      // Lazy-load perspectives in the background — non-fatal on failure.
      loadPerspectives(auth, wsId, resolvedId)
        .then((p) => {
          setPerspectives(p.perspectives.map((x) => ({ name: x.name })));
          setPerspectiveMembers(p.members);
        })
        .catch(() => { /* silent */ });
    } catch (err) {
      setStatus({
        msg: `Error: ${err instanceof Error ? err.message : String(err)}`,
        color: "#ff3b30",
      });
    } finally {
      setLoading(false);
    }
  }, [auth, workspace, datasetName, datasetId]);

  // v0.91 — Auto-load on selection (mirrors Memory Analyzer / Model BPA pattern).
  // The Load Model button has been removed; selecting a workspace + semantic
  // model in the shared picker triggers handleLoad automatically. handleLoad
  // is memoised on those inputs, so the effect re-runs whenever they change.
  useEffect(() => { void handleLoad(); }, [handleLoad]);

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
      } else if (nodeType === "hierarchy") {
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
      // Hierarchies — expand each so nested level rows render.
      for (const hn of Object.keys(t.hierarchies ?? {})) {
        all.add(`hierarchy:${tName}:${hn}`);
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
    (table: string, measure: string, field: keyof MeasurePatch, value: string | boolean) => {
      const key = `${table}::${measure}`;
      setPendingMeasureEdits((prev) => {
        const cur = { ...(prev[key] ?? {}) } as Record<string, unknown>;
        cur[field] = value;
        return { ...prev, [key]: cur as MeasurePatch };
      });
    },
    []
  );

  const setColumnEdit = useCallback(
    (table: string, column: string, field: keyof ColumnPatch, value: string | boolean) => {
      const key = `${table}::${column}`;
      setPendingColumnEdits((prev) => {
        const cur = { ...(prev[key] ?? {}) } as Record<string, unknown>;
        cur[field] = value;
        return { ...prev, [key]: cur as ColumnPatch };
      });
    },
    []
  );

  const setTableEdit = useCallback(
    (table: string, field: keyof TablePatch, value: string | boolean) => {
      setPendingTableEdits((prev) => {
        const cur = { ...(prev[table] ?? {}) } as Record<string, unknown>;
        cur[field] = value;
        return { ...prev, [table]: cur as TablePatch };
      });
    },
    []
  );

  const setRelEdit = useCallback(
    (idx: number, field: keyof RelPatch, value: string | boolean) => {
      const key = String(idx);
      setPendingRelEdits((prev) => {
        const cur = { ...(prev[key] ?? {}) } as Record<string, unknown>;
        cur[field] = value;
        return { ...prev, [key]: cur as RelPatch };
      });
    },
    []
  );

  // v0.72 — partition expression setter (M / DAX body).
  const setPartitionEdit = useCallback(
    (table: string, partition: string, value: string) => {
      const key = `${table}::${partition}`;
      setPendingPartitionEdits((prev) => ({
        ...prev,
        [key]: { expression: value },
      }));
    },
    []
  );

  const handleDiscardEdits = useCallback(() => {
    setPendingMeasureEdits({});
    setPendingColumnEdits({});
    setPendingTableEdits({});
    setPendingRelEdits({});
    setPendingPartitionEdits({});
    setStatus({ msg: "Discarded pending changes", color: GRAY_COLOR });
  }, []);

  // Format the currently-edited measure expression via daxformatter.com.
  // On CORS / network failure, fall back to copying the expression to
  // the clipboard and opening daxformatter.com so the user can paste.
  const handleFormatDax = useCallback(async () => {
    if (!selectedMeasure) return;
    const current = expressionValue;
    if (!current.trim()) return;
    setFormatting(true);
    setStatus({ msg: "Formatting via daxformatter.com…", color: GRAY_COLOR });
    try {
      const formatted = await formatDax(current);
      setMeasureEdit(selectedMeasure.table, selectedMeasure.measure, "expression", formatted);
      setStatus({ msg: "DAX formatted", color: "#34c759" });
    } catch (err) {
      try { await navigator.clipboard.writeText(current); } catch { /* ignore */ }
      window.open("https://www.daxformatter.com/", "_blank", "noopener,noreferrer");
      setStatus({
        msg: `Formatter unreachable (${err instanceof Error ? err.message : String(err)}); expression copied to clipboard, daxformatter.com opened in a new tab.`,
        color: "#ff9500",
      });
    } finally {
      setFormatting(false);
    }
  }, [selectedMeasure, expressionValue, setMeasureEdit]);

  // Load TOPN(100) preview for a table via /executeQueries.
  const handleLoadTablePreview = useCallback(
    async (tableName: string) => {
      if (!resolvedIds) return;
      setTablePreview((prev) => ({
        ...prev,
        [tableName]: { loading: true, rows: [], error: null },
      }));
      try {
        const escName = tableName.replace(/'/g, "''");
        const dax = `EVALUATE TOPN(100, '${escName}')`;
        const rows = await executeDax(auth, resolvedIds.wsId, resolvedIds.datasetId, dax);
        setTablePreview((prev) => ({
          ...prev,
          [tableName]: { loading: false, rows, error: null },
        }));
      } catch (err) {
        setTablePreview((prev) => ({
          ...prev,
          [tableName]: {
            loading: false,
            rows: [],
            error: err instanceof Error ? err.message : String(err),
          },
        }));
      }
    },
    [auth, resolvedIds]
  );

  const handleSaveEdits = useCallback(async () => {
    if (!resolvedIds || !modelData) return;
    const measureEditsArr: MeasureEdit[] = Object.entries(pendingMeasureEdits).map(([k, patch]) => {
      const [table, measure] = k.split("::");
      return { table, measure, ...patch };
    });
    const columnEditsArr: ColumnEdit[] = Object.entries(pendingColumnEdits).map(([k, patch]) => {
      const [table, column] = k.split("::");
      return { table, column, ...patch };
    });
    const tableEditsArr: TableEdit[] = Object.entries(pendingTableEdits).map(([table, patch]) => ({
      table, ...patch,
    }));
    const relEditsArr: RelationshipEdit[] = Object.entries(pendingRelEdits).map(([idxStr, patch]) => {
      const r = modelData.relationships[Number(idxStr)];
      return {
        fromTable: r.fromTable,
        fromColumn: r.fromColumn,
        toTable: r.toTable,
        toColumn: r.toColumn,
        ...patch,
      };
    });
    const partitionEditsArr: PartitionEdit[] = Object.entries(pendingPartitionEdits)
      .filter(([, patch]) => typeof patch.expression === "string")
      .map(([k, patch]) => {
        const [table, partition] = k.split("::");
        return { table, partition, expression: patch.expression as string };
      });
    const total = measureEditsArr.length + columnEditsArr.length + tableEditsArr.length + relEditsArr.length + partitionEditsArr.length;
    if (total === 0) return;
    setSaving(true);
    setStatus({ msg: `Saving ${total} change(s)...`, color: GRAY_COLOR });
    try {
      const results = await Promise.all([
        measureEditsArr.length
          ? updateMeasureProperties(auth, resolvedIds.wsId, resolvedIds.datasetId, measureEditsArr)
          : Promise.resolve({ updated: 0, errors: [] as string[] }),
        columnEditsArr.length
          ? updateColumnProperties(auth, resolvedIds.wsId, resolvedIds.datasetId, columnEditsArr)
          : Promise.resolve({ updated: 0, errors: [] as string[] }),
        tableEditsArr.length
          ? updateTableProperties(auth, resolvedIds.wsId, resolvedIds.datasetId, tableEditsArr)
          : Promise.resolve({ updated: 0, errors: [] as string[] }),
        relEditsArr.length
          ? updateRelationshipProperties(auth, resolvedIds.wsId, resolvedIds.datasetId, relEditsArr)
          : Promise.resolve({ updated: 0, errors: [] as string[] }),
        partitionEditsArr.length
          ? updatePartitionExpressions(auth, resolvedIds.wsId, resolvedIds.datasetId, partitionEditsArr)
          : Promise.resolve({ updated: 0, errors: [] as string[] }),
      ]);
      const res = {
        updated: results.reduce((s, r) => s + r.updated, 0),
        errors: results.flatMap((r) => r.errors),
      };
      const editsArr = measureEditsArr; // retained for the post-save model patch loop below
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
        for (const e of columnEditsArr) {
          const tbl = next.tables[e.table];
          if (!tbl) continue;
          const c = tbl.columns[e.column];
          if (!c) continue;
          tbl.columns = {
            ...tbl.columns,
            [e.column]: {
              ...c,
              ...(e.description !== undefined ? { description: e.description } as never : {}),
              ...(e.displayFolder !== undefined ? { displayFolder: e.displayFolder } : {}),
              ...(e.isHidden !== undefined ? { isHidden: e.isHidden } : {}),
              ...(e.summarizeBy !== undefined ? { summarizeBy: e.summarizeBy } : {}),
              ...(e.dataCategory !== undefined ? { dataCategory: e.dataCategory } : {}),
              ...(e.formatString !== undefined ? { formatString: e.formatString } as never : {}),
            },
          };
        }
        for (const e of tableEditsArr) {
          const tbl = next.tables[e.table];
          if (!tbl) continue;
          next.tables[e.table] = {
            ...tbl,
            ...(e.description !== undefined ? { description: e.description } : {}),
            ...(e.isHidden !== undefined ? { isHidden: e.isHidden } : {}),
          };
        }
        if (relEditsArr.length > 0) {
          next.relationships = next.relationships.map((r) => {
            const match = relEditsArr.find(
              (e) => e.fromTable === r.fromTable && e.fromColumn === r.fromColumn
                && e.toTable === r.toTable && e.toColumn === r.toColumn,
            );
            if (!match) return r;
            return {
              ...r,
              ...(match.isActive !== undefined ? { isActive: match.isActive } : {}),
              ...(match.crossFilteringBehavior !== undefined ? { crossFilter: match.crossFilteringBehavior } : {}),
            };
          });
        }
        // v0.72 — reflect partition expression edits in local model.
        for (const e of partitionEditsArr) {
          const tbl = next.tables[e.table];
          if (!tbl) continue;
          const newPartitions = (tbl.partitions ?? []).map((p) =>
            p.name === e.partition ? { ...p, expression: e.expression } : p,
          );
          next.tables[e.table] = { ...tbl, partitions: newPartitions };
        }
        return next;
      });
      setPendingMeasureEdits({});
      setPendingColumnEdits({});
      setPendingTableEdits({});
      setPendingRelEdits({});
      setPendingPartitionEdits({});
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
  }, [auth, resolvedIds, modelData, pendingMeasureEdits, pendingColumnEdits, pendingTableEdits, pendingRelEdits, pendingPartitionEdits]);

  // v0.73 — Quick fixer integration. Lists all backend semantic-model
  // fixers and runs them in parallel scan-only mode against the loaded
  // model. Only fixers reporting findings are surfaced to the user.
  const modelFixers = useMemo<Fixer[]>(
    () => FIXERS.filter((f) => f.scope === "sm" && f.mode === "backend"),
    []
  );

  const buildFixerCtx = useCallback((): FixerContext | null => {
    if (!resolvedIds) return null;
    return {
      auth,
      workspaceId: resolvedIds.wsId,
      datasetId: resolvedIds.datasetId,
      model: modelData ?? undefined,
    };
  }, [auth, resolvedIds, modelData]);

  const handleScanFixers = useCallback(async () => {
    const ctx = buildFixerCtx();
    if (!ctx) {
      setStatus({ msg: "Load a model first", color: "#ff3b30" });
      return;
    }
    setScanning(true);
    setStatus({ msg: `Scanning ${modelFixers.length} model fixer(s)…`, color: GRAY_COLOR });
    try {
      const entries = await Promise.all(
        modelFixers.map(async (fx) => {
          try {
            const res = await fx.scan(ctx);
            return [fx.id, res] as const;
          } catch (err) {
            return [fx.id, { findings: [], applied: false, log: [`scan error: ${err instanceof Error ? err.message : String(err)}`] }] as const;
          }
        })
      );
      const next: Record<string, FixerResult> = {};
      for (const [id, res] of entries) next[id] = res;
      setScanResults(next);
      setScanRanOnce(true);
      const withFindings = entries.filter(([, r]) => r.findings.length > 0).length;
      setStatus({
        msg: withFindings === 0
          ? "Scan complete — no model issues found"
          : `Scan complete — ${withFindings} fixer(s) with issues`,
        color: withFindings === 0 ? "#34c759" : "#ff9500",
      });
    } finally {
      setScanning(false);
    }
  }, [buildFixerCtx, modelFixers]);

  const handleApplyFixer = useCallback(async (fx: Fixer) => {
    const ctx = buildFixerCtx();
    if (!ctx) return;
    setApplyingFixerId(fx.id);
    setStatus({ msg: `Applying ${fx.title}…`, color: GRAY_COLOR });
    try {
      const res = await fx.apply(ctx);
      // Re-scan just this one fixer so its findings (and the model's
      // current state on Fabric) are reflected in the panel.
      let rescan: FixerResult | null = null;
      try {
        rescan = await fx.scan(ctx);
      } catch { /* ignore — keep prior result */ }
      setScanResults((prev) => ({ ...prev, [fx.id]: rescan ?? { ...res, applied: false } }));
      // Reload the model so structural changes (e.g. new tables, dropped
      // columns) appear in the tree without requiring a manual reload.
      if (res.applied && resolvedIds) {
        try {
          const data = await loadModelData(auth, resolvedIds.wsId, resolvedIds.datasetId);
          setModelData(data);
        } catch { /* keep existing model on reload failure */ }
      }
      setStatus({
        msg: res.applied
          ? `Applied ${fx.title} — ${res.findings.length} change(s)`
          : `${fx.title}: ${res.findings.length} finding(s), nothing applied`,
        color: res.applied ? "#34c759" : (res.findings.length ? "#ff9500" : GRAY_COLOR),
      });
    } catch (err) {
      setStatus({ msg: `Apply failed: ${err instanceof Error ? err.message : String(err)}`, color: "#ff3b30" });
    } finally {
      setApplyingFixerId(null);
    }
  }, [auth, buildFixerCtx, resolvedIds]);

  const fixersWithFindings = useMemo(
    () => modelFixers.filter((f) => (scanResults[f.id]?.findings.length ?? 0) > 0),
    [modelFixers, scanResults]
  );

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
        newName: patch.newName ?? measureName,
        formatString: patch.formatString ?? m.formatString,
        description: patch.description ?? m.description,
        displayFolder: patch.displayFolder ?? m.displayFolder,
        isHidden: patch.isHidden ?? m.isHidden,
      };
      return (
        <>
          <PropRow label="Table" value={tableName} />
          <PropEditRow label="Name">
            <Input
              size="small"
              value={cur.newName}
              onChange={(_, d) => setMeasureEdit(tableName, measureName, "newName", d.value)}
            />
          </PropEditRow>
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
      const tableName = parts[1];
      const columnName = parts[2];
      const editKey = `${tableName}::${columnName}`;
      const patch = pendingColumnEdits[editKey] ?? {};
      const cur = {
        newName: patch.newName ?? columnName,
        displayFolder: patch.displayFolder ?? c.displayFolder,
        isHidden: patch.isHidden ?? c.isHidden,
        summarizeBy: patch.summarizeBy ?? c.summarizeBy,
        dataCategory: patch.dataCategory ?? c.dataCategory,
      };
      return (
        <>
          <PropRow label="Table" value={tableName} />
          <PropEditRow label="Name">
            <Input
              size="small"
              value={cur.newName}
              onChange={(_, d) => setColumnEdit(tableName, columnName, "newName", d.value)}
            />
          </PropEditRow>
          <PropRow label="Data Type" value={c.dataType} />
          <PropRow label="Column Type" value={c.type} />
          <PropEditRow label="Summarize By">
            <Input
              size="small"
              value={cur.summarizeBy}
              onChange={(_, d) => setColumnEdit(tableName, columnName, "summarizeBy", d.value)}
            />
          </PropEditRow>
          <PropEditRow label="Display Folder">
            <Input
              size="small"
              value={cur.displayFolder}
              onChange={(_, d) => setColumnEdit(tableName, columnName, "displayFolder", d.value)}
            />
          </PropEditRow>
          <PropRow label="Is Key" value={String(c.isKey)} />
          <PropEditRow label="Data Category">
            <Input
              size="small"
              value={cur.dataCategory}
              onChange={(_, d) => setColumnEdit(tableName, columnName, "dataCategory", d.value)}
            />
          </PropEditRow>
          <PropRow label="Sort By" value={c.sortByColumn} />
          <PropRow label="Encoding Hint" value={c.encodingHint} />
          <PropRow label="Nullable" value={String(c.isNullable)} />
          <PropEditRow label="Hidden">
            <Switch
              checked={cur.isHidden}
              onChange={(_, d) => setColumnEdit(tableName, columnName, "isHidden", d.checked)}
            />
          </PropEditRow>
        </>
      );
    }

    if (nodeType === "table") {
      const t = modelData.tables[parts[1]];
      if (!t) return null;
      const tName = parts[1];
      const prev = tablePreview[tName];
      const tablePatch = pendingTableEdits[tName] ?? {};
      const tCur = {
        description: tablePatch.description ?? t.description,
        isHidden: tablePatch.isHidden ?? t.isHidden,
      };
      return (
        <>
          <PropRow label="Name" value={tName} />
          <PropRow label="Type" value={t.type} />
          <PropEditRow label="Description">
            <Textarea
              size="small"
              value={tCur.description}
              resize="vertical"
              style={{ width: "100%", minHeight: "48px" }}
              onChange={(_, d) => setTableEdit(tName, "description", d.value)}
            />
          </PropEditRow>
          <PropEditRow label="Hidden">
            <Switch
              checked={tCur.isHidden}
              onChange={(_, d) => setTableEdit(tName, "isHidden", d.checked)}
            />
          </PropEditRow>
          <PropRow label="Columns" value={String(Object.keys(t.columns).length)} />
          <PropRow label="Measures" value={String(Object.keys(t.measures).length)} />
          <PropRow label="Partitions" value={String(t.partitions?.length ?? 0)} />
          {t.partitions?.map((p, i) => {
            const editKey = `${tName}::${p.name}`;
            const partPatch = pendingPartitionEdits[editKey] ?? {};
            const exprValue = partPatch.expression ?? p.expression;
            return (
              <React.Fragment key={`p${i}`}>
                <PropRow label={`Partition ${i + 1}`} value={p.name} />
                <PropRow label={`  Source Type`} value={p.sourceType} />
                <PropEditRow label={`  Expression`}>
                  <Textarea
                    size="small"
                    value={exprValue}
                    resize="vertical"
                    style={{ width: "100%", minHeight: "120px", fontFamily: "Consolas, 'Cascadia Code', monospace", fontSize: "12px" }}
                    onChange={(_, d) => setPartitionEdit(tName, p.name, d.value)}
                  />
                </PropEditRow>
              </React.Fragment>
            );
          })}
          <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
            <Button
              size="small"
              appearance="secondary"
              onClick={() => handleLoadTablePreview(tName)}
              disabled={prev?.loading || !resolvedIds}
              icon={prev?.loading ? <Spinner size="tiny" /> : undefined}
            >
              Preview data (TOPN 100)
            </Button>
            {prev?.rows && prev.rows.length > 0 && (
              <span style={{ fontSize: 12, color: GRAY_COLOR }}>
                {prev.rows.length} row(s)
              </span>
            )}
          </div>
          {prev?.error && (
            <div style={{ marginTop: 6, fontSize: 12, color: "#ff3b30" }}>{prev.error}</div>
          )}
          {prev?.rows && prev.rows.length > 0 && (
            <div
              style={{
                marginTop: 8,
                overflow: "auto",
                maxHeight: 280,
                border: `1px solid ${BORDER_COLOR}`,
                borderRadius: 4,
              }}
            >
              <table style={{ borderCollapse: "collapse", fontSize: 12, width: "100%" }}>
                <thead style={{ background: SECTION_BG, position: "sticky", top: 0 }}>
                  <tr>
                    {Object.keys(prev.rows[0]).map((c) => (
                      <th
                        key={c}
                        style={{
                          textAlign: "left",
                          padding: "4px 8px",
                          borderBottom: `1px solid ${BORDER_COLOR}`,
                          fontWeight: 600,
                          whiteSpace: "nowrap",
                        }}
                      >
                        {c.replace(/^[^\[]*\[/, "").replace(/\]$/, "")}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {prev.rows.map((row, ri) => (
                    <tr key={ri}>
                      {Object.keys(prev.rows[0]).map((c) => (
                        <td
                          key={c}
                          style={{
                            padding: "3px 8px",
                            borderBottom: `1px solid ${BORDER_COLOR}`,
                            whiteSpace: "nowrap",
                          }}
                        >
                          {String(row[c] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      );
    }

    if (nodeType === "hierarchy") {
      const t = modelData.tables[parts[1]];
      const h = t?.hierarchies[parts[2]];
      if (!h) return null;
      return (
        <>
          <PropRow label="Table" value={parts[1]} />
          <PropRow label="Name" value={parts[2]} />
          <PropRow label="Levels" value={String(h.levels?.length ?? 0)} />
          {(h.levels ?? []).map((lvl, i) => (
            <PropRow key={i} label={`  Level ${i + 1}`} value={lvl} />
          ))}
        </>
      );
    }

    if (nodeType === "level") {
      const t = modelData.tables[parts[1]];
      const h = t?.hierarchies[parts[2]];
      const idx = Number(parts[3]);
      const lvl = h?.levels?.[idx];
      if (!lvl) return null;
      return (
        <>
          <PropRow label="Table" value={parts[1]} />
          <PropRow label="Hierarchy" value={parts[2]} />
          <PropRow label="Position" value={String(idx + 1)} />
          <PropRow label="Name" value={lvl} />
        </>
      );
    }

    if (nodeType === "partition") {
      const tableName = parts[1];
      const t = modelData.tables[tableName];
      const p = t?.partitions?.find((x) => x.name === parts[2]);
      if (!p) return null;
      const editKey = `${tableName}::${p.name}`;
      const partPatch = pendingPartitionEdits[editKey] ?? {};
      const exprValue = partPatch.expression ?? p.expression;
      return (
        <>
          <PropRow label="Table" value={tableName} />
          <PropRow label="Name" value={p.name} />
          <PropRow label="Source Type" value={p.sourceType} />
          {/* v0.87 — Expression editor expands to fill the remaining
              vertical space of the Properties panel instead of staying
              at a fixed 160px and leaving the bottom empty. */}
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              flex: 1,
              minHeight: 0,
              marginTop: "6px",
            }}
          >
            <span
              className={styles.propLabel}
              style={{ marginBottom: 4, minWidth: 0 }}
            >
              Expression (M / DAX)
            </span>
            <Textarea
              size="small"
              value={exprValue}
              resize="none"
              style={{
                width: "100%",
                flex: 1,
                minHeight: "160px",
                display: "flex",
              }}
              textarea={{
                style: {
                  height: "100%",
                  fontFamily: "Consolas, 'Cascadia Code', monospace",
                  fontSize: "12px",
                },
              }}
              onChange={(_, d) => setPartitionEdit(tableName, p.name, d.value)}
            />
          </div>
        </>
      );
    }

    if (nodeType === "rel" || nodeType === "rels") {
      const idx = Number(parts[2]);
      const r = modelData.relationships?.[idx];
      if (!r) return null;
      const relPatch = pendingRelEdits[String(idx)] ?? {};
      const rCur = {
        isActive: relPatch.isActive ?? r.isActive,
        crossFilteringBehavior: relPatch.crossFilteringBehavior ?? r.crossFilter,
      };
      return (
        <>
          <PropRow label="From" value={`${r.fromTable}[${r.fromColumn}]`} />
          <PropRow label="To" value={`${r.toTable}[${r.toColumn}]`} />
          <PropEditRow label="Active">
            <Switch
              checked={rCur.isActive}
              onChange={(_, d) => setRelEdit(idx, "isActive", d.checked)}
            />
          </PropEditRow>
          <PropEditRow label="Cross Filter">
            <Input
              size="small"
              value={rCur.crossFilteringBehavior}
              onChange={(_, d) => setRelEdit(idx, "crossFilteringBehavior", d.value)}
            />
          </PropEditRow>
          <PropRow label="Multiplicity" value={r.multiplicity} />
          <PropRow label="Security Filtering" value={r.securityFiltering} />
        </>
      );
    }

    return null;
  }, [selectedKey, modelData, pendingMeasureEdits, pendingColumnEdits, pendingTableEdits, pendingRelEdits, pendingPartitionEdits, setMeasureEdit, setColumnEdit, setTableEdit, setRelEdit, setPartitionEdit, tablePreview, handleLoadTablePreview, resolvedIds]);

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
        <h1 className={styles.heroTitle}>Model Explorer</h1>
        <p className={styles.heroSubtitle}>
          Browse tables, measures, columns and relationships of the loaded
          semantic model. Inspect DAX expressions, edit properties, and run
          model fixes.
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
          disabled={!modelData}
        >
          Expand All
        </Button>
        <Button
          appearance="primary"
          className={styles.loadCta}
          icon={<ArrowCollapseAll20Regular />}
          onClick={handleCollapseAll}
          disabled={!modelData}
        >
          Collapse All
        </Button>
        <Button
          appearance="primary"
          className={styles.loadCta}
          icon={scanning ? <Spinner size="tiny" /> : <Wrench20Regular />}
          onClick={handleScanFixers}
          disabled={!resolvedIds || scanning}
        >
          {scanning ? "Scanning" : "Scan model fixes"}
        </Button>
        {loading && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: GRAY_COLOR, fontSize: 12 }}>
            <Spinner size="tiny" /> Loading model...
          </span>
        )}
        {perspectives.length > 0 && (
          <Dropdown
            size="small"
            placeholder="All perspectives"
            value={perspectiveFilter || "All perspectives"}
            selectedOptions={perspectiveFilter ? [perspectiveFilter] : []}
            onOptionSelect={(_, d) => setPerspectiveFilter(d.optionValue ?? "")}
            style={{ minWidth: 180 }}
          >
            <Option key="__all" value="">All perspectives</Option>
            {perspectives.map((p) => (
              <Option key={p.name} value={p.name}>{p.name}</Option>
            ))}
          </Dropdown>
        )}
        {/* v0.69 — toolbar status pill is reserved for ERRORS only.
            Loading state is conveyed by the primary button's spinner + label;
            success/info messages would just clutter the toolbar. */}
        {status.msg && (status.color === "#ff3b30" || status.color === "#a4262c") && (
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
            {perspectiveFilteredOptions.map((option) => {
              const key = treeResult.keyMap[option];
              const isSelected = key === selectedKey;
              return (
                <div
                  key={option}
                  className={`${styles.treeItem} ${isSelected ? styles.treeItemSelected : ""}`}
                  onClick={() => handleSelect(option)}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    if (key) {
                      setSelectedKey(key);
                      setCtxMenu({ x: e.clientX, y: e.clientY, key });
                    }
                  }}
                >
                  {option}
                </div>
              );
            })}
            {perspectiveFilteredOptions.length === 0 && !loading && (
              <div style={{ padding: "20px", color: GRAY_COLOR, textAlign: "center", fontStyle: "italic" }}>
                {modelData ? "No matching items" : "Pick a workspace and semantic model to load"}
              </div>
            )}
          </div>
        </div>

        <div className={styles.rightPanel}>
          {showPreviewPanel && (
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
            {selectedMeasure && (
              <div style={{ marginBottom: 4 }}>
                <Tooltip content="Format DAX via daxformatter.com" relationship="label">
                  <Button
                    appearance="subtle"
                    size="small"
                    onClick={handleFormatDax}
                    disabled={formatting}
                    icon={formatting ? <Spinner size="tiny" /> : undefined}
                  >
                    Format DAX
                  </Button>
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
          )}

          <div className={styles.propertiesPanel}>
            <div className={styles.sectionLabel}>Properties</div>
            {propertiesContent ?? (
              <div style={{ padding: "12px", color: GRAY_COLOR, fontSize: "13px", fontStyle: "italic" }}>
                Select an object to view properties
              </div>
            )}
            {totalPendingEdits > 0 && (
              <div style={{ display: "flex", gap: "8px", marginTop: "12px", paddingTop: "8px", borderTop: `1px solid ${BORDER_COLOR}` }}>
                <Button
                  appearance="primary"
                  size="small"
                  onClick={handleSaveEdits}
                  disabled={saving || !resolvedIds}
                  icon={saving ? <Spinner size="tiny" /> : undefined}
                >
                  Save {totalPendingEdits} change{totalPendingEdits === 1 ? "" : "s"}
                </Button>
                <Button appearance="secondary" size="small" onClick={handleDiscardEdits} disabled={saving}>
                  Discard
                </Button>
              </div>
            )}
            {/* v0.73 — Quick fixer scan results. Surfaces only fixers
                that reported findings; "clean" fixers are hidden so the
                panel acts like a to-do list of remaining issues. */}
            {scanRanOnce && (
              <div style={{ marginTop: "16px", paddingTop: "8px", borderTop: `1px solid ${BORDER_COLOR}` }}>
                <div className={styles.sectionLabel} style={{ marginBottom: 6 }}>
                  Quick fixes {scanRanOnce && `— ${fixersWithFindings.length} issue type(s)`}
                </div>
                {fixersWithFindings.length === 0 ? (
                  <div style={{ fontSize: 12, color: GRAY_COLOR, fontStyle: "italic" }}>
                    No model issues found.
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {fixersWithFindings.map((fx) => {
                      const r = scanResults[fx.id];
                      const count = r?.findings.length ?? 0;
                      const busy = applyingFixerId === fx.id;
                      return (
                        <div key={fx.id} style={{ display: "flex", flexDirection: "column", gap: 2, padding: "6px 8px", background: SECTION_BG, borderRadius: 4 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{fx.title}</span>
                            <span style={{ fontSize: 11, color: GRAY_COLOR }}>{count} finding{count === 1 ? "" : "s"}</span>
                            <Button
                              appearance="primary"
                              size="small"
                              disabled={busy || applyingFixerId !== null}
                              icon={busy ? <Spinner size="tiny" /> : undefined}
                              onClick={() => void handleApplyFixer(fx)}
                            >
                              Apply
                            </Button>
                          </div>
                          {r && r.findings.length > 0 && (
                            <div style={{ fontSize: 11, color: GRAY_COLOR, fontFamily: "monospace", maxHeight: 110, overflow: "auto" }}>
                              {r.findings.slice(0, 5).map((f, i) => (
                                <div key={i}>• {f.objectPath}{f.detail ? ` — ${f.detail}` : ""}</div>
                              ))}
                              {r.findings.length > 5 && (
                                <div style={{ fontStyle: "italic" }}>… +{r.findings.length - 5} more</div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
      {ctxMenu && (
        <div
          style={{ position: "fixed", inset: 0, zIndex: 1000 }}
          onClick={() => setCtxMenu(null)}
          onContextMenu={(e) => { e.preventDefault(); setCtxMenu(null); }}
        >
          <div
            style={{
              position: "absolute",
              left: ctxMenu.x,
              top: ctxMenu.y,
              background: tokens.colorNeutralBackground1,
              border: `1px solid ${BORDER_COLOR}`,
              borderRadius: 4,
              boxShadow: tokens.shadow8,
              minWidth: 200,
              padding: 4,
              fontSize: 13,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <CtxItem
              label="Copy DAX reference"
              onClick={() => {
                const ref = getDaxReference(ctxMenu.key);
                if (ref) navigator.clipboard.writeText(ref);
                setCtxMenu(null);
              }}
            />
            <CtxItem
              label="Copy node key"
              onClick={() => {
                navigator.clipboard.writeText(ctxMenu.key);
                setCtxMenu(null);
              }}
            />
            {ctxMenu.key.startsWith("table:") && (
              <CtxItem
                label="Preview data (TOPN 100)"
                onClick={() => {
                  handleLoadTablePreview(ctxMenu.key.split(":")[1]);
                  setCtxMenu(null);
                }}
              />
            )}
            {ctxMenu.key.startsWith("measure:") && (
              <CtxItem
                label="Copy expression"
                onClick={() => {
                  const p = ctxMenu.key.split(":");
                  const expr = modelData?.tables[p[1]]?.measures[p[2]]?.expression ?? "";
                  if (expr) navigator.clipboard.writeText(expr);
                  setCtxMenu(null);
                }}
              />
            )}
          </div>
        </div>
      )}
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

const CtxItem: React.FC<{ label: string; onClick: () => void }> = ({ label, onClick }) => (
  <div
    role="menuitem"
    style={{
      padding: "6px 10px",
      cursor: "pointer",
      borderRadius: 3,
      userSelect: "none",
    }}
    onMouseEnter={(e) => (e.currentTarget.style.background = tokens.colorNeutralBackground1Hover)}
    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    onClick={onClick}
  >
    {label}
  </div>
);
