// WS-F — Perspectives API.
//
// Reads perspectives from the TMDL semantic model definition (same
// endpoint loadModelData uses). This is more robust than DAX
// INFO.PERSPECTIVES() which is not available on all compat levels.
//
// Write-back (TOM) is stubbed for v0.15 — the sempy-labs backend
// bridge is not yet wired. Apply surfaces a friendly "deferred"
// message so the UX contract ships without changing model state.

import { getSemanticModelDefinition, type PbiAuth } from "./fabricApi";

export interface PerspectiveInfo {
  id: number;
  name: string;
}

/** Membership entry. `path` uniquely identifies the object:
 *  `Table` | `Table[Column]` | `Table[Measure]` | `Table::Hierarchy`. */
export interface PerspectiveMember {
  perspectiveName: string;
  objectType: "Table" | "Column" | "Measure" | "Hierarchy";
  tableName: string;
  memberName: string;
  path: string;
}

export interface PerspectivesData {
  perspectives: PerspectiveInfo[];
  members: PerspectiveMember[];
}

function decodePart(b64: string): string {
  try {
    return atob(b64);
  } catch {
    return "";
  }
}

function unquote(s: string): string {
  const t = s.trim();
  if ((t.startsWith("'") && t.endsWith("'")) || (t.startsWith('"') && t.endsWith('"'))) {
    return t.slice(1, -1);
  }
  return t;
}

/** Parse a `perspectives/<name>.tmdl` part.
 *  Expected TMDL shape (simplified):
 *    perspective 'Sales'
 *        perspectiveTable 'Customer'
 *            perspectiveColumn Name
 *            perspectiveMeasure 'Total Sales'
 *            perspectiveHierarchy 'Calendar'
 */
function parsePerspectiveTmdl(
  text: string,
  members: PerspectiveMember[],
  perspectiveNames: Set<string>,
): void {
  const lines = text.split(/\r?\n/);
  let currentPersp: string | null = null;
  let currentTable: string | null = null;

  for (const raw of lines) {
    const line = raw.replace(/\t/g, "    ");
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("//")) continue;
    const indent = line.length - line.trimStart().length;

    const mPersp = trimmed.match(/^perspective\s+(.+?)\s*$/);
    if (mPersp && indent === 0) {
      currentPersp = unquote(mPersp[1]);
      perspectiveNames.add(currentPersp);
      currentTable = null;
      continue;
    }
    if (!currentPersp) continue;

    const mTable = trimmed.match(/^perspectiveTable\s+(.+?)\s*$/);
    if (mTable) {
      currentTable = unquote(mTable[1]);
      members.push({
        perspectiveName: currentPersp,
        objectType: "Table",
        tableName: currentTable,
        memberName: "",
        path: currentTable,
      });
      continue;
    }
    if (!currentTable) continue;

    const mCol = trimmed.match(/^perspectiveColumn\s+(.+?)\s*$/);
    if (mCol) {
      const name = unquote(mCol[1]);
      members.push({
        perspectiveName: currentPersp, objectType: "Column",
        tableName: currentTable, memberName: name,
        path: `${currentTable}[${name}]`,
      });
      continue;
    }
    const mMeas = trimmed.match(/^perspectiveMeasure\s+(.+?)\s*$/);
    if (mMeas) {
      const name = unquote(mMeas[1]);
      members.push({
        perspectiveName: currentPersp, objectType: "Measure",
        tableName: currentTable, memberName: name,
        path: `${currentTable}[${name}]`,
      });
      continue;
    }
    const mHier = trimmed.match(/^perspectiveHierarchy\s+(.+?)\s*$/);
    if (mHier) {
      const name = unquote(mHier[1]);
      members.push({
        perspectiveName: currentPersp, objectType: "Hierarchy",
        tableName: currentTable, memberName: name,
        path: `${currentTable}::${name}`,
      });
      continue;
    }
  }
}

export async function loadPerspectives(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
): Promise<PerspectivesData> {
  const parts = await getSemanticModelDefinition(auth, workspaceId, datasetId);
  const members: PerspectiveMember[] = [];
  const perspectiveNames = new Set<string>();

  for (const p of parts) {
    if (!/definition\/perspectives\//i.test(p.path)) continue;
    const text = decodePart(p.payload);
    if (text) parsePerspectiveTmdl(text, members, perspectiveNames);
  }

  let id = 1;
  const perspectives: PerspectiveInfo[] = Array.from(perspectiveNames)
    .sort((a, b) => a.localeCompare(b))
    .map((name) => ({ id: id++, name }));

  return { perspectives, members };
}

/* ------------------------------------------------------------------ */
/* Write operations (stubbed until sempy-labs backend bridge lands).   */
/* ------------------------------------------------------------------ */

export interface PerspectiveChangeSet {
  add: { perspectiveName: string; objectType: string; path: string }[];
  remove: { perspectiveName: string; objectType: string; path: string }[];
  createPerspectives: string[];
  renamePerspectives: { from: string; to: string }[];
  deletePerspectives: string[];
}

export function isEmptyChangeSet(cs: PerspectiveChangeSet): boolean {
  return (
    cs.add.length === 0 &&
    cs.remove.length === 0 &&
    cs.createPerspectives.length === 0 &&
    cs.renamePerspectives.length === 0 &&
    cs.deletePerspectives.length === 0
  );
}

export async function applyPerspectiveChanges(
  _auth: PbiAuth,
  _workspaceId: string,
  _datasetId: string,
  _changes: PerspectiveChangeSet,
): Promise<{ applied: boolean; message: string }> {
  return {
    applied: false,
    message: "Backend bridge (sempy-labs TOM write) not yet wired — Apply deferred.",
  };
}
