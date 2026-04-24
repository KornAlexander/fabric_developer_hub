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
    let currentObject: null | { kind: "column" | "measure" | "hierarchy"; name: string; indent: number; props: Record<string, string>; expression: string[] } = null;

    const flush = () => {
      if (!currentObject || !tableInfo) return;
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
        tableInfo.hierarchies[currentObject.name] = {};
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

      const objMatch = trimmed.match(/^(column|measure|hierarchy)\s+(['"]?)([^=]+?)\2(?:\s*=\s*(.*))?$/);
      if (objMatch) {
        flush();
        currentObject = {
          kind: objMatch[1] as "column" | "measure" | "hierarchy",
          name: objMatch[3].trim(),
          indent,
          props: {},
          expression: objMatch[4] ? [objMatch[4].trim()] : [],
        };
        continue;
      }

      if (currentObject) {
        const propMatch = trimmed.match(/^(\w+):\s*(.*)$/);
        if (propMatch) {
          currentObject.props[propMatch[1]] = propMatch[2].replace(/^['"]|['"]$/g, "");
          continue;
        }
        // TMDL keyword properties (no colon) — annotations, lineage tags,
        // changed-property markers, etc. These are NOT part of the DAX
        // expression and must not be appended to it.
        if (/^(annotation|lineageTag|changedProperty|extendedProperty|kind|sourceLineageTag|queryGroup|relatedColumnDetails)\b/.test(trimmed)) {
          continue;
        }
        if (currentObject.kind === "measure" || currentObject.kind === "column") {
          // Continuation of expression (multi-line DAX block)
          currentObject.expression.push(trimmed);
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
  const editKeys = (Object.keys(edits) as (keyof typeof edits)[]).filter((k) => k !== "expression");
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

