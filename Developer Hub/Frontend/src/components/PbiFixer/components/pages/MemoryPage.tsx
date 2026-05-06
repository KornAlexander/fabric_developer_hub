// WS-B — Memory Analyzer page (a.k.a. Vertipaq Analyzer).
//
// Phase 2 implementation: structural metadata only. The Power BI
// `executeQueries` REST API permits the friendly `INFO.VIEW.*` family of
// DAX queries but rejects the raw `INFO.STORAGETABLE*`, `INFO.PARTITIONS()`,
// `INFO.HIERARCHIES()` functions with `DatasetExecuteQueriesError` /
// `AnalysisServicesErrorCode 3239575574`. Per-column dictionary/data/segment
// sizes therefore require the Phase 1 backend bridge to
// `sempy_labs.vertipaq_analyzer()` over the XMLA endpoint. The page surfaces
// this clearly with a banner so the limitation is obvious.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Button,
  Input,
  Spinner,
  Title3,
  Text,
  Badge,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  TabList,
  Tab,
  type SelectTabData,
  type SelectTabEvent,
  makeStyles,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import {
  ArrowClockwise20Regular,
  ArrowDownload20Regular,
  ArrowSortDown20Regular,
  ArrowSortUp20Regular,
} from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import {
  loadVertipaqData,
  exportSectionToCsv,
  downloadCsv,
  formatNumber,
  type VertipaqData,
  type VertipaqTableRow,
  type VertipaqColumnRow,
  type VertipaqMeasureRow,
  type VertipaqRelationshipRow,
} from "../../services/memoryApi";

type SectionKey = "summary" | "tables" | "columns" | "measures" | "relationships";

const useStyles = makeStyles({
  root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0, ...shorthands.gap("12px") },
  toolbar: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("10px"),
    flexWrap: "wrap",
    ...shorthands.padding("4px"),
  },
  grow: { flex: 1 },
  cardRow: { display: "flex", flexWrap: "wrap", ...shorthands.gap("12px") },
  card: {
    minWidth: "180px",
    flex: "1 1 180px",
    ...shorthands.padding("12px", "14px"),
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
    fontSize: tokens.fontSizeBase600,
    fontWeight: tokens.fontWeightSemibold,
    marginTop: "2px",
  },
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
    cursor: "pointer",
    userSelect: "none",
  },
  td: {
    ...shorthands.padding("4px", "10px"),
    borderBottom: `1px solid ${tokens.colorNeutralStroke3}`,
    whiteSpace: "nowrap",
    maxWidth: "420px",
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  tdNum: {
    ...shorthands.padding("4px", "10px"),
    borderBottom: `1px solid ${tokens.colorNeutralStroke3}`,
    whiteSpace: "nowrap",
    textAlign: "right",
    fontVariantNumeric: "tabular-nums",
  },
  empty: {
    ...shorthands.padding("28px"),
    color: tokens.colorNeutralForeground3,
    textAlign: "center",
  },
});

type SortDir = "asc" | "desc";
interface SortState { key: string; dir: SortDir }

function compareVals(a: unknown, b: unknown): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a ?? "").localeCompare(String(b ?? ""));
}

function applySort<T extends Record<string, unknown>>(rows: T[], sort: SortState | null): T[] {
  if (!sort) return rows;
  const sorted = [...rows].sort((a, b) => compareVals(a[sort.key], b[sort.key]));
  return sort.dir === "desc" ? sorted.reverse() : sorted;
}

interface ColumnDef<T> {
  key: keyof T & string;
  header: string;
  render?: (row: T) => React.ReactNode;
  numeric?: boolean;
}

function SortableTable<T extends Record<string, unknown>>(props: {
  rows: T[];
  columns: ColumnDef<T>[];
  emptyText: string;
  defaultSort?: SortState;
  filterText?: string;
  filterKeys?: (keyof T & string)[];
}): React.ReactElement {
  const styles = useStyles();
  const [sort, setSort] = useState<SortState | null>(props.defaultSort ?? null);

  const filtered = useMemo(() => {
    if (!props.filterText) return props.rows;
    const q = props.filterText.toLowerCase();
    const keys = props.filterKeys ?? props.columns.map((c) => c.key);
    return props.rows.filter((r) =>
      keys.some((k) => String(r[k] ?? "").toLowerCase().includes(q))
    );
  }, [props.rows, props.filterText, props.filterKeys, props.columns]);

  const sorted = useMemo(() => applySort(filtered, sort), [filtered, sort]);

  const onHeaderClick = (key: string): void => {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "desc" };
      return { key, dir: prev.dir === "desc" ? "asc" : "desc" };
    });
  };

  if (sorted.length === 0) {
    return <div className={styles.empty}>{props.emptyText}</div>;
  }

  return (
    <table className={styles.table}>
      <thead>
        <tr>
          {props.columns.map((c) => (
            <th key={c.key} className={styles.th} onClick={() => onHeaderClick(c.key)}>
              {c.header}
              {sort?.key === c.key && (
                <span style={{ marginLeft: 4, opacity: 0.6 }}>
                  {sort.dir === "desc" ? <ArrowSortDown20Regular /> : <ArrowSortUp20Regular />}
                </span>
              )}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((row, i) => (
          <tr key={i}>
            {props.columns.map((c) => (
              <td key={c.key} className={c.numeric ? styles.tdNum : styles.td} title={String(row[c.key] ?? "")}>
                {c.render ? c.render(row) : String(row[c.key] ?? "")}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export const MemoryPage: React.FC<PageProps> = ({ auth, workspaceId, datasetId, datasetName }) => {
  const styles = useStyles();
  const [data, setData] = useState<VertipaqData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [section, setSection] = useState<SectionKey>("summary");
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    if (!workspaceId || !datasetId) return;
    setLoading(true); setErr("");
    try {
      const d = await loadVertipaqData(auth, workspaceId, datasetId);
      setData(d);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth, workspaceId, datasetId]);

  useEffect(() => { void load(); }, [load]);

  const onExport = useCallback(() => {
    if (!data) return;
    if (section === "summary") return;
    const csv = exportSectionToCsv(section, data);
    const ds = (datasetName || datasetId || "model").replace(/[^a-z0-9_-]+/gi, "_");
    downloadCsv(`memory-${ds}-${section}.csv`, csv);
  }, [data, section, datasetName, datasetId]);

  if (!workspaceId || !datasetId) {
    return (
      <div className={styles.root}>
        <Title3>Memory</Title3>
        <Text>Select a workspace and a semantic model in the connection bar above to begin.</Text>
      </div>
    );
  }

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        <Title3>Memory Analyzer</Title3>
        {datasetName && <Badge appearance="tint">{datasetName}</Badge>}
        <span className={styles.grow} />
        <Input
          placeholder="Filter…"
          value={filter}
          onChange={(_, d) => setFilter(d.value)}
          style={{ minWidth: 200 }}
          disabled={loading || !data || section === "summary"}
        />
        <Button
          appearance="secondary"
          icon={<ArrowDownload20Regular />}
          onClick={onExport}
          disabled={!data || section === "summary"}
        >
          Export CSV
        </Button>
        <Button
          appearance="primary"
          icon={<ArrowClockwise20Regular />}
          onClick={() => void load()}
          disabled={loading}
        >
          Refresh
        </Button>
      </div>

      <MessageBar intent="info">
        <MessageBarBody>
          <MessageBarTitle>Storage breakdown coming with the backend bridge</MessageBarTitle>
          Per-column dictionary/data/segment sizes require the
          {" "}<code>sempy_labs.vertipaq_analyzer()</code>{" "}
          XMLA bridge (Phase 1, planned). Phase 2 (this view) ships row counts
          and structural metadata via the friendly{" "}<code>INFO.VIEW.*</code>{" "}
          DAX functions that the Power BI REST API permits.
        </MessageBarBody>
      </MessageBar>

      {err && (
        <MessageBar intent="error">
          <MessageBarBody>
            <MessageBarTitle>Failed to load Memory data</MessageBarTitle>
            {err}
          </MessageBarBody>
        </MessageBar>
      )}

      <TabList
        selectedValue={section}
        onTabSelect={(_e: SelectTabEvent, d: SelectTabData) => setSection(d.value as SectionKey)}
      >
        <Tab value="summary">Summary</Tab>
        <Tab value="tables">Tables ({data?.tables.length ?? 0})</Tab>
        <Tab value="columns">Columns ({data?.columns.length ?? 0})</Tab>
        <Tab value="measures">Measures ({data?.measures.length ?? 0})</Tab>
        <Tab value="relationships">Relationships ({data?.relationships.length ?? 0})</Tab>
      </TabList>

      {loading && (
        <div className={styles.empty}>
          <Spinner label="Loading model metadata…" />
        </div>
      )}

      {!loading && data && section === "summary" && (
        <div className={styles.cardRow}>
          <Card label="Total rows" value={formatNumber(data.summary.totalRowCount)} />
          <Card label="Tables" value={String(data.summary.tableCount)} />
          <Card label="Columns" value={String(data.summary.columnCount)} />
          <Card label="Measures" value={String(data.summary.measureCount)} />
          <Card label="Relationships" value={String(data.summary.relationshipCount)} />
        </div>
      )}

      {!loading && data && section === "tables" && (
        <div className={styles.gridWrap}>
          <SortableTable<VertipaqTableRow>
            rows={data.tables}
            filterText={filter}
            filterKeys={["table", "mode"]}
            defaultSort={{ key: "rows", dir: "desc" }}
            emptyText="No tables returned by INFO.VIEW.TABLES()."
            columns={[
              { key: "table", header: "Table" },
              { key: "rows", header: "Rows", numeric: true, render: (r) => formatNumber(r.rows) },
              { key: "columns", header: "Columns", numeric: true, render: (r) => String(r.columns) },
              { key: "mode", header: "Mode" },
              { key: "isHidden", header: "Hidden", render: (r) => (r.isHidden ? "✓" : "—") },
              { key: "modified", header: "Modified" },
            ]}
          />
        </div>
      )}

      {!loading && data && section === "columns" && (
        <div className={styles.gridWrap}>
          <SortableTable<VertipaqColumnRow>
            rows={data.columns}
            filterText={filter}
            filterKeys={["table", "column", "dataType", "folder"]}
            defaultSort={{ key: "table", dir: "asc" }}
            emptyText="No columns returned by INFO.VIEW.COLUMNS()."
            columns={[
              { key: "table", header: "Table" },
              { key: "column", header: "Column" },
              { key: "dataType", header: "Data Type" },
              { key: "isHidden", header: "Hidden", render: (r) => (r.isHidden ? "✓" : "—") },
              { key: "isKey", header: "Key", render: (r) => (r.isKey ? "✓" : "—") },
              { key: "folder", header: "Folder" },
              { key: "formatString", header: "Format" },
            ]}
          />
        </div>
      )}

      {!loading && data && section === "measures" && (
        <div className={styles.gridWrap}>
          <SortableTable<VertipaqMeasureRow>
            rows={data.measures}
            filterText={filter}
            filterKeys={["table", "measure", "folder", "expression"]}
            defaultSort={{ key: "table", dir: "asc" }}
            emptyText="No measures returned by INFO.VIEW.MEASURES()."
            columns={[
              { key: "table", header: "Table" },
              { key: "measure", header: "Measure" },
              { key: "dataType", header: "Data Type" },
              { key: "formatString", header: "Format" },
              { key: "folder", header: "Folder" },
              { key: "isHidden", header: "Hidden", render: (r) => (r.isHidden ? "✓" : "—") },
              { key: "expression", header: "Expression" },
            ]}
          />
        </div>
      )}

      {!loading && data && section === "relationships" && (
        <div className={styles.gridWrap}>
          <SortableTable<VertipaqRelationshipRow>
            rows={data.relationships}
            filterText={filter}
            filterKeys={["fromTable", "fromColumn", "toTable", "toColumn"]}
            defaultSort={{ key: "fromTable", dir: "asc" }}
            emptyText="No relationships returned by INFO.VIEW.RELATIONSHIPS()."
            columns={[
              { key: "fromTable", header: "From Table" },
              { key: "fromColumn", header: "From Column" },
              { key: "toTable", header: "To Table" },
              { key: "toColumn", header: "To Column" },
              { key: "cardinality", header: "Cardinality" },
              { key: "isActive", header: "Active", render: (r) => (r.isActive ? "✓" : "—") },
              { key: "crossFilter", header: "Cross Filter" },
            ]}
          />
        </div>
      )}
    </div>
  );
};

const Card: React.FC<{ label: string; value: string }> = ({ label, value }) => {
  const styles = useStyles();
  return (
    <div className={styles.card}>
      <div className={styles.cardLabel}>{label}</div>
      <div className={styles.cardValue}>{value}</div>
    </div>
  );
};
