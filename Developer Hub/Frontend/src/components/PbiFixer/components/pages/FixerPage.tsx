// WS-E — Fixer Execution Page.
//
// First-cut UX: checkbox list of fixers grouped by scope, ⚡ Scan /
// ⚡ Apply mega-button, Apply safety switch + diff preview + confirm
// dialog, live log panel. Listens to `pbifixer:bpa-fix` CustomEvent
// emitted by WS-C / WS-D so BPA "Fix it" preselects the right fixer.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Checkbox,
  Switch,
  Text,
  Title3,
  Title2,
  Divider,
  Accordion,
  AccordionHeader,
  AccordionItem,
  AccordionPanel,
  Dialog,
  DialogSurface,
  DialogBody,
  DialogTitle,
  DialogContent,
  DialogActions,
  DialogTrigger,
  Spinner,
  Badge,
  makeStyles,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import {
  Flash20Filled,
  Warning20Regular,
  CheckmarkCircle20Filled,
  ShieldError20Regular,
} from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import type { ReportData } from "../../types/report";
import type { ModelData } from "../../types";
import { loadReportDefinition, loadModelData } from "../../services/fabricApi";
import { FIXERS, findFixerForBpaRule, type FixerResult } from "../../fixers";

const useStyles = makeStyles({
  root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0, ...shorthands.gap("12px") },
  hero: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("12px"),
    ...shorthands.padding("12px", "16px"),
    backgroundColor: tokens.colorNeutralBackground2,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
  },
  heroMsg: { flex: 1, display: "flex", flexDirection: "column", ...shorthands.gap("2px") },
  main: { flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "1fr 1fr", ...shorthands.gap("12px") },
  panel: {
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  panelHead: {
    ...shorthands.padding("8px", "12px"),
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    fontWeight: tokens.fontWeightSemibold,
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("8px"),
  },
  panelBody: { flex: 1, overflow: "auto", ...shorthands.padding("8px", "12px") },
  group: { marginBottom: "8px" },
  groupLabel: {
    fontSize: "11px",
    textTransform: "uppercase",
    color: tokens.colorNeutralForeground3,
    letterSpacing: "0.04em",
    ...shorthands.margin("8px", "0", "4px", "0"),
  },
  fixerRow: {
    display: "flex",
    alignItems: "flex-start",
    ...shorthands.gap("8px"),
    ...shorthands.padding("6px", "4px"),
    borderRadius: tokens.borderRadiusSmall,
  },
  fixerRowSelected: { backgroundColor: tokens.colorNeutralBackground1Hover },
  fixerMeta: { display: "flex", flexDirection: "column", flex: 1 },
  fixerTitle: { fontWeight: tokens.fontWeightSemibold },
  fixerSub: { fontSize: "11px", color: tokens.colorNeutralForeground3 },
  badges: { display: "flex", ...shorthands.gap("4px"), marginLeft: "auto" },
  log: {
    fontFamily: "Consolas, 'Courier New', monospace",
    fontSize: "12px",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  diffBlock: {
    fontFamily: "Consolas, 'Courier New', monospace",
    fontSize: "11px",
    whiteSpace: "pre-wrap",
    ...shorthands.padding("6px", "8px"),
    backgroundColor: tokens.colorNeutralBackground3,
    borderRadius: tokens.borderRadiusSmall,
  },
  empty: {
    display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
    height: "100%", ...shorthands.padding("32px"), ...shorthands.gap("8px"),
    color: tokens.colorNeutralForeground3, textAlign: "center",
  },
});

type RunStatus = "idle" | "scanning" | "applying" | "done" | "error";

interface RunState {
  results: Record<string, FixerResult>;
  log: string[];
  status: RunStatus;
}

const INITIAL_RUN: RunState = { results: {}, log: [], status: "idle" };

export const FixerPage: React.FC<PageProps> = ({ auth, workspaceId, datasetId, datasetName, reportId, reportName }) => {
  const styles = useStyles();

  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [report, setReport] = useState<ReportData | null>(null);
  const [model, setModel] = useState<ModelData | null>(null);
  const [applyMode, setApplyMode] = useState(false);
  const [diffReviewed, setDiffReviewed] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [run, setRun] = useState<RunState>(INITIAL_RUN);
  const loadedRef = useRef({ ws: "", rpt: "", ds: "" });

  // Load report + model lazily when ids change.
  useEffect(() => {
    (async () => {
      if (workspaceId && reportId && loadedRef.current.rpt !== reportId) {
        try {
          const r = await loadReportDefinition(auth, workspaceId, reportId, reportName ?? "");
          setReport(r);
          loadedRef.current.rpt = reportId;
        } catch { /* ignore */ }
      }
      if (workspaceId && datasetId && loadedRef.current.ds !== datasetId) {
        try {
          const m = await loadModelData(auth, workspaceId, datasetId, datasetName ?? "");
          setModel(m);
          loadedRef.current.ds = datasetId;
        } catch { /* ignore */ }
      }
    })();
  }, [auth, workspaceId, reportId, reportName, datasetId, datasetName]);

  // BPA "Fix it" — preselect the matching fixer + log which finding triggered it.
  useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<{ ruleId?: string; objectPath?: string; source?: string }>;
      const ruleId = ce.detail?.ruleId;
      if (!ruleId) return;
      const fx = findFixerForBpaRule(ruleId);
      if (!fx) return;
      setSelected((prev) => ({ ...prev, [fx.id]: true }));
      setRun((prev) => ({
        ...prev,
        log: [...prev.log, `↪ Preselected ${fx.id} from ${ce.detail?.source} finding ${ruleId}${ce.detail?.objectPath ? ` (${ce.detail.objectPath})` : ""}.`],
      }));
    };
    window.addEventListener("pbifixer:bpa-fix", handler);
    return () => window.removeEventListener("pbifixer:bpa-fix", handler);
  }, []);

  const selectedFixers = useMemo(() => FIXERS.filter((f) => selected[f.id]), [selected]);

  const runScan = useCallback(async () => {
    if (selectedFixers.length === 0) return;
    setRun((prev) => ({ results: {}, log: [...prev.log, `— Scan started (${selectedFixers.length} fixer(s)) —`], status: "scanning" }));
    const results: Record<string, FixerResult> = {};
    const log: string[] = [];
    for (const fx of selectedFixers) {
      try {
        const r = fx.scan({ report: report ?? undefined, model: model ?? undefined });
        results[fx.id] = r;
        log.push(`[${fx.id}] ${r.findings.length} finding(s).`);
        for (const line of r.log) log.push(`  ${line}`);
      } catch (e) {
        log.push(`[${fx.id}] ERROR: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    setRun((prev) => ({ results, log: [...prev.log, ...log, "— Scan complete —"], status: "done" }));
    setDiffReviewed(false);
  }, [selectedFixers, report, model]);

  const runApply = useCallback(async () => {
    if (selectedFixers.length === 0) return;
    setConfirmOpen(false);
    setRun((prev) => ({ ...prev, status: "applying", log: [...prev.log, `— Apply started (${selectedFixers.length} fixer(s)) —`] }));
    const results: Record<string, FixerResult> = {};
    const log: string[] = [];
    for (const fx of selectedFixers) {
      try {
        const r = await fx.apply({ report: report ?? undefined, model: model ?? undefined });
        results[fx.id] = r;
        log.push(`[${fx.id}] applied=${r.applied} · ${r.findings.length} finding(s).`);
        for (const line of r.log) log.push(`  ${line}`);
      } catch (e) {
        log.push(`[${fx.id}] ERROR: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    setRun((prev) => ({ results, log: [...prev.log, ...log, "— Apply complete —"], status: "done" }));
  }, [selectedFixers, report, model]);

  const totalFindings = useMemo(
    () => Object.values(run.results).reduce((n, r) => n + r.findings.length, 0),
    [run.results],
  );
  const hasDiffs = useMemo(
    () => Object.values(run.results).some((r) => r.diff && r.diff.length > 0),
    [run.results],
  );
  const applyEnabled = applyMode && diffReviewed && selectedFixers.length > 0 && run.status === "done";

  if (!workspaceId) {
    return (
      <div className={styles.empty}>
        <Title3>Fixer</Title3>
        <Text>Select a workspace above to begin. Pick a report for report fixers and a semantic model for model fixers.</Text>
      </div>
    );
  }

  const reportFixers = FIXERS.filter((f) => f.scope === "report");
  const smFixers = FIXERS.filter((f) => f.scope === "sm");

  return (
    <div className={styles.root}>
      {/* Hero bar */}
      <div className={styles.hero}>
        <Flash20Filled style={{ color: applyMode ? tokens.colorPaletteRedForeground1 : tokens.colorBrandForeground1 }} />
        <div className={styles.heroMsg}>
          <Title3>{applyMode ? "Apply selected fixers" : "Scan selected fixers"}</Title3>
          <Text size={200}>
            {applyMode
              ? "Apply mode ON — write-back will run when you confirm. Review the diff and tick 'I reviewed the diff' to enable Apply."
              : "Scan-only mode — no changes are written. Flip the Apply switch when you're ready to write back."}
          </Text>
        </div>
        <Switch
          label={applyMode ? "Apply changes" : "Scan only"}
          checked={applyMode}
          onChange={(_, d) => { setApplyMode(d.checked); setDiffReviewed(false); }}
        />
        {applyMode ? (
          <Button
            appearance="primary"
            icon={<Flash20Filled />}
            disabled={!applyEnabled || run.status === "applying"}
            onClick={() => setConfirmOpen(true)}
          >
            Apply selected ({selectedFixers.length})
          </Button>
        ) : (
          <Button
            appearance="primary"
            icon={<Flash20Filled />}
            disabled={selectedFixers.length === 0 || run.status === "scanning"}
            onClick={runScan}
          >
            Scan selected ({selectedFixers.length})
          </Button>
        )}
      </div>

      <div className={styles.main}>
        {/* LEFT: fixer list */}
        <div className={styles.panel}>
          <div className={styles.panelHead}>Fixers</div>
          <div className={styles.panelBody}>
            <div className={styles.group}>
              <div className={styles.groupLabel}>Report ({reportFixers.length})</div>
              {reportFixers.map((fx) => (
                <FixerCheckbox
                  key={fx.id}
                  fx={fx}
                  selected={!!selected[fx.id]}
                  result={run.results[fx.id]}
                  onToggle={(v) => setSelected((p) => ({ ...p, [fx.id]: v }))}
                />
              ))}
            </div>
            <Divider />
            <div className={styles.group}>
              <div className={styles.groupLabel}>Semantic Model ({smFixers.length})</div>
              {smFixers.map((fx) => (
                <FixerCheckbox
                  key={fx.id}
                  fx={fx}
                  selected={!!selected[fx.id]}
                  result={run.results[fx.id]}
                  onToggle={(v) => setSelected((p) => ({ ...p, [fx.id]: v }))}
                />
              ))}
            </div>
          </div>
        </div>

        {/* RIGHT: log + diff */}
        <div className={styles.panel}>
          <div className={styles.panelHead}>
            {run.status === "scanning" || run.status === "applying" ? <Spinner size="tiny" /> : null}
            <span>Run log</span>
            {run.status === "done" && (
              <Badge appearance="tint" color={totalFindings > 0 ? "warning" : "success"}>
                {totalFindings} finding{totalFindings === 1 ? "" : "s"}
              </Badge>
            )}
            <span style={{ marginLeft: "auto", fontSize: "11px", color: tokens.colorNeutralForeground3 }}>
              {report ? `report: ${report.reportId.slice(0, 8)}…` : "no report"} · {model ? `model: ${datasetName}` : "no model"}
            </span>
          </div>
          <div className={styles.panelBody}>
            {run.log.length === 0 ? (
              <Text style={{ color: tokens.colorNeutralForeground3 }}>
                Select fixers on the left, then click Scan.
              </Text>
            ) : (
              <div className={styles.log}>{run.log.join("\n")}</div>
            )}

            {hasDiffs && (
              <div style={{ marginTop: "12px" }}>
                <Title3 style={{ fontSize: "14px" }}>Diff preview</Title3>
                <Accordion collapsible multiple>
                  {Object.entries(run.results).filter(([, r]) => r.diff).map(([id, r]) => (
                    <AccordionItem key={id} value={id}>
                      <AccordionHeader>{id} — {r.findings.length} change(s)</AccordionHeader>
                      <AccordionPanel>
                        <div className={styles.diffBlock}>{r.diff}</div>
                      </AccordionPanel>
                    </AccordionItem>
                  ))}
                </Accordion>
                {applyMode && (
                  <Checkbox
                    label="I reviewed the diff"
                    checked={diffReviewed}
                    onChange={(_, d) => setDiffReviewed(!!d.checked)}
                  />
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      <Dialog open={confirmOpen} onOpenChange={(_, d) => setConfirmOpen(d.open)}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle><Warning20Regular /> Confirm Apply</DialogTitle>
            <DialogContent>
              <Text>The following fixers will write back to Fabric:</Text>
              <ul>
                {selectedFixers.map((fx) => (
                  <li key={fx.id}>
                    <strong>{fx.id}</strong> — {fx.title}
                    {fx.mode === "backend" && <Badge color="warning" style={{ marginLeft: "6px" }}>backend bridge</Badge>}
                  </li>
                ))}
              </ul>
              <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>
                For v0.14 the write-back is stubbed (scan-only). Apply will only emit log
                entries — no definition is mutated.
              </Text>
            </DialogContent>
            <DialogActions>
              <DialogTrigger disableButtonEnhancement>
                <Button appearance="secondary">Cancel</Button>
              </DialogTrigger>
              <Button appearance="primary" icon={<CheckmarkCircle20Filled />} onClick={runApply}>
                Apply
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  );
};

const FixerCheckbox: React.FC<{
  fx: typeof FIXERS[number];
  selected: boolean;
  result?: FixerResult;
  onToggle: (v: boolean) => void;
}> = ({ fx, selected, result, onToggle }) => {
  const styles = useStyles();
  return (
    <div className={`${styles.fixerRow} ${selected ? styles.fixerRowSelected : ""}`}>
      <Checkbox checked={selected} onChange={(_, d) => onToggle(!!d.checked)} />
      <div className={styles.fixerMeta}>
        <span className={styles.fixerTitle}>{fx.id}</span>
        <span className={styles.fixerSub}>{fx.title}</span>
      </div>
      <div className={styles.badges}>
        {fx.mode === "backend" && (
          <Badge appearance="tint" color="warning" icon={<ShieldError20Regular />}>backend</Badge>
        )}
        {result && (
          <Badge appearance="tint" color={result.findings.length > 0 ? "warning" : "success"}>
            {result.findings.length}
          </Badge>
        )}
      </div>
    </div>
  );
};
