// Fabric REST API service layer
// Routes all calls through the AgentHub backend proxy so the backend
// can do the OBO token exchange (the workload-iframe SDK only issues
// workload-audience tokens, which the Fabric / Power BI APIs reject).

import {
  ModelData,
  TableInfo,
  ReportData,
  PageInfo,
  VisualInfo,
} from "../types";
import { pbiFixerProxy } from "../../../controller/AgentHubApi";

export interface PbiAuth {
  githubToken: string;
  fabricToken: string;
}

async function fabricGet<T>(auth: PbiAuth, path: string): Promise<T> {
  return pbiFixerProxy<T>("fabric", path, "GET", null, auth);
}

async function fabricPost<T>(auth: PbiAuth, path: string, body: unknown): Promise<T> {
  return pbiFixerProxy<T>("fabric", path, "POST", body, auth);
}

async function pbiGet<T>(auth: PbiAuth, path: string): Promise<T> {
  return pbiFixerProxy<T>("pbi", path, "GET", null, auth);
}

async function pbiPost<T>(auth: PbiAuth, path: string, body: unknown): Promise<T> {
  return pbiFixerProxy<T>("pbi", path, "POST", body, auth);
}

// ---------------------------------------------------------------------------
// Workspace helpers
// ---------------------------------------------------------------------------

export async function listWorkspaces(
  auth: PbiAuth
): Promise<{ id: string; name: string }[]> {
  const data = await fabricGet<{ value: { id: string; displayName: string }[] }>(
    auth,
    `/workspaces`
  );
  return data.value.map((w) => ({ id: w.id, name: w.displayName }));
}

export async function resolveWorkspaceId(
  auth: PbiAuth,
  nameOrId: string
): Promise<string> {
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(nameOrId)) {
    return nameOrId;
  }
  const workspaces = await listWorkspaces(auth);
  const match = workspaces.find(
    (w) => w.name.toLowerCase() === nameOrId.toLowerCase()
  );
  if (!match) throw new Error(`Workspace '${nameOrId}' not found`);
  return match.id;
}

// ---------------------------------------------------------------------------
// Semantic Model (Dataset) APIs
// ---------------------------------------------------------------------------

export async function listSemanticModels(
  auth: PbiAuth,
  workspaceId: string
): Promise<{ id: string; name: string }[]> {
  const data = await pbiGet<{
    value: { id: string; name: string }[];
  }>(auth, `/groups/${workspaceId}/datasets`);
  return data.value.map((d) => ({ id: d.id, name: d.name }));
}

interface PbiTable {
  name: string;
  description?: string;
  isHidden?: boolean;
  columns?: PbiColumn[];
  measures?: PbiMeasure[];
}

interface PbiColumn {
  name: string;
  dataType?: string;
  isHidden?: boolean;
  expression?: string;
  columnType?: string;
  summarizeBy?: string;
  displayFolder?: string;
  isKey?: boolean;
  dataCategory?: string;
  sortByColumn?: string;
}

interface PbiMeasure {
  name: string;
  expression?: string;
  formatString?: string;
  description?: string;
  displayFolder?: string;
  isHidden?: boolean;
}

interface PbiRelationship {
  fromTable: string;
  fromColumn: string;
  toTable: string;
  toColumn: string;
  crossFilteringBehavior?: string;
  isActive?: boolean;
}

export async function loadModelData(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
  datasetName: string
): Promise<ModelData> {
  const modelData: ModelData = {
    tables: {},
    relationships: [],
    perspectives: [],
    modelProperties: { compatibilityLevel: "", defaultMode: "" },
    datasetName,
  };

  const errors: string[] = [];

  // Strategy 1: Fabric semanticModels/{id}/getDefinition (TMDL).
  // Uses the same workspace-level auth that makes reports work. Only
  // requires the user to be a workspace Contributor/Admin. No Build
  // permission on the dataset needed, unlike /executeQueries.
  try {
    const def = await fabricPost<{
      definition: {
        parts: { path: string; payload: string; payloadType: string }[];
      };
    }>(
      auth,
      `/workspaces/${workspaceId}/semanticModels/${datasetId}/getDefinition`,
      null,
    );
    parseTmdlDefinition(def.definition.parts, modelData);
  } catch (e) {
    errors.push(`TMDL: ${e instanceof Error ? e.message : String(e)}`);
  }

  // Strategy 2: Legacy PBI /datasets/{id}/tables (metadata-only, no DAX).
  // 404s for Fabric-native models but still works for classic Power BI
  // datasets. Only attempted if TMDL came back empty.
  if (Object.keys(modelData.tables).length === 0) {
    try {
      const tablesResp = await pbiGet<{ value: PbiTable[] }>(
        auth,
        `/groups/${workspaceId}/datasets/${datasetId}/tables`
      );
      for (const t of tablesResp.value) {
        const tableInfo: TableInfo = {
          description: t.description ?? "",
          isHidden: t.isHidden ?? false,
          type: "Table",
          columns: {},
          measures: {},
          hierarchies: {},
          calcItems: {},
          partitions: [],
        };
        if (t.columns) {
          for (const c of t.columns) {
            tableInfo.columns[c.name] = {
              dataType: c.dataType ?? "",
              isHidden: c.isHidden ?? false,
              expression: c.expression ?? null,
              type: c.columnType ?? "",
              summarizeBy: c.summarizeBy ?? "",
              displayFolder: c.displayFolder ?? "",
              isKey: c.isKey ?? false,
              dataCategory: c.dataCategory ?? "",
              sortByColumn: c.sortByColumn ?? "",
              encodingHint: "",
              isNullable: true,
            };
          }
        }
        if (t.measures) {
          for (const m of t.measures) {
            tableInfo.measures[m.name] = {
              expression: m.expression ?? "",
              formatString: m.formatString ?? "",
              description: m.description ?? "",
              displayFolder: m.displayFolder ?? "",
              isHidden: m.isHidden ?? false,
            };
          }
        }
        modelData.tables[t.name] = tableInfo;
      }
    } catch (e) {
      errors.push(`PBI metadata: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  // Relationships — legacy endpoint (silently ignored on failure).
  try {
    const relsResp = await pbiGet<{ value: PbiRelationship[] }>(
      auth,
      `/groups/${workspaceId}/datasets/${datasetId}/relationships`
    );
    modelData.relationships = relsResp.value.map((r) => ({
      fromTable: r.fromTable,
      fromColumn: r.fromColumn,
      toTable: r.toTable,
      toColumn: r.toColumn,
      crossFilter: r.crossFilteringBehavior ?? "",
      isActive: r.isActive ?? true,
      multiplicity: "",
      securityFiltering: "",
      relyOnRri: false,
    }));
  } catch {
    /* ignore */
  }

  if (Object.keys(modelData.tables).length === 0 && errors.length > 0) {
    // Surface the actual error(s) instead of returning a silent empty model.
    throw new Error(errors.join(" | "));
  }

  return modelData;
}

/** Parse a TMDL definition (array of `{path, payload (base64), payloadType}`)
 *  into the app's ModelData shape. Handles a flat subset of TMDL:
 *  tables, columns, measures, hierarchies. Enough to populate the
 *  explorer; not a full TMDL parser. */
function parseTmdlDefinition(
  parts: { path: string; payload: string; payloadType: string }[],
  modelData: ModelData,
): void {
  for (const part of parts) {
    if (!part.path.match(/definition\/tables\/[^/]+\.tmdl$/)) continue;
    let text = "";
    try {
      text = atob(part.payload);
    } catch {
      continue;
    }
    const lines = text.split(/\r?\n/);
    let currentTable: string | null = null;
    let tableInfo: TableInfo | null = null;
    let currentObject: null | { kind: "column" | "measure" | "hierarchy" | "partition"; name: string; indent: number; props: Record<string, string>; expression: string[]; levels: string[]; sourceType?: string } = null;
    // Track the currently-open `level <name>` sub-block inside a hierarchy
    // so we can swallow its `column: <col>` property line without it
    // leaking back onto the hierarchy's own props bag.
    let currentLevel: null | { name: string; indent: number } = null;

    const flush = () => {
      if (!currentObject || !tableInfo) return;
      currentLevel = null;
      if (currentObject.kind === "column") {
        tableInfo.columns[currentObject.name] = {
          dataType: currentObject.props.dataType ?? "",
          isHidden: currentObject.props.isHidden === "true",
          expression: currentObject.expression.length ? currentObject.expression.join("\n") : null,
          type: currentObject.props.type ?? "",
          summarizeBy: currentObject.props.summarizeBy ?? "",
          displayFolder: currentObject.props.displayFolder ?? "",
          isKey: currentObject.props.isKey === "true",
          dataCategory: currentObject.props.dataCategory ?? "",
          sortByColumn: currentObject.props.sortByColumn ?? "",
          encodingHint: currentObject.props.encodingHint ?? "",
          isNullable: currentObject.props.isNullable !== "false",
        };
      } else if (currentObject.kind === "measure") {
        tableInfo.measures[currentObject.name] = {
          expression: currentObject.expression.join("\n"),
          formatString: currentObject.props.formatString ?? "",
          description: currentObject.props.description ?? "",
          displayFolder: currentObject.props.displayFolder ?? "",
          isHidden: currentObject.props.isHidden === "true",
        };
      } else if (currentObject.kind === "hierarchy") {
        tableInfo.hierarchies[currentObject.name] = {
          levels: currentObject.levels.slice(),
        };
      } else if (currentObject.kind === "partition") {
        // v0.72 — capture partition with its M / DAX source. Strip
        // surrounding TMDL fence markers (```) from the body but keep
        // line-internal whitespace as authored.
        const body = currentObject.expression
          .filter((l) => l.trim() !== "```")
          .join("\n")
          .trim();
        tableInfo.partitions.push({
          name: currentObject.name,
          sourceType: currentObject.sourceType ?? currentObject.props.mode ?? "",
          expression: body,
        });
      }
      currentObject = null;
    };

    for (const rawLine of lines) {
      const line = rawLine.replace(/\t/g, "    ");
      const trimmed = line.trim();
      if (!trimmed) continue;
      const indent = line.length - line.trimStart().length;

      // Close object when indentation unwinds
      if (currentObject && indent <= currentObject.indent && !line.startsWith(" ".repeat(currentObject.indent + 1))) {
        flush();
      }

      const tableMatch = trimmed.match(/^table\s+(['"]?)(.+?)\1\s*$/);
      if (tableMatch && indent === 0) {
        currentTable = tableMatch[2];
        tableInfo = {
          description: "",
          isHidden: false,
          type: "Table",
          columns: {},
          measures: {},
          hierarchies: {},
          calcItems: {},
          partitions: [],
        };
        modelData.tables[currentTable] = tableInfo;
        continue;
      }

      if (!tableInfo) continue;

      const objMatch = trimmed.match(/^(column|measure|hierarchy|partition)\s+(['"]?)([^=]+?)\2(?:\s*=\s*(.*))?$/);
      if (objMatch) {
        flush();
        const kind = objMatch[1] as "column" | "measure" | "hierarchy" | "partition";
        const headerExpr = objMatch[4];
        currentObject = {
          kind,
          name: objMatch[3].trim(),
          indent,
          props: {},
          expression: [],
          levels: [],
        };
        // Partition header: `partition <name> = <kind>` where <kind> is
        // the source type (m / calculated / entity / policyRange / …),
        // NOT the start of the expression. The actual M/DAX body lives
        // in the `source = …` property below.
        if (kind === "partition") {
          if (headerExpr) currentObject.sourceType = headerExpr.trim();
        } else if (headerExpr) {
          currentObject.expression.push(headerExpr.trim());
        }
        continue;
      }

      // `level <name>` inside a hierarchy block — record the level name,
      // then swallow the following `column: <col>` line via currentLevel.
      if (currentObject?.kind === "hierarchy") {
        const levelMatch = trimmed.match(/^level\s+(['"]?)([^=]+?)\1\s*$/);
        if (levelMatch) {
          const lvlName = levelMatch[2].trim();
          currentObject.levels.push(lvlName);
          currentLevel = { name: lvlName, indent };
          continue;
        }
      }

      if (currentObject) {
        // v0.72 — partition `source = …` is the M / DAX body, NOT a
        // colon-property. Match it explicitly so it doesn't fall into
        // the propMatch bucket (it wouldn't anyway — `=`, not `:`) and
        // start expression accumulation.
        if (currentObject.kind === "partition") {
          const srcMatch = trimmed.match(/^source\s*=\s*(.*)$/);
          if (srcMatch) {
            const v = srcMatch[1].trim();
            if (v && v !== "```") currentObject.expression.push(v);
            continue;
          }
        }
        const propMatch = trimmed.match(/^(\w+):\s*(.*)$/);
        if (propMatch) {
          // If this prop is nested inside a `level` sub-block (deeper
          // indent than the level header), it belongs to the level,
          // not to the hierarchy itself — swallow it.
          if (currentLevel && indent > currentLevel.indent) {
            continue;
          }
          currentLevel = null;
          currentObject.props[propMatch[1]] = propMatch[2].replace(/^['"]|['"]$/g, "");
          continue;
        }
        // TMDL keyword properties (no colon) — annotations, lineage tags,
        // changed-property markers, etc. These are NOT part of the DAX
        // expression and must not be appended to it.
        if (/^(annotation|lineageTag|changedProperty|extendedProperty|kind|sourceLineageTag|queryGroup|relatedColumnDetails)\b/.test(trimmed)) {
          continue;
        }
        if (currentObject.kind === "measure" || currentObject.kind === "column" || currentObject.kind === "partition") {
          // Continuation of expression (multi-line DAX / M block)
          currentObject.expression.push(trimmed);
        }
      } else {
        // Table-level property (no currentObject open) — capture
        // `description:` / `isHidden:` so the editable Properties pane
        // can show real values instead of always-empty strings.
        const tableProp = trimmed.match(/^(description|isHidden):\s*(.*)$/);
        if (tableProp) {
          const key = tableProp[1];
          const val = tableProp[2].replace(/^['"]|['"]$/g, "");
          if (key === "description") tableInfo.description = val;
          else if (key === "isHidden") tableInfo.isHidden = val === "true";
          continue;
        }
      }
    }
    flush();
  }
}

/** Fetch the raw TMDL definition parts for a semantic model.
 *  Used by WS-F to parse perspectives (TMDL is the only endpoint that
 *  reliably surfaces perspective membership without requiring the
 *  newer INFO.PERSPECTIVES() DAX family). */
export async function getSemanticModelDefinition(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
): Promise<{ path: string; payload: string; payloadType: string }[]> {
  const def = await fabricPost<{
    definition: { parts: { path: string; payload: string; payloadType: string }[] };
  }>(
    auth,
    `/workspaces/${workspaceId}/semanticModels/${datasetId}/getDefinition`,
    null,
  );
  return def.definition?.parts ?? [];
}

// ---------------------------------------------------------------------------
// Measure property write-back (TMDL roundtrip)
// ---------------------------------------------------------------------------

export interface MeasureEdit {
  table: string;
  measure: string;
  /** v0.71 — rename the measure. Rewrites only the TMDL header line
   *  (`measure '<old>' = …` → `measure '<new>' = …`); references in
   *  other measures' DAX are NOT cascaded — the user is expected to
   *  fix those by hand or via a follow-up save. */
  newName?: string;
  expression?: string;
  formatString?: string;
  description?: string;
  displayFolder?: string;
  isHidden?: boolean;
}

/** UTF-8 safe base64 encode (the TMDL payload may contain non-ASCII). */
function utf8ToBase64(s: string): string {
  // encodeURIComponent → percent escapes → unescape to binary string → btoa
  return btoa(unescape(encodeURIComponent(s)));
}

function base64ToUtf8(b: string): string {
  return decodeURIComponent(escape(atob(b)));
}

/** Patch a single measure block inside a table TMDL string.
 *
 *  TMDL measure block layout (4-space indent under the table):
 *      measure 'Sales' = SUM(Sales[Amount])
 *          formatString: "#,##0"
 *          displayFolder: KPIs
 *          description: "Total sales"
 *          isHidden: false
 *
 *  Strategy:
 *  - Locate the line `measure '<name>' ...` (with or without quotes).
 *  - Walk forward through the block (lines indented deeper than the
 *    measure header) skipping continuation lines of the DAX expression
 *    until we hit a property line (`key: value`).
 *  - For each requested edit, either replace an existing property line
 *    in-place, or insert a new property line at the bottom of the block
 *    just before the next sibling/dedent.
 *  - Untouched properties are left exactly as written.
 */
function patchMeasureInTmdl(
  text: string,
  measureName: string,
  edits: Omit<MeasureEdit, "table" | "measure">,
): { text: string; matched: boolean } {
  const lines = text.split(/\r?\n/);
  // Match either quoted or unquoted measure header. We capture the raw
  // indent string (tabs / spaces) so we can mirror the file's indent
  // style when generating new property lines.
  const escName = measureName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const headerRe = new RegExp(
    `^([\\t ]*)measure\\s+(['"]?)${escName}\\2(\\s|=|$)`,
  );
  let headerIdx = -1;
  let headerIndentStr = "";
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(headerRe);
    if (m) {
      headerIdx = i;
      headerIndentStr = m[1];
      break;
    }
  }
  if (headerIdx < 0) return { text, matched: false };
  const headerIndent = headerIndentStr.length;

  // v0.71 — rename support. Rewrite the measure name on the header
  // line (and on the synthesised header in the expression-rewrite pass
  // below) before any further work. Quote the new name iff it isn't a
  // bare identifier (TMDL: `[A-Za-z_][A-Za-z0-9_]*`); single quotes
  // inside the name are escaped by doubling them.
  if (typeof edits.newName === "string" && edits.newName !== measureName) {
    const newName = edits.newName;
    const headerLine = lines[headerIdx];
    const quotedNew = /^[A-Za-z_][A-Za-z0-9_]*$/.test(newName)
      ? newName
      : `'${newName.replace(/'/g, "''")}'`;
    // Replace `measure <oldname-with-or-without-quotes>` once.
    lines[headerIdx] = headerLine.replace(
      new RegExp(`(measure\\s+)(['"]?)${escName}\\2`),
      `$1${quotedNew}`,
    );
    // From here on, the patcher needs the new name for the
    // expression-rewrite header synthesis below.
    measureName = newName;
  }

  // Find the end of the block: first line whose indent <= headerIndent (and non-blank).
  let endIdx = lines.length;
  for (let i = headerIdx + 1; i < lines.length; i++) {
    const ln = lines[i];
    if (!ln.trim()) continue;
    const indent = ln.length - ln.trimStart().length;
    if (indent <= headerIndent) {
      endIdx = i;
      break;
    }
  }

  // Determine where the DAX expression body ends and properties begin.
  // Properties match `<indent>key: value` (colon-delimited) OR are TMDL
  // keyword properties (annotation/lineageTag/etc.). The expression body
  // is everything between the header line and the first such property line.
  const PROP_KEYWORDS = /^(annotation|lineageTag|changedProperty|extendedProperty|kind|sourceLineageTag|queryGroup|relatedColumnDetails)\b/;
  let exprEndIdx = endIdx; // index of first property line (exclusive end of expression block)
  for (let i = headerIdx + 1; i < endIdx; i++) {
    const ln = lines[i];
    if (!ln.trim()) continue;
    const t = ln.trim();
    if (/^\w+:\s/.test(t) || PROP_KEYWORDS.test(t)) {
      exprEndIdx = i;
      break;
    }
  }

  // Detect the per-level indent unit by sampling a sibling property
  // line ("\s+key: ..."). DAX expression continuation lines are
  // typically indented deeper than properties, so sampling the first
  // child line is unreliable — restrict to lines matching "key: value".
  // If no property exists in this block, scan another measure in the
  // file. As a last resort, fall back to a single tab (TMDL default).
  let unit = "\t";
  let unitFound = false;
  const findUnitIn = (start: number, end: number): boolean => {
    for (let i = start; i < end; i++) {
      const ln = lines[i];
      if (!ln.trim()) continue;
      const m = ln.match(/^([\t ]+)\w+:\s/);
      if (!m) continue;
      const indentLen = m[1].length;
      if (indentLen > headerIndent) {
        unit = m[1].slice(headerIndent);
        return true;
      }
    }
    return false;
  };
  unitFound = findUnitIn(headerIdx + 1, endIdx);
  if (!unitFound) unitFound = findUnitIn(0, lines.length);
  const propIndent = headerIndentStr + unit;
  const propLineFor = (key: string, val: string | boolean | undefined): string => {
    if (key === "isHidden") return `${propIndent}isHidden: ${val ? "true" : "false"}`;
    // Quote string values that contain spaces or special chars; quote
    // all formatString and description values for safety.
    const sval = String(val ?? "");
    if (key === "formatString" || key === "description") {
      // Escape backslashes and double quotes inside the string.
      const esc = sval.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      return `${propIndent}${key}: "${esc}"`;
    }
    // displayFolder: keep unquoted unless empty/contains ":" (then quote)
    if (sval === "") return `${propIndent}${key}: `;
    if (/[:#]/.test(sval)) {
      const esc = sval.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      return `${propIndent}${key}: "${esc}"`;
    }
    return `${propIndent}${key}: ${sval}`;
  };

  // Pass 0: replace the DAX expression (header line + body) if requested.
  // Must happen before property passes since it shifts line indices.
  if (typeof edits.expression === "string") {
    const newExpr = edits.expression.replace(/\r\n/g, "\n");
    const headerPrefix = `${headerIndentStr}measure ${measureName.match(/^[A-Za-z_][A-Za-z0-9_]*$/) ? measureName : `'${measureName.replace(/'/g, "''")}'`} =`;
    const newHeaderAndBody: string[] = [];
    if (!newExpr.includes("\n")) {
      // Single-line: inline after `=`
      newHeaderAndBody.push(`${headerPrefix} ${newExpr.trim()}`);
    } else {
      // Multi-line: use TMDL ``` block. Indent each line at propIndent.
      newHeaderAndBody.push(`${headerPrefix} \`\`\``);
      for (const ln of newExpr.split("\n")) {
        newHeaderAndBody.push(ln.length > 0 ? `${propIndent}${ln}` : "");
      }
      newHeaderAndBody.push(`${propIndent}\`\`\``);
    }
    // Replace lines [headerIdx, exprEndIdx) with newHeaderAndBody.
    const removed = exprEndIdx - headerIdx;
    lines.splice(headerIdx, removed, ...newHeaderAndBody);
    // Adjust block end index.
    endIdx = endIdx - removed + newHeaderAndBody.length;
  }

  // Pass 1: replace existing property lines in-place.
  // v0.71 — `newName` is a header rewrite (handled above); skip here.
  const editKeys = (Object.keys(edits) as (keyof typeof edits)[]).filter(
    (k) => k !== "expression" && k !== "newName",
  );
  const handled = new Set<string>();
  for (let i = headerIdx + 1; i < endIdx; i++) {
    const trimmed = lines[i].trim();
    const propMatch = trimmed.match(/^(\w+):/);
    if (!propMatch) continue;
    const key = propMatch[1];
    if (editKeys.includes(key as keyof typeof edits) && !handled.has(key)) {
      lines[i] = propLineFor(key, edits[key as keyof typeof edits] as string | boolean | undefined);
      handled.add(key);
    }
  }

  // Pass 2: insert any missing edits at the end of the block.
  const inserts: string[] = [];
  for (const k of editKeys) {
    if (handled.has(k)) continue;
    const v = edits[k];
    if (v === undefined) continue;
    inserts.push(propLineFor(k, v as string | boolean | undefined));
  }
  if (inserts.length > 0) {
    // Find the last non-blank line of the block to insert after.
    let insertAt = endIdx;
    while (insertAt > headerIdx + 1 && !lines[insertAt - 1].trim()) insertAt--;
    lines.splice(insertAt, 0, ...inserts);
  }

  return { text: lines.join("\n"), matched: true };
}

/** Apply a batch of measure-property edits to a semantic model.
 *
 *  Pulls the TMDL definition, patches each affected
 *  `definition/tables/<table>.tmdl` part, and POSTs `updateDefinition`
 *  with the modified parts. Returns nothing; throws on failure.
 *
 *  A measure that cannot be located is reported as an error so the
 *  caller can surface it; partial success is allowed.
 */
export async function updateMeasureProperties(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
  edits: MeasureEdit[],
): Promise<{ updated: number; errors: string[] }> {
  if (edits.length === 0) return { updated: 0, errors: [] };

  const parts = await getSemanticModelDefinition(auth, workspaceId, datasetId);
  if (parts.length === 0) {
    throw new Error("Semantic model definition is empty");
  }

  // Group edits by table.
  const byTable = new Map<string, MeasureEdit[]>();
  for (const e of edits) {
    const arr = byTable.get(e.table) ?? [];
    arr.push(e);
    byTable.set(e.table, arr);
  }

  const errors: string[] = [];
  let updated = 0;

  // Build a new parts array with patched payloads.
  const newParts = parts.map((p) => ({ ...p }));
  for (const [tableName, tableEdits] of byTable) {
    // Match the table TMDL part. Filename may be url-encoded or escaped
    // by Fabric (e.g. "definition/tables/Sales%20Orders.tmdl"); decode
    // the path segment and compare against the table name.
    const part = newParts.find((p) => {
      const m = p.path.match(/definition\/tables\/(.+)\.tmdl$/);
      if (!m) return false;
      try {
        return decodeURIComponent(m[1]) === tableName;
      } catch {
        return m[1] === tableName;
      }
    });
    if (!part) {
      errors.push(`Table TMDL not found: '${tableName}'`);
      continue;
    }
    let text: string;
    try {
      text = base64ToUtf8(part.payload);
    } catch {
      errors.push(`Failed to decode TMDL for table '${tableName}'`);
      continue;
    }
    for (const edit of tableEdits) {
      const { table: _t, measure, ...props } = edit;
      const result = patchMeasureInTmdl(text, measure, props);
      if (!result.matched) {
        errors.push(`Measure '${tableName}'[${measure}] not found in TMDL`);
        continue;
      }
      text = result.text;
      updated++;
    }
    part.payload = utf8ToBase64(text);
  }

  if (updated === 0) {
    return { updated: 0, errors: errors.length ? errors : ["No matching measures patched"] };
  }

  // updateDefinition body shape per Fabric docs:
  // { definition: { parts: [{ path, payload, payloadType }] } }
  await fabricPost<unknown>(
    auth,
    `/workspaces/${workspaceId}/semanticModels/${datasetId}/updateDefinition`,
    { definition: { parts: newParts } },
  );

  return { updated, errors };
}

// ---------------------------------------------------------------------------
// Column / Table / Relationship property write-back (TMDL roundtrip)
// Generalised pattern after `patchMeasureInTmdl`. Each *Edit type lists
// the editable props for one TMDL block kind; `patchTmdlBlockProps`
// handles the property replace+insert plumbing (no expression rewrite,
// kept measure-specific in `patchMeasureInTmdl`).
// ---------------------------------------------------------------------------

export interface ColumnEdit {
  table: string;
  column: string;
  /** v0.71 — rename the column. Rewrites only the TMDL header line
   *  (`column '<old>'` → `column '<new>'`); references in DAX measures /
   *  relationships are NOT cascaded. */
  newName?: string;
  description?: string;
  displayFolder?: string;
  isHidden?: boolean;
  summarizeBy?: string;
  dataCategory?: string;
  formatString?: string;
}

export interface TableEdit {
  table: string;
  description?: string;
  isHidden?: boolean;
}

export interface RelationshipEdit {
  /** Identifier of the relationship via from/to columns (since TMDL
   *  stores the relationship by an opaque guid that isn't surfaced in
   *  ModelData). The patcher locates the matching `relationship` block
   *  by walking until it finds matching `fromColumn:` + `toColumn:` lines. */
  fromTable: string;
  fromColumn: string;
  toTable: string;
  toColumn: string;
  isActive?: boolean;
  crossFilteringBehavior?: string;
}

const ALWAYS_QUOTE_KEYS = new Set(["description", "formatString"]);

function tmdlPropLineFor(
  indent: string,
  key: string,
  val: string | boolean | undefined,
): string {
  if (typeof val === "boolean") return `${indent}${key}: ${val ? "true" : "false"}`;
  const s = String(val ?? "");
  if (ALWAYS_QUOTE_KEYS.has(key)) {
    const esc = s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
    return `${indent}${key}: "${esc}"`;
  }
  if (s === "") return `${indent}${key}: `;
  if (/[:#"\s]/.test(s)) {
    const esc = s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
    return `${indent}${key}: "${esc}"`;
  }
  return `${indent}${key}: ${s}`;
}

/** Generic property patcher for any TMDL block — locates the header line
 *  via the supplied regex (must capture the leading indent in group 1),
 *  walks forward until the indent unwinds, then runs the same two-pass
 *  replace+insert as `patchMeasureInTmdl` (no expression rewrite). */
function patchTmdlBlockProps(
  text: string,
  headerRe: RegExp,
  edits: Record<string, string | boolean | undefined>,
): { text: string; matched: boolean } {
  const lines = text.split(/\r?\n/);
  let headerIdx = -1;
  let headerIndentStr = "";
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(headerRe);
    if (m) {
      headerIdx = i;
      headerIndentStr = m[1];
      break;
    }
  }
  if (headerIdx < 0) return { text, matched: false };
  const headerIndent = headerIndentStr.length;

  let endIdx = lines.length;
  for (let i = headerIdx + 1; i < lines.length; i++) {
    const ln = lines[i];
    if (!ln.trim()) continue;
    const indent = ln.length - ln.trimStart().length;
    if (indent <= headerIndent) { endIdx = i; break; }
  }

  let unit = "\t";
  const findUnitIn = (start: number, end: number): boolean => {
    for (let i = start; i < end; i++) {
      const ln = lines[i];
      if (!ln.trim()) continue;
      const m = ln.match(/^([\t ]+)\w+:\s/);
      if (!m) continue;
      const indentLen = m[1].length;
      if (indentLen > headerIndent) {
        unit = m[1].slice(headerIndent);
        return true;
      }
    }
    return false;
  };
  if (!findUnitIn(headerIdx + 1, endIdx)) findUnitIn(0, lines.length);
  const propIndent = headerIndentStr + unit;

  const editKeys = Object.keys(edits).filter((k) => edits[k] !== undefined);
  const handled = new Set<string>();
  for (let i = headerIdx + 1; i < endIdx; i++) {
    const trimmed = lines[i].trim();
    const propMatch = trimmed.match(/^(\w+):/);
    if (!propMatch) continue;
    const key = propMatch[1];
    if (editKeys.includes(key) && !handled.has(key)) {
      lines[i] = tmdlPropLineFor(propIndent, key, edits[key]);
      handled.add(key);
    }
  }

  const inserts: string[] = [];
  for (const k of editKeys) {
    if (handled.has(k)) continue;
    inserts.push(tmdlPropLineFor(propIndent, k, edits[k]));
  }
  if (inserts.length > 0) {
    let insertAt = endIdx;
    while (insertAt > headerIdx + 1 && !lines[insertAt - 1].trim()) insertAt--;
    lines.splice(insertAt, 0, ...inserts);
  }

  return { text: lines.join("\n"), matched: true };
}

function patchColumnInTmdl(
  text: string,
  columnName: string,
  edits: Omit<ColumnEdit, "table" | "column">,
): { text: string; matched: boolean } {
  const escName = columnName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Column header: `<indent>column '<name>'` or `<indent>column <name>`,
  // optionally followed by ` = <expression>` (calculated columns).
  const headerRe = new RegExp(
    `^([\\t ]*)column\\s+(['"]?)${escName}\\2(\\s|=|$)`,
  );

  // v0.71 — handle rename as a header-line rewrite, then delegate the
  // remaining property edits (without `newName`) to the generic patcher.
  let workingText = text;
  if (typeof edits.newName === "string" && edits.newName !== columnName) {
    const lines = workingText.split(/\r?\n/);
    let headerIdx = -1;
    for (let i = 0; i < lines.length; i++) {
      if (headerRe.test(lines[i])) { headerIdx = i; break; }
    }
    if (headerIdx < 0) return { text, matched: false };
    const newName = edits.newName;
    const quotedNew = /^[A-Za-z_][A-Za-z0-9_]*$/.test(newName)
      ? newName
      : `'${newName.replace(/'/g, "''")}'`;
    lines[headerIdx] = lines[headerIdx].replace(
      new RegExp(`(column\\s+)(['"]?)${escName}\\2`),
      `$1${quotedNew}`,
    );
    workingText = lines.join("\n");
    // Subsequent property edits must locate the renamed column.
    const newEscName = newName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const newHeaderRe = new RegExp(
      `^([\\t ]*)column\\s+(['"]?)${newEscName}\\2(\\s|=|$)`,
    );
    const { newName: _drop, ...rest } = edits;
    return patchTmdlBlockProps(workingText, newHeaderRe, rest as Record<string, string | boolean | undefined>);
  }

  const { newName: _drop, ...rest } = edits;
  return patchTmdlBlockProps(workingText, headerRe, rest as Record<string, string | boolean | undefined>);
}

function patchTableInTmdl(
  text: string,
  tableName: string,
  edits: Omit<TableEdit, "table">,
): { text: string; matched: boolean } {
  const escName = tableName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Table header is always at indent 0.
  const headerRe = new RegExp(
    `^()table\\s+(['"]?)${escName}\\2\\s*$`,
  );
  return patchTmdlBlockProps(text, headerRe, edits as Record<string, string | boolean | undefined>);
}

/** Patch a relationship by from/to identifiers. TMDL relationships are
 *  keyed by an opaque guid in the header (e.g. `relationship 1234-...`)
 *  so we walk every relationship block and match the one whose body
 *  contains both `fromColumn: '<fromTable>'.'<fromCol>'` AND
 *  `toColumn: '<toTable>'.'<toCol>'`. */
function patchRelationshipInTmdl(
  text: string,
  ident: { fromTable: string; fromColumn: string; toTable: string; toColumn: string },
  edits: Omit<RelationshipEdit, "fromTable" | "fromColumn" | "toTable" | "toColumn">,
): { text: string; matched: boolean } {
  const lines = text.split(/\r?\n/);
  // Find every `relationship` header line (any indent).
  const headers: { idx: number; indent: number }[] = [];
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^([\t ]*)relationship\s+\S+/);
    if (m) headers.push({ idx: i, indent: m[1].length });
  }
  // Locate the matching block. The fromColumn/toColumn lines look like:
  //   fromColumn: 'TableName'.'ColumnName'
  // (single quotes stripped where unambiguous). Match either form.
  const colMatches = (line: string, key: "fromColumn" | "toColumn", t: string, c: string): boolean => {
    const re = new RegExp(`^${key}:\\s*['"]?${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}['"]?\\s*\\.\\s*['"]?${c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}['"]?\\s*$`);
    return re.test(line.trim());
  };
  let matchIdx = -1;
  for (let h = 0; h < headers.length; h++) {
    const start = headers[h].idx;
    const end = h + 1 < headers.length ? headers[h + 1].idx : lines.length;
    let sawFrom = false;
    let sawTo = false;
    for (let i = start + 1; i < end; i++) {
      if (colMatches(lines[i], "fromColumn", ident.fromTable, ident.fromColumn)) sawFrom = true;
      if (colMatches(lines[i], "toColumn", ident.toTable, ident.toColumn)) sawTo = true;
    }
    if (sawFrom && sawTo) { matchIdx = start; break; }
  }
  if (matchIdx < 0) return { text, matched: false };

  // Re-derive the header for patchTmdlBlockProps and slice/patch.
  // Easiest path: inline the same prop replace+insert here instead of
  // re-running the regex (we already know matchIdx).
  const headerIndentStr = lines[matchIdx].match(/^([\t ]*)/)?.[1] ?? "";
  const headerIndent = headerIndentStr.length;
  let endIdx = lines.length;
  for (let i = matchIdx + 1; i < lines.length; i++) {
    const ln = lines[i];
    if (!ln.trim()) continue;
    const indent = ln.length - ln.trimStart().length;
    if (indent <= headerIndent) { endIdx = i; break; }
  }
  let unit = "\t";
  for (let i = matchIdx + 1; i < endIdx; i++) {
    const ln = lines[i];
    if (!ln.trim()) continue;
    const m = ln.match(/^([\t ]+)\w+:\s/);
    if (m && m[1].length > headerIndent) { unit = m[1].slice(headerIndent); break; }
  }
  const propIndent = headerIndentStr + unit;
  const editEntries: [string, string | boolean | undefined][] = Object.entries(edits).filter(
    ([, v]) => v !== undefined,
  ) as [string, string | boolean | undefined][];
  const handled = new Set<string>();
  for (let i = matchIdx + 1; i < endIdx; i++) {
    const trimmed = lines[i].trim();
    const propMatch = trimmed.match(/^(\w+):/);
    if (!propMatch) continue;
    const key = propMatch[1];
    const editVal = editEntries.find(([k]) => k === key);
    if (editVal && !handled.has(key)) {
      lines[i] = tmdlPropLineFor(propIndent, key, editVal[1]);
      handled.add(key);
    }
  }
  const inserts: string[] = [];
  for (const [k, v] of editEntries) {
    if (handled.has(k)) continue;
    inserts.push(tmdlPropLineFor(propIndent, k, v));
  }
  if (inserts.length > 0) {
    let insertAt = endIdx;
    while (insertAt > matchIdx + 1 && !lines[insertAt - 1].trim()) insertAt--;
    lines.splice(insertAt, 0, ...inserts);
  }
  return { text: lines.join("\n"), matched: true };
}

/** Apply column property edits via TMDL roundtrip. Mirrors
 *  `updateMeasureProperties` shape: pulls definition, patches each
 *  affected `definition/tables/<table>.tmdl` part, posts updateDefinition. */
export async function updateColumnProperties(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
  edits: ColumnEdit[],
): Promise<{ updated: number; errors: string[] }> {
  if (edits.length === 0) return { updated: 0, errors: [] };
  const parts = await getSemanticModelDefinition(auth, workspaceId, datasetId);
  if (parts.length === 0) throw new Error("Semantic model definition is empty");

  const byTable = new Map<string, ColumnEdit[]>();
  for (const e of edits) {
    const arr = byTable.get(e.table) ?? [];
    arr.push(e);
    byTable.set(e.table, arr);
  }

  const errors: string[] = [];
  let updated = 0;
  const newParts = parts.map((p) => ({ ...p }));
  for (const [tableName, tableEdits] of byTable) {
    const part = newParts.find((p) => {
      const m = p.path.match(/definition\/tables\/(.+)\.tmdl$/);
      if (!m) return false;
      try { return decodeURIComponent(m[1]) === tableName; }
      catch { return m[1] === tableName; }
    });
    if (!part) { errors.push(`Table TMDL not found: '${tableName}'`); continue; }
    let text: string;
    try { text = base64ToUtf8(part.payload); }
    catch { errors.push(`Failed to decode TMDL for table '${tableName}'`); continue; }
    for (const edit of tableEdits) {
      const { table: _t, column, ...props } = edit;
      const result = patchColumnInTmdl(text, column, props);
      if (!result.matched) {
        errors.push(`Column '${tableName}'[${column}] not found in TMDL`);
        continue;
      }
      text = result.text;
      updated++;
    }
    part.payload = utf8ToBase64(text);
  }

  if (updated === 0) {
    return { updated: 0, errors: errors.length ? errors : ["No matching columns patched"] };
  }
  await fabricPost<unknown>(
    auth,
    `/workspaces/${workspaceId}/semanticModels/${datasetId}/updateDefinition`,
    { definition: { parts: newParts } },
  );
  return { updated, errors };
}

/** Apply table-level property edits via TMDL roundtrip. */
export async function updateTableProperties(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
  edits: TableEdit[],
): Promise<{ updated: number; errors: string[] }> {
  if (edits.length === 0) return { updated: 0, errors: [] };
  const parts = await getSemanticModelDefinition(auth, workspaceId, datasetId);
  if (parts.length === 0) throw new Error("Semantic model definition is empty");

  const errors: string[] = [];
  let updated = 0;
  const newParts = parts.map((p) => ({ ...p }));
  for (const edit of edits) {
    const part = newParts.find((p) => {
      const m = p.path.match(/definition\/tables\/(.+)\.tmdl$/);
      if (!m) return false;
      try { return decodeURIComponent(m[1]) === edit.table; }
      catch { return m[1] === edit.table; }
    });
    if (!part) { errors.push(`Table TMDL not found: '${edit.table}'`); continue; }
    let text: string;
    try { text = base64ToUtf8(part.payload); }
    catch { errors.push(`Failed to decode TMDL for table '${edit.table}'`); continue; }
    const { table: _t, ...props } = edit;
    const result = patchTableInTmdl(text, edit.table, props);
    if (!result.matched) {
      errors.push(`Table '${edit.table}' header not found in TMDL`);
      continue;
    }
    part.payload = utf8ToBase64(result.text);
    updated++;
  }
  if (updated === 0) {
    return { updated: 0, errors: errors.length ? errors : ["No matching tables patched"] };
  }
  await fabricPost<unknown>(
    auth,
    `/workspaces/${workspaceId}/semanticModels/${datasetId}/updateDefinition`,
    { definition: { parts: newParts } },
  );
  return { updated, errors };
}

/** Apply relationship property edits via TMDL roundtrip. Relationships
 *  may live in `definition/relationships.tmdl` (top-level) OR inline in
 *  `definition/model.tmdl`. We try every part whose path matches either;
 *  the patcher only mutates the part actually containing the matching
 *  relationship block. */
export async function updateRelationshipProperties(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
  edits: RelationshipEdit[],
): Promise<{ updated: number; errors: string[] }> {
  if (edits.length === 0) return { updated: 0, errors: [] };
  const parts = await getSemanticModelDefinition(auth, workspaceId, datasetId);
  if (parts.length === 0) throw new Error("Semantic model definition is empty");

  const errors: string[] = [];
  let updated = 0;
  const newParts = parts.map((p) => ({ ...p }));
  // Candidate parts: relationships.tmdl + model.tmdl.
  const candidates = newParts.filter((p) =>
    /definition\/relationships\.tmdl$/.test(p.path) || /definition\/model\.tmdl$/.test(p.path),
  );

  for (const edit of edits) {
    const { isActive, crossFilteringBehavior, ...ident } = edit;
    let matched = false;
    for (const part of candidates) {
      let text: string;
      try { text = base64ToUtf8(part.payload); }
      catch { continue; }
      const result = patchRelationshipInTmdl(text, ident, { isActive, crossFilteringBehavior });
      if (result.matched) {
        part.payload = utf8ToBase64(result.text);
        matched = true;
        updated++;
        break;
      }
    }
    if (!matched) {
      errors.push(`Relationship ${ident.fromTable}[${ident.fromColumn}] → ${ident.toTable}[${ident.toColumn}] not found in TMDL`);
    }
  }

  if (updated === 0) {
    return { updated: 0, errors: errors.length ? errors : ["No matching relationships patched"] };
  }
  await fabricPost<unknown>(
    auth,
    `/workspaces/${workspaceId}/semanticModels/${datasetId}/updateDefinition`,
    { definition: { parts: newParts } },
  );
  return { updated, errors };
}

// ---------------------------------------------------------------------------
// Partition source (M / DAX) write-back (TMDL roundtrip) — v0.72
// ---------------------------------------------------------------------------

export interface PartitionEdit {
  table: string;
  partition: string;
  /** New M (Power Query) or DAX expression body for the partition's
   *  `source = …` block. */
  expression: string;
}

/** Patch a single `partition <name>` block's `source = …` body with the
 *  supplied expression. Strategy mirrors `patchMeasureInTmdl`'s
 *  expression-rewrite pass:
 *  - Locate the partition header (`partition '<name>' = <kind>`).
 *  - Find the existing `source = …` line within the block.
 *  - Determine its body span: inline (`source = X`), fenced
 *    (`source = ```` … ````), or none (re-emit fenced).
 *  - Replace span with new expression. Multi-line bodies are emitted
 *    with TMDL ```` fence markers at the partition's child indent. */
function patchPartitionInTmdl(
  text: string,
  partitionName: string,
  expression: string,
): { text: string; matched: boolean } {
  const lines = text.split(/\r?\n/);
  const escName = partitionName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const headerRe = new RegExp(
    `^([\\t ]*)partition\\s+(['"]?)${escName}\\2(\\s|=|$)`,
  );
  let headerIdx = -1;
  let headerIndentStr = "";
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(headerRe);
    if (m) { headerIdx = i; headerIndentStr = m[1]; break; }
  }
  if (headerIdx < 0) return { text, matched: false };
  const headerIndent = headerIndentStr.length;

  // End of partition block.
  let endIdx = lines.length;
  for (let i = headerIdx + 1; i < lines.length; i++) {
    const ln = lines[i];
    if (!ln.trim()) continue;
    const indent = ln.length - ln.trimStart().length;
    if (indent <= headerIndent) { endIdx = i; break; }
  }

  // Detect indent unit by sampling a sibling property line.
  let unit = "\t";
  for (let i = headerIdx + 1; i < endIdx; i++) {
    const ln = lines[i];
    if (!ln.trim()) continue;
    const m = ln.match(/^([\t ]+)\w+(:|\s*=)/);
    if (m && m[1].length > headerIndent) { unit = m[1].slice(headerIndent); break; }
  }
  const childIndent = headerIndentStr + unit;
  const bodyIndent = childIndent + unit;

  // Locate `source = …` line.
  let sourceIdx = -1;
  let sourceIndentStr = "";
  let sourceInlineVal = "";
  for (let i = headerIdx + 1; i < endIdx; i++) {
    const m = lines[i].match(/^([\t ]+)source\s*=\s*(.*)$/);
    if (m) {
      sourceIdx = i;
      sourceIndentStr = m[1];
      sourceInlineVal = m[2];
      break;
    }
  }

  // Build replacement lines.
  const newExpr = expression.replace(/\r\n/g, "\n").replace(/\s+$/, "");
  const isMultiLine = newExpr.includes("\n");
  const replacement: string[] = [];
  if (!isMultiLine) {
    replacement.push(`${childIndent}source = ${newExpr.trim()}`);
  } else {
    replacement.push(`${childIndent}source = \`\`\``);
    for (const ln of newExpr.split("\n")) {
      replacement.push(ln.length > 0 ? `${bodyIndent}${ln}` : "");
    }
    replacement.push(`${bodyIndent}\`\`\``);
  }

  if (sourceIdx < 0) {
    // No existing source — insert at end of block.
    let insertAt = endIdx;
    while (insertAt > headerIdx + 1 && !lines[insertAt - 1].trim()) insertAt--;
    lines.splice(insertAt, 0, ...replacement);
    return { text: lines.join("\n"), matched: true };
  }

  // Determine span of existing source block.
  let sourceEndIdx = sourceIdx + 1;
  if (sourceInlineVal.trim() === "```") {
    // Fenced: walk until the matching closing fence at the body indent.
    while (sourceEndIdx < endIdx) {
      if (lines[sourceEndIdx].trim() === "```") { sourceEndIdx++; break; }
      sourceEndIdx++;
    }
  } else if (sourceInlineVal === "" || sourceInlineVal.trim() === "") {
    // Block form: walk until we hit a sibling property or dedent.
    const srcIndent = sourceIndentStr.length;
    while (sourceEndIdx < endIdx) {
      const ln = lines[sourceEndIdx];
      if (!ln.trim()) { sourceEndIdx++; continue; }
      const li = ln.length - ln.trimStart().length;
      if (li <= srcIndent) break;
      sourceEndIdx++;
    }
  }
  // else: inline single-line — sourceEndIdx already = sourceIdx + 1

  lines.splice(sourceIdx, sourceEndIdx - sourceIdx, ...replacement);
  return { text: lines.join("\n"), matched: true };
}

/** Apply partition source (M / DAX) edits via TMDL roundtrip. */
export async function updatePartitionExpressions(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
  edits: PartitionEdit[],
): Promise<{ updated: number; errors: string[] }> {
  if (edits.length === 0) return { updated: 0, errors: [] };
  const parts = await getSemanticModelDefinition(auth, workspaceId, datasetId);
  if (parts.length === 0) throw new Error("Semantic model definition is empty");

  const byTable = new Map<string, PartitionEdit[]>();
  for (const e of edits) {
    const arr = byTable.get(e.table) ?? [];
    arr.push(e);
    byTable.set(e.table, arr);
  }

  const errors: string[] = [];
  let updated = 0;
  const newParts = parts.map((p) => ({ ...p }));
  for (const [tableName, tableEdits] of byTable) {
    const part = newParts.find((p) => {
      const m = p.path.match(/definition\/tables\/(.+)\.tmdl$/);
      if (!m) return false;
      try { return decodeURIComponent(m[1]) === tableName; }
      catch { return m[1] === tableName; }
    });
    if (!part) { errors.push(`Table TMDL not found: '${tableName}'`); continue; }
    let text: string;
    try { text = base64ToUtf8(part.payload); }
    catch { errors.push(`Failed to decode TMDL for table '${tableName}'`); continue; }
    for (const edit of tableEdits) {
      const result = patchPartitionInTmdl(text, edit.partition, edit.expression);
      if (!result.matched) {
        errors.push(`Partition '${tableName}'[${edit.partition}] not found in TMDL`);
        continue;
      }
      text = result.text;
      updated++;
    }
    part.payload = utf8ToBase64(text);
  }

  if (updated === 0) {
    return { updated: 0, errors: errors.length ? errors : ["No matching partitions patched"] };
  }
  await fabricPost<unknown>(
    auth,
    `/workspaces/${workspaceId}/semanticModels/${datasetId}/updateDefinition`,
    { definition: { parts: newParts } },
  );
  return { updated, errors };
}

// ---------------------------------------------------------------------------
// DAX formatting via daxformatter.com
// ---------------------------------------------------------------------------

/** Format a DAX expression via the public daxformatter.com REST API
 *  (sqlbi). Returns the formatted DAX string. Throws on network/CORS
 *  failure so the caller can offer a clipboard fallback. */
export async function formatDax(
  dax: string,
  opts?: { maxLineLength?: number; shortFormat?: boolean },
): Promise<string> {
  const body = new URLSearchParams({
    Dax: dax,
    MaxLineLength: String(opts?.maxLineLength ?? 0),
    SkipSpaceAfterFunctionName: "0",
    ListSeparator: ",",
    DecimalSeparator: ".",
  });
  const resp = await fetch("https://www.daxformatter.com/api/daxformatter/DaxFormat", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!resp.ok) {
    throw new Error(`daxformatter.com responded ${resp.status}`);
  }
  const text = await resp.text();
  // Response is a JSON-encoded string (wrapped in quotes); strip if so.
  const trimmed = text.trim();
  if (trimmed.startsWith('"') && trimmed.endsWith('"')) {
    try { return JSON.parse(trimmed); } catch { /* fall through */ }
  }
  return text;
}

export async function executeDax(
  auth: PbiAuth,
  workspaceId: string,
  datasetId: string,
  daxQuery: string
): Promise<Record<string, unknown>[]> {
  const resp = await pbiPost<{
    results: { tables: { rows: Record<string, unknown>[] }[] }[];
  }>(
    auth,
    `/groups/${workspaceId}/datasets/${datasetId}/executeQueries`,
    {
      queries: [{ query: daxQuery }],
      serializerSettings: { includeNulls: true },
    }
  );
  return resp.results?.[0]?.tables?.[0]?.rows ?? [];
}

// ---------------------------------------------------------------------------
// Report APIs
// ---------------------------------------------------------------------------

export async function listReports(
  auth: PbiAuth,
  workspaceId: string
): Promise<{ id: string; name: string; reportType?: string }[]> {
  const data = await pbiGet<{
    value: { id: string; name: string; reportType?: string }[];
  }>(auth, `/groups/${workspaceId}/reports`);
  return data.value.map((r) => ({
    id: r.id,
    name: r.name,
    reportType: r.reportType,
  }));
}

export async function loadReportDefinition(
  auth: PbiAuth,
  workspaceId: string,
  reportId: string,
  _reportName: string
): Promise<ReportData> {
  const reportData: ReportData = {
    pages: {},
    format: "",
    reportId,
    workspaceId,
  };

  // Note: very large reports may return 202 with a polling URL — the
  // backend proxy currently does not surface that, so this single-shot
  // call only handles the synchronous (most common) case. Returns an
  // empty report on failure.
  try {
    const result = await fabricPost<{
      definition: {
        parts: { path: string; payload: string; payloadType: string }[];
      };
    }>(
      auth,
      `/workspaces/${workspaceId}/reports/${reportId}/getDefinition`,
      null,
    );
    return parseReportDefinition(result.definition.parts, reportId, workspaceId);
  } catch {
    return reportData;
  }
}

interface ReportDefinitionPart {
  path: string;
  payload: string;
  payloadType: string;
}

function parseReportDefinition(
  parts: ReportDefinitionPart[],
  reportId: string,
  workspaceId: string
): ReportData {
  const reportData: ReportData = {
    pages: {},
    format: "PBIR",
    reportId,
    workspaceId,
  };

  // Find pages.json for page order
  const pageOrderMap: Record<string, number> = {};
  const pagesJsonPart = parts.find((p) => p.path.endsWith("pages.json"));
  if (pagesJsonPart) {
    try {
      const pagesJson = JSON.parse(atob(pagesJsonPart.payload));
      (pagesJson.pageOrder as string[])?.forEach((name: string, idx: number) => {
        pageOrderMap[name] = idx;
      });
    } catch {
      // ignore parse errors
    }
  }

  // Parse individual page.json files
  for (const part of parts) {
    const pageMatch = part.path.match(
      /definition\/pages\/([^/]+)\/page\.json$/
    );
    if (pageMatch) {
      try {
        const pageJson = JSON.parse(atob(part.payload));
        const pageName = pageMatch[1];
        const pageInfo: PageInfo = {
          displayName: pageJson.displayName ?? pageName,
          width: pageJson.width ?? 1280,
          height: pageJson.height ?? 720,
          hidden: pageJson.visibility === "HiddenInViewMode",
          visualCount: 0,
          ordinal: pageOrderMap[pageName] ?? 9999,
          visuals: {},
          rawJson: pageJson,
        };
        reportData.pages[pageName] = pageInfo;
      } catch {
        // skip bad page
      }
    }
  }

  // Parse visual.json files
  for (const part of parts) {
    const visualMatch = part.path.match(
      /definition\/pages\/([^/]+)\/visuals\/([^/]+)\/visual\.json$/
    );
    if (visualMatch) {
      try {
        const visualJson = JSON.parse(atob(part.payload));
        const pageName = visualMatch[1];
        const visualName = visualMatch[2];
        if (reportData.pages[pageName]) {
          const position = visualJson.position ?? {};
          const visual: VisualInfo = {
            type: visualJson.visual?.visualType ?? "",
            displayType: visualJson.visual?.visualType ?? "",
            x: position.x ?? 0,
            y: position.y ?? 0,
            width: position.width ?? 0,
            height: position.height ?? 0,
            hidden: visualJson.isHidden ?? false,
            title:
              visualJson.visual?.visualContainerObjects?.title?.[0]?.properties
                ?.text?.expr?.Literal?.Value ??
              "",
            rawJson: visualJson,
          };
          reportData.pages[pageName].visuals[visualName] = visual;
          reportData.pages[pageName].visualCount++;
        }
      } catch {
        // skip bad visual
      }
    }
  }

  return reportData;
}

// ---------------------------------------------------------------------------
// Notebook create (used by Sempy Runner)
// ---------------------------------------------------------------------------

/**
 * Create a Synapse notebook artifact in a workspace from a Jupyter
 * .ipynb JSON payload. Returns the new item id.
 *
 * Fabric expects a `definition.parts` array; we send a single
 * `notebook-content.ipynb` part containing the base64-encoded ipynb.
 * The proxy already handles 202 LRO + the `OperationHasNoResult` quirk.
 */
export async function createNotebook(
  auth: PbiAuth,
  workspaceId: string,
  displayName: string,
  ipynbJson: string,
  description?: string,
): Promise<{ id: string; displayName: string }> {
  // btoa needs latin-1; encode the JSON via UTF-8 → base64 the safe way.
  const utf8 = new TextEncoder().encode(ipynbJson);
  let binary = "";
  for (let i = 0; i < utf8.length; i++) binary += String.fromCharCode(utf8[i]);
  const payloadB64 = btoa(binary);

  const body = {
    displayName,
    description: description ?? "Generated by PBI Fixer · Sempy Runner",
    definition: {
      format: "ipynb",
      parts: [
        {
          path: "notebook-content.ipynb",
          payload: payloadB64,
          payloadType: "InlineBase64",
        },
      ],
    },
  };

  const result = await fabricPost<{
    id?: string;
    displayName?: string;
  }>(auth, `/workspaces/${workspaceId}/notebooks`, body);

  return {
    id: result.id ?? "",
    displayName: result.displayName ?? displayName,
  };
}
// ---------------------------------------------------------------------------
// Power BI embed token (used by ReportExplorer live preview)
// ---------------------------------------------------------------------------

export interface ReportEmbedInfo {
  /** Short-lived embed token returned by GenerateToken. */
  token: string;
  /** ISO timestamp when the token expires. */
  expiration: string;
  /** `https://app.powerbi.com/reportEmbed?reportId=…&groupId=…` */
  embedUrl: string;
  reportId: string;
  workspaceId: string;
}

/**
 * Mint a Power BI embed token for a report so the live preview iframe
 * doesn't depend on third-party app.powerbi.com cookies (which Fabric's
 * iframe-in-iframe context strips). Calls
 * `POST /v1.0/myorg/groups/{ws}/reports/{id}/GenerateToken` with
 * `accessLevel: "View"`. The OBO PBI token in the proxy already has
 * the right audience.
 */
export async function getReportEmbedToken(
  auth: PbiAuth,
  workspaceId: string,
  reportId: string,
): Promise<ReportEmbedInfo> {
  const resp = await pbiPost<{
    token: string;
    expiration: string;
    tokenId?: string;
  }>(
    auth,
    `/groups/${workspaceId}/reports/${reportId}/GenerateToken`,
    { accessLevel: "View" },
  );

  return {
    token: resp.token,
    expiration: resp.expiration,
    embedUrl: `https://app.powerbi.com/reportEmbed?reportId=${reportId}&groupId=${workspaceId}`,
    reportId,
    workspaceId,
  };
}
