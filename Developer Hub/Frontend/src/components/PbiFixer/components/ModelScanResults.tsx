// ModelScanResults — renders the unified "Scan Model" output panel
// shown below the Model Explorer tree+properties layout.
//
// Holds three cards in this order:
//   1. Quick fixes  (model fixer scan-only results)
//   2. Model BPA    (rule grid with severity/category filters + Fix it relay)
//   3. Memory Analyzer (Vertipaq Analyzer summary + tables/columns/...)
//
// Data is precomputed by ModelExplorer.handleScanModel and pushed in via
// props — this component is purely presentational. The Memory tab
// supports a manual rerun via onReloadVertipaq for the case where the
// initial scan failed (REST permission errors, transient XMLA hiccups).

import React, { useCallback, useMemo, useState } from "react";
import {
  Button,
  Combobox,
  Option,
  Field,
  Input,
  Spinner,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Text,
  Badge,
  TabList,
  Tab,
  type SelectTabData,
  type SelectTabEvent,
  makeStyles,
  mergeClasses,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import {
  ArrowDownload20Regular,
  ArrowClockwise20Regular,
  ArrowSortDown20Regular,
  ArrowSortUp20Regular,
  Wrench20Regular,
} from "@fluentui/react-icons";
import {
  BPA_RULES,
  type BpaFinding,
  type BpaSeverity,
} from "../services/modelBpaApi";
import {
  formatBytes,
  formatNumber,
  formatPct,
  type VertipaqAnalyzerResult,
  type VertipaqTableRow,
  type VertipaqColumnRow,
  type VertipaqHierarchyRow,
  type VertipaqRelationshipRow,
  type VertipaqPartitionRow,
} from "../services/vertipaqApi";
import { BORDER_COLOR, GRAY_COLOR, SECTION_BG } from "../utils";
import type { Fixer, FixerResult } from "../fixers";

// ---------------------------------------------------------------------------
// Styles — shared across the three cards.
// ---------------------------------------------------------------------------

const useStyles = makeStyles({
  section: {
    display: "flex",
    flexDirection: "column",
    ...shorthands.gap("16px"),
    marginTop: "16px",
  },
  sectionHeader: {
    fontSize: "20px",
    fontWeight: 700,
    color: tokens.colorNeutralForeground1,
    marginBottom: "4px",
  },
  card: {
    ...shorthands.border("1px", "solid", BORDER_COLOR),
    ...shorthands.borderRadius("8px"),
    backgroundColor: "#ffffff",
    boxShadow: "0 1px 2px rgba(0, 0, 0, 0.04)",
    ...shorthands.padding("12px", "16px"),
    display: "flex",
    flexDirection: "column",
    ...shorthands.gap("10px"),
  },
  cardTitleRow: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("8px"),
  },
  cardTitle: {
    fontSize: "16px",
    fontWeight: 600,
    color: tokens.colorNeutralForeground1,
  },
  toolbar: {
    display: "flex",
    alignItems: "flex-end",
    ...shorthands.gap("12px"),
    flexWrap: "wrap",
  },
  grow: { flex: 1 },
  actions: { display: "flex", alignItems: "center", ...shorthands.gap("8px"), marginLeft: "auto" },
  gridWrap: {
    maxHeight: "420px",
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
    cursor: "pointer",
    userSelect: "none",
    zIndex: 1,
    whiteSpace: "nowrap",
  },
  td: {
    ...shorthands.padding("6px", "10px"),
    borderBottom: `1px solid ${tokens.colorNeutralStroke3}`,
    verticalAlign: "top",
  },
  tdNum: {
    ...shorthands.padding("4px", "10px"),
    borderBottom: `1px solid ${tokens.colorNeutralStroke3}`,
    whiteSpace: "nowrap",
    textAlign: "right",
    fontVariantNumeric: "tabular-nums",
  },
  sev: {
    display: "inline-flex",
    alignItems: "center",
    ...shorthands.padding("2px", "8px"),
    borderRadius: tokens.borderRadiusMedium,
    fontSize: "11px",
    fontWeight: tokens.fontWeightSemibold,
  },
  sevError: { backgroundColor: "rgba(164, 38, 44, 0.12)", color: "#a4262c" },
  sevWarning: { backgroundColor: "rgba(180, 100, 30, 0.15)", color: "#8a4500" },
  sevInfo: { backgroundColor: "rgba(0, 95, 170, 0.10)", color: "#004c87" },
  stats: {
    display: "flex",
    ...shorthands.gap("12px"),
    alignItems: "center",
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
    flexWrap: "wrap",
  },
  empty: {
    ...shorthands.padding("28px"),
    color: tokens.colorNeutralForeground3,
    textAlign: "center",
    fontStyle: "italic",
    fontSize: "13px",
  },
  cardRow: { display: "flex", flexWrap: "wrap", ...shorthands.gap("12px") },
  summaryCard: {
    minWidth: "160px",
    flex: "1 1 160px",
    ...shorthands.padding("10px", "12px"),
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  summaryLabel: {
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground3,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
  },
  summaryValue: {
    fontSize: tokens.fontSizeBase500,
    fontWeight: tokens.fontWeightSemibold,
    marginTop: "2px",
  },
  pctCell: {
    ...shorthands.padding("4px", "10px"),
    borderBottom: `1px solid ${tokens.colorNeutralStroke3}`,
    minWidth: "120px",
  },
  pctBarTrack: {
    position: "relative",
    width: "100%",
    height: "14px",
    backgroundColor: tokens.colorNeutralBackground3,
    borderRadius: tokens.borderRadiusSmall,
    overflow: "hidden",
  },
  pctBarFill: {
    position: "absolute",
    inset: 0,
    backgroundColor: tokens.colorBrandBackground,
    opacity: 0.7,
  },
  pctBarLabel: {
    position: "absolute",
    inset: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "10px",
    fontVariantNumeric: "tabular-nums",
    color: tokens.colorNeutralForeground1,
  },
});

const SEVERITY_ORDER: BpaSeverity[] = ["Error", "Warning", "Info"];

// ---------------------------------------------------------------------------
// Card 1 — Quick fixes
// ---------------------------------------------------------------------------

interface QuickFixesCardProps {
  fixersWithFindings: Fixer[];
  scanResults: Record<string, FixerResult>;
  applyingFixerId: string | null;
  onApplyFixer: (fx: Fixer) => void;
}

const QuickFixesCard: React.FC<QuickFixesCardProps> = ({
  fixersWithFindings,
  scanResults,
  applyingFixerId,
  onApplyFixer,
}) => {
  const styles = useStyles();
  return (
    <div className={styles.card}>
      <div className={styles.cardTitleRow}>
        <span className={styles.cardTitle}>
          Quick fixes — {fixersWithFindings.length} issue type(s)
        </span>
      </div>
      {fixersWithFindings.length === 0 ? (
        <div className={styles.empty}>No model issues found.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {fixersWithFindings.map((fx) => {
            const r = scanResults[fx.id];
            const count = r?.findings.length ?? 0;
            const busy = applyingFixerId === fx.id;
            return (
              <div
                key={fx.id}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  padding: "6px 8px",
                  background: SECTION_BG,
                  borderRadius: 4,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{fx.title}</span>
                  <span style={{ fontSize: 11, color: GRAY_COLOR }}>
                    {count} finding{count === 1 ? "" : "s"}
                  </span>
                  <Button
                    appearance="primary"
                    size="small"
                    disabled={busy || applyingFixerId !== null}
                    icon={busy ? <Spinner size="tiny" /> : undefined}
                    onClick={() => onApplyFixer(fx)}
                  >
                    Apply
                  </Button>
                </div>
                {r && r.findings.length > 0 && (
                  <div
                    style={{
                      fontSize: 11,
                      color: GRAY_COLOR,
                      fontFamily: "monospace",
                      maxHeight: 110,
                      overflow: "auto",
                    }}
                  >
                    {r.findings.slice(0, 5).map((f, i) => (
                      <div key={i}>
                        • {f.objectPath}
                        {f.detail ? ` — ${f.detail}` : ""}
                      </div>
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
  );
};

// ---------------------------------------------------------------------------
// Card 2 — Model BPA
// ---------------------------------------------------------------------------

type BpaSortKey = "severity" | "category" | "rule" | "object";

interface BpaCardProps {
  findings: BpaFinding[];
  onNavigate?: (key: string) => void;
}

const BpaCard: React.FC<BpaCardProps> = ({ findings, onNavigate }) => {
  const styles = useStyles();
  const [sevFilter, setSevFilter] = useState<string>("all");
  const [catFilter, setCatFilter] = useState<string>("all");
  const [filterText, setFilterText] = useState("");
  const [sortKey, setSortKey] = useState<BpaSortKey>("severity");
  const [sortDesc, setSortDesc] = useState(false);

  const categories = useMemo(() => {
    const s = new Set<string>();
    for (const r of BPA_RULES) s.add(r.category);
    return Array.from(s).sort();
  }, []);

  const filtered = useMemo(() => {
    const needle = filterText.trim().toLowerCase();
    const out = findings.filter((f) => {
      if (sevFilter !== "all" && f.rule.severity !== sevFilter) return false;
      if (catFilter !== "all" && f.rule.category !== catFilter) return false;
      if (needle) {
        const hay = `${f.rule.name} ${f.rule.category} ${f.objectPath} ${f.detail ?? ""}`.toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
    const sevRank = (s: BpaSeverity) => SEVERITY_ORDER.indexOf(s);
    out.sort((a, b) => {
      let d = 0;
      switch (sortKey) {
        case "severity": d = sevRank(a.rule.severity) - sevRank(b.rule.severity); break;
        case "category": d = a.rule.category.localeCompare(b.rule.category); break;
        case "rule":     d = a.rule.name.localeCompare(b.rule.name); break;
        case "object":   d = a.objectPath.localeCompare(b.objectPath); break;
      }
      return sortDesc ? -d : d;
    });
    return out;
  }, [findings, sevFilter, catFilter, filterText, sortKey, sortDesc]);

  const counts = useMemo(() => {
    const c: Record<BpaSeverity, number> = { Error: 0, Warning: 0, Info: 0 };
    for (const f of findings) c[f.rule.severity] += 1;
    return c;
  }, [findings]);

  const handleSort = useCallback((k: BpaSortKey) => {
    if (k === sortKey) setSortDesc((v) => !v);
    else { setSortKey(k); setSortDesc(false); }
  }, [sortKey]);

  const handleFixIt = useCallback((f: BpaFinding) => {
    window.dispatchEvent(new CustomEvent("pbifixer:bpa-fix", {
      detail: {
        source: "model-bpa",
        ruleId: f.rule.id,
        fixKind: f.rule.fixKind,
        objectType: f.objectType,
        objectPath: f.objectPath,
      },
    }));
    if (onNavigate) onNavigate("fixer");
  }, [onNavigate]);

  const handleExport = useCallback(() => {
    const esc = (s: string) => `"${(s ?? "").replace(/"/g, '""')}"`;
    const header = ["severity", "category", "rule", "objectType", "objectPath", "detail"].join(",");
    const lines = filtered.map((f) =>
      [f.rule.severity, f.rule.category, f.rule.name, f.objectType, f.objectPath, f.detail ?? ""]
        .map(esc).join(","),
    );
    const blob = new Blob([[header, ...lines].join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `model-bpa-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [filtered]);

  return (
    <div className={styles.card}>
      <div className={styles.cardTitleRow}>
        <span className={styles.cardTitle}>Model BPA — {findings.length} finding(s)</span>
        <span className={styles.grow} />
        <Button
          icon={<ArrowDownload20Regular />}
          onClick={handleExport}
          disabled={filtered.length === 0}
          size="small"
        >
          Export CSV
        </Button>
      </div>
      <div className={styles.toolbar}>
        <Field label="Severity">
          <Combobox
            value={sevFilter === "all" ? "All" : sevFilter}
            selectedOptions={[sevFilter]}
            onOptionSelect={(_, d) => d.optionValue && setSevFilter(d.optionValue)}
          >
            <Option value="all">All</Option>
            {SEVERITY_ORDER.map((s) => <Option key={s} value={s}>{s}</Option>)}
          </Combobox>
        </Field>
        <Field label="Category">
          <Combobox
            value={catFilter === "all" ? "All" : catFilter}
            selectedOptions={[catFilter]}
            onOptionSelect={(_, d) => d.optionValue && setCatFilter(d.optionValue)}
          >
            <Option value="all">All</Option>
            {categories.map((c) => <Option key={c} value={c}>{c}</Option>)}
          </Combobox>
        </Field>
        <Field label="Filter">
          <Input
            placeholder="rule, object, text…"
            value={filterText}
            onChange={(_, d) => setFilterText(d.value)}
          />
        </Field>
      </div>
      <div className={styles.stats}>
        <span className={mergeClasses(styles.sev, styles.sevError)}>Error {counts.Error}</span>
        <span className={mergeClasses(styles.sev, styles.sevWarning)}>Warning {counts.Warning}</span>
        <span className={mergeClasses(styles.sev, styles.sevInfo)}>Info {counts.Info}</span>
        <span>·</span>
        <span>{filtered.length} shown</span>
      </div>
      <div className={styles.gridWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th} onClick={() => handleSort("severity")}>Severity {sortKey === "severity" ? (sortDesc ? "▼" : "▲") : ""}</th>
              <th className={styles.th} onClick={() => handleSort("category")}>Category {sortKey === "category" ? (sortDesc ? "▼" : "▲") : ""}</th>
              <th className={styles.th} onClick={() => handleSort("rule")}>Rule {sortKey === "rule" ? (sortDesc ? "▼" : "▲") : ""}</th>
              <th className={styles.th} onClick={() => handleSort("object")}>Object {sortKey === "object" ? (sortDesc ? "▼" : "▲") : ""}</th>
              <th className={styles.th}>Detail</th>
              <th className={styles.th}>Fix</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((f, i) => {
              const sevClass = f.rule.severity === "Error"
                ? mergeClasses(styles.sev, styles.sevError)
                : f.rule.severity === "Warning"
                  ? mergeClasses(styles.sev, styles.sevWarning)
                  : mergeClasses(styles.sev, styles.sevInfo);
              return (
                <tr key={`${f.rule.id}::${f.objectPath}::${i}`}>
                  <td className={styles.td}><span className={sevClass}>{f.rule.severity}</span></td>
                  <td className={styles.td}>{f.rule.category}</td>
                  <td className={styles.td}>
                    <strong>{f.rule.name}</strong>
                    <div style={{ color: tokens.colorNeutralForeground3, fontSize: "11px" }}>
                      {f.rule.description}
                    </div>
                  </td>
                  <td className={styles.td}><code>{f.objectPath}</code></td>
                  <td className={styles.td}>{f.detail ?? ""}</td>
                  <td className={styles.td}>
                    {f.rule.fixKind && (
                      <Button size="small" icon={<Wrench20Regular />} onClick={() => handleFixIt(f)}>Fix it</Button>
                    )}
                  </td>
                </tr>
              );
            })}
            {filtered.length === 0 && (
              <tr>
                <td className={styles.td} colSpan={6}>
                  <div className={styles.empty}>
                    No findings — either the model is clean or the active filters hide them.
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Card 3 — Memory Analyzer (Vertipaq)
// ---------------------------------------------------------------------------

type SectionKey = "summary" | "tables" | "partitions" | "columns" | "hierarchies" | "relationships";

type SortDir = "asc" | "desc";
interface SortState { key: string; dir: SortDir }

function compareVals(a: unknown, b: unknown): number {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a ?? "").localeCompare(String(b ?? ""));
}

function applySort<T extends object>(rows: T[], sort: SortState | null): T[] {
  if (!sort) return rows;
  const sorted = [...rows].sort((a, b) =>
    compareVals((a as Record<string, unknown>)[sort.key], (b as Record<string, unknown>)[sort.key]),
  );
  return sort.dir === "desc" ? sorted.reverse() : sorted;
}

interface ColumnDef<T> {
  key: keyof T & string;
  header: string;
  render?: (row: T) => React.ReactNode;
  numeric?: boolean;
  pctBar?: boolean;
}

// Generic over object shapes; we don't require a string index signature
// because the Vertipaq* row types are concrete interfaces from
// vertipaqApi.ts and TypeScript can't infer an index signature for them.

const PctBar: React.FC<{ pct: number }> = ({ pct }) => {
  const styles = useStyles();
  const w = Math.max(0, Math.min(100, pct || 0));
  return (
    <div className={styles.pctBarTrack}>
      <div className={styles.pctBarFill} style={{ right: `${100 - w}%` }} />
      <div className={styles.pctBarLabel}>{formatPct(pct)}</div>
    </div>
  );
};

function SortableTable<T extends object>(props: {
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
      keys.some((k) => String((r as Record<string, unknown>)[k] ?? "").toLowerCase().includes(q)),
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
            {props.columns.map((c) => {
              if (c.pctBar) {
                const v = Number(row[c.key] ?? 0);
                return (
                  <td key={c.key} className={styles.pctCell}>
                    <PctBar pct={v} />
                  </td>
                );
              }
              return (
                <td
                  key={c.key}
                  className={c.numeric ? styles.tdNum : styles.td}
                  title={String(row[c.key] ?? "")}
                >
                  {c.render ? c.render(row) : String(row[c.key] ?? "")}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function csvEscape(v: unknown): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function rowsToCsv<T extends object>(
  rows: T[],
  cols: { key: keyof T & string; header: string }[],
): string {
  const header = cols.map((c) => csvEscape(c.header)).join(",");
  const body = rows.map((r) => cols.map((c) => csvEscape((r as Record<string, unknown>)[c.key])).join(",")).join("\n");
  return `${header}\n${body}`;
}

function downloadCsv(name: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

interface MemoryCardProps {
  data: VertipaqAnalyzerResult | null;
  loading: boolean;
  err: string;
  onReload: () => void;
  datasetName?: string;
  datasetId?: string;
}

const MemoryCard: React.FC<MemoryCardProps> = ({ data, loading, err, onReload, datasetName, datasetId }) => {
  const styles = useStyles();
  const [section, setSection] = useState<SectionKey>("summary");
  const [filter, setFilter] = useState("");

  const onExport = useCallback(() => {
    if (!data) return;
    const ds = (datasetName || datasetId || "model").replace(/[^a-z0-9_-]+/gi, "_");
    let csv = "";
    if (section === "tables") {
      csv = rowsToCsv(data.sections.tables, [
        { key: "table", header: "Table" },
        { key: "rows", header: "Rows" },
        { key: "totalSize", header: "Total Size (bytes)" },
        { key: "dataSize", header: "Data Size" },
        { key: "dictionarySize", header: "Dictionary Size" },
        { key: "hierarchySize", header: "Auto-Hierarchy Size" },
        { key: "userHierarchySize", header: "User-Hierarchy Size" },
        { key: "relationshipSize", header: "Relationship Size" },
        { key: "pctDb", header: "% DB" },
        { key: "columnsCount", header: "Columns" },
        { key: "partitionsCount", header: "Partitions" },
        { key: "segmentsCount", header: "Segments" },
        { key: "mode", header: "Mode" },
      ]);
    } else if (section === "columns") {
      csv = rowsToCsv(data.sections.columns, [
        { key: "table", header: "Table" }, { key: "column", header: "Column" },
        { key: "totalSize", header: "Total Size" }, { key: "dataSize", header: "Data Size" },
        { key: "dictionarySize", header: "Dictionary" }, { key: "hierarchySize", header: "Hierarchies" },
        { key: "encoding", header: "Encoding" }, { key: "isResident", header: "Resident" },
        { key: "temperature", header: "Temperature" }, { key: "lastAccessed", header: "Last Accessed" },
        { key: "records", header: "Records" }, { key: "segments", header: "Segments" },
        { key: "dataType", header: "Data Type" }, { key: "pctDb", header: "% DB" },
        { key: "pctTable", header: "% Table" },
      ]);
    } else if (section === "partitions") {
      csv = rowsToCsv(data.sections.partitions, [
        { key: "table", header: "Table" }, { key: "partition", header: "Partition" },
        { key: "mode", header: "Mode" }, { key: "dataSourceType", header: "Source" },
        { key: "modifiedTime", header: "Modified" }, { key: "refreshedTime", header: "Refreshed" },
      ]);
    } else if (section === "hierarchies") {
      csv = rowsToCsv(data.sections.hierarchies, [
        { key: "table", header: "Table" }, { key: "hierarchy", header: "Hierarchy" },
        { key: "usedSize", header: "Used Size" }, { key: "rowsCount", header: "Rows" },
      ]);
    } else if (section === "relationships") {
      csv = rowsToCsv(data.sections.relationships, [
        { key: "fromTable", header: "From Table" }, { key: "fromColumn", header: "From Column" },
        { key: "toTable", header: "To Table" }, { key: "toColumn", header: "To Column" },
        { key: "usedSize", header: "Used Size" }, { key: "maxFromCardinality", header: "Max From Cardinality" },
        { key: "maxToCardinality", header: "Max To Cardinality" }, { key: "missingKeys", header: "Missing Keys" },
      ]);
    }
    if (csv) downloadCsv(`vertipaq-${ds}-${section}.csv`, csv);
  }, [data, section, datasetName, datasetId]);

  const summary = data?.sections.model?.[0];

  return (
    <div className={styles.card}>
      <div className={styles.cardTitleRow}>
        <span className={styles.cardTitle}>Memory Analyzer</span>
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
          size="small"
        >
          Export CSV
        </Button>
        <Button
          appearance="secondary"
          icon={<ArrowClockwise20Regular />}
          onClick={onReload}
          disabled={loading}
          size="small"
        >
          Refresh
        </Button>
      </div>

      {err && (
        <MessageBar intent="error">
          <MessageBarBody>
            <MessageBarTitle>Failed to load Vertipaq Analyzer</MessageBarTitle>
            {err}
          </MessageBarBody>
        </MessageBar>
      )}

      {(data || loading) && (
        <TabList
          selectedValue={section}
          onTabSelect={(_e: SelectTabEvent, d: SelectTabData) => setSection(d.value as SectionKey)}
        >
          <Tab value="summary">Summary</Tab>
          <Tab value="tables">Tables ({data?.sections.tables.length ?? 0})</Tab>
          <Tab value="partitions">Partitions ({data?.sections.partitions.length ?? 0})</Tab>
          <Tab value="columns">Columns ({data?.sections.columns.length ?? 0})</Tab>
          <Tab value="hierarchies">Hierarchies ({data?.sections.hierarchies.length ?? 0})</Tab>
          <Tab value="relationships">Relationships ({data?.sections.relationships.length ?? 0})</Tab>
        </TabList>
      )}

      {loading && (
        <div className={styles.empty}>
          <Spinner label="Running Vertipaq Analyzer over XMLA…" />
        </div>
      )}

      {!loading && !data && !err && (
        <div className={styles.empty}>
          <Text>No memory data — run Scan Model to load.</Text>
        </div>
      )}

      {!loading && data && section === "summary" && summary && (
        <div className={styles.cardRow}>
          <SummaryTile label="Total size" value={formatBytes(summary.totalSize)} />
          <SummaryTile label="Total rows" value={formatNumber(summary.totalRows)} />
          <SummaryTile label="Tables" value={String(summary.tableCount)} />
          <SummaryTile label="Columns" value={String(summary.columnCount)} />
          <SummaryTile label="Partitions" value={String(summary.partitionCount)} />
          <SummaryTile label="Hierarchies" value={String(summary.hierarchyCount)} />
          <SummaryTile label="Relationships" value={String(summary.relationshipCount)} />
          <SummaryTile label="Compatibility" value={summary.compatibilityLevel || "—"} />
          <SummaryTile label="Default mode" value={summary.defaultMode || "—"} />
        </div>
      )}

      {!loading && data && section === "tables" && (
        <div className={styles.gridWrap}>
          <SortableTable<VertipaqTableRow>
            rows={data.sections.tables}
            filterText={filter}
            filterKeys={["table", "mode"]}
            defaultSort={{ key: "totalSize", dir: "desc" }}
            emptyText="No tables returned by DISCOVER_STORAGE_TABLES."
            columns={[
              { key: "table", header: "Table" },
              { key: "rows", header: "Rows", numeric: true, render: (r) => formatNumber(r.rows) },
              { key: "totalSize", header: "Total Size", numeric: true, render: (r) => formatBytes(r.totalSize) },
              { key: "dataSize", header: "Data", numeric: true, render: (r) => formatBytes(r.dataSize) },
              { key: "dictionarySize", header: "Dictionary", numeric: true, render: (r) => formatBytes(r.dictionarySize) },
              { key: "hierarchySize", header: "Auto-Hier.", numeric: true, render: (r) => formatBytes(r.hierarchySize) },
              { key: "userHierarchySize", header: "User-Hier.", numeric: true, render: (r) => formatBytes(r.userHierarchySize) },
              { key: "relationshipSize", header: "Rels", numeric: true, render: (r) => formatBytes(r.relationshipSize) },
              { key: "pctDb", header: "% DB", pctBar: true },
              { key: "columnsCount", header: "Cols", numeric: true, render: (r) => String(r.columnsCount) },
              { key: "partitionsCount", header: "Parts", numeric: true, render: (r) => String(r.partitionsCount) },
              { key: "segmentsCount", header: "Segs", numeric: true, render: (r) => String(r.segmentsCount) },
              { key: "mode", header: "Mode" },
            ]}
          />
        </div>
      )}

      {!loading && data && section === "partitions" && (
        <div className={styles.gridWrap}>
          <SortableTable<VertipaqPartitionRow>
            rows={data.sections.partitions}
            filterText={filter}
            filterKeys={["table", "partition", "mode", "dataSourceType"]}
            defaultSort={{ key: "table", dir: "asc" }}
            emptyText="No partitions returned by TMSCHEMA_PARTITIONS."
            columns={[
              { key: "table", header: "Table" },
              { key: "partition", header: "Partition" },
              { key: "mode", header: "Mode" },
              { key: "dataSourceType", header: "Source" },
              { key: "modifiedTime", header: "Modified" },
              { key: "refreshedTime", header: "Refreshed" },
            ]}
          />
        </div>
      )}

      {!loading && data && section === "columns" && (
        <div className={styles.gridWrap}>
          <SortableTable<VertipaqColumnRow>
            rows={data.sections.columns}
            filterText={filter}
            filterKeys={["table", "column", "encoding", "dataType"]}
            defaultSort={{ key: "totalSize", dir: "desc" }}
            emptyText="No columns returned by DISCOVER_STORAGE_TABLE_COLUMNS."
            columns={[
              { key: "table", header: "Table" },
              { key: "column", header: "Column" },
              { key: "totalSize", header: "Total", numeric: true, render: (r) => formatBytes(r.totalSize) },
              { key: "dataSize", header: "Data", numeric: true, render: (r) => formatBytes(r.dataSize) },
              { key: "dictionarySize", header: "Dictionary", numeric: true, render: (r) => formatBytes(r.dictionarySize) },
              { key: "hierarchySize", header: "Hier.", numeric: true, render: (r) => formatBytes(r.hierarchySize) },
              { key: "encoding", header: "Encoding" },
              { key: "isResident", header: "Resident", render: (r) => (r.isResident ? "✓" : "—") },
              { key: "temperature", header: "Temp", numeric: true, render: (r) => r.temperature.toFixed(2) },
              { key: "records", header: "Records", numeric: true, render: (r) => formatNumber(r.records) },
              { key: "segments", header: "Segs", numeric: true, render: (r) => String(r.segments) },
              { key: "pctDb", header: "% DB", pctBar: true },
              { key: "pctTable", header: "% Table", pctBar: true },
            ]}
          />
        </div>
      )}

      {!loading && data && section === "hierarchies" && (
        <div className={styles.gridWrap}>
          <SortableTable<VertipaqHierarchyRow>
            rows={data.sections.hierarchies}
            filterText={filter}
            filterKeys={["table", "hierarchy"]}
            defaultSort={{ key: "usedSize", dir: "desc" }}
            emptyText="No user hierarchies in this model."
            columns={[
              { key: "table", header: "Table" },
              { key: "hierarchy", header: "Hierarchy" },
              { key: "usedSize", header: "Used Size", numeric: true, render: (r) => formatBytes(r.usedSize) },
              { key: "rowsCount", header: "Rows", numeric: true, render: (r) => formatNumber(r.rowsCount) },
            ]}
          />
        </div>
      )}

      {!loading && data && section === "relationships" && (
        <div className={styles.gridWrap}>
          <SortableTable<VertipaqRelationshipRow>
            rows={data.sections.relationships}
            filterText={filter}
            filterKeys={["fromTable", "fromColumn", "toTable", "toColumn"]}
            defaultSort={{ key: "usedSize", dir: "desc" }}
            emptyText="No relationships returned by DISCOVER_STORAGE_TABLE_RELATIONSHIPS."
            columns={[
              { key: "fromTable", header: "From Table" },
              { key: "fromColumn", header: "From Column" },
              { key: "toTable", header: "To Table" },
              { key: "toColumn", header: "To Column" },
              { key: "usedSize", header: "Used Size", numeric: true, render: (r) => formatBytes(r.usedSize) },
              { key: "maxFromCardinality", header: "Max From", numeric: true, render: (r) => formatNumber(r.maxFromCardinality) },
              { key: "maxToCardinality", header: "Max To", numeric: true, render: (r) => formatNumber(r.maxToCardinality) },
              { key: "missingKeys", header: "Missing Keys", numeric: true, render: (r) => String(r.missingKeys) },
            ]}
          />
        </div>
      )}
    </div>
  );
};

const SummaryTile: React.FC<{ label: string; value: string }> = ({ label, value }) => {
  const styles = useStyles();
  return (
    <div className={styles.summaryCard}>
      <div className={styles.summaryLabel}>{label}</div>
      <div className={styles.summaryValue}>{value}</div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export interface ModelScanResultsProps {
  scanRanOnce: boolean;
  // Quick fixes
  fixersWithFindings: Fixer[];
  scanResults: Record<string, FixerResult>;
  applyingFixerId: string | null;
  onApplyFixer: (fx: Fixer) => void;
  // BPA
  bpaFindings: BpaFinding[] | null;
  // Memory
  vertipaq: VertipaqAnalyzerResult | null;
  vertipaqLoading: boolean;
  vertipaqError: string;
  onReloadVertipaq: () => void;
  datasetName?: string;
  datasetId?: string;
  // Nav relay (BPA "Fix it" → Fixer page)
  onNavigate?: (key: string) => void;
}

export const ModelScanResults = React.forwardRef<HTMLDivElement, ModelScanResultsProps>(
  function ModelScanResults(props, ref) {
    const styles = useStyles();
    if (!props.scanRanOnce) return null;
    return (
      <div ref={ref} className={styles.section}>
        <div className={styles.sectionHeader}>Scan results</div>
        <QuickFixesCard
          fixersWithFindings={props.fixersWithFindings}
          scanResults={props.scanResults}
          applyingFixerId={props.applyingFixerId}
          onApplyFixer={props.onApplyFixer}
        />
        {props.bpaFindings !== null && (
          <BpaCard findings={props.bpaFindings} onNavigate={props.onNavigate} />
        )}
        <MemoryCard
          data={props.vertipaq}
          loading={props.vertipaqLoading}
          err={props.vertipaqError}
          onReload={props.onReloadVertipaq}
          datasetName={props.datasetName}
          datasetId={props.datasetId}
        />
      </div>
    );
  },
);
