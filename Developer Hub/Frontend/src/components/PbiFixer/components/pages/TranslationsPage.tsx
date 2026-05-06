// WS-G — Translations page.
// Workflow: Load model → select scope + target culture → Generate
// proposal → review/edit in grid → Apply (gated by confirmation
// dialog). Apply writes the accepted rows to the model's TMDL
// `definition/cultures/<culture>.tmdl` part via the backend (which
// round-trips getDefinition / updateDefinition under the user's OBO
// Fabric token).
// the UI shows a clear banner and offers a JSON/CSV export instead.

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
  Checkbox,
  Dialog,
  DialogSurface,
  DialogBody,
  DialogTitle,
  DialogContent,
  DialogActions,
  DialogTrigger,
  makeStyles,
  shorthands,
  tokens,
  mergeClasses,
} from "@fluentui/react-components";
import { ArrowDownload20Regular, ArrowUpload20Regular, Sparkle20Regular, Checkmark20Regular, Dismiss20Regular } from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import type { ModelData } from "../../types";
import { loadModelData } from "../../services/fabricApi";
import {
  proposeTranslations,
  applyTranslations,
  type TranslationProposalItem,
  type TranslationSourceItem,
  type TranslationObjectType,
} from "../../services/translationsApi";

// Cultures we ship out of the box. More can be typed into the combobox.
const CULTURES = [
  { code: "de-DE", label: "German (de-DE)" },
  { code: "fr-FR", label: "French (fr-FR)" },
  { code: "es-ES", label: "Spanish (es-ES)" },
  { code: "it-IT", label: "Italian (it-IT)" },
  { code: "pt-PT", label: "Portuguese (pt-PT)" },
  { code: "nl-NL", label: "Dutch (nl-NL)" },
  { code: "pl-PL", label: "Polish (pl-PL)" },
  { code: "ja-JP", label: "Japanese (ja-JP)" },
];

type Scope = "all" | "tables" | "columns" | "measures";

interface Row extends TranslationProposalItem {
  accepted: boolean;
  /** `true` once the user edits the proposed caption — keeps edits
   *  from being stomped when the grid is re-filtered. */
  edited: boolean;
}

const useStyles = makeStyles({
  root: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    minHeight: 0,
    ...shorthands.gap("12px"),
  },
  toolbar: {
    display: "flex",
    alignItems: "flex-end",
    ...shorthands.gap("12px"),
    flexWrap: "wrap",
    ...shorthands.padding("8px", "4px"),
  },
  grow: { flex: 1 },
  actions: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("8px"),
    marginLeft: "auto",
  },
  gridWrap: {
    flex: 1,
    minHeight: 0,
    overflow: "auto",
    ...shorthands.border("1px", "solid", tokens.colorNeutralStroke2),
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  gridTable: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: tokens.fontSizeBase200,
  },
  th: {
    textAlign: "left",
    ...shorthands.padding("6px", "10px"),
    position: "sticky",
    top: 0,
    backgroundColor: tokens.colorNeutralBackground2,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    fontWeight: tokens.fontWeightSemibold,
    zIndex: 1,
  },
  td: {
    ...shorthands.padding("4px", "10px"),
    borderBottom: `1px solid ${tokens.colorNeutralStroke3}`,
    verticalAlign: "middle",
  },
  badge: {
    display: "inline-flex",
    alignItems: "center",
    ...shorthands.padding("2px", "6px"),
    borderRadius: tokens.borderRadiusMedium,
    fontSize: "11px",
    fontWeight: tokens.fontWeightSemibold,
    lineHeight: "16px",
  },
  badgeNew:      { backgroundColor: "rgba(15, 123, 15, 0.12)",  color: "#0f7b0f" },
  badgeOver:     { backgroundColor: "rgba(180, 100, 30, 0.15)", color: "#8a4500" },
  badgeUnchg:    { backgroundColor: "rgba(0, 95, 170, 0.1)",    color: "#004c87" },
  editInput: {
    width: "100%",
    minWidth: "180px",
  },
  filterBar: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("10px"),
    ...shorthands.padding("6px", "4px"),
    flexWrap: "wrap",
  },
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
});

export const TranslationsPage: React.FC<PageProps> = ({
  auth,
  workspaceId,
  datasetId,
  datasetName,
}) => {
  const styles = useStyles();

  const [model, setModel] = useState<ModelData | null>(null);
  const [modelError, setModelError] = useState<string>("");
  const [modelLoading, setModelLoading] = useState<boolean>(false);

  const [scope, setScope] = useState<Scope>("all");
  const [targetCulture, setTargetCulture] = useState<string>("de-DE");
  const [proposing, setProposing] = useState<boolean>(false);
  const [proposeError, setProposeError] = useState<string>("");
  const [rows, setRows] = useState<Row[]>([]);
  const [filterText, setFilterText] = useState<string>("");
  const [showChangedOnly, setShowChangedOnly] = useState<boolean>(false);
  const [showEmptyOnly, setShowEmptyOnly] = useState<boolean>(false);

  const [confirmOpen, setConfirmOpen] = useState<boolean>(false);
  const [applyError, setApplyError] = useState<string>("");
  const [applyInfo, setApplyInfo] = useState<string>("");

  // Model load — driven by the selected dataset from the outer shell.
  useEffect(() => {
    if (!workspaceId || !datasetId) {
      setModel(null);
      return;
    }
    let cancelled = false;
    setModelLoading(true);
    setModelError("");
    (async () => {
      try {
        const m = await loadModelData(auth, workspaceId, datasetId, datasetName ?? "");
        if (!cancelled) setModel(m);
      } catch (e) {
        if (!cancelled) setModelError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setModelLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [auth, workspaceId, datasetId, datasetName]);

  /** Build the list of translatable items from the loaded model,
   *  honouring the scope selection. Skips hidden + technical objects. */
  const sourceItems: TranslationSourceItem[] = useMemo(() => {
    if (!model) return [];
    const items: TranslationSourceItem[] = [];
    for (const [tName, t] of Object.entries(model.tables)) {
      if (t.isHidden) continue;
      if (scope === "all" || scope === "tables") {
        items.push({
          objectType: "Table" as TranslationObjectType,
          objectPath: tName,
          sourceCaption: tName,
        });
      }
      if (scope === "all" || scope === "columns") {
        for (const [cName, c] of Object.entries(t.columns)) {
          if (c.isHidden) continue;
          items.push({
            objectType: "Column" as TranslationObjectType,
            objectPath: `${tName}[${cName}]`,
            sourceCaption: cName,
          });
        }
      }
      if (scope === "all" || scope === "measures") {
        for (const [mName, m] of Object.entries(t.measures)) {
          if (m.isHidden) continue;
          items.push({
            objectType: "Measure" as TranslationObjectType,
            objectPath: `${tName}[${mName}]`,
            sourceCaption: mName,
          });
        }
      }
    }
    return items;
  }, [model, scope]);

  const handleGenerate = useCallback(async () => {
    if (!workspaceId || !datasetId) return;
    if (sourceItems.length === 0) {
      setProposeError("No objects in scope — load a model and pick a scope first.");
      return;
    }
    setProposing(true);
    setProposeError("");
    setApplyInfo("");
    setApplyError("");
    try {
      const resp = await proposeTranslations(auth, {
        workspaceId,
        datasetId,
        targetCultures: [targetCulture],
        sourceItems,
      });
      // Merge: default-accept only rows whose proposal differs from
      // the existing caption. User can bulk-accept / reject later.
      setRows(resp.items.map((it) => ({
        ...it,
        accepted: (it.proposedCaption ?? "") !== (it.existingCaption ?? it.sourceCaption),
        edited: false,
      })));
    } catch (e) {
      setProposeError(e instanceof Error ? e.message : String(e));
    } finally {
      setProposing(false);
    }
  }, [auth, workspaceId, datasetId, targetCulture, sourceItems]);

  // Derived: rows after the review-grid filters are applied.
  const visibleRows = useMemo(() => {
    const needle = filterText.trim().toLowerCase();
    return rows.filter((r) => {
      if (needle && !(
        r.objectPath.toLowerCase().includes(needle) ||
        r.sourceCaption.toLowerCase().includes(needle) ||
        r.proposedCaption.toLowerCase().includes(needle)
      )) return false;
      if (showChangedOnly && r.proposedCaption === (r.existingCaption ?? r.sourceCaption)) return false;
      if (showEmptyOnly && (r.existingCaption ?? "").trim().length > 0) return false;
      return true;
    });
  }, [rows, filterText, showChangedOnly, showEmptyOnly]);

  const acceptedCount = useMemo(
    () => rows.reduce((n, r) => n + (r.accepted ? 1 : 0), 0),
    [rows],
  );

  const setRowAt = useCallback((idx: number, patch: Partial<Row>) => {
    setRows((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], ...patch };
      return next;
    });
  }, []);

  const acceptAll = useCallback(() => {
    setRows((prev) => prev.map((r) => ({ ...r, accepted: true })));
  }, []);
  const rejectAll = useCallback(() => {
    setRows((prev) => prev.map((r) => ({ ...r, accepted: false })));
  }, []);

  // --- Export / import ----------------------------------------------
  const handleExportJson = useCallback(() => {
    const acceptedRows = rows.filter((r) => r.accepted);
    const payload = {
      workspaceId,
      datasetId,
      culture: targetCulture,
      items: acceptedRows.map(({ accepted, edited, ...rest }) => rest),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `translations-${targetCulture}-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [rows, workspaceId, datasetId, targetCulture]);

  const handleExportCsv = useCallback(() => {
    const acceptedRows = rows.filter((r) => r.accepted);
    const esc = (s: string) => `"${(s ?? "").replace(/"/g, '""')}"`;
    const header = ["objectType", "objectPath", "sourceCaption", "existingCaption", "proposedCaption"].join(",");
    const lines = acceptedRows.map((r) =>
      [r.objectType, r.objectPath, r.sourceCaption, r.existingCaption ?? "", r.proposedCaption]
        .map(esc).join(","),
    );
    const blob = new Blob([[header, ...lines].join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `translations-${targetCulture}-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [rows, targetCulture]);

  const handleImportJson = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "application/json";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const parsed = JSON.parse(text) as { items?: TranslationProposalItem[] };
        if (!parsed.items || !Array.isArray(parsed.items)) {
          setProposeError("Imported JSON has no `items` array");
          return;
        }
        setRows(parsed.items.map((it) => ({ ...it, accepted: true, edited: false })));
        setProposeError("");
      } catch (e) {
        setProposeError(e instanceof Error ? e.message : String(e));
      }
    };
    input.click();
  }, []);

  // --- Apply --------------------------------------------------------
  const handleApply = useCallback(async () => {
    setApplyError("");
    setApplyInfo("");
    const toApply = rows.filter((r) => r.accepted).map(({ accepted, edited, ...rest }) => rest);
    try {
      const res = await applyTranslations(auth, {
        workspaceId,
        datasetId: datasetId ?? "",
        culture: targetCulture,
        items: toApply,
      });
      const created = res.createdCultureFile ? " (new culture file created)" : "";
      setApplyInfo(`Applied ${res.applied} translation(s) to ${targetCulture}${created}.`);
      setConfirmOpen(false);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setApplyError(msg);
      setConfirmOpen(false);
    }
  }, [auth, rows, workspaceId, datasetId, targetCulture]);

  // -------------------------------------------------------------------
  if (!workspaceId || !datasetId) {
    return (
      <div className={styles.empty}>
        <Title3>Translations</Title3>
        <Text>Select a workspace and a semantic model in the connection bar above to begin.</Text>
      </div>
    );
  }

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        <Field label="Target culture">
          <Combobox
            value={CULTURES.find((c) => c.code === targetCulture)?.label ?? targetCulture}
            selectedOptions={[targetCulture]}
            onOptionSelect={(_, d) => d.optionValue && setTargetCulture(d.optionValue)}
            placeholder="Pick a culture"
          >
            {CULTURES.map((c) => (
              <Option key={c.code} value={c.code}>{c.label}</Option>
            ))}
          </Combobox>
        </Field>

        <Field label="Scope">
          <Combobox
            value={scope}
            selectedOptions={[scope]}
            onOptionSelect={(_, d) => d.optionValue && setScope(d.optionValue as Scope)}
          >
            <Option value="all">All</Option>
            <Option value="tables">Tables</Option>
            <Option value="columns">Columns</Option>
            <Option value="measures">Measures</Option>
          </Combobox>
        </Field>

        <Button
          appearance="primary"
          icon={<Sparkle20Regular />}
          disabled={modelLoading || proposing || sourceItems.length === 0}
          onClick={handleGenerate}
        >
          {proposing ? "Generating…" : `Generate proposal (${sourceItems.length})`}
        </Button>

        <div className={styles.actions}>
          <Button icon={<ArrowDownload20Regular />} onClick={handleExportJson} disabled={acceptedCount === 0}>Export JSON</Button>
          <Button icon={<ArrowDownload20Regular />} onClick={handleExportCsv} disabled={acceptedCount === 0}>Export CSV</Button>
          <Button icon={<ArrowUpload20Regular />} onClick={handleImportJson}>Import JSON</Button>
          <Button
            appearance="primary"
            style={{ backgroundColor: "#a4262c", borderColor: "#a4262c", color: "white" }}
            disabled={acceptedCount === 0}
            onClick={() => setConfirmOpen(true)}
          >
            Apply {acceptedCount > 0 ? `(${acceptedCount})` : ""}
          </Button>
        </div>
      </div>

      {modelLoading && (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Spinner size="tiny" /> <Text>Loading model…</Text>
        </div>
      )}
      {modelError && (
        <MessageBar intent="error">
          <MessageBarBody><MessageBarTitle>Model load failed</MessageBarTitle> {modelError}</MessageBarBody>
        </MessageBar>
      )}
      {proposeError && (
        <MessageBar intent="error">
          <MessageBarBody><MessageBarTitle>Propose failed</MessageBarTitle> {proposeError}</MessageBarBody>
        </MessageBar>
      )}
      {applyInfo && (
        <MessageBar intent="success">
          <MessageBarBody>{applyInfo}</MessageBarBody>
        </MessageBar>
      )}
      {applyError && (
        <MessageBar intent="warning">
          <MessageBarBody><MessageBarTitle>Apply failed</MessageBarTitle> {applyError}</MessageBarBody>
        </MessageBar>
      )}

      {rows.length > 0 && (
        <>
          <div className={styles.filterBar}>
            <Input
              placeholder="Filter by object, source or proposed caption…"
              value={filterText}
              onChange={(_, d) => setFilterText(d.value)}
              contentBefore={<span style={{ color: tokens.colorNeutralForeground3 }}>🔍</span>}
            />
            <Checkbox label="Changed only" checked={showChangedOnly} onChange={(_, d) => setShowChangedOnly(!!d.checked)} />
            <Checkbox label="Empty existing only" checked={showEmptyOnly} onChange={(_, d) => setShowEmptyOnly(!!d.checked)} />
            <span style={{ flex: 1 }} />
            <Button size="small" icon={<Checkmark20Regular />} onClick={acceptAll}>Accept all</Button>
            <Button size="small" icon={<Dismiss20Regular />} onClick={rejectAll}>Reject all</Button>
            <Text size={200}>{visibleRows.length} of {rows.length} shown · {acceptedCount} accepted</Text>
          </div>

          <div className={styles.gridWrap}>
            <table className={styles.gridTable}>
              <thead>
                <tr>
                  <th className={styles.th}>Type</th>
                  <th className={styles.th}>Object</th>
                  <th className={styles.th}>Source</th>
                  <th className={styles.th}>Existing</th>
                  <th className={styles.th}>Proposed</th>
                  <th className={styles.th}>Diff</th>
                  <th className={styles.th}>Accept</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((r) => {
                  // The original index in `rows` (setRowAt relies on it).
                  const idx = rows.indexOf(r);
                  const hasExisting = (r.existingCaption ?? "").trim().length > 0;
                  const changed = r.proposedCaption !== (r.existingCaption ?? r.sourceCaption);
                  const diffClass = !hasExisting
                    ? mergeClasses(styles.badge, styles.badgeNew)
                    : changed
                      ? mergeClasses(styles.badge, styles.badgeOver)
                      : mergeClasses(styles.badge, styles.badgeUnchg);
                  const diffLabel = !hasExisting ? "new" : changed ? "overwrite" : "unchanged";
                  return (
                    <tr key={`${r.objectType}::${r.objectPath}`}>
                      <td className={styles.td}>{r.objectType}</td>
                      <td className={styles.td}><code>{r.objectPath}</code></td>
                      <td className={styles.td}>{r.sourceCaption}</td>
                      <td className={styles.td}>{r.existingCaption ?? ""}</td>
                      <td className={styles.td}>
                        <Input
                          className={styles.editInput}
                          value={r.proposedCaption}
                          onChange={(_, d) => setRowAt(idx, { proposedCaption: d.value, edited: true })}
                        />
                      </td>
                      <td className={styles.td}><span className={diffClass}>{diffLabel}</span></td>
                      <td className={styles.td}>
                        <Checkbox
                          checked={r.accepted}
                          onChange={(_, d) => setRowAt(idx, { accepted: !!d.checked })}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      <Dialog open={confirmOpen} onOpenChange={(_, d) => setConfirmOpen(d.open)}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Apply translations?</DialogTitle>
            <DialogContent>
              <Text>
                This will write <strong>{acceptedCount}</strong> translation(s) to
                culture <strong>{targetCulture}</strong> in the selected model.
                The change is applied directly to the semantic model's TMDL
                culture file via the Fabric REST API.
              </Text>
            </DialogContent>
            <DialogActions>
              <DialogTrigger disableButtonEnhancement>
                <Button appearance="secondary">Cancel</Button>
              </DialogTrigger>
              <Button appearance="primary" onClick={handleApply}>Apply</Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  );
};
