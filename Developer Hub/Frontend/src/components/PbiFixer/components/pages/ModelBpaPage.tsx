// WS-C — Model BPA page.
// Loads the semantic model, runs the built-in BPA rules and renders
// findings in a sortable/filterable grid. "Fix it" emits a typed
// window event so WS-N can wire the Fixer page later without coupling
// the two workstreams today.

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
import { ArrowClockwise20Regular, ArrowDownload20Regular, Play20Regular, Wrench20Regular } from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import type { ModelData } from "../../types";
import { loadModelData } from "../../services/fabricApi";
import { runModelBpa, BPA_RULES, type BpaFinding, type BpaSeverity } from "../../services/modelBpaApi";
import { runSllModelBpa, type SllModelBpaResponse } from "../../services/sllApi";

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
  grow: { flex: 1 },
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

export const ModelBpaPage: React.FC<PageProps> = ({ auth, workspaceId, datasetId, datasetName, onNavigate }) => {
  const styles = useStyles();

  const [model, setModel] = useState<ModelData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const [sevFilter, setSevFilter] = useState<string>("all");
  const [catFilter, setCatFilter] = useState<string>("all");
  const [filterText, setFilterText] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("severity");
  const [sortDesc, setSortDesc] = useState(false);

  // v0.74 — SLL (sempy_labs.run_model_bpa) live result.
  const [sll, setSll] = useState<SllModelBpaResponse | null>(null);
  const [sllLoading, setSllLoading] = useState(false);
  const [sllErr, setSllErr] = useState("");

  const handleRunSll = useCallback(async () => {
    if (!workspaceId || !datasetId) return;
    setSllLoading(true);
    setSllErr("");
    try {
      const r = await runSllModelBpa(auth, workspaceId, datasetId);
      setSll(r);
    } catch (e) {
      setSllErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSllLoading(false);
    }
  }, [auth, workspaceId, datasetId]);

  const loadModel = useCallback(async () => {
    if (!workspaceId || !datasetId) return;
    setLoading(true);
    setErr("");
    try {
      const m = await loadModelData(auth, workspaceId, datasetId, datasetName ?? "");
      setModel(m);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth, workspaceId, datasetId, datasetName]);

  useEffect(() => { void loadModel(); }, [loadModel]);

  const findings: BpaFinding[] = useMemo(() => (model ? runModelBpa(model) : []), [model]);

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
    // WS-N wires this into the Fixer page. For now we dispatch a
    // typed event + jump to the Fixer nav entry so users see the
    // linkage even before the backend TOM-write lands.
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

  if (!workspaceId || !datasetId) {
    return (
      <div className={styles.empty}>
        <Title3>Model BPA</Title3>
        <Text>Select a workspace and a semantic model in the connection bar above to begin.</Text>
      </div>
    );
  }

  return (
    <div className={styles.root}>
      {/* ── v0.74 SLL panel (Michael Kovalsky's run_model_bpa) ── */}
      <div style={{
        display: "flex", flexDirection: "column", gap: 8,
        padding: "10px 12px",
        border: `1px solid ${tokens.colorNeutralStroke2}`,
        borderRadius: tokens.borderRadiusMedium,
        backgroundColor: tokens.colorNeutralBackground2,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Title3 style={{ margin: 0 }}>semantic-link-labs · run_model_bpa</Title3>
          <span style={{ color: tokens.colorNeutralForeground3, fontSize: 12 }}>
            Michael Kovalsky · executed via the SLL sidecar (Service Principal)
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
            {sllLoading && <Spinner size="tiny" label="Running…" />}
            <Button appearance="primary" icon={<Play20Regular />} onClick={handleRunSll} disabled={sllLoading || !workspaceId || !datasetId}>
              {sll ? "Re-run" : "Run"}
            </Button>
          </div>
        </div>
        {sllErr && (
          <MessageBar intent="error">
            <MessageBarBody><MessageBarTitle>SLL run failed</MessageBarTitle> {sllErr}</MessageBarBody>
          </MessageBar>
        )}
        {sll && sll.rows.length > 0 && (
          <div style={{ maxHeight: 360, overflow: "auto", border: `1px solid ${tokens.colorNeutralStroke2}`, borderRadius: tokens.borderRadiusSmall, backgroundColor: tokens.colorNeutralBackground1 }}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {sll.columns.map((c) => <th key={c} className={styles.th}>{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {sll.rows.map((row, i) => (
                  <tr key={i}>
                    {sll.columns.map((c) => (
                      <td key={c} className={styles.td} title={String(row[c] ?? "")}>
                        {String(row[c] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {sll && sll.rows.length === 0 && !sllErr && (
          <Text>No violations reported by run_model_bpa — model is clean.</Text>
        )}
        {!sll && !sllErr && !sllLoading && (
          <Text style={{ color: tokens.colorNeutralForeground3 }}>
            Click <strong>Run</strong> to execute Michael Kovalsky&apos;s actual
            <code> run_model_bpa</code> against this semantic model and display
            its raw findings.
          </Text>
        )}
      </div>

      <Text style={{ color: tokens.colorNeutralForeground3, fontSize: 11, marginTop: 4 }}>
        Below: built-in client-side rule engine (offline, no SP required).
      </Text>
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
          <Button icon={<ArrowClockwise20Regular />} onClick={loadModel} disabled={loading}>Rerun</Button>
          <Button icon={<ArrowDownload20Regular />} onClick={handleExport} disabled={filtered.length === 0}>Export CSV</Button>
        </div>
      </div>

      <div className={styles.stats}>
        {loading && <><Spinner size="tiny" /><span>Loading model…</span></>}
        {!loading && (
          <>
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
          <MessageBarBody><MessageBarTitle>Model load failed</MessageBarTitle> {err}</MessageBarBody>
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
