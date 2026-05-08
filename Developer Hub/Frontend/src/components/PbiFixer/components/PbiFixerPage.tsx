// PbiFixer shell — WS-A flat-tree navigation.
// Left column hosts the nav; right column is a content slot. The
// connection bar stays above both so workspace / dataset / report
// selections survive page switches.
//
// Page components are chosen by `activeNav` and remounted (via `key`)
// whenever workspace or the selection relevant to that page changes.

import React, { useState, useCallback, useEffect, useMemo, useRef } from "react";
import {
  Combobox,
  Option,
  OptionGroup,
  Field,
  Button,
  Checkbox,
  Spinner,
  Text,
  makeStyles,
  mergeClasses,
  shorthands,
  tokens,
} from "@fluentui/react-components";
import { ArrowSync20Regular, Play20Regular } from "@fluentui/react-icons";
import { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { ModelExplorer } from "./ModelExplorer";
import { StackedSection } from "./common/StackedSection";
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
  PrototypePage,
  ReversePrototypePage,
  SempyRunnerPage,
} from "./pages";
import { DEFAULT_NAV_KEY, NavKey } from "../types/nav";
import type { PageProps } from "../types/shared";
import * as api from "../../../controller/AgentHubApi";
import { getFabricTokenCached } from "../../../controller/AgentHubController";
import { PBI_FIXER_VERSION } from "../utils/version";

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
    height: "36px",
    flexShrink: 0,
    backgroundColor: "#faf9f8",
    borderBottomStyle: "solid",
    borderBottomWidth: "1px",
    borderBottomColor: "rgba(192, 199, 212, 0.1)",
  },
  title: {
    fontSize: tokens.fontSizeBase400,
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorNeutralForeground1,
  },
  // Version pill — matches the AgentHub AboutPage badge style so all
  // version badges across the hub read as siblings (12 px monospace,
  // neutral foreground 2, hairline border on neutral background 3).
  version: {
    fontSize: "12px",
    color: tokens.colorNeutralForeground2,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    ...shorthands.padding("2px", "6px"),
    ...shorthands.border("1px", "solid", "rgba(192, 199, 212, 0.4)"),
    ...shorthands.borderRadius("4px"),
    backgroundColor: tokens.colorNeutralBackground3,
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
    ...shorthands.padding("8px", "24px", "24px", "24px"),
    backgroundColor: "#faf9f8",
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
  // URL ``?nav=`` query is the single source of truth so each editor
  // tab pins its own PBI Fixer sub-page (Model / Report / …) and
  // browser back/forward navigates between them. WS-O Decision #6
  // dropped the legacy ``sessionStorage["pbiFixer.activeNav"]`` fallback.
  try {
    if (typeof window !== "undefined") {
      const sp = new URLSearchParams(window.location.search);
      const fromUrl = sp.get("nav");
      if (fromUrl) {
        // Iframe bootstrap URL can nest a second ``?…`` inside the
        // nav value (e.g. ``nav=report?experience=fabric-developer``).
        // Strip it down to the first alphanumeric token.
        const m = fromUrl.match(/^[A-Za-z0-9_-]+/);
        if (m) {
          // v0.102 — Model BPA + Memory Analyzer + Report BPA were
          // consolidated into the Model / Report Explorer "Scan" panel.
          // Re-route any cached deep-links so they don't 404.
          const raw = m[0];
          if (raw === "modelBpa" || raw === "memory") return "model" as NavKey;
          if (raw === "reportBpa") return "report" as NavKey;
          return raw as NavKey;
        }
      }
    }
  } catch { /* ignore */ }
  return DEFAULT_NAV_KEY;
}

export const PbiFixerPage: React.FC<PbiFixerPageProps> = ({
  workloadClient,
  initialNav,
}) => {
  const styles = useStyles();
  const [activeNav, setActiveNav] = useState<NavKey>(
    () => initialNav ?? readNavKey()
  );

  // T7: persist the connection bar selection in sessionStorage so a
  // new PBI Fixer sub-tab (Model, Report, Model BPA, …) inherits the
  // workspace + dataset + report from the previously-active sub-tab
  // instead of forcing the user to re-pick everything every time.
  // Each PBI Fixer tab spawns its own PbiFixerPage instance; without
  // this the connection bar starts empty in every tab.
  const PBIFIXER_CONN_STORAGE_KEY = "pbiFixer.connection.v1";
  const PBIFIXER_MULTI_STORAGE_KEY = "pbiFixer.multiMode.v1";
  type PersistedConnection = {
    workspaceId: string;
    workspaceInput: string;
    datasetId: string;
    datasetInput: string;
    reportId: string;
    reportInput: string;
    /** v0.93+: arrays of every selected id/name in Multi mode. Optional
     *  for backwards compatibility with v0.92 sessionStorage payloads. */
    datasetIds?: string[];
    datasetNames?: string[];
    reportIds?: string[];
    reportNames?: string[];
  };
  const readPersistedConn = (): Partial<PersistedConnection> => {
    const tryRead = (store: Storage | undefined): Partial<PersistedConnection> | null => {
      try {
        if (!store) return null;
        const raw = store.getItem(PBIFIXER_CONN_STORAGE_KEY);
        if (!raw) return null;
        const obj = JSON.parse(raw);
        if (obj && typeof obj === "object") return obj as PersistedConnection;
      } catch { /* ignore parse / quota errors */ }
      return null;
    };
    // v0.93+: each PBI Fixer sub-tab is its own iframe with isolated
    // ``sessionStorage`` (the workload-client mounts each tab in a
    // fresh browsing context). To propagate selections across tabs we
    // mirror to ``localStorage`` (shared across all same-origin
    // iframes) and prefer it on read; sessionStorage stays as a
    // secondary fallback for the in-tab case.
    return tryRead(typeof window !== "undefined" ? window.localStorage : undefined)
      ?? tryRead(typeof window !== "undefined" ? window.sessionStorage : undefined)
      ?? {};
  };
  const persistedConn = readPersistedConn();

  // Connection / selection state.
  const [workspaceId, setWorkspaceId] = useState(persistedConn.workspaceId ?? "");
  const [datasetId, setDatasetId] = useState(persistedConn.datasetId ?? "");
  const [reportId, setReportId] = useState(persistedConn.reportId ?? "");
  const [workspaceInput, setWorkspaceInput] = useState(persistedConn.workspaceInput ?? "");
  const [datasetInput, setDatasetInput] = useState(persistedConn.datasetInput ?? "");
  const [reportInput, setReportInput] = useState(persistedConn.reportInput ?? "");

  // v0.93 Multi mode — when ON the user can pick multiple datasets and/or
  // reports. The Combobox swaps to multiselect, an Apply button appears
  // in the connection bar, and pages render stacked sections (one per
  // loaded item). When OFF (default) behaviour is identical to v0.92:
  // single selection, auto-load on selection change, no Apply button.
  const readPersistedMulti = (): boolean => {
    try {
      return sessionStorage.getItem(PBIFIXER_MULTI_STORAGE_KEY) === "1";
    } catch { return false; }
  };
  const [multiMode, setMultiMode] = useState<boolean>(readPersistedMulti);
  // Pending (= staged in the picker) and committed (= last Apply) lists.
  // In single mode pending and committed are kept in lockstep with the
  // primary scalar id so existing pages keep working unchanged.
  const seedIds = (single: string | undefined, arr: string[] | undefined): string[] => {
    if (arr && arr.length) return arr;
    return single ? [single] : [];
  };
  const seedNames = (single: string | undefined, arr: string[] | undefined): string[] => {
    if (arr && arr.length) return arr;
    return single ? [single] : [];
  };
  const [pendingDatasetIds, setPendingDatasetIds] = useState<string[]>(
    () => seedIds(persistedConn.datasetId, persistedConn.datasetIds),
  );
  const [pendingDatasetNames, setPendingDatasetNames] = useState<string[]>(
    () => seedNames(persistedConn.datasetInput, persistedConn.datasetNames),
  );
  const [pendingReportIds, setPendingReportIds] = useState<string[]>(
    () => seedIds(persistedConn.reportId, persistedConn.reportIds),
  );
  const [pendingReportNames, setPendingReportNames] = useState<string[]>(
    () => seedNames(persistedConn.reportInput, persistedConn.reportNames),
  );
  // Committed = the list pages actually see. Bumped on Apply (multi) or
  // immediately on selection change (single). Starts equal to pending.
  const [committedDatasetIds, setCommittedDatasetIds] = useState<string[]>(pendingDatasetIds);
  const [committedDatasetNames, setCommittedDatasetNames] = useState<string[]>(pendingDatasetNames);
  const [committedReportIds, setCommittedReportIds] = useState<string[]>(pendingReportIds);
  const [committedReportNames, setCommittedReportNames] = useState<string[]>(pendingReportNames);
  const [commitToken, setCommitToken] = useState(0);
  // True when Multi-mode pending differs from committed → Apply is "dirty".
  const arrayEq = (a: string[], b: string[]): boolean =>
    a.length === b.length && a.every((v, i) => v === b[i]);
  const pendingDirty = !arrayEq(pendingDatasetIds, committedDatasetIds)
    || !arrayEq(pendingReportIds, committedReportIds);

  // SINGLE mode: keep pending/committed locked to the scalar primary
  // selection. Selection-change handlers update the scalars; this effect
  // mirrors them into the arrays so pages always see consistent props.
  useEffect(() => {
    if (multiMode) return;
    const dsIds = datasetId ? [datasetId] : [];
    const dsNames = datasetId ? [datasetInput] : [];
    const rpIds = reportId ? [reportId] : [];
    const rpNames = reportId ? [reportInput] : [];
    setPendingDatasetIds(dsIds);
    setPendingDatasetNames(dsNames);
    setPendingReportIds(rpIds);
    setPendingReportNames(rpNames);
    setCommittedDatasetIds(dsIds);
    setCommittedDatasetNames(dsNames);
    setCommittedReportIds(rpIds);
    setCommittedReportNames(rpNames);
    setCommitToken((t) => t + 1);
  }, [multiMode, datasetId, datasetInput, reportId, reportInput]);

  // MULTI mode: keep the scalar primary id pointing at the FIRST committed
  // entry so pages that haven't migrated yet still receive a usable
  // datasetId / reportId. Pure mirror — does not bump commitToken.
  useEffect(() => {
    if (!multiMode) return;
    const firstDsId = committedDatasetIds[0] ?? "";
    const firstDsName = committedDatasetNames[0] ?? "";
    const firstRpId = committedReportIds[0] ?? "";
    const firstRpName = committedReportNames[0] ?? "";
    if (firstDsId !== datasetId) setDatasetId(firstDsId);
    if (firstDsName !== datasetInput) setDatasetInput(firstDsName);
    if (firstRpId !== reportId) setReportId(firstRpId);
    if (firstRpName !== reportInput) setReportInput(firstRpName);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [multiMode, committedDatasetIds, committedDatasetNames, committedReportIds, committedReportNames]);

  const applyMulti = useCallback(() => {
    setCommittedDatasetIds(pendingDatasetIds);
    setCommittedDatasetNames(pendingDatasetNames);
    setCommittedReportIds(pendingReportIds);
    setCommittedReportNames(pendingReportNames);
    setCommitToken((t) => t + 1);
  }, [pendingDatasetIds, pendingDatasetNames, pendingReportIds, pendingReportNames]);

  // Persist Multi mode separately from the connection blob so toggling it
  // while everything else is empty still survives a reload.
  useEffect(() => {
    try {
      sessionStorage.setItem(PBIFIXER_MULTI_STORAGE_KEY, multiMode ? "1" : "0");
    } catch { /* ignore */ }
  }, [multiMode]);

  // Persist on every change so the next sub-tab to mount picks it up.
  // v0.96: write to both localStorage (cross-iframe) and sessionStorage
  // (back-compat). Two anti-clobber guards:
  //   1) skip if our state is fully empty (initial mount race);
  //   2) merge with whatever is already in localStorage — never write
  //      an EMPTY field over an EXISTING non-empty one. This protects
  //      against the v0.95 failure where the destination tab cleared
  //      datasets/reports on its first workspaceId-change effect run
  //      and then the persist effect wiped the source tab's value.
  useEffect(() => {
    const allEmpty =
      !workspaceId && !datasetId && !reportId &&
      !workspaceInput && !datasetInput && !reportInput &&
      !committedDatasetIds.length && !committedReportIds.length;
    if (allEmpty) return;
    let existing: Partial<PersistedConnection> = {};
    try {
      const raw = window.localStorage.getItem(PBIFIXER_CONN_STORAGE_KEY);
      if (raw) existing = JSON.parse(raw) as PersistedConnection;
    } catch { /* ignore */ }
    const merged = {
      workspaceId: workspaceId || existing.workspaceId || "",
      workspaceInput: workspaceInput || existing.workspaceInput || "",
      datasetId: datasetId || existing.datasetId || "",
      datasetInput: datasetInput || existing.datasetInput || "",
      reportId: reportId || existing.reportId || "",
      reportInput: reportInput || existing.reportInput || "",
      datasetIds: committedDatasetIds.length ? committedDatasetIds : (existing.datasetIds || []),
      datasetNames: committedDatasetNames.length ? committedDatasetNames : (existing.datasetNames || []),
      reportIds: committedReportIds.length ? committedReportIds : (existing.reportIds || []),
      reportNames: committedReportNames.length ? committedReportNames : (existing.reportNames || []),
    };
    const payload = JSON.stringify(merged);
    try { window.localStorage.setItem(PBIFIXER_CONN_STORAGE_KEY, payload); } catch { /* ignore */ }
    try { window.sessionStorage.setItem(PBIFIXER_CONN_STORAGE_KEY, payload); } catch { /* ignore */ }
  }, [
    workspaceId, workspaceInput, datasetId, datasetInput, reportId, reportInput,
    committedDatasetIds, committedDatasetNames, committedReportIds, committedReportNames,
  ]);

  // v0.93: cross-iframe propagation — listen for ``storage`` events so
  // a tab that was already mounted when a sibling persisted a selection
  // updates its own pickers. The event only fires in OTHER documents
  // (never the one that wrote), which is exactly what we want. Only
  // adopt fields that are still empty in THIS tab so we never yank a
  // value the user is actively editing.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== PBIFIXER_CONN_STORAGE_KEY || !e.newValue) return;
      try {
        const obj = JSON.parse(e.newValue) as PersistedConnection;
        setWorkspaceId((cur) => cur || obj.workspaceId || "");
        setWorkspaceInput((cur) => cur || obj.workspaceInput || "");
        setDatasetId((cur) => cur || obj.datasetId || "");
        setDatasetInput((cur) => cur || obj.datasetInput || "");
        setReportId((cur) => cur || obj.reportId || "");
        setReportInput((cur) => cur || obj.reportInput || "");
      } catch { /* ignore malformed payload */ }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const [workspaces, setWorkspaces] = useState<api.Workspace[]>([]);
  const [workspacesLoading, setWorkspacesLoading] = useState(false);
  const [items, setItems] = useState<api.WorkspaceItem[]>([]);
  const [itemsLoading, setItemsLoading] = useState(false);

  const [accessToken, setAccessToken] = useState("");
  const [tokenLoading, setTokenLoading] = useState(false);
  const [tokenError, setTokenError] = useState("");

  const githubToken = sessionStorage.getItem("github_token") || "";

  // WS-O #6: URL ``?nav=`` is the single source of truth. Listen for
  // browser back/forward (popstate) so navigating history updates the
  // active sub-page. The host shell still updates the URL on sub-nav
  // clicks (via the Fabric tabs API), and we react to that here too.
  useEffect(() => {
    const sync = () => setActiveNav(readNavKey());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

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

  // Merged Semantic Model + Report picker — used by pages that target
  // either scope (Fixer, Sempy Runner, Script Runner). A semantic model
  // and a report typically share the same name in the same folder, and
  // most sempy / script functions accept either scope, so collapsing
  // both pickers into one keeps the connection bar uncluttered. We
  // dedupe by `<folderId>|<name>` and remember whichever ids match.
  const [pairInput, setPairInput] = useState("");
  type PairItem = { name: string; folderId?: string | null; datasetId?: string; reportId?: string };
  const pairItems = useMemo<PairItem[]>(() => {
    const map = new Map<string, PairItem>();
    const keyOf = (folderId: string | null | undefined, name: string) => `${folderId ?? ""}|${name}`;
    for (const d of datasets) {
      const k = keyOf(d.folderId, d.name);
      map.set(k, { name: d.name, folderId: d.folderId, datasetId: d.id });
    }
    for (const r of reports) {
      const k = keyOf(r.folderId, r.name);
      const cur = map.get(k);
      if (cur) cur.reportId = r.id;
      else map.set(k, { name: r.name, folderId: r.folderId, reportId: r.id });
    }
    return [...map.values()];
  }, [datasets, reports]);
  const pairKey = (p: PairItem) => `${p.folderId ?? ""}|${p.name}|${p.datasetId ?? ""}|${p.reportId ?? ""}`;
  const pairGroups = useMemo(
    () => groupByFolder(pairItems, pairInput),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pairItems, pairInput, folderName],
  );
  // Prefer dataset name when both ids are set (they normally match anyway).
  const selectedPair = useMemo<PairItem | undefined>(() => {
    if (!datasetId && !reportId) return undefined;
    return pairItems.find((p) => (datasetId && p.datasetId === datasetId) || (reportId && p.reportId === reportId));
  }, [pairItems, datasetId, reportId]);
  // Keep `pairInput` in sync with whichever scope-specific input changes.
  useEffect(() => {
    if (selectedPair) setPairInput(selectedPair.name);
  }, [selectedPair]);
  const workspaceGroups = useMemo(() => {
    const needle = workspaceInput.toLowerCase();
    return workspaceInput
      ? workspaces.filter((w) => w.name.toLowerCase().includes(needle))
      : workspaces;
  }, [workspaces, workspaceInput]);

  const acquireToken = useCallback(async () => {
    setTokenLoading(true);
    setTokenError("");
    try {
      const token = await getFabricTokenCached(workloadClient);
      if (token) {
        setAccessToken(token);
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

  // v0.96: this effect clears the dataset/report selection when the
  // user CHANGES workspace. It must NOT run on initial mount, otherwise
  // it wipes the dataset/report ids that were just hydrated from
  // localStorage when the destination sub-tab mounts (root cause of
  // the v0.95 test failure: switching from Model to Report sub-page
  // mounted the Report iframe with both ids hydrated, then this effect
  // immediately fired with workspaceId="Demo" and cleared them, then
  // the persist effect wrote the empty payload to localStorage, also
  // wiping the source tab's value).
  const lastWorkspaceIdRef = useRef<string>(workspaceId);
  useEffect(() => {
    if (lastWorkspaceIdRef.current === workspaceId) {
      // First run on mount, or workspaceId did not actually change.
      lastWorkspaceIdRef.current = workspaceId;
      return;
    }
    lastWorkspaceIdRef.current = workspaceId;
    setDatasetId("");
    setDatasetInput("");
    setReportId("");
    setReportInput("");
    setPendingDatasetIds([]);
    setPendingDatasetNames([]);
    setPendingReportIds([]);
    setPendingReportNames([]);
    setCommittedDatasetIds([]);
    setCommittedDatasetNames([]);
    setCommittedReportIds([]);
    setCommittedReportNames([]);
  }, [workspaceId]);

  // v0.93: auto-pair must NOT fire when the user is in Multi mode — they
  // are explicitly picking multiple items and a magic counterpart pick
  // would be surprising and would clobber their staged selection.
  useEffect(() => {
    if (multiMode) return;
    if (itemsLoading) return;
    if (datasetId && !reportId) {
      const pair = pairItems.find((p) => p.datasetId === datasetId);
      if (pair && pair.reportId) {
        setReportId(pair.reportId);
        setReportInput(pair.name);
      }
    } else if (reportId && !datasetId) {
      const pair = pairItems.find((p) => p.reportId === reportId);
      if (pair && pair.datasetId) {
        setDatasetId(pair.datasetId);
        setDatasetInput(pair.name);
      }
    }
  }, [pairItems, itemsLoading, datasetId, reportId]);

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
    datasetIds: committedDatasetIds,
    datasetNames: committedDatasetNames,
    reportIds: committedReportIds,
    reportNames: committedReportNames,
    multiMode,
    commitToken,
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

    const remountKey = `${activeNav}::${workspaceId}::${datasetId}::${reportId}::${commitToken}`;

    // v0.95: Multi mode for SM Explorer / Report Explorer.
    //
    // When Multi is ON and the user has committed >=1 selections via Apply,
    // render one StackedSection per committed entry, each holding its own
    // independent <ModelExplorer> / <ReportExplorer> instance with its own
    // load state. The shared picker (connectionSlot) is hoisted to the top
    // so it isn't duplicated inside every section.
    if (multiMode && activeNav === "model") {
      const ids = committedDatasetIds;
      const names = committedDatasetNames;
      if (ids.length === 0) {
        return (
          <div>
            <div className={styles.connectionBar}>{pickerFields}</div>
            <div className={styles.empty}>
              <Text size={400}>
                Multi mode is on. Pick one or more semantic models above and click Apply.
              </Text>
            </div>
          </div>
        );
      }
      return (
        <div>
          <div className={styles.connectionBar}>{pickerFields}</div>
          {names.map((name, i) => (
            <StackedSection
              key={`${ids[i] || name}::${commitToken}`}
              title={name}
              defaultExpanded={i === 0}
            >
              <ModelExplorer
                auth={pageProps.auth}
                workspace={workspaceId}
                datasetName={name}
                datasetId={ids[i] || undefined}
                version={PBI_FIXER_VERSION}
                onNavigate={(k) => setActiveNav(k as NavKey)}
              />
            </StackedSection>
          ))}
        </div>
      );
    }
    if (multiMode && activeNav === "report") {
      const ids = committedReportIds;
      const names = committedReportNames;
      if (ids.length === 0) {
        return (
          <div>
            <div className={styles.connectionBar}>{pickerFields}</div>
            <div className={styles.empty}>
              <Text size={400}>
                Multi mode is on. Pick one or more reports above and click Apply.
              </Text>
            </div>
          </div>
        );
      }
      return (
        <div>
          <div className={styles.connectionBar}>{pickerFields}</div>
          {names.map((name, i) => (
            <StackedSection
              key={`${ids[i] || name}::${commitToken}`}
              title={name}
              defaultExpanded={i === 0}
            >
              <ReportExplorer
                auth={pageProps.auth}
                workspace={workspaceId}
                reportName={name}
                reportId={ids[i] || undefined}
                onNavigateToModel={() => setActiveNav("model")}
                version={PBI_FIXER_VERSION}
                onNavigate={(k) => setActiveNav(k as NavKey)}
              />
            </StackedSection>
          ))}
        </div>
      );
    }

    switch (activeNav) {
      case "model":
        return (
          <ModelExplorer
            key={remountKey}
            auth={pageProps.auth}
            workspace={workspaceId}
            datasetName={datasetName}
            datasetId={datasetId || undefined}
            connectionSlot={pickerFields}
            version={PBI_FIXER_VERSION}
            onNavigate={(k) => setActiveNav(k as NavKey)}
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
            connectionSlot={pickerFields}
            version={PBI_FIXER_VERSION}
            onNavigate={(k) => setActiveNav(k as NavKey)}
          />
        );
      case "fixer":        return <FixerPage        key={remountKey} {...pageProps} />;
      // v0.102 — Model BPA / Memory Analyzer / Report BPA were folded
      // into the Model / Report Explorer "Scan" panels. The standalone
      // pages remain as redirect stubs so cached deep-links land on the
      // correct explorer tab instead of crashing.
      case "modelBpa":     return <ModelBpaPage     key={remountKey} {...pageProps} />;
      case "reportBpa":    return <ReportBpaPage    key={remountKey} {...pageProps} />;
      case "memory":       return <MemoryPage       key={remountKey} {...pageProps} />;
      case "perspectives": return <PerspectivesPage key={remountKey} {...pageProps} />;
      case "translations": return <TranslationsPage key={remountKey} {...pageProps} />;
      case "delta":        return <DeltaPage        key={remountKey} {...pageProps} />;
      case "diagram":      return <DiagramPage      key={remountKey} {...pageProps} />;
      case "prototype":    return <PrototypePage    key={remountKey} {...pageProps} />;
      case "reversePrototype": return <ReversePrototypePage key={remountKey} {...pageProps} />;
      case "sempyRunner":  return <SempyRunnerPage  key={remountKey} {...pageProps} />;
      default:
        return null;
    }
  };

  // Only one picker should ever be visible at a time — Report-scoped
  // pages show the Report picker, everything else shows the Semantic
  // Model picker. Pages that can target either scope (Fixer, Sempy
  // Runner, Script Runner) show a SINGLE merged "Semantic Model /
  // Report" picker — most functions accept either and the two items
  // typically share the same name in the same folder.
  const isReportScoped = activeNav === "report" || activeNav === "reportBpa" || activeNav === "reversePrototype";
  const needsBothPickers = activeNav === "fixer" || activeNav === "sempyRunner";
  const showDatasetPicker = !needsBothPickers && !isReportScoped;
  const showReportPicker = !needsBothPickers && isReportScoped;
  const showPairPicker = needsBothPickers;

  // v0.89: Workspace + Semantic Model / Report pickers used to live in
  // the page-level chrome bar above every PBI Fixer sub-page. For the
  // Model Explorer and Report Explorer pages the picker now renders
  // INLINE inside the page (between the description text and the Load
  // Model / Load Report toolbar) — extracted as a JSX node so the
  // connection state stays owned by `PbiFixerPage` and survives sub-tab
  // switches. Other pages keep the picker in the chrome bar.
  const inlinePickerActive = activeNav === "model" || activeNav === "report";

  const pickerFields = (
    <>
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

      {showPairPicker && (
        <Field label="Semantic Model / Report" style={{ flex: "0 0 320px" }}>
          <Combobox
            key="pair-picker"
            value={pairInput}
            selectedOptions={selectedPair ? [pairKey(selectedPair)] : []}
            placeholder={
              !workspaceId
                ? "Pick a workspace first"
                : itemsLoading
                ? "Loading…"
                : pairItems.length
                ? "Select a semantic model or report"
                : "No semantic models or reports found"
            }
            onOptionSelect={(_, data) => {
              const k = data.optionValue || "";
              const found = pairItems.find((p) => pairKey(p) === k);
              if (found) {
                setDatasetId(found.datasetId || "");
                setDatasetInput(found.datasetId ? found.name : "");
                setReportId(found.reportId || "");
                setReportInput(found.reportId ? found.name : "");
                setPairInput(found.name);
              } else {
                setPairInput(data.optionText || "");
              }
            }}
            onChange={(e) => setPairInput((e.target as HTMLInputElement).value)}
            disabled={!workspaceId || itemsLoading}
            freeform
          >
            {pairGroups.length === 1 && pairGroups[0].folder === ""
              ? pairGroups[0].items.map((p) => (
                  <Option key={pairKey(p)} value={pairKey(p)} text={p.name}>
                    {p.name}
                    {p.datasetId && p.reportId
                      ? ""
                      : p.datasetId
                      ? " · model only"
                      : " · report only"}
                  </Option>
                ))
              : pairGroups.map((g) => (
                  <OptionGroup key={g.folder || "__root"} label={g.folder || "Root"}>
                    {g.items.map((p) => (
                      <Option key={pairKey(p)} value={pairKey(p)} text={p.name}>
                        {p.name}
                        {p.datasetId && p.reportId
                          ? ""
                          : p.datasetId
                          ? " · model only"
                          : " · report only"}
                      </Option>
                    ))}
                  </OptionGroup>
                ))}
          </Combobox>
        </Field>
      )}

      {showDatasetPicker && (
        <Field label={multiMode ? "Semantic Models" : "Semantic Model"} style={{ flex: "0 0 260px" }}>
          <Combobox
            key={multiMode ? "model-picker-multi" : "model-picker"}
            multiselect={multiMode || undefined}
            value={multiMode
              ? (pendingDatasetNames.join(", "))
              : datasetInput}
            selectedOptions={multiMode
              ? pendingDatasetIds
              : (datasetId ? [datasetId] : [])}
            placeholder={
              !workspaceId
                ? "Pick a workspace first"
                : itemsLoading
                ? "Loading…"
                : datasets.length
                ? (multiMode ? "Pick one or more semantic models" : "Select a semantic model")
                : "No semantic models found"
            }
            onOptionSelect={(_, data) => {
              if (multiMode) {
                const ids = data.selectedOptions || [];
                const names = ids.map((id) => {
                  const found = datasets.find((d) => d.id === id);
                  return found?.name ?? "";
                }).filter(Boolean);
                setPendingDatasetIds(ids);
                setPendingDatasetNames(names);
                return;
              }
              const id = data.optionValue || "";
              const name = data.optionText || "";
              setDatasetId(id);
              setDatasetInput(name);
              const pair = pairItems.find((p) => p.datasetId === id);
              if (pair && pair.reportId) {
                setReportId(pair.reportId);
                setReportInput(pair.name);
              }
            }}
            onChange={(e) => {
              if (multiMode) return;
              setDatasetInput((e.target as HTMLInputElement).value);
            }}
            disabled={!workspaceId || itemsLoading}
            freeform={!multiMode}
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
        <Field label={multiMode ? "Reports" : "Report"} style={{ flex: "0 0 260px" }}>
          <Combobox
            key={multiMode ? "report-picker-multi" : "report-picker"}
            multiselect={multiMode || undefined}
            value={multiMode
              ? (pendingReportNames.join(", "))
              : reportInput}
            selectedOptions={multiMode
              ? pendingReportIds
              : (reportId ? [reportId] : [])}
            placeholder={
              !workspaceId
                ? "Pick a workspace first"
                : itemsLoading
                ? "Loading…"
                : reports.length
                ? (multiMode ? "Pick one or more reports" : "Select a report")
                : "No reports found"
            }
            onOptionSelect={(_, data) => {
              if (multiMode) {
                const ids = data.selectedOptions || [];
                const names = ids.map((id) => {
                  const found = reports.find((r) => r.id === id);
                  return found?.name ?? "";
                }).filter(Boolean);
                setPendingReportIds(ids);
                setPendingReportNames(names);
                return;
              }
              const id = data.optionValue || "";
              const name = data.optionText || "";
              setReportId(id);
              setReportInput(name);
              const pair = pairItems.find((p) => p.reportId === id);
              if (pair && pair.datasetId) {
                setDatasetId(pair.datasetId);
                setDatasetInput(pair.name);
              }
            }}
            onChange={(e) => {
              if (multiMode) return;
              setReportInput((e.target as HTMLInputElement).value);
            }}
            disabled={!workspaceId || itemsLoading}
            freeform={!multiMode}
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
      {multiMode && (
        <Field label="\u00A0" style={{ flex: "0 0 auto" }}>
          <Button
            appearance="primary"
            size="medium"
            icon={<Play20Regular />}
            onClick={applyMulti}
            disabled={!workspaceId || itemsLoading || (!pendingDirty && commitToken > 0)}
            title={pendingDirty
              ? "Apply the staged selection — pages will (re)load each item"
              : "Selection unchanged since last Apply"}
          >
            Apply
          </Button>
        </Field>
      )}
      <Field label="\u00A0" style={{ flex: "0 0 auto" }}>
        <Checkbox
          label="Multi"
          checked={multiMode}
          onChange={(_, data) => setMultiMode(!!data.checked)}
          title="Pick multiple semantic models / reports and load them all at once"
        />
      </Field>
    </>
  );

  return (
    <div className={styles.root}>
      <div className={styles.header}>
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

      {!inlinePickerActive && (
        <div className={styles.connectionBar}>{pickerFields}</div>
      )}

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
