import React, { useEffect, useMemo, useState } from "react";
import {
    Spinner,
} from "@fluentui/react-components";
import {
    PeopleTeam20Regular,
    Dismiss24Regular,
    Folder20Filled,
    ArrowClockwise16Regular,
    ChevronRight16Regular,
    Open20Regular,
} from "@fluentui/react-icons";
// Official Fabric item icons (MIT-licensed, published by Microsoft as
// ``@fabric-msft/svg-icons`` specifically for workload extensions).
// These render the exact coloured glyphs Fabric uses in its own portal.
import {
    Apps20Item,
    CopyJob20Item,
    Dashboard20Item,
    DataAgent20Item,
    DataFactory20Item,
    DataWarehouse20Item,
    Dataflow20Item,
    DataflowGen220Item,
    Datamart20Item,
    Environment20Item,
    EventHouse20Item,
    Eventstream20Item,
    Experiments20Item,
    Exploration20Item,
    FunctionSet20Item,
    GenericPlaceholder20Item,
    KqlDatabase20Item,
    KqlQueryset20Item,
    KqlScript20Item,
    Lakehouse20Item,
    MetricSets20Item,
    MirroredGenericDatabase20Item,
    MobileReport20Item,
    Model20Item,
    Notebook20Item,
    OperationsAgent20Item,
    PaginatedReport20Item,
    Pipeline20Item,
    RdlReport20Item,
    RealTimeDashboard20Item,
    Report20Item,
    SchemaModel20Item,
    Scorecard20Item,
    SemanticModel20Item,
    SparkJobDirection20Item,
    SqlDatabase20Item,
    UserDataFunction20Item,
    VariableLibrary20Item,
} from "@fabric-msft/svg-icons";
import type { WorkloadClientAPI } from "@ms-fabric/workload-client";
import { openExternalTab } from "./openExternalTab";
import type { WorkspaceItem } from "../../controller/AgentHubApi";

/**
 * Resolve a Fabric item ``type`` string to a Fluent icon + a CSS class
 * suffix used for colour. The CSS suffix drives the
 * ``.workspace-preview-icon--<kind>`` rule in ``styles.scss`` so each
 * item class has a colour close to Fabric's own portal palette.
 *
 * Types come straight from Fabric's ``/items`` REST response, which uses
 * PascalCase like ``Lakehouse``, ``SemanticModel``, ``KustoDatabase``,
 * ``DataflowGen2`` etc. Matching is case-insensitive so new synonyms
 * (e.g. ``DataflowFabric``) land on the same bucket without code changes.
 */
type IconPick = { kind: string; Icon: React.ComponentType };
function iconFor(rawType: string): IconPick {
    const t = (rawType || "").toLowerCase();
    if (t.includes("lakehouse"))       return { kind: "lakehouse", Icon: Lakehouse20Item };
    if (t.includes("warehouse"))       return { kind: "warehouse", Icon: DataWarehouse20Item };
    // ``SQLEndpoint`` / ``SQLAnalyticsEndpoint`` is Fabric's auto-
    // generated read endpoint for data stores. The Fabric icon pack
    // doesn't ship a dedicated ``sql_analytics_endpoint_item`` SVG —
    // the portal reuses ``sql_database_20_item`` (coloured cylinder-on-
    // gradient-square) for both SQL Database items and SQL analytics
    // endpoints.
    if (t === "sqlendpoint" || t === "sqlanalyticsendpoint" || t.includes("sqlanalytics"))
        return { kind: "sqlendpoint", Icon: SqlDatabase20Item };
    if (t.includes("sqldb") || t.includes("sqldatabase") || t.includes("pgsql"))
        return { kind: "sqldb", Icon: SqlDatabase20Item };
    if (t.includes("mirrored") || t.includes("dataversemirror"))
        return { kind: "mirrored", Icon: MirroredGenericDatabase20Item };
    if (t.includes("kustodatabase") || t.includes("kqldatabase"))
        return { kind: "kqldatabase", Icon: KqlDatabase20Item };
    if (t.includes("kustoeventhouse") || t.includes("eventhouse"))
        return { kind: "eventhouse", Icon: EventHouse20Item };
    if (t.includes("kqlqueryset"))     return { kind: "kqlqueryset", Icon: KqlQueryset20Item };
    if (t.includes("kqlscript"))       return { kind: "kqlscript", Icon: KqlScript20Item };
    if (t.includes("eventstream"))     return { kind: "eventstream", Icon: Eventstream20Item };
    if (t.includes("realtimedashboard"))
        return { kind: "rtdashboard", Icon: RealTimeDashboard20Item };
    if (t.includes("reflex") || t.includes("dataactivator"))
        return { kind: "reflex", Icon: OperationsAgent20Item };
    if (t.includes("notebook"))        return { kind: "notebook", Icon: Notebook20Item };
    if (t.includes("sparkjob"))        return { kind: "sparkjob", Icon: SparkJobDirection20Item };
    if (t.includes("environment"))     return { kind: "environment", Icon: Environment20Item };
    if (t.includes("pipeline"))        return { kind: "pipeline", Icon: Pipeline20Item };
    if (t.includes("copyjob"))         return { kind: "copyjob", Icon: CopyJob20Item };
    if (t.includes("datafactory"))     return { kind: "datafactory", Icon: DataFactory20Item };
    if (t.includes("dataflowgen2"))    return { kind: "dataflowgen2", Icon: DataflowGen220Item };
    if (t.includes("dataflow"))        return { kind: "dataflow", Icon: Dataflow20Item };
    if (t.includes("datamart"))        return { kind: "datamart", Icon: Datamart20Item };
    if (t.includes("paginatedreport")) return { kind: "paginated", Icon: PaginatedReport20Item };
    if (t.includes("rdlreport"))       return { kind: "rdlreport", Icon: RdlReport20Item };
    if (t.includes("mobilereport"))    return { kind: "mobilereport", Icon: MobileReport20Item };
    if (t.includes("scorecard") || t.includes("goal"))
        return { kind: "scorecard", Icon: Scorecard20Item };
    if (t.includes("metricset") || t === "metric" || t.includes("metrics"))
        return { kind: "metric", Icon: MetricSets20Item };
    if (t.includes("report"))          return { kind: "report", Icon: Report20Item };
    if (t.includes("dashboard"))       return { kind: "dashboard", Icon: Dashboard20Item };
    if (t.includes("semanticmodel") || t === "dataset")
        return { kind: "semantic", Icon: SemanticModel20Item };
    if (t.includes("schemamodel"))     return { kind: "schemamodel", Icon: SchemaModel20Item };
    if (t.includes("mlmodel"))         return { kind: "mlmodel", Icon: Model20Item };
    if (t.includes("mlexperiment"))    return { kind: "mlexperiment", Icon: Experiments20Item };
    if (t.includes("userdatafunction") || t.includes("datafunction"))
        return { kind: "userfunction", Icon: UserDataFunction20Item };
    if (t.includes("functionset"))     return { kind: "functionset", Icon: FunctionSet20Item };
    if (t.includes("variable"))        return { kind: "variables", Icon: VariableLibrary20Item };
    if (t.includes("dataexploration") || t.includes("exploration"))
        return { kind: "exploration", Icon: Exploration20Item };
    // Only match explicit Fabric "DataAgent" / "OperationsAgent" types.
    // Matching bare ``agent`` would swallow completely unrelated types.
    if (t.includes("dataagent"))       return { kind: "dataagent", Icon: DataAgent20Item };
    if (t.includes("operationsagent")) return { kind: "opsagent", Icon: OperationsAgent20Item };
    if (t.includes("orgapp") || t === "app")
        return { kind: "app", Icon: Apps20Item };
    return { kind: "generic", Icon: GenericPlaceholder20Item };
}

export interface WorkspacePreviewModalProps {
    /** Target workspace — always required so the header renders the name. */
    workspace: { id: string; name: string };
    /** Close callback; the modal does NOT manage its own visibility. */
    onClose: () => void;

    /** Items to render. Provide when operating in historical-snapshot
     *  mode (no ``onRefresh``); in live mode pass the most recent fetch
     *  result so the modal can react to refresh() returning new data. */
    items: WorkspaceItem[] | null;
    /** ISO-8601 timestamp that goes with ``items``. Rendered as
     *  "Loaded HH:MM:SS" in live mode and "As-of DATE HH:MM" in
     *  snapshot mode. */
    capturedAt: string | null;

    /** Optional — when provided the header shows a Refresh button that
     *  calls back. Loading/error states are driven by the parent. */
    onRefresh?: () => void;
    loading?: boolean;
    error?: string | null;

    /** When true the timestamp is labelled "As-of" and refresh is
     *  hidden even if ``onRefresh`` is set; used by the session detail
     *  view where the snapshot represents a moment in the past. */
    snapshot?: boolean;

    /** Workload client — when provided, each row gets a "View details
     *  in a new browser tab" button that calls
     *  ``navigation.openBrowserTab`` to escape the iframe sandbox.
     *  The workload iframe has ``allow-scripts`` and ``allow-popups``
     *  but the host still blocks in-frame navigations, so plain
     *  ``<a target="_blank">`` is silently dropped — the
     *  ``workloadClient`` API is the only path that reliably escapes
     *  (same trick we use for attachment downloads). */
    workloadClient?: WorkloadClientAPI;

    /** When set, the modal scrolls the row with this id into view and
     *  briefly flashes it on first render — used when opening the
     *  modal from a context-pill click so the user sees exactly which
     *  item they picked. If the item lives in a subfolder, the modal
     *  auto-drills into that folder first. */
    highlightItemId?: string | null;

    /** When true, once the highlighted item's webUrl is known we
     *  automatically kick off the "Open in Fabric" flow. In-sandbox
     *  this surfaces the copy-URL banner; Ctrl/Cmd click on the user's
     *  end isn't required. Used when the user clicks an item context
     *  pill — their intent is clearly "take me to this thing", so we
     *  shouldn't make them click a second time inside the modal. */
    autoOpenHighlighted?: boolean;
}

/**
 * Fabric-style workspace preview — a modal table of items + folders.
 *
 * Supports two modes:
 *   • **Live**  (pass ``onRefresh``): header shows a relative timestamp
 *     and a Refresh button that forces a cache-busting re-fetch.
 *   • **Snapshot** (pass ``snapshot={true}``, no ``onRefresh``): shows a
 *     historical timestamp and no refresh — used by the Session detail
 *     page to show how the workspace looked at plan creation.
 *
 * Folder rows are clickable (drill in); each row also exposes a small
 * "open in new tab" icon linking to the Fabric portal URL. Closes on
 * Escape and backdrop click.
 */
export function WorkspacePreviewModal(props: WorkspacePreviewModalProps) {
    const { workspace, onClose, items, capturedAt, onRefresh, loading, error, snapshot, workloadClient, highlightItemId, autoOpenHighlighted } = props;

    // Current folder the user has drilled into. ``null`` = root view.
    // Reset whenever we switch workspaces.
    const [folderStack, setFolderStack] = useState<Array<{ id: string; name: string }>>([]);
    useEffect(() => { setFolderStack([]); }, [workspace.id]);

    // When the caller asks us to highlight a specific item, auto-drill
    // into the folder that contains it so the row is actually visible
    // in the current view. Runs when ``highlightItemId`` or the items
    // list changes.
    useEffect(() => {
        if (!highlightItemId || !items) return;
        const target = items.find(i => i.id === highlightItemId);
        if (!target || !target.folderId) return;
        // Walk ancestor folders so breadcrumb matches Fabric's layout.
        const path: Array<{ id: string; name: string }> = [];
        let cursor: string | null | undefined = target.folderId;
        const seen = new Set<string>();
        while (cursor && !seen.has(cursor)) {
            seen.add(cursor);
            const folder = items.find(i => i.id === cursor && i.type === "Folder");
            if (!folder) break;
            path.unshift({ id: folder.id, name: folder.name });
            cursor = folder.parentFolderId || null;
        }
        if (path.length) setFolderStack(path);
        // Only run once per highlightItemId change; do not depend on
        // folderStack (would cause loops).
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [highlightItemId, items]);

    // Scroll the highlighted row into view + flash when it lands.
    useEffect(() => {
        if (!highlightItemId) return undefined;
        const id = window.requestAnimationFrame(() => {
            const row = document.querySelector<HTMLElement>(`tr[data-item-id="${CSS.escape(highlightItemId)}"]`);
            if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
        });
        return () => window.cancelAnimationFrame(id);
    }, [highlightItemId, items, folderStack]);

    // When ``autoOpenHighlighted`` is set, fire the "Open in Fabric"
    // flow once as soon as we know the item's webUrl. Guarded by a
    // ref so we don't re-open on every items refresh.
    const autoOpenFiredRef = React.useRef<string | null>(null);
    useEffect(() => {
        if (!autoOpenHighlighted || !highlightItemId || !items) return;
        if (autoOpenFiredRef.current === highlightItemId) return;
        const target = items.find(i => i.id === highlightItemId);
        if (!target || !target.webUrl) return;
        autoOpenFiredRef.current = highlightItemId;
        void openInNewTab(target.webUrl);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [autoOpenHighlighted, highlightItemId, items]);

    // When the SDK-based tab-open fails (or no SDK is available) we
    // surface the URL in a banner so the user can copy or Ctrl-click it.
    const [fallbackUrl, setFallbackUrl] = useState<string | null>(null);
    const [fallbackCopied, setFallbackCopied] = useState(false);

    // Close on Escape.
    useEffect(() => {
        function onKey(e: KeyboardEvent) {
            if (e.key === "Escape") onClose();
        }
        document.addEventListener("keydown", onKey);
        return () => document.removeEventListener("keydown", onKey);
    }, [onClose]);

    const activeFolderId = folderStack.length ? folderStack[folderStack.length - 1].id : null;

    // Open the Fabric portal URL in a new browser tab.
    //
    // Delegates to the shared ``openExternalTab`` helper which handles
    // the SDK call, the ``experience`` param retry, the ``window.open``
    // fallback, and the clipboard copy. If every automatic path fails
    // we surface the URL in the banner below so the user can copy it
    // or Ctrl/Cmd-click it into a new tab manually.
    async function openInNewTab(rawUrl: string): Promise<void> {
        const outcome = await openExternalTab(workloadClient, rawUrl, {
            onFallback: (url) => {
                setFallbackUrl(url);
                setFallbackCopied(false);
            },
        });
        // eslint-disable-next-line no-console
        console.log("[WorkspacePreview] openInNewTab outcome", outcome, rawUrl);
    }

    // Fabric auto-creates a ``SQLAnalyticsEndpoint`` (a.k.a.
    // ``SQLEndpoint``) for every data-backed item: Lakehouse,
    // MirroredDatabase, MirroredAzureDatabricksCatalog, SqlDatabase,
    // KQLDatabase, Warehouse (in some cases), etc. In the Fabric portal
    // these are rendered as nested children of their parent rather
    // than as standalone items. The REST API doesn't surface a parent
    // pointer, but the endpoint always shares its parent's
    // ``displayName`` and ``folderId`` — so we pair them up here.
    //
    // Any non-endpoint item sharing (displayName, folderId) with an
    // endpoint is treated as the parent. If multiple candidates share
    // the tuple, we prefer item types known to own endpoints.
    const PARENT_TYPES = new Set([
        "lakehouse",
        "mirroreddatabase",
        "mirroredazuredatabrickscatalog",
        "sqldatabase",
        "kqldatabase",
        "kustodatabase",
        "warehouse",
        "dataversemirror",
    ]);
    const { lakehouseChildren, childIds } = useMemo(() => {
        const children = new Map<string, WorkspaceItem[]>();
        const hide = new Set<string>();
        if (!items) return { lakehouseChildren: children, childIds: hide };
        const endpoints = items.filter(i => {
            const t = (i.type || "").toLowerCase();
            return t === "sqlendpoint" || t === "sqlanalyticsendpoint";
        });
        for (const ep of endpoints) {
            // Prefer a parent whose type is in the known-parents set;
            // fall back to any same-name same-folder non-endpoint item.
            const candidates = items.filter(i => {
                if (i.id === ep.id) return false;
                if (i.type === "Folder") return false;
                const t = (i.type || "").toLowerCase();
                if (t === "sqlendpoint" || t === "sqlanalyticsendpoint") return false;
                return i.name === ep.name && (i.folderId || null) === (ep.folderId || null);
            });
            const parent =
                candidates.find(i => PARENT_TYPES.has((i.type || "").toLowerCase())) ||
                candidates[0];
            if (parent && parent.id) {
                const arr = children.get(parent.id) || [];
                arr.push(ep);
                children.set(parent.id, arr);
                if (ep.id) hide.add(ep.id);
            }
        }
        return { lakehouseChildren: children, childIds: hide };
    }, [items]);

    const folderCounts = useMemo(() => {
        const counts = new Map<string, number>();
        if (!items) return counts;
        for (const it of items) {
            if (!it.folderId) continue;
            if (it.id && childIds.has(it.id)) continue;
            counts.set(it.folderId, (counts.get(it.folderId) || 0) + 1);
        }
        return counts;
    }, [items, childIds]);

    const visibleItems = useMemo(() => {
        if (!items) return [] as WorkspaceItem[];
        return items.filter(it => {
            if (it.id && childIds.has(it.id)) return false;
            return (it.folderId || null) === activeFolderId;
        });
    }, [items, childIds, activeFolderId]);

    // The Fabric /items REST API only returns ``createdBy`` /
    // ``lastModifiedBy`` for a subset of item types, so the Owner
    // column is often entirely empty. Hide it outright in that case
    // rather than showing a misleading blank column — matches Fabric's
    // own behaviour where unowned items simply omit the value.
    const hasAnyOwner = useMemo(() => {
        if (!items) return false;
        return items.some(it => !!it.owner);
    }, [items]);


    function tsLabel(): string {
        if (!capturedAt) return "";
        const d = new Date(capturedAt);
        if (Number.isNaN(d.getTime())) return "";
        const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
        if (snapshot) {
            return `state as of ${d.toLocaleDateString()} ${time}`;
        }
        return `state as of ${time}`;
    }

    // Header count string — items (non-folder rows) only, because
    // folders aren't artifacts in Fabric's eyes. Shows the visible
    // count for context and the workspace-wide total when they differ.
    function countLabel(): string {
        if (!items || !visibleItems) return "";
        // Exclude folders (not "items" in Fabric's eyes) AND auto-generated
        // SQL endpoints (rendered as nested children of their Lakehouse).
        const isCountable = (it: WorkspaceItem) =>
            it.type !== "Folder" && !(it.id && childIds.has(it.id));
        const totalItems = items.filter(isCountable).length;
        const visibleItemsOnly = visibleItems.filter(isCountable).length;
        if (activeFolderId === null) {
            return `${totalItems} item${totalItems === 1 ? "" : "s"}`;
        }
        const inFolder = `${visibleItemsOnly} item${visibleItemsOnly === 1 ? "" : "s"} in folder`;
        const inWs = `${totalItems} item${totalItems === 1 ? "" : "s"} in workspace`;
        return `${inFolder}  •  ${inWs}`;
    }

    function renderRow(it: WorkspaceItem, isChild: boolean): React.ReactNode {
        const isFolder = it.type === "Folder";
        const { kind: iconKind, Icon: TypeIcon } = iconFor(it.type);
        const rowClickable = isFolder;
        const folderChildCount = isFolder ? (folderCounts.get(it.id) || 0) : 0;
        const isHighlighted = !!highlightItemId && it.id === highlightItemId;
        const rowClasses = [
            rowClickable ? "workspace-preview-row--clickable" : "",
            isChild ? "workspace-preview-row--child" : "",
            isHighlighted ? "workspace-preview-row--highlight" : "",
        ].filter(Boolean).join(" ") || undefined;
        return (
            <tr
                key={it.id}
                data-item-id={it.id || undefined}
                className={rowClasses}
                onClick={rowClickable
                    ? () => setFolderStack([...folderStack, { id: it.id, name: it.name }])
                    : undefined}
                role={rowClickable ? "button" : undefined}
                tabIndex={rowClickable ? 0 : undefined}
                onKeyDown={rowClickable
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            setFolderStack([...folderStack, { id: it.id, name: it.name }]);
                        }
                    }
                    : undefined}
            >
                <td className="workspace-preview-col-icon">
                    <span
                        className={`workspace-preview-icon workspace-preview-icon--${iconKind}`}
                        aria-hidden
                    >
                        {isFolder ? <Folder20Filled /> : <TypeIcon />}
                    </span>
                </td>
                <td className="workspace-preview-col-name">
                    <span className="workspace-preview-name-inner">
                        {isChild && (
                            <span className="workspace-preview-child-connector" aria-hidden />
                        )}
                        <span className="workspace-preview-name-text">{it.name}</span>
                        {isFolder && (
                            <span className="workspace-preview-folder-count" title="Direct items">
                                {folderChildCount} item{folderChildCount === 1 ? "" : "s"}
                            </span>
                        )}
                        {it.webUrl && (
                            // Plain left-click → "Open in Fabric"
                            // (``navigation.openBrowserTab`` with
                            // fallback banner). Ctrl/Cmd/middle-click
                            // keep using the browser's native new-tab
                            // handling (the anchor's ``href`` +
                            // ``target="_blank"``).
                            <a
                                href={it.webUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="workspace-preview-open"
                                aria-label={`Open ${it.name} in Fabric`}
                                title="Open in Fabric"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
                                    e.preventDefault();
                                    void openInNewTab(it.webUrl!);
                                }}
                                onAuxClick={(e) => { e.stopPropagation(); }}
                            >
                                <Open20Regular />
                            </a>
                        )}
                    </span>
                </td>
                {hasAnyOwner && (
                    <td className="workspace-preview-col-owner">{it.owner || ""}</td>
                )}
                <td className="workspace-preview-col-type">{it.type}</td>
            </tr>
        );
    }

    return (
        <div
            className="attachment-preview-backdrop"
            onClick={onClose}
            role="presentation"
        >
            <div
                className="attachment-preview-dialog workspace-preview-dialog"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
                aria-label={`Items in workspace ${workspace.name}`}
            >
                <header className="attachment-preview-head">
                    <span className="attachment-preview-name workspace-preview-title" title={workspace.name}>
                        <PeopleTeam20Regular aria-hidden />
                        <span className="workspace-preview-title-name">{workspace.name}</span>
                        {items && (
                            <span className="workspace-preview-count">{countLabel()}</span>
                        )}
                    </span>
                    <div className="workspace-preview-head-actions">
                        {capturedAt && (
                            <span
                                className="workspace-preview-ts"
                                title={new Date(capturedAt).toLocaleString()}
                            >
                                {tsLabel()}
                            </span>
                        )}
                        {onRefresh && !snapshot && (
                            <button
                                type="button"
                                className="workspace-preview-refresh"
                                onClick={onRefresh}
                                disabled={loading}
                                aria-label="Refresh"
                                title="Refresh from Fabric"
                            >
                                <ArrowClockwise16Regular />
                            </button>
                        )}
                        <button
                            type="button"
                            className="attachment-preview-close"
                            onClick={onClose}
                            aria-label="Close preview"
                        >
                            <Dismiss24Regular />
                        </button>
                    </div>
                </header>
                {folderStack.length > 0 && (
                    <nav className="workspace-preview-crumbs" aria-label="Folder breadcrumb">
                        <button
                            type="button"
                            className="workspace-preview-crumb"
                            onClick={() => setFolderStack([])}
                        >
                            {workspace.name}
                        </button>
                        {folderStack.map((f, idx) => (
                            <React.Fragment key={f.id}>
                                <ChevronRight16Regular aria-hidden />
                                <button
                                    type="button"
                                    className="workspace-preview-crumb"
                                    onClick={() => setFolderStack(folderStack.slice(0, idx + 1))}
                                    disabled={idx === folderStack.length - 1}
                                >
                                    {f.name}
                                </button>
                            </React.Fragment>
                        ))}
                    </nav>
                )}
                <div className="workspace-preview-body">
                    {fallbackUrl && (
                        <div className="workspace-preview-fallback" role="status">
                            <span className="workspace-preview-fallback-msg">
                                Couldn&apos;t open the Fabric link automatically (sandbox blocked it).
                                The URL has been copied to your clipboard — paste it into a new browser tab,
                                or Ctrl/Cmd-click the link below.
                            </span>
                            <a
                                href={fallbackUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="workspace-preview-fallback-link"
                                onClick={(e) => e.stopPropagation()}
                            >
                                {fallbackUrl}
                            </a>
                            <div className="workspace-preview-fallback-actions">
                                <button
                                    type="button"
                                    onClick={async () => {
                                        try {
                                            await navigator.clipboard.writeText(fallbackUrl);
                                            setFallbackCopied(true);
                                            setTimeout(() => setFallbackCopied(false), 1500);
                                        } catch { /* no-op */ }
                                    }}
                                >
                                    {fallbackCopied ? "Copied!" : "Copy URL"}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setFallbackUrl(null)}
                                >
                                    Dismiss
                                </button>
                            </div>
                        </div>
                    )}
                    {loading && (
                        <div className="workspace-preview-loading">
                            <Spinner size="small" /> Loading items…
                        </div>
                    )}
                    {error && !loading && (
                        <div className="workspace-preview-error">{error}</div>
                    )}
                    {!loading && !error && visibleItems && visibleItems.length === 0 && (
                        <div className="workspace-preview-empty">
                            {activeFolderId ? "This folder is empty." : "This workspace has no items."}
                        </div>
                    )}
                    {!loading && !error && visibleItems && visibleItems.length > 0 && (
                        <table className="workspace-preview-table">
                            <thead>
                                <tr>
                                    <th className="workspace-preview-col-icon" aria-label="Type" />
                                    <th className="workspace-preview-col-name">Name</th>
                                    {hasAnyOwner && (
                                        <th className="workspace-preview-col-owner">Owner</th>
                                    )}
                                    <th className="workspace-preview-col-type">Type</th>
                                </tr>
                            </thead>
                            <tbody>
                                {visibleItems.flatMap(it => {
                                    const rows = [renderRow(it, false)];
                                    const kids = lakehouseChildren.get(it.id || "") || [];
                                    for (const k of kids) rows.push(renderRow(k, true));
                                    return rows;
                                })}
                            </tbody>
                        </table>
                    )}
                </div>
            </div>
        </div>
    );
}
