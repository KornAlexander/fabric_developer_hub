// ReportScanResults — renders the unified "Scan Report" output panel
// shown below the Report Explorer tree+properties layout.
//
// Two cards in this order:
//   1. Quick fixes  (report fixer scan-only results, report-wide)
//   2. Report BPA   (rule grid w/ severity/category filters + Fix it relay)

import React, { useCallback, useMemo, useState } from "react";
import {
  Button,
  Combobox,
  Option,
  Field,
  Input,
  Spinner,
  makeStyles,
  mergeClasses,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import { ArrowDownload20Regular, Wrench20Regular } from "@fluentui/react-icons";
import {
  BPA_RULES,
  type BpaFinding,
  type BpaSeverity,
} from "../services/reportBpaApi";
import { BORDER_COLOR, GRAY_COLOR, SECTION_BG } from "../utils";
import type { Fixer, FixerResult } from "../fixers";

const SEVERITY_ORDER: BpaSeverity[] = ["Error", "Warning", "Info"];

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
});

// ---------------------------------------------------------------------------
// Card 1 — Quick fixes
// ---------------------------------------------------------------------------

interface QuickFixesCardProps {
  fixersWithFindings: Fixer[];
  scanResults: Record<string, FixerResult>;
  runningFixerId: string | null;
  onApplyFixer: (fx: Fixer) => void;
}

const QuickFixesCard: React.FC<QuickFixesCardProps> = ({
  fixersWithFindings,
  scanResults,
  runningFixerId,
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
        <div className={styles.empty}>No report issues found.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {fixersWithFindings.map((fx) => {
            const r = scanResults[fx.id];
            const count = r?.findings.length ?? 0;
            const busy = runningFixerId === fx.id;
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
                    disabled={busy || runningFixerId !== null}
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
// Card 2 — Report BPA
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

  return (
    <div className={styles.card}>
      <div className={styles.cardTitleRow}>
        <span className={styles.cardTitle}>Report BPA — {findings.length} finding(s)</span>
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

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export interface ReportScanResultsProps {
  scanRanOnce: boolean;
  fixersWithFindings: Fixer[];
  scanResults: Record<string, FixerResult>;
  runningFixerId: string | null;
  onApplyFixer: (fx: Fixer) => void;
  bpaFindings: BpaFinding[] | null;
  onNavigate?: (key: string) => void;
}

export const ReportScanResults = React.forwardRef<HTMLDivElement, ReportScanResultsProps>(
  function ReportScanResults(props, ref) {
    const styles = useStyles();
    if (!props.scanRanOnce) return null;
    return (
      <div ref={ref} className={styles.section}>
        <div className={styles.sectionHeader}>Scan results</div>
        <QuickFixesCard
          fixersWithFindings={props.fixersWithFindings}
          scanResults={props.scanResults}
          runningFixerId={props.runningFixerId}
          onApplyFixer={props.onApplyFixer}
        />
        {props.bpaFindings !== null && (
          <BpaCard findings={props.bpaFindings} onNavigate={props.onNavigate} />
        )}
      </div>
    );
  },
);
