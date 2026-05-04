// PbiFixer shell — WS-A flat-tree navigation.
// Left column hosts the nav; right column is a content slot. The
// connection bar stays above both so workspace / dataset / report
// selections survive page switches.
//
// Page components are chosen by `activeNav` and remounted (via `key`)
// whenever workspace or the selection relevant to that page changes.

import React, { useState, useCallback, useEffect, useMemo } from "react";
import {
  Combobox,
  Option,
  OptionGroup,
  Field,
  Button,
  Spinner,
  Text,
  makeStyles,
  mergeClasses,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import { ArrowSync20Regular } from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { ModelExplorer } from "./ModelExplorer";
import { ReportExplorer } from "./ReportExplorer";
import {
  FixerPage,
  ModelBpaPage,
  ReportBpaPage,
  MemoryPage,
  PerspectivesPage,
  TranslationsPage,
  DeltaPage,
  DiagramPage,
  ScriptRunnerPage,
  PrototypePage,
  ReversePrototypePage,
  SempyRunnerPage,
  AboutPage,
} from "./pages";
import { DEFAULT_NAV_KEY, NavKey } from "../types/nav";
import type { PageProps } from "../types/shared";
import * as api from "../../../controller/AgentHubApi";
import { PBI_FIXER_VERSION } from "../utils/version";

const STORAGE_NAV_KEY = "pbiFixer.activeNav";

const useStyles = makeStyles({
  root: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    backgroundColor: tokens.colorNeutralBackground1,
    color: tokens.colorNeutralForeground1,
  },
  // WS-O Phase 1: header restyled to mirror the AgentHub topbar
  // (warm `#faf9f8` surface, ~48 px tall, hairline border, motion curve
  // matches `cubic-bezier(0.33, 0, 0.67, 1)`).
  header: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("12px"),
    ...shorthands.padding("0", "24px"),
    height: "48px",
    flexShrink: 0,
    backgroundColor: "#faf9f8",
    borderBottomStyle: "solid",
    borderBottomWidth: "1px",
    borderBottomColor: "rgba(192, 199, 212, 0.1)",
  },
  title: {
    fontSize: tokens.fontSizeBase400,
    fontWeight: tokens.fontWeightSemibold,
    color: "#1a1c1c",
  },
  // Version pill — matches the AgentHub workload-version badge style so
  // both badges (workload + Fixer) read as siblings.
  version: {
    fontSize: "11px",
    color: "#605e5c",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    ...shorthands.padding("2px", "6px"),
    ...shorthands.border("1px", "solid", "rgba(192, 199, 212, 0.4)"),
    ...shorthands.borderRadius("4px"),
    backgroundColor: "#ffffff",
  },
  headerRight: {
    marginLeft: "auto",
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("8px"),
  },
  // Connection bar reads as a sub-header strip immediately under the
  // header (option (a) from WS-O appendix B). Same warm surface, same
  // hairline divider; the picker row sits flush so topbar + connection
  // bar feel like a single chrome unit, not two competing stripes.
  connectionBar: {
    display: "flex",
    alignItems: "flex-end",
    ...shorthands.gap("12px"),
    ...shorthands.padding("10px", "24px"),
    flexWrap: "wrap",
    backgroundColor: "#faf9f8",
    borderBottomStyle: "solid",
    borderBottomWidth: "1px",
    borderBottomColor: "rgba(192, 199, 212, 0.1)",
  },
  body: {
    flex: 1,
    display: "flex",
    flexDirection: "row",
    minHeight: 0,
  },
  // WS-O Phase 1.7: bumped padding to 24 px to match AgentHub breathing
  // room. Phase 2.2: opacity crossfade on `activeNav` change via the
  // `key` on the inner wrapper.
  content: {
    flex: 1,
    minWidth: 0,
    overflowY: "auto",
    ...shorthands.padding("24px"),
    backgroundColor: tokens.colorNeutralBackground1,
  },
  contentFade: {
    animationDuration: "160ms",
    animationTimingFunction: "cubic-bezier(0.33, 0, 0.67, 1)",
    animationName: {
      from: { opacity: 0 },
      to:   { opacity: 1 },
    },
  },
  tokenStatus: {
    fontSize: "12px",
    ...shorthands.padding("2px", "8px"),
    ...shorthands.borderRadius(tokens.borderRadiusSmall),
  },
  tokenOk: {
    backgroundColor: tokens.colorPaletteGreenBackground2,
    color: tokens.colorPaletteGreenForeground2,
  },
  tokenErr: {
    backgroundColor: tokens.colorPaletteRedBackground2,
    color: tokens.colorPaletteRedForeground2,
    maxWidth: "480px",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  empty: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    color: tokens.colorNeutralForeground3,
    textAlign: "center",
    ...shorthands.padding("48px"),
  },
});

export interface PbiFixerPageProps {
  workloadClient: WorkloadClientAPI;
  /** Optional explicit initial sub-nav (e.g. "model", "report"). When
   *  set, it overrides the URL/sessionStorage fallback in `readNavKey`.
   *  Used by `AgentHubLayout` to inject the per-tab `?nav=` value so
   *  every PBI Fixer editor tab renders its own page even though they
   *  all share the outer `window.location`. */
  initialNav?: NavKey;
}

function readNavKey(): NavKey {
  // 1) URL ``?nav=`` query takes precedence so each editor tab can
  //    pin its own PBI Fixer sub-page (Model / Report / …).
  try {
    if (typeof window !== "undefined") {
      const sp = new URLSearchParams(window.location.search);
      const fromUrl = sp.get("nav");
      if (fromUrl) {
        // Iframe bootstrap URL can nest a second ``?…`` inside the
        // nav value (e.g. ``nav=report?experience=fabric-developer``).
        // Strip it down to the first alphanumeric token.
        const m = fromUrl.match(/^[A-Za-z0-9_-]+/);
        if (m) return m[0] as NavKey;
      }
    }
  } catch { /* ignore */ }
  try {
    const raw = sessionStorage.getItem(STORAGE_NAV_KEY);
    if (!raw) return DEFAULT_NAV_KEY;
    return raw as NavKey;
  } catch {
    return DEFAULT_NAV_KEY;
  }
}

export const PbiFixerPage: React.FC<PbiFixerPageProps> = ({
  workloadClient,
  initialNav,
}) => {
  const styles = useStyles();
  const [activeNav, setActiveNav] = useState<NavKey>(
    () => initialNav ?? readNavKey()
  );

  // Connection / selection state.
  const [workspaceId, setWorkspaceId] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [reportId, setReportId] = useState("");
  const [workspaceInput, setWorkspaceInput] = useState("");
  const [datasetInput, setDatasetInput] = useState("");
  const [reportInput, setReportInput] = useState("");

  const [workspaces, setWorkspaces] = useState<api.Workspace[]>([]);
  const [workspacesLoading, setWorkspacesLoading] = useState(false);
  const [items, setItems] = useState<api.WorkspaceItem[]>([]);
  const [itemsLoading, setItemsLoading] = useState(false);

  const [accessToken, setAccessToken] = useState("");
  const [tokenLoading, setTokenLoading] = useState(false);
  const [tokenError, setTokenError] = useState("");

  const githubToken = sessionStorage.getItem("github_token") || "";

  // Persist nav state.
  useEffect(() => {
    try { sessionStorage.setItem(STORAGE_NAV_KEY, activeNav); } catch { /* ignore */ }
  }, [activeNav]);

  // Note: each PBI Fixer tab is independent now (one tab per nav key).
  // The legacy ``pbifixer:navchange`` cross-tab event is intentionally
  // not subscribed to here — it would otherwise hijack the active nav
  // of every open PBI Fixer tab.

  // Folder id → name lookup for grouping pickers.
  const folderName = useMemo(() => {
    const map = new Map<string, string>();
    for (const it of items) {
      if (it.type === "Folder" && it.id) map.set(it.id, it.name);
    }
    return map;
  }, [items]);

  const datasets = useMemo(
    () => items.filter((i) => i.type === "SemanticModel" || i.type === "Dataset"),
    [items],
  );
  const reports = useMemo(
    () => items.filter((i) => i.type === "Report" || i.type === "PaginatedReport"),
    [items],
  );

  function groupByFolder<T extends { folderId?: string | null; name: string }>(
    list: T[],
    filter: string,
  ): Array<{ folder: string; items: T[] }> {
    const needle = filter.toLowerCase();
    const visible = needle
      ? list.filter((x) => x.name.toLowerCase().includes(needle))
      : list;
    const byFolder = new Map<string, T[]>();
    for (const it of visible) {
      const key = it.folderId ? folderName.get(it.folderId) || "" : "";
      if (!byFolder.has(key)) byFolder.set(key, []);
      byFolder.get(key)!.push(it);
    }
    const rootItems = byFolder.get("") || [];
    const folderEntries = [...byFolder.entries()]
      .filter(([k]) => k !== "")
      .sort((a, b) => a[0].localeCompare(b[0]));
    const out: Array<{ folder: string; items: T[] }> = [];
    if (rootItems.length) out.push({ folder: "", items: rootItems });
    for (const [folder, its] of folderEntries) out.push({ folder, items: its });
    return out;
  }

  const datasetGroups = useMemo(
    () => groupByFolder(datasets, datasetInput),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [datasets, datasetInput, folderName],
  );
  const reportGroups = useMemo(
    () => groupByFolder(reports, reportInput),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [reports, reportInput, folderName],
  );
  const workspaceGroups = useMemo(() => {
    const needle = workspaceInput.toLowerCase();
    return workspaceInput
      ? workspaces.filter((w) => w.name.toLowerCase().includes(needle))
      : workspaces;
  }, [workspaces, workspaceInput]);

  const acquireToken = useCallback(async () => {
    setTokenLoading(true);
    setTokenError("");
    const fabricScopes = [
      "https://api.fabric.microsoft.com/Workspace.Read.All",
      "https://api.fabric.microsoft.com/Item.Read.All",
      "https://analysis.windows.net/powerbi/api/Dataset.Read.All",
      "https://analysis.windows.net/powerbi/api/Workspace.Read.All",
      "https://analysis.windows.net/powerbi/api/Report.Read.All",
    ];
    try {
      const result = await workloadClient.auth.acquireAccessToken({
        additionalScopesToConsent: fabricScopes,
        claimsForConditionalAccessPolicy: "",
      });
      if (result?.token) {
        setAccessToken(result.token);
      } else {
        setTokenError("No token returned");
      }
    } catch (err) {
      const detail =
        err instanceof Error
          ? `${err.name}: ${err.message}`
          : typeof err === "object"
          ? JSON.stringify(err)
          : String(err);
      // eslint-disable-next-line no-console
      console.error("[PBI Fixer] acquireAccessToken failed", err);
      const cached = sessionStorage.getItem("pbi_fixer_token");
      if (cached) {
        setAccessToken(cached);
      } else {
        setTokenError(detail);
      }
    } finally {
      setTokenLoading(false);
    }
  }, [workloadClient]);

  useEffect(() => {
    acquireToken();
  }, [acquireToken]);

  useEffect(() => {
    if (!accessToken) return;
    let cancelled = false;
    (async () => {
      setWorkspacesLoading(true);
      try {
        const data = await api.getWorkspaces({ githubToken, fabricToken: accessToken });
        if (cancelled) return;
        setWorkspaces(data.workspaces || []);
        const urlWs = new URLSearchParams(window.location.search).get("ws");
        if (urlWs && (data.workspaces || []).some((w) => w.id === urlWs)) {
          setWorkspaceId(urlWs);
          const match = data.workspaces!.find((w) => w.id === urlWs);
          if (match) setWorkspaceInput(match.name);
        }
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn("[PBI Fixer] failed to load workspaces", e);
      } finally {
        if (!cancelled) setWorkspacesLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [accessToken, githubToken]);

  useEffect(() => {
    if (!workspaceId || !accessToken) {
      setItems([]);
      return;
    }
    let cancelled = false;
    (async () => {
      setItemsLoading(true);
      try {
        const resp = await api.listWorkspaceItems(workspaceId, {
          githubToken,
          fabricToken: accessToken,
        });
        if (cancelled) return;
        setItems(resp.items || []);
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn("[PBI Fixer] failed to load workspace items", e);
        if (!cancelled) setItems([]);
      } finally {
        if (!cancelled) setItemsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [workspaceId, accessToken, githubToken]);

  useEffect(() => {
    setDatasetId("");
    setDatasetInput("");
    setReportId("");
    setReportInput("");
  }, [workspaceId]);

  const datasetName = useMemo(
    () => datasets.find((d) => d.id === datasetId)?.name ?? datasetInput,
    [datasets, datasetId, datasetInput],
  );
  const reportName = useMemo(
    () => reports.find((r) => r.id === reportId)?.name ?? reportInput,
    [reports, reportId, reportInput],
  );
  const workspaceName = useMemo(
    () => workspaces.find((w) => w.id === workspaceId)?.name ?? workspaceInput,
    [workspaces, workspaceId, workspaceInput],
  );

  const pageProps: PageProps = {
    auth: { githubToken, fabricToken: accessToken },
    workloadClient,
    workspaceId,
    workspaceName,
    datasetId: datasetId || undefined,
    datasetName,
    reportId: reportId || undefined,
    reportName,
    reportType: reports.find((r) => r.id === reportId)?.type,
    onNavigate: (key: string) => setActiveNav(key as NavKey),
  };

  const renderPage = () => {
    if (!accessToken && !tokenLoading) {
      return (
        <div className={styles.empty}>
          <Text size={400}>
            Authentication required. Click &quot;Refresh Token&quot; to connect.
          </Text>
        </div>
      );
    }
    if (!accessToken) return null;

    const remountKey = `${activeNav}::${workspaceId}::${datasetId}::${reportId}`;
    switch (activeNav) {
      case "model":
        return (
          <ModelExplorer
            key={remountKey}
            auth={pageProps.auth}
            workspace={workspaceId}
            datasetName={datasetName}
            datasetId={datasetId || undefined}
          />
        );
      case "report":
        return (
          <ReportExplorer
            key={remountKey}
            auth={pageProps.auth}
            workspace={workspaceId}
            reportName={reportName}
            reportId={reportId || undefined}
            onNavigateToModel={() => setActiveNav("model")}
          />
        );
      case "fixer":        return <FixerPage        key={remountKey} {...pageProps} />;
      case "modelBpa":     return <ModelBpaPage     key={remountKey} {...pageProps} />;
      case "reportBpa":    return <ReportBpaPage    key={remountKey} {...pageProps} />;
      case "memory":       return <MemoryPage       key={remountKey} {...pageProps} />;
      case "perspectives": return <PerspectivesPage key={remountKey} {...pageProps} />;
      case "translations": return <TranslationsPage key={remountKey} {...pageProps} />;
      case "delta":        return <DeltaPage        key={remountKey} {...pageProps} />;
      case "diagram":      return <DiagramPage      key={remountKey} {...pageProps} />;
      case "scriptRunner": return <ScriptRunnerPage key={remountKey} {...pageProps} />;
      case "prototype":    return <PrototypePage    key={remountKey} {...pageProps} />;
      case "reversePrototype": return <ReversePrototypePage key={remountKey} {...pageProps} />;
      case "sempyRunner":  return <SempyRunnerPage  key={remountKey} {...pageProps} />;
      case "about":        return <AboutPage        key={remountKey} {...pageProps} />;
      default:
        return null;
    }
  };

  // Only one picker should ever be visible at a time — Report-scoped
  // pages show the Report picker, everything else shows the Semantic
  // Model picker. Keeps the connection bar uncluttered and avoids the
  // "which one applies to this page?" ambiguity. Pages that can target
  // either scope (Fixer, Sempy Runner, Script Runner) show both pickers
  // so the user can pick whichever the function/script needs.
  const isReportScoped = activeNav === "report" || activeNav === "reportBpa" || activeNav === "reversePrototype";
  const needsBothPickers = activeNav === "fixer" || activeNav === "sempyRunner" || activeNav === "scriptRunner";
  const showDatasetPicker = needsBothPickers || !isReportScoped;
  const showReportPicker = needsBothPickers || isReportScoped;

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <span className={styles.title}>PBI Fixer</span>
        <span className={styles.version}>{PBI_FIXER_VERSION}</span>
        <div className={styles.headerRight}>
          {tokenLoading && <Spinner size="tiny" />}
          {accessToken && (
            <span className={mergeClasses(styles.tokenStatus, styles.tokenOk)}>
              Authenticated
            </span>
          )}
          {tokenError && (
            <span
              className={mergeClasses(styles.tokenStatus, styles.tokenErr)}
              title={tokenError}
            >
              {tokenError}
            </span>
          )}
          <Button
            appearance="subtle"
            size="small"
            icon={<ArrowSync20Regular />}
            onClick={acquireToken}
            disabled={tokenLoading}
          >
            Refresh Token
          </Button>
        </div>
      </div>

      <div className={styles.connectionBar}>
        <Field label="Workspace" style={{ flex: "0 0 260px" }}>
          <Combobox
            value={workspaceInput}
            selectedOptions={workspaceId ? [workspaceId] : []}
            placeholder={workspacesLoading ? "Loading workspaces…" : "Select a workspace"}
            onOptionSelect={(_, data) => {
              setWorkspaceId(data.optionValue || "");
              setWorkspaceInput(data.optionText || "");
            }}
            onChange={(e) => setWorkspaceInput((e.target as HTMLInputElement).value)}
            disabled={workspacesLoading || !accessToken}
            freeform
          >
            {workspaceGroups.map((w) => (
              <Option key={w.id} value={w.id} text={w.name}>
                {w.name}
              </Option>
            ))}
          </Combobox>
        </Field>

        {showDatasetPicker && (
          <Field label="Semantic Model" style={{ flex: "0 0 260px" }}>
            <Combobox
              key="model-picker"
              value={datasetInput}
              selectedOptions={datasetId ? [datasetId] : []}
              placeholder={
                !workspaceId
                  ? "Pick a workspace first"
                  : itemsLoading
                  ? "Loading…"
                  : datasets.length
                  ? "Select a semantic model"
                  : "No semantic models found"
              }
              onOptionSelect={(_, data) => {
                setDatasetId(data.optionValue || "");
                setDatasetInput(data.optionText || "");
              }}
              onChange={(e) => setDatasetInput((e.target as HTMLInputElement).value)}
              disabled={!workspaceId || itemsLoading}
              freeform
            >
              {datasetGroups.length === 1 && datasetGroups[0].folder === ""
                ? datasetGroups[0].items.map((d) => (
                    <Option key={d.id} value={d.id} text={d.name}>
                      {d.name}
                    </Option>
                  ))
                : datasetGroups.map((g) => (
                    <OptionGroup key={g.folder || "__root"} label={g.folder || "Root"}>
                      {g.items.map((d) => (
                        <Option key={d.id} value={d.id} text={d.name}>
                          {d.name}
                        </Option>
                      ))}
                    </OptionGroup>
                  ))}
            </Combobox>
          </Field>
        )}

        {showReportPicker && (
          <Field label="Report" style={{ flex: "0 0 260px" }}>
            <Combobox
              key="report-picker"
              value={reportInput}
              selectedOptions={reportId ? [reportId] : []}
              placeholder={
                !workspaceId
                  ? "Pick a workspace first"
                  : itemsLoading
                  ? "Loading…"
                  : reports.length
                  ? "Select a report"
                  : "No reports found"
              }
              onOptionSelect={(_, data) => {
                setReportId(data.optionValue || "");
                setReportInput(data.optionText || "");
              }}
              onChange={(e) => setReportInput((e.target as HTMLInputElement).value)}
              disabled={!workspaceId || itemsLoading}
              freeform
            >
              {reportGroups.length === 1 && reportGroups[0].folder === ""
                ? reportGroups[0].items.map((r) => (
                    <Option key={r.id} value={r.id} text={r.name}>
                      {r.name}
                    </Option>
                  ))
                : reportGroups.map((g) => (
                    <OptionGroup key={g.folder || "__root"} label={g.folder || "Root"}>
                      {g.items.map((r) => (
                        <Option key={r.id} value={r.id} text={r.name}>
                          {r.name}
                        </Option>
                      ))}
                    </OptionGroup>
                  ))}
            </Combobox>
          </Field>
        )}
      </div>

      <div className={styles.body}>
        <div className={styles.content}>
          {/* WS-O Phase 2.2: opacity crossfade on activeNav change — the
              `key` swap remounts the wrapper so the animation re-fires. */}
          <div key={activeNav} className={styles.contentFade}>{renderPage()}</div>
        </div>
      </div>
    </div>
  );
};
