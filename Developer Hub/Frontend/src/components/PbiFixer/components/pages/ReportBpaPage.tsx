// WS-D — Report BPA page.
// Clone of ModelBpaPage adapted to ReportData + report rule engine.

import React, { useCallback, useEffect, useMemo, useState } from "react";
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
  Title3,
  makeStyles,
  mergeClasses,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import { ArrowClockwise20Regular, ArrowDownload20Regular, Wrench20Regular } from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import type { ReportData } from "../../types/report";
import { loadReportDefinition } from "../../services/fabricApi";
import { runReportBpa, BPA_RULES, type BpaFinding, type BpaSeverity } from "../../services/reportBpaApi";

const SEVERITY_ORDER: BpaSeverity[] = ["Error", "Warning", "Info"];

const useStyles = makeStyles({
  root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0, ...shorthands.gap("12px") },
  toolbar: {
    display: "flex",
    alignItems: "flex-end",
    ...shorthands.gap("12px"),
    flexWrap: "wrap",
    ...shorthands.padding("4px"),
  },
  actions: { display: "flex", alignItems: "center", ...shorthands.gap("8px"), marginLeft: "auto" },
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
    cursor: "pointer",
    userSelect: "none",
    zIndex: 1,
  },
  td: {
    ...shorthands.padding("6px", "10px"),
    borderBottom: `1px solid ${tokens.colorNeutralStroke3}`,
    verticalAlign: "top",
  },
  sev: {
    display: "inline-flex",
    alignItems: "center",
    ...shorthands.padding("2px", "8px"),
    borderRadius: tokens.borderRadiusMedium,
    fontSize: "11px",
    fontWeight: tokens.fontWeightSemibold,
  },
  sevError:   { backgroundColor: "rgba(164, 38, 44, 0.12)",  color: "#a4262c" },
  sevWarning: { backgroundColor: "rgba(180, 100, 30, 0.15)", color: "#8a4500" },
  sevInfo:    { backgroundColor: "rgba(0, 95, 170, 0.10)",   color: "#004c87" },
  empty: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    ...shorthands.padding("32px"),
    ...shorthands.gap("8px"),
    color: tokens.colorNeutralForeground3,
    textAlign: "center",
  },
  stats: {
    display: "flex",
    ...shorthands.gap("12px"),
    alignItems: "center",
    ...shorthands.padding("4px"),
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
  },
});

type SortKey = "severity" | "category" | "rule" | "object";

export const ReportBpaPage: React.FC<PageProps> = ({ auth, workspaceId, reportId, reportName, onNavigate }) => {
  const styles = useStyles();

  const [report, setReport] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const [sevFilter, setSevFilter] = useState<string>("all");
  const [catFilter, setCatFilter] = useState<string>("all");
  const [filterText, setFilterText] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("severity");
  const [sortDesc, setSortDesc] = useState(false);

  const loadReport = useCallback(async () => {
    if (!workspaceId || !reportId) return;
    setLoading(true);
    setErr("");
    try {
      const r = await loadReportDefinition(auth, workspaceId, reportId, reportName ?? "");
      setReport(r);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth, workspaceId, reportId, reportName]);

  useEffect(() => { void loadReport(); }, [loadReport]);

  const findings: BpaFinding[] = useMemo(() => (report ? runReportBpa(report) : []), [report]);

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

  const handleSort = useCallback((k: SortKey) => {
    if (k === sortKey) setSortDesc((v) => !v);
    else { setSortKey(k); setSortDesc(false); }
  }, [sortKey]);

  const handleFixIt = useCallback((f: BpaFinding) => {
    window.dispatchEvent(new CustomEvent("pbifixer:bpa-fix", {
      detail: {
        source: "report-bpa",
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
    a.download = `report-bpa-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [filtered]);

  if (!workspaceId || !reportId) {
    return (
      <div className={styles.empty}>
        <Title3>Report BPA</Title3>
        <Text>Select a workspace and a report in the connection bar above to begin.</Text>
      </div>
    );
  }

  const pageCount = report ? Object.keys(report.pages ?? {}).length : 0;

  return (
    <div className={styles.root}>
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
        <div className={styles.actions}>
          <Button icon={<ArrowClockwise20Regular />} onClick={loadReport} disabled={loading}>Rerun</Button>
          <Button icon={<ArrowDownload20Regular />} onClick={handleExport} disabled={filtered.length === 0}>Export CSV</Button>
        </div>
      </div>

      <div className={styles.stats}>
        {loading && <><Spinner size="tiny" /><span>Loading report…</span></>}
        {!loading && (
          <>
            <span>{pageCount} page{pageCount === 1 ? "" : "s"}</span>
            <span>·</span>
            <span>{findings.length} findings</span>
            <span className={mergeClasses(styles.sev, styles.sevError)}>Error {counts.Error}</span>
            <span className={mergeClasses(styles.sev, styles.sevWarning)}>Warning {counts.Warning}</span>
            <span className={mergeClasses(styles.sev, styles.sevInfo)}>Info {counts.Info}</span>
            <span>·</span>
            <span>{filtered.length} shown</span>
          </>
        )}
      </div>

      {err && (
        <MessageBar intent="error">
          <MessageBarBody><MessageBarTitle>Report load failed</MessageBarTitle> {err}</MessageBarBody>
        </MessageBar>
      )}

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
            {filtered.length === 0 && !loading && (
              <tr>
                <td className={styles.td} colSpan={6}>
                  <div style={{ textAlign: "center", padding: "16px", color: tokens.colorNeutralForeground3 }}>
                    No findings — either the report is clean or the active filters hide them.
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
