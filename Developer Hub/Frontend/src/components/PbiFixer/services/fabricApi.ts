// Fabric REST API service layer
// Replaces sempy_labs / sempy.fabric Python calls with direct REST API calls.
// Authentication is handled externally (token passed in).

import {
  ModelData,
  TableInfo,
  ReportData,
  PageInfo,
  VisualInfo,
} from "../types";

const FABRIC_API = "https://api.fabric.microsoft.com/v1";
const PBI_API = "https://api.powerbi.com/v1.0/myorg";

async function apiFetch<T>(
  url: string,
  token: string,
  method: "GET" | "POST" = "GET",
  body?: unknown
): Promise<T> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
  const resp = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API ${method} ${url} failed (${resp.status}): ${text}`);
  }
  return resp.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Workspace helpers
// ---------------------------------------------------------------------------

export async function listWorkspaces(
  token: string
): Promise<{ id: string; name: string }[]> {
  const data = await apiFetch<{ value: { id: string; displayName: string }[] }>(
    `${FABRIC_API}/workspaces`,
    token
  );
  return data.value.map((w) => ({ id: w.id, name: w.displayName }));
}

export async function resolveWorkspaceId(
  token: string,
  nameOrId: string
): Promise<string> {
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(nameOrId)) {
    return nameOrId;
  }
  const workspaces = await listWorkspaces(token);
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
  token: string,
  workspaceId: string
): Promise<{ id: string; name: string }[]> {
  const data = await apiFetch<{
    value: { id: string; name: string }[];
  }>(`${PBI_API}/groups/${workspaceId}/datasets`, token);
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
  token: string,
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

  try {
    const tablesResp = await apiFetch<{ value: PbiTable[] }>(
      `${PBI_API}/groups/${workspaceId}/datasets/${datasetId}/tables`,
      token
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
  } catch {
    // Tables endpoint may not be available for all dataset types
  }

  // Relationships
  try {
    const relsResp = await apiFetch<{ value: PbiRelationship[] }>(
      `${PBI_API}/groups/${workspaceId}/datasets/${datasetId}/relationships`,
      token
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
    // Relationships endpoint may fail
  }

  return modelData;
}

export async function executeDax(
  token: string,
  workspaceId: string,
  datasetId: string,
  daxQuery: string
): Promise<Record<string, unknown>[]> {
  const resp = await apiFetch<{
    results: { tables: { rows: Record<string, unknown>[] }[] }[];
  }>(
    `${PBI_API}/groups/${workspaceId}/datasets/${datasetId}/executeQueries`,
    token,
    "POST",
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
  token: string,
  workspaceId: string
): Promise<{ id: string; name: string; reportType?: string }[]> {
  const data = await apiFetch<{
    value: { id: string; name: string; reportType?: string }[];
  }>(`${PBI_API}/groups/${workspaceId}/reports`, token);
  return data.value.map((r) => ({
    id: r.id,
    name: r.name,
    reportType: r.reportType,
  }));
}

export async function loadReportDefinition(
  token: string,
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

  const defResp = await fetch(
    `${FABRIC_API}/workspaces/${workspaceId}/reports/${reportId}/getDefinition`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
    }
  );

  if (defResp.status === 202) {
    const location = defResp.headers.get("Location");
    const retryAfter = parseInt(defResp.headers.get("Retry-After") ?? "5", 10);
    if (location) {
      await new Promise((r) => setTimeout(r, (retryAfter + 2) * 1000));
      const resultResp = await apiFetch<{
        definition: {
          parts: { path: string; payload: string; payloadType: string }[];
        };
      }>(`${location}/result`, token);
      return parseReportDefinition(resultResp.definition.parts, reportId, workspaceId);
    }
  } else if (defResp.ok) {
    const result = (await defResp.json()) as {
      definition: {
        parts: { path: string; payload: string; payloadType: string }[];
      };
    };
    return parseReportDefinition(result.definition.parts, reportId, workspaceId);
  }

  return reportData;
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
