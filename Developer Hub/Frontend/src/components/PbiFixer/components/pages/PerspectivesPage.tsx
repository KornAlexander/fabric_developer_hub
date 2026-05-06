// WS-F — Perspectives page.
//
// Matrix view: rows = model objects (grouped by Table), columns = perspectives.
// Each cell is a checkbox indicating membership. Table-level rows use
// `indeterminate` state when some but not all children are members.
//
// Add/remove membership, create/rename/delete perspectives all accumulate in a
// `PerspectiveChangeSet`. Apply switch + confirm dialog gate the write-back
// (which is stubbed until the sempy-labs backend bridge lands).

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Button,
  Checkbox,
  Input,
  Switch,
  Spinner,
  Text,
  Title3,
  Badge,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
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
} from "@fluentui/react-components";
import {
  ArrowClockwise20Regular,
  Add20Regular,
  Delete20Regular,
  Rename20Regular,
  CheckmarkCircle20Filled,
  Warning20Regular,
} from "@fluentui/react-icons";
import type { PageProps } from "../../types/shared";
import {
  loadPerspectives,
  applyPerspectiveChanges,
  type PerspectivesData,
  type PerspectiveMember,
  type PerspectiveChangeSet,
} from "../../services/perspectivesApi";

const useStyles = makeStyles({
  root: { display: "flex", flexDirection: "column", height: "100%", minHeight: 0, ...shorthands.gap("12px") },
  toolbar: {
    display: "flex",
    alignItems: "center",
    ...shorthands.gap("10px"),
    flexWrap: "wrap",
    ...shorthands.padding("4px"),
  },
  grow: { flex: 1 },
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
    whiteSpace: "nowrap",
    zIndex: 1,
  },
  perspCol: {
    textAlign: "center",
    position: "relative",
    minWidth: "100px",
  },
  perspHeadInner: { display: "inline-flex", alignItems: "center", ...shorthands.gap("4px") },
  td: {
    ...shorthands.padding("4px", "10px"),
    borderBottom: `1px solid ${tokens.colorNeutralStroke3}`,
    verticalAlign: "middle",
  },
  cell: { textAlign: "center" },
  tableRow: { backgroundColor: tokens.colorNeutralBackground2, fontWeight: tokens.fontWeightSemibold },
  typeBadge: {
    display: "inline-block",
    ...shorthands.padding("1px", "6px"),
    borderRadius: tokens.borderRadiusSmall,
    fontSize: "10px",
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorNeutralForeground3,
    marginRight: "6px",
  },
  empty: {
    display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
    height: "100%", ...shorthands.padding("32px"), ...shorthands.gap("8px"),
    color: tokens.colorNeutralForeground3, textAlign: "center",
  },
  stats: {
    display: "flex", ...shorthands.gap("12px"), alignItems: "center",
    ...shorthands.padding("4px"),
    color: tokens.colorNeutralForeground2, fontSize: tokens.fontSizeBase200,
  },
});

type MembershipKey = string; // `perspName::path`
const key = (persp: string, path: string): MembershipKey => `${persp}::${path}`;

const EMPTY_CHANGES: PerspectiveChangeSet = {
  add: [], remove: [], createPerspectives: [], renamePerspectives: [], deletePerspectives: [],
};

interface ObjectRow {
  kind: "Table" | "Column" | "Measure" | "Hierarchy";
  tableName: string;
  memberName: string;
  path: string;
}

export const PerspectivesPage: React.FC<PageProps> = ({ auth, workspaceId, datasetId, datasetName }) => {
  const styles = useStyles();

  const [data, setData] = useState<PerspectivesData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const [memberships, setMemberships] = useState<Set<MembershipKey>>(new Set());
  const [initialMemberships, setInitialMemberships] = useState<Set<MembershipKey>>(new Set());
  const [perspectives, setPerspectives] = useState<string[]>([]);
  const [initialPerspectives, setInitialPerspectives] = useState<string[]>([]);

  const [applyMode, setApplyMode] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [applyResult, setApplyResult] = useState<string>("");

  const [newName, setNewName] = useState("");
  const [renameTarget, setRenameTarget] = useState("");
  const [renameTo, setRenameTo] = useState("");

  const loadData = useCallback(async () => {
    if (!workspaceId || !datasetId) return;
    setLoading(true); setErr(""); setApplyResult("");
    try {
      const d = await loadPerspectives(auth, workspaceId, datasetId);
      setData(d);
      const ms = new Set<MembershipKey>();
      for (const m of d.members) ms.add(key(m.perspectiveName, m.path));
      setMemberships(ms);
      setInitialMemberships(new Set(ms));
      const names = d.perspectives.map((p) => p.name);
      setPerspectives(names);
      setInitialPerspectives(names);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [auth, workspaceId, datasetId]);

  useEffect(() => { void loadData(); }, [loadData]);

  // Build object rows grouped by table, with Table rows first.
  const rows = useMemo<ObjectRow[]>(() => {
    if (!data) return [];
    const byTable = new Map<string, ObjectRow[]>();
    const tableSet = new Set<string>();
    for (const m of data.members) {
      tableSet.add(m.tableName);
      if (m.objectType !== "Table") {
        if (!byTable.has(m.tableName)) byTable.set(m.tableName, []);
        byTable.get(m.tableName)!.push({
          kind: m.objectType as ObjectRow["kind"],
          tableName: m.tableName,
          memberName: m.memberName,
          path: m.path,
        });
      }
    }
    const out: ObjectRow[] = [];
    for (const t of Array.from(tableSet).sort()) {
      out.push({ kind: "Table", tableName: t, memberName: "", path: t });
      const children = byTable.get(t) ?? [];
      children.sort((a, b) => a.kind.localeCompare(b.kind) || a.memberName.localeCompare(b.memberName));
      // De-dupe by path
      const seen = new Set<string>();
      for (const c of children) {
        if (seen.has(c.path)) continue;
        seen.add(c.path);
        out.push(c);
      }
    }
    return out;
  }, [data]);

  const tableChildPaths = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const r of rows) {
      if (r.kind !== "Table") {
        if (!m.has(r.tableName)) m.set(r.tableName, []);
        m.get(r.tableName)!.push(r.path);
      }
    }
    return m;
  }, [rows]);

  const toggleMembership = useCallback((persp: string, path: string) => {
    setMemberships((prev) => {
      const next = new Set(prev);
      const k = key(persp, path);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
  }, []);

  const toggleTableMembership = useCallback((persp: string, tableName: string) => {
    const children = tableChildPaths.get(tableName) ?? [];
    setMemberships((prev) => {
      const next = new Set(prev);
      // If currently all members, remove table + children. Otherwise add them.
      const allIn = [tableName, ...children].every((p) => next.has(key(persp, p)));
      const paths = [tableName, ...children];
      for (const p of paths) {
        const k = key(persp, p);
        if (allIn) next.delete(k); else next.add(k);
      }
      return next;
    });
  }, [tableChildPaths]);

  const changes: PerspectiveChangeSet = useMemo(() => {
    const add: PerspectiveChangeSet["add"] = [];
    const remove: PerspectiveChangeSet["remove"] = [];
    // Memberships diff
    for (const k of memberships) {
      if (!initialMemberships.has(k)) {
        const [p, ...rest] = k.split("::");
        const path = rest.join("::");
        const r = rows.find((x) => x.path === path);
        add.push({ perspectiveName: p, objectType: r?.kind ?? "Table", path });
      }
    }
    for (const k of initialMemberships) {
      if (!memberships.has(k)) {
        const [p, ...rest] = k.split("::");
        const path = rest.join("::");
        const r = rows.find((x) => x.path === path);
        remove.push({ perspectiveName: p, objectType: r?.kind ?? "Table", path });
      }
    }
    const createPerspectives = perspectives.filter((n) => !initialPerspectives.includes(n));
    const deletePerspectives = initialPerspectives.filter((n) => !perspectives.includes(n));
    // Renames are not represented symmetrically; we pass them explicitly from the rename dialog.
    return { add, remove, createPerspectives, renamePerspectives: [], deletePerspectives };
  }, [memberships, initialMemberships, perspectives, initialPerspectives, rows]);

  const dirty = changes.add.length + changes.remove.length + changes.createPerspectives.length + changes.deletePerspectives.length > 0;

  const handleAdd = useCallback(() => {
    const name = newName.trim();
    if (!name || perspectives.includes(name)) return;
    setPerspectives((prev) => [...prev, name]);
    setNewName("");
  }, [newName, perspectives]);

  const handleDelete = useCallback((name: string) => {
    setPerspectives((prev) => prev.filter((n) => n !== name));
    // Drop memberships referring to that perspective
    setMemberships((prev) => {
      const next = new Set(prev);
      for (const k of Array.from(next)) if (k.startsWith(`${name}::`)) next.delete(k);
      return next;
    });
  }, []);

  const handleRename = useCallback(() => {
    const from = renameTarget.trim();
    const to = renameTo.trim();
    if (!from || !to || from === to || !perspectives.includes(from) || perspectives.includes(to)) return;
    setPerspectives((prev) => prev.map((n) => (n === from ? to : n)));
    setMemberships((prev) => {
      const next = new Set<string>();
      for (const k of prev) {
        if (k.startsWith(`${from}::`)) next.add(k.replace(`${from}::`, `${to}::`));
        else next.add(k);
      }
      return next;
    });
    setRenameTarget(""); setRenameTo("");
  }, [renameTarget, renameTo, perspectives]);

  const handleApply = useCallback(async () => {
    setConfirmOpen(false);
    const res = await applyPerspectiveChanges(auth, workspaceId, datasetId, changes);
    setApplyResult(res.message);
  }, [auth, workspaceId, datasetId, changes]);

  if (!workspaceId || !datasetId) {
    return (
      <div className={styles.empty}>
        <Title3>Perspectives</Title3>
        <Text>Select a workspace and a semantic model in the connection bar above to begin.</Text>
      </div>
    );
  }

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        <Button icon={<ArrowClockwise20Regular />} onClick={loadData} disabled={loading}>Rerun</Button>
        <div className={styles.grow} />
        <Input
          placeholder="New perspective name…"
          value={newName}
          onChange={(_, d) => setNewName(d.value)}
          style={{ maxWidth: "220px" }}
        />
        <Button icon={<Add20Regular />} onClick={handleAdd} disabled={!newName.trim() || perspectives.includes(newName.trim())}>
          Add
        </Button>
        <Input
          placeholder="Rename from…"
          value={renameTarget}
          onChange={(_, d) => setRenameTarget(d.value)}
          style={{ maxWidth: "160px" }}
        />
        <Input
          placeholder="…to"
          value={renameTo}
          onChange={(_, d) => setRenameTo(d.value)}
          style={{ maxWidth: "160px" }}
        />
        <Button icon={<Rename20Regular />} onClick={handleRename}>Rename</Button>
        <Switch
          label={applyMode ? "Apply changes" : "Scan only"}
          checked={applyMode}
          onChange={(_, d) => setApplyMode(!!d.checked)}
        />
        <Button
          appearance="primary"
          icon={<CheckmarkCircle20Filled />}
          disabled={!applyMode || !dirty}
          onClick={() => setConfirmOpen(true)}
        >
          Apply
        </Button>
      </div>

      <div className={styles.stats}>
        {loading && <><Spinner size="tiny" /><span>Loading perspectives…</span></>}
        {!loading && data && (
          <>
            <span>{perspectives.length} perspective{perspectives.length === 1 ? "" : "s"}</span>
            <span>·</span>
            <span>{rows.filter((r) => r.kind !== "Table").length} object{rows.length === 1 ? "" : "s"}</span>
            <span>·</span>
            <Badge appearance="tint" color={dirty ? "warning" : "success"}>
              {dirty ? `${changes.add.length}+ / ${changes.remove.length}- / ${changes.createPerspectives.length} new / ${changes.deletePerspectives.length} deleted` : "no pending changes"}
            </Badge>
            <span style={{ marginLeft: "auto", color: tokens.colorNeutralForeground3 }}>{datasetName}</span>
          </>
        )}
      </div>

      {err && (
        <MessageBar intent="error">
          <MessageBarBody><MessageBarTitle>Load failed</MessageBarTitle> {err}</MessageBarBody>
        </MessageBar>
      )}
      {applyResult && (
        <MessageBar intent="warning">
          <MessageBarBody><MessageBarTitle>Apply</MessageBarTitle> {applyResult}</MessageBarBody>
        </MessageBar>
      )}

      {data && perspectives.length === 0 ? (
        <div className={styles.empty}>
          <Title3>No perspectives yet</Title3>
          <Text>Add one above to get started. Changes are local until you flip the Apply switch.</Text>
        </div>
      ) : (
        <div className={styles.gridWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>Object</th>
                {perspectives.map((p) => (
                  <th key={p} className={`${styles.th} ${styles.perspCol}`}>
                    <span className={styles.perspHeadInner}>
                      {p}
                      <Button
                        size="small"
                        appearance="subtle"
                        icon={<Delete20Regular />}
                        onClick={() => handleDelete(p)}
                        title={`Delete ${p}`}
                      />
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const isTable = r.kind === "Table";
                const rowClass = isTable ? `${styles.td} ${styles.tableRow}` : styles.td;
                return (
                  <tr key={`${r.kind}::${r.path}`}>
                    <td className={rowClass}>
                      {!isTable && <span className={styles.typeBadge}>{r.kind}</span>}
                      {isTable ? <strong>{r.tableName}</strong> : r.memberName}
                    </td>
                    {perspectives.map((p) => {
                      if (isTable) {
                        const children = tableChildPaths.get(r.tableName) ?? [];
                        const allPaths = [r.path, ...children];
                        const checkedChildren = allPaths.filter((pp) => memberships.has(key(p, pp))).length;
                        const checked = checkedChildren === allPaths.length && allPaths.length > 0;
                        const indeterminate = checkedChildren > 0 && !checked;
                        return (
                          <td key={p} className={`${styles.td} ${styles.cell} ${styles.tableRow}`}>
                            <Checkbox
                              checked={indeterminate ? "mixed" : checked}
                              onChange={() => toggleTableMembership(p, r.tableName)}
                            />
                          </td>
                        );
                      }
                      return (
                        <td key={p} className={`${styles.td} ${styles.cell}`}>
                          <Checkbox
                            checked={memberships.has(key(p, r.path))}
                            onChange={() => toggleMembership(p, r.path)}
                          />
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={confirmOpen} onOpenChange={(_, d) => setConfirmOpen(d.open)}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle><Warning20Regular /> Confirm Apply</DialogTitle>
            <DialogContent>
              <Text>Pending changes:</Text>
              <ul>
                <li><strong>{changes.add.length}</strong> membership additions</li>
                <li><strong>{changes.remove.length}</strong> membership removals</li>
                <li><strong>{changes.createPerspectives.length}</strong> new perspective(s){changes.createPerspectives.length > 0 && `: ${changes.createPerspectives.join(", ")}`}</li>
                <li><strong>{changes.deletePerspectives.length}</strong> deleted perspective(s){changes.deletePerspectives.length > 0 && `: ${changes.deletePerspectives.join(", ")}`}</li>
              </ul>
              <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>
                For v0.15 the TOM write-back is stubbed — Apply will only log.
              </Text>
            </DialogContent>
            <DialogActions>
              <DialogTrigger disableButtonEnhancement>
                <Button appearance="secondary">Cancel</Button>
              </DialogTrigger>
              <Button appearance="primary" icon={<CheckmarkCircle20Filled />} onClick={handleApply}>Apply</Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  );
};

// Suppress unused-import warning for the typed re-export below.
export type { PerspectiveMember };
