// WS-P — Sempy Runner page.
//
// Pick a sempy / sempy-labs function from a curated catalog → typed
// param inputs (workspace / report / dataset auto-bind from the
// connection bar; everything else renders as a plain input). Live
// code preview in the middle. Bottom pane: Copy / Download .py /
// Create + open Fabric notebook. The Fabric notebook path is the
// "no install needed" answer — Fabric Spark ships sempy + sempy-labs
// preinstalled, so the generated notebook just runs.

import React, { useCallback, useMemo, useState } from "react";
import {
  Button,
  Combobox,
  Option,
  Field,
  Input,
  Switch,
  Spinner,
  Text,
  Textarea,
  Title3,
  Badge,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Tooltip,
  Link,
  makeStyles,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import {
  Copy20Regular,
  ArrowDownload20Regular,
  Open20Regular,
  PlayCircle20Regular,
} from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import { createNotebook } from "../../services/fabricApi";
import {
  SEMPY_CATALOG,
  generateSempyCode,
  codeToNotebookJson,
  type SempyCategory,
  type SempyFunction,
  type SempyParam,
  type SempyArgValues,
} from "../../services/sempyCatalog";

const useStyles = makeStyles({
  root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0, ...shorthands.gap("12px") },
  builder: {
    display: "flex", ...shorthands.gap("12px"), flexWrap: "wrap",
    ...shorthands.padding("4px"),
  },
  paramGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
    ...shorthands.gap("12px"),
    ...shorthands.padding("8px"),
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  desc: {
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
    ...shorthands.padding("4px", "8px"),
  },
  codeWrap: {
    flex: 1, minHeight: 0,
    display: "flex", flexDirection: "column",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: "#1e1e1e",
    color: "#d4d4d4",
    overflow: "hidden",
  },
  codeToolbar: {
    display: "flex", alignItems: "center", ...shorthands.gap("8px"),
    ...shorthands.padding("6px", "10px"),
    backgroundColor: "#252526",
    borderBottom: `1px solid #333`,
  },
  codeToolbarSpacer: { flex: 1 },
  codeBlock: {
    flex: 1, minHeight: "180px",
    overflow: "auto",
    margin: 0,
    ...shorthands.padding("12px", "16px"),
    fontFamily: "Cascadia Code, Consolas, ui-monospace, monospace",
    fontSize: "13px",
    lineHeight: "20px",
    whiteSpace: "pre",
    backgroundColor: "#1e1e1e",
    color: "#d4d4d4",
  },
  runRow: {
    display: "flex", alignItems: "center", ...shorthands.gap("12px"),
    flexWrap: "wrap",
    ...shorthands.padding("4px"),
  },
  empty: {
    display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
    height: "100%", ...shorthands.padding("32px"), ...shorthands.gap("8px"),
    color: tokens.colorNeutralForeground3, textAlign: "center",
  },
});

const CATEGORIES: (SempyCategory | "All")[] = [
  "All", "Workspace", "Model", "Report", "Refresh", "Vertipaq", "Lakehouse", "Misc",
];

function downloadBlob(filename: string, text: string, mime: string) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const SempyRunnerPage: React.FC<PageProps> = ({
  auth,
  workloadClient,
  workspaceId,
  workspaceName,
  datasetName,
  reportName,
}) => {
  const styles = useStyles();
  const [category, setCategory] = useState<SempyCategory | "All">("All");
  const [fnId, setFnId] = useState<string>(SEMPY_CATALOG[0]?.id ?? "");
  const [overrides, setOverrides] = useState<SempyArgValues>({});
  const [creating, setCreating] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState<string>("");

  const visibleFns = useMemo(
    () => (category === "All"
      ? SEMPY_CATALOG
      : SEMPY_CATALOG.filter(f => f.category === category)),
    [category],
  );

  const fn: SempyFunction | undefined = useMemo(
    () => SEMPY_CATALOG.find(f => f.id === fnId),
    [fnId],
  );

  /** Auto-bound value for a typed param from the connection bar. */
  const autoValue = useCallback((p: SempyParam): string | undefined => {
    switch (p.kind) {
      case "workspace": return workspaceName || workspaceId || undefined;
      case "report":    return reportName || undefined;
      case "dataset":   return datasetName || undefined;
      default:          return undefined;
    }
  }, [workspaceName, workspaceId, reportName, datasetName]);

  /** Effective value for a param: explicit user override → auto-bind → default. */
  const valueFor = useCallback((p: SempyParam): string | number | boolean | undefined => {
    const ov = overrides[p.name];
    if (ov !== undefined && ov !== "") return ov;
    const auto = autoValue(p);
    if (auto !== undefined && auto !== "") return auto;
    return p.default;
  }, [overrides, autoValue]);

  const code = useMemo(() => {
    if (!fn) return "";
    const values: SempyArgValues = {};
    for (const p of fn.params) {
      const v = valueFor(p);
      if (v !== undefined && v !== "") values[p.name] = v as any;
    }
    return generateSempyCode(fn, values);
  }, [fn, valueFor]);

  const safeFnName = (fn?.name ?? "sempy_call").replace(/[^A-Za-z0-9_]+/g, "_");
  const notebookTitle = `${fn?.module ?? "sempy"} · ${fn?.name ?? ""}`;

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setStatus("Copied to clipboard.");
    } catch (e: any) {
      setErrorMsg(`Clipboard write failed: ${e?.message || e}`);
    }
  };

  const onDownloadPy = () => {
    downloadBlob(`${safeFnName}.py`, code, "text/x-python");
    setStatus(`Downloaded ${safeFnName}.py.`);
  };

  const onCreateNotebook = async () => {
    if (!fn || !workspaceId) return;
    setCreating(true);
    setErrorMsg("");
    setStatus("");
    try {
      const ipynb = codeToNotebookJson(code, notebookTitle);
      const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const displayName = `Sempy Runner · ${fn.name} · ${stamp}`;
      const created = await createNotebook(auth, workspaceId, displayName, ipynb);
      setStatus(`Created notebook "${displayName}". Opening in Fabric…`);
      // Try to navigate the host into the new notebook. The path
      // varies by tenant; if navigation fails the user still sees
      // the notebook in the workspace listing.
      try {
        if (workloadClient?.navigation && created.id) {
          await workloadClient.navigation.navigate("host", { path: `/groups/${workspaceId}/synapsenotebooks/${created.id}` });
        }
      } catch {
        // non-fatal
      }
    } catch (e: any) {
      setErrorMsg(`Notebook create failed: ${e?.message || e}`);
    } finally {
      setCreating(false);
    }
  };

  if (!workspaceId) {
    return (
      <div className={styles.empty}>
        <Title3>Sempy Runner</Title3>
        <Text>Select a workspace above to start building a sempy / sempy-labs call.</Text>
      </div>
    );
  }

  return (
    <div className={styles.root}>
      {/* ── Builder ─────────────────────────────────────────── */}
      <div className={styles.builder}>
        <Field label="Category" style={{ minWidth: 160 }}>
          <Combobox
            value={category}
            selectedOptions={[category]}
            onOptionSelect={(_, d) => {
              const v = (d.optionValue || "All") as SempyCategory | "All";
              setCategory(v);
              const next = (v === "All" ? SEMPY_CATALOG : SEMPY_CATALOG.filter(f => f.category === v))[0];
              if (next) setFnId(next.id);
            }}
          >
            {CATEGORIES.map(c => <Option key={c} value={c}>{c}</Option>)}
          </Combobox>
        </Field>
        <Field label="Function" style={{ minWidth: 320, flex: 1 }}>
          <Combobox
            value={fn ? `${fn.module}.${fn.name}` : ""}
            selectedOptions={fn ? [fn.id] : []}
            onOptionSelect={(_, d) => {
              if (d.optionValue) {
                setFnId(d.optionValue);
                setOverrides({});
                setStatus("");
                setErrorMsg("");
              }
            }}
          >
            {visibleFns.map(f => (
              <Option key={f.id} value={f.id} text={`${f.module}.${f.name}`}>
                {f.module}.{f.name} — {f.description}
              </Option>
            ))}
          </Combobox>
        </Field>
      </div>

      {fn && (
        <>
          <div className={styles.desc}>
            <Badge appearance="tint" color="informative" style={{ marginRight: 8 }}>{fn.category}</Badge>
            {fn.description}
            {fn.docUrl && <> · <Link href={fn.docUrl} target="_blank">docs</Link></>}
          </div>

          {/* ── Param inputs ─────────────────────────────────── */}
          {fn.params.length > 0 ? (
            <div className={styles.paramGrid}>
              {fn.params.map(p => {
                const auto = autoValue(p);
                const ov = overrides[p.name];
                const effective = ov !== undefined ? ov : (auto ?? (p.default !== undefined ? String(p.default) : ""));
                const label = `${p.name}${p.required ? " *" : ""}${p.kind !== "text" && p.kind !== "multiline" ? ` (${p.kind})` : ""}`;
                if (p.kind === "bool") {
                  return (
                    <Field key={p.name} label={label} hint={p.hint}>
                      <Switch
                        checked={effective === true || effective === "true" || effective === "True"}
                        onChange={(_, d) => setOverrides(o => ({ ...o, [p.name]: !!d.checked }))}
                        label={effective === true || effective === "true" || effective === "True" ? "True" : "False"}
                      />
                    </Field>
                  );
                }
                if (p.kind === "multiline") {
                  return (
                    <Field key={p.name} label={label} hint={p.hint} style={{ gridColumn: "1 / -1" }}>
                      <Textarea
                        value={String(effective ?? "")}
                        onChange={(_, d) => setOverrides(o => ({ ...o, [p.name]: d.value }))}
                        rows={5}
                        resize="vertical"
                        style={{ fontFamily: "Cascadia Code, Consolas, ui-monospace, monospace", fontSize: "12px" }}
                      />
                    </Field>
                  );
                }
                const placeholder = auto ? `auto: ${auto}` : (p.default !== undefined ? `default: ${p.default}` : "");
                return (
                  <Field key={p.name} label={label} hint={p.hint || (auto ? "Auto-bound from connection bar — override if needed." : undefined)}>
                    <Input
                      value={ov !== undefined ? String(ov) : (auto ?? (p.default !== undefined ? String(p.default) : ""))}
                      placeholder={placeholder}
                      type={p.kind === "number" ? "number" : "text"}
                      onChange={(_, d) => setOverrides(o => ({ ...o, [p.name]: d.value }))}
                    />
                  </Field>
                );
              })}
            </div>
          ) : (
            <div className={styles.desc}>
              <em>No parameters — this function takes no arguments.</em>
            </div>
          )}

          {/* ── Code preview ─────────────────────────────────── */}
          <div className={styles.codeWrap}>
            <div className={styles.codeToolbar}>
              <Tooltip content="Generated Python — sent into the new notebook as cell #1." relationship="label">
                <Text style={{ color: "#cccccc", fontSize: 12 }}>Python preview</Text>
              </Tooltip>
              <div className={styles.codeToolbarSpacer} />
              <Button size="small" appearance="subtle" icon={<Copy20Regular />} onClick={onCopy} style={{ color: "#d4d4d4" }}>
                Copy
              </Button>
              <Button size="small" appearance="subtle" icon={<ArrowDownload20Regular />} onClick={onDownloadPy} style={{ color: "#d4d4d4" }}>
                .py
              </Button>
            </div>
            <pre className={styles.codeBlock}>{code}</pre>
          </div>

          {/* ── Run pane ─────────────────────────────────────── */}
          <div className={styles.runRow}>
            <Button
              appearance="primary"
              icon={creating ? <Spinner size="tiny" /> : <PlayCircle20Regular />}
              onClick={onCreateNotebook}
              disabled={creating || !workspaceId}
            >
              {creating ? "Creating notebook…" : "Create + open Fabric notebook"}
            </Button>
            <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>
              Drops a Synapse notebook into the workspace with this code in cell&nbsp;1. Fabric Spark already has sempy + sempy-labs — no install needed.
            </Text>
            {status && (
              <Badge appearance="tint" color="success" icon={<Open20Regular />}>
                {status}
              </Badge>
            )}
          </div>

          {errorMsg && (
            <MessageBar intent="error">
              <MessageBarBody>
                <MessageBarTitle>Failed</MessageBarTitle>
                {errorMsg}
              </MessageBarBody>
            </MessageBar>
          )}
        </>
      )}
    </div>
  );
};
