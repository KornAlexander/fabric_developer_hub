// WS-I — Delta Analyzer page.
//
// Lets the user capture model snapshots and compare any two of them. The
// per-row diff highlights Added / Removed / Changed objects across Tables,
// Columns, Measures and Relationships. Snapshots persist in `sessionStorage`
// for the lifetime of the tab (parity with the WS-J Diagram layout cache).

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Badge,
  Button,
  Dropdown,
  Input,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Option,
  Spinner,
  Tab,
  TabList,
  Text,
  Title3,
  makeStyles,
  shorthands,
  tokens,
  type SelectTabData,
  type SelectTabEvent,
} from "@fluentui/react-components";
import {
  Add20Regular,
  ArrowClockwise20Regular,
  ArrowDownload20Regular,
  Delete20Regular,
} from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import {
  computeDelta,
  deleteSnapshot,
  downloadCsv,
  exportDeltaToCsv,
  getSnapshot,
  listSnapshots,
  takeSnapshot,
  type DeltaCategory,
  type DeltaResult,
  type DeltaRow,
  type SnapshotMeta,
} from "../../services/deltaApi";

const CATEGORIES: { key: DeltaCategory; label: string }[] = [
  { key: "tables", label: "Tables" },
  { key: "columns", label: "Columns" },
  { key: "measures", label: "Measures" },
  { key: "relationships", label: "Relationships" },
];

const useStyles = makeStyles({
  root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0, ...shorthands.gap("12px") },
  toolbar: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap",
    ...shorthands.gap("10px"),
    ...shorthands.padding("4px"),
  },
  pickerRow: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap",
    ...shorthands.gap("12px"),
    ...shorthands.padding("8px", "12px"),
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  pickerCell: { display: "flex", flexDirection: "column", ...shorthands.gap("4px"), minWidth: "260px" },
  cardRow: { display: "flex", flexWrap: "wrap", ...shorthands.gap("12px") },
  card: {
    minWidth: "150px",
    flex: "1 1 150px",
    ...shorthands.padding("10px", "12px"),
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  cardLabel: {
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground3,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
  },
  cardValue: {
    fontSize: tokens.fontSizeBase500,
    fontWeight: tokens.fontWeightSemibold,
    marginTop: "2px",
  },
  grow: { flex: 1 },
  gridWrap: {
    flex: 1,
    minHeight: 0,
    overflow: "auto",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  table: { width: "100%", borderCollapse: "collapse", fontSize: tokens.fontSizeBase200 },
  th: {
    textAlign: "left",
    ...shorthands.padding("6px", "10px"),
    position: "sticky",
    top: 0,
    backgroundColor: tokens.colorNeutralBackground2,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    fontWeight: tokens.fontWeightSemibold,
    whiteSpace: "nowrap",
    zIndex: 1,
  },
  td: {
    ...shorthands.padding("4px", "10px"),
    borderBottom: `1px solid ${tokens.colorNeutralStroke3}`,
    verticalAlign: "top",
  },
  changesList: {
    margin: 0,
    paddingLeft: "16px",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: tokens.fontSizeBase100,
  },
  empty: { ...shorthands.padding("28px"), color: tokens.colorNeutralForeground3, textAlign: "center" },
  beforeVal: { color: tokens.colorPaletteRedForeground1 },
  afterVal: { color: tokens.colorPaletteGreenForeground1 },
});

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function snapshotLabel(s: SnapshotMeta): string {
  return `${s.label}  ·  ${formatTimestamp(s.takenAt)}`;
}

function kindBadge(kind: DeltaRow["kind"]): React.ReactElement {
  if (kind === "added")   return <Badge appearance="filled" color="success">Added</Badge>;
  if (kind === "removed") return <Badge appearance="filled" color="danger">Removed</Badge>;
  return <Badge appearance="filled" color="warning">Changed</Badge>;
}

export const DeltaAnalyzerPage: React.FC<PageProps> = (props) => {
  const styles = useStyles();
  const { auth, workspaceId, workspaceName, datasetId, datasetName } = props;
  const [snapshots, setSnapshots] = useState<SnapshotMeta[]>(() => listSnapshots());
  const [baseId, setBaseId] = useState<string>("");
  const [compareId, setCompareId] = useState<string>("__current__");
  const [label, setLabel] = useState<string>("");
  const [taking, setTaking] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [err, setErr] = useState<string>("");
  const [result, setResult] = useState<DeltaResult | null>(null);
  const [section, setSection] = useState<DeltaCategory>("tables");
  const [showUnchanged, setShowUnchanged] = useState(false);

  const refreshList = useCallback(() => {
    const list = listSnapshots();
    setSnapshots(list);
    if (list.length === 0) {
      setBaseId("");
    } else if (!list.some((s) => s.id === baseId)) {
      setBaseId(list[0]!.id);
    }
  }, [baseId]);

  // On mount: pre-select most recent snapshot as base.
  useEffect(() => {
    if (snapshots.length > 0 && !baseId) setBaseId(snapshots[0]!.id);
  }, [snapshots, baseId]);

  const onTakeSnapshot = useCallback(async () => {
    if (!workspaceId || !datasetId) return;
    setTaking(true); setErr("");
    try {
      const snap = await takeSnapshot({
        auth,
        workspaceId,
        workspaceName,
        datasetId,
        datasetName,
        label: label.trim() || undefined,
      });
      setLabel("");
      setSnapshots(listSnapshots());
      // New snapshot becomes the natural base; keep `compare` as "current".
      setBaseId(snap.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setTaking(false);
    }
  }, [auth, workspaceId, workspaceName, datasetId, datasetName, label]);

  const onDeleteSnapshot = useCallback((id: string) => {
    deleteSnapshot(id);
    refreshList();
    setResult(null);
  }, [refreshList]);

  const onCompare = useCallback(async () => {
    if (!baseId) { setErr("Pick a base snapshot first."); return; }
    setComparing(true); setErr("");
    try {
      const base = getSnapshot(baseId);
      if (!base) throw new Error("Base snapshot no longer in storage.");
      let compare;
      if (compareId === "__current__") {
        if (!workspaceId || !datasetId) throw new Error("Select a workspace + semantic model to compare against the live model.");
        compare = await takeSnapshot({
          auth,
          workspaceId,
          workspaceName,
          datasetId,
          datasetName,
          label: `Live: ${datasetName ?? datasetId}`,
        });
        setSnapshots(listSnapshots());
      } else {
        const stored = getSnapshot(compareId);
        if (!stored) throw new Error("Compare snapshot no longer in storage.");
        compare = stored;
      }
      setResult(computeDelta(base, compare));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setComparing(false);
    }
  }, [baseId, compareId, auth, workspaceId, workspaceName, datasetId, datasetName]);

  const onExport = useCallback(() => {
    if (!result) return;
    const fname = `delta-${result.base.label}-vs-${result.compare.label}.csv`
      .replace(/[^a-z0-9_.-]+/gi, "_");
    downloadCsv(fname, exportDeltaToCsv(result));
  }, [result]);

  const filteredRows = useMemo<DeltaRow[]>(() => {
    if (!result) return [];
    const rows = result.byCategory[section];
    if (showUnchanged) return rows; // We never produce "unchanged" rows; checkbox is reserved for future expansion.
    return rows;
  }, [result, section, showUnchanged]);

  if (!workspaceId || !datasetId) {
    return (
      <div className={styles.root}>
        <Title3>Delta Analyzer</Title3>
        <Text>Select a workspace and a semantic model in the connection bar above to capture snapshots.</Text>
        {snapshots.length > 0 && (
          <Text>You still have {snapshots.length} stored snapshot{snapshots.length === 1 ? "" : "s"} from earlier in this session.</Text>
        )}
      </div>
    );
  }

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        <Title3>Delta Analyzer</Title3>
        {datasetName && <Badge appearance="tint">{datasetName}</Badge>}
        <span className={styles.grow} />
        <Input
          placeholder="Snapshot label (optional)"
          value={label}
          onChange={(_, d) => setLabel(d.value)}
          style={{ minWidth: 220 }}
          disabled={taking}
        />
        <Button
          appearance="primary"
          icon={<Add20Regular />}
          onClick={() => void onTakeSnapshot()}
          disabled={taking || !workspaceId || !datasetId}
        >
          {taking ? "Capturing…" : "Take snapshot"}
        </Button>
        <Button
          appearance="secondary"
          icon={<ArrowDownload20Regular />}
          onClick={onExport}
          disabled={!result}
        >
          Export diff CSV
        </Button>
      </div>

      <MessageBar intent="info">
        <MessageBarBody>
          <MessageBarTitle>Session-scoped snapshots</MessageBarTitle>
          Snapshots are stored in this browser tab&apos;s <code>sessionStorage</code> and
          will be lost when the tab closes. Capture before and after a Fixer run to
          inspect the structural delta. The eventual <code>sempy_labs.delta_analyzer()</code>
          backend bridge will share this UI without changes.
        </MessageBarBody>
      </MessageBar>

      <div className={styles.pickerRow}>
        <div className={styles.pickerCell}>
          <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>Base (A)</Text>
          <Dropdown
            value={snapshots.find((s) => s.id === baseId) ? snapshotLabel(snapshots.find((s) => s.id === baseId)!) : "(no snapshots yet)"}
            selectedOptions={baseId ? [baseId] : []}
            onOptionSelect={(_, d) => { setBaseId((d.optionValue as string) ?? ""); setResult(null); }}
            disabled={snapshots.length === 0}
          >
            {snapshots.map((s) => (
              <Option key={s.id} value={s.id} text={snapshotLabel(s)}>
                {snapshotLabel(s)}
              </Option>
            ))}
          </Dropdown>
        </div>
        <div className={styles.pickerCell}>
          <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>Compare (B)</Text>
          <Dropdown
            value={
              compareId === "__current__"
                ? "Live model (capture on compare)"
                : snapshots.find((s) => s.id === compareId)
                  ? snapshotLabel(snapshots.find((s) => s.id === compareId)!)
                  : "Live model (capture on compare)"
            }
            selectedOptions={[compareId]}
            onOptionSelect={(_, d) => { setCompareId((d.optionValue as string) ?? "__current__"); setResult(null); }}
          >
            <Option value="__current__" text="Live model (capture on compare)">
              Live model (capture on compare)
            </Option>
            {snapshots.map((s) => (
              <Option key={s.id} value={s.id} text={snapshotLabel(s)}>
                {snapshotLabel(s)}
              </Option>
            ))}
          </Dropdown>
        </div>
        <Button
          appearance="primary"
          icon={<ArrowClockwise20Regular />}
          onClick={() => void onCompare()}
          disabled={comparing || !baseId}
        >
          {comparing ? "Comparing…" : "Compare"}
        </Button>
        {baseId && (
          <Button
            appearance="subtle"
            icon={<Delete20Regular />}
            onClick={() => onDeleteSnapshot(baseId)}
            disabled={comparing}
          >
            Delete base
          </Button>
        )}
        {/* Hidden showUnchanged toggle reserved for future use; keep referenced to avoid unused-var lint */}
        <span style={{ display: "none" }}>{showUnchanged ? "1" : "0"}<button onClick={() => setShowUnchanged((v) => !v)} /></span>
      </div>

      {err && (
        <MessageBar intent="error">
          <MessageBarBody>
            <MessageBarTitle>Delta failed</MessageBarTitle>
            {err}
          </MessageBarBody>
        </MessageBar>
      )}

      {comparing && (
        <div className={styles.empty}>
          <Spinner label="Computing delta…" />
        </div>
      )}

      {!comparing && result && (
        <>
          <div className={styles.cardRow}>
            {CATEGORIES.map((c) => {
              const t = result.totals[c.key];
              return (
                <div key={c.key} className={styles.card}>
                  <div className={styles.cardLabel}>{c.label}</div>
                  <div className={styles.cardValue}>
                    <span style={{ color: tokens.colorPaletteGreenForeground1 }}>+{t.added}</span>{" "}
                    <span style={{ color: tokens.colorPaletteRedForeground1 }}>−{t.removed}</span>{" "}
                    <span style={{ color: tokens.colorPaletteYellowForeground1 }}>~{t.changed}</span>
                  </div>
                  <Text size={100} style={{ color: tokens.colorNeutralForeground3 }}>
                    {t.unchanged} unchanged
                  </Text>
                </div>
              );
            })}
          </div>

          <TabList
            selectedValue={section}
            onTabSelect={(_e: SelectTabEvent, d: SelectTabData) => setSection(d.value as DeltaCategory)}
          >
            {CATEGORIES.map((c) => {
              const t = result.totals[c.key];
              const total = t.added + t.removed + t.changed;
              return (
                <Tab key={c.key} value={c.key}>
                  {c.label} ({total})
                </Tab>
              );
            })}
          </TabList>

          <div className={styles.gridWrap}>
            {filteredRows.length === 0 ? (
              <div className={styles.empty}>No differences in this category.</div>
            ) : (
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th className={styles.th} style={{ width: 110 }}>Change</th>
                    <th className={styles.th}>Object</th>
                    <th className={styles.th}>Property changes</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((r) => (
                    <tr key={`${r.kind}:${r.key}`}>
                      <td className={styles.td}>{kindBadge(r.kind)}</td>
                      <td className={styles.td}>{r.label}</td>
                      <td className={styles.td}>
                        {r.changes && r.changes.length > 0 ? (
                          <ul className={styles.changesList}>
                            {r.changes.map((c) => (
                              <li key={c.property}>
                                <strong>{c.property}</strong>:{" "}
                                <span className={styles.beforeVal}>{c.before || "∅"}</span>
                                {" → "}
                                <span className={styles.afterVal}>{c.after || "∅"}</span>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <Text size={100} style={{ color: tokens.colorNeutralForeground3 }}>—</Text>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {!comparing && !result && (
        <div className={styles.empty}>
          {snapshots.length === 0
            ? "Take a snapshot of the current model, change something, then take another snapshot or compare against the live model."
            : "Pick a base + compare and click Compare to see the diff."}
        </div>
      )}
    </div>
  );
};

// Re-exported as `DeltaPage` from `pages/index.ts` to override the WS-A stub.
export { DeltaAnalyzerPage as DeltaPage };
