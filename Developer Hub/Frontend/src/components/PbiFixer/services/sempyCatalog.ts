// WS-P — Sempy Runner catalog.
//
// Hand-curated subset of semantic-link / semantic-link-labs functions
// most useful for PBI / model administration. Each entry drives the
// Sempy Runner builder: typed params (workspace / report / dataset /
// lakehouse) auto-bind from the connection bar, the rest render as
// generic inputs. Functions whose params don't fit at all still work
// — every param shows up as a free-text input.
//
// Catalog grows over time; safe to extend without touching the page.

export type SempyParamKind =
  | "workspace"
  | "report"
  | "dataset"
  | "lakehouse"
  | "text"
  | "multiline"
  | "bool"
  | "number";

export interface SempyParam {
  name: string;
  kind: SempyParamKind;
  required?: boolean;
  default?: string | number | boolean;
  /** Short helper text shown below the input. */
  hint?: string;
}

export type SempyCategory =
  | "Workspace"
  | "Model"
  | "Report"
  | "Refresh"
  | "Vertipaq"
  | "Lakehouse"
  | "Misc";

export interface SempyFunction {
  id: string;
  /** Module to import — `sempy_labs` or `sempy.fabric`. */
  module: "sempy_labs" | "sempy.fabric" | "sempy_labs.report" | "sempy_labs.lakehouse" | "sempy_labs.tom";
  /** Alias used in the generated code (e.g. `labs`, `fabric`). */
  alias: string;
  name: string;
  description: string;
  category: SempyCategory;
  params: SempyParam[];
  returnsDataFrame: boolean;
  docUrl?: string;
}

export const SEMPY_CATALOG: SempyFunction[] = [
  // ── Workspace ──────────────────────────────────────────────────
  {
    id: "list_workspaces",
    module: "sempy.fabric",
    alias: "fabric",
    name: "list_workspaces",
    description: "List all Fabric workspaces the user can access.",
    category: "Workspace",
    params: [],
    returnsDataFrame: true,
    docUrl: "https://learn.microsoft.com/python/api/semantic-link-sempy/sempy.fabric",
  },
  {
    id: "list_items",
    module: "sempy.fabric",
    alias: "fabric",
    name: "list_items",
    description: "List all items in a workspace (reports, semantic models, lakehouses, …).",
    category: "Workspace",
    params: [
      { name: "workspace", kind: "workspace", required: false },
      { name: "type", kind: "text", required: false, hint: "Optional filter, e.g. \"Report\" or \"SemanticModel\"." },
    ],
    returnsDataFrame: true,
  },
  {
    id: "list_reports",
    module: "sempy.fabric",
    alias: "fabric",
    name: "list_reports",
    description: "List all Power BI reports in a workspace.",
    category: "Workspace",
    params: [{ name: "workspace", kind: "workspace", required: false }],
    returnsDataFrame: true,
  },
  {
    id: "list_datasets",
    module: "sempy.fabric",
    alias: "fabric",
    name: "list_datasets",
    description: "List all semantic models in a workspace.",
    category: "Workspace",
    params: [{ name: "workspace", kind: "workspace", required: false }],
    returnsDataFrame: true,
  },
  {
    id: "list_capacities",
    module: "sempy_labs",
    alias: "labs",
    name: "list_capacities",
    description: "List all Fabric capacities in the tenant.",
    category: "Workspace",
    params: [],
    returnsDataFrame: true,
  },
  {
    id: "list_dashboards",
    module: "sempy.fabric",
    alias: "fabric",
    name: "list_dashboards",
    description: "List Power BI dashboards in a workspace.",
    category: "Workspace",
    params: [{ name: "workspace", kind: "workspace", required: false }],
    returnsDataFrame: true,
  },

  // ── Model (semantic) ───────────────────────────────────────────
  {
    id: "list_tables",
    module: "sempy.fabric",
    alias: "fabric",
    name: "list_tables",
    description: "List tables in a semantic model.",
    category: "Model",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "list_columns",
    module: "sempy.fabric",
    alias: "fabric",
    name: "list_columns",
    description: "List columns in a semantic model.",
    category: "Model",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "list_measures",
    module: "sempy.fabric",
    alias: "fabric",
    name: "list_measures",
    description: "List measures (with DAX) in a semantic model.",
    category: "Model",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "list_relationships",
    module: "sempy.fabric",
    alias: "fabric",
    name: "list_relationships",
    description: "List relationships in a semantic model.",
    category: "Model",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "list_calculation_groups",
    module: "sempy_labs",
    alias: "labs",
    name: "list_calculation_groups",
    description: "List calculation groups defined on a semantic model.",
    category: "Model",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "list_translations",
    module: "sempy_labs",
    alias: "labs",
    name: "list_translations",
    description: "List culture translations on a semantic model.",
    category: "Model",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "list_perspectives",
    module: "sempy_labs",
    alias: "labs",
    name: "list_perspectives",
    description: "List perspectives on a semantic model.",
    category: "Model",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "evaluate_dax",
    module: "sempy.fabric",
    alias: "fabric",
    name: "evaluate_dax",
    description: "Run a DAX query against a semantic model and return rows as a DataFrame.",
    category: "Model",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "dax_string", kind: "multiline", required: true, hint: "Full DAX, e.g. EVALUATE INFO.MEASURES()" },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "run_model_bpa",
    module: "sempy_labs",
    alias: "labs",
    name: "run_model_bpa",
    description: "Run Best Practice Analyzer on a semantic model.",
    category: "Model",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
      { name: "extended", kind: "bool", required: false, default: false, hint: "Include vertipaq metrics in evaluation." },
    ],
    returnsDataFrame: true,
  },
  {
    id: "deploy_semantic_model",
    module: "sempy_labs",
    alias: "labs",
    name: "deploy_semantic_model",
    description: "Deploy a semantic model to a target workspace.",
    category: "Model",
    params: [
      { name: "source_dataset", kind: "dataset", required: true },
      { name: "source_workspace", kind: "workspace", required: false },
      { name: "target_dataset", kind: "text", required: true },
      { name: "target_workspace", kind: "text", required: false },
      { name: "refresh_target_dataset", kind: "bool", required: false, default: true },
    ],
    returnsDataFrame: false,
  },

  // ── Report ─────────────────────────────────────────────────────
  {
    id: "list_report_pages",
    module: "sempy_labs",
    alias: "labs",
    name: "list_report_pages",
    description: "List pages defined in a Power BI report (PBIR).",
    category: "Report",
    params: [
      { name: "report", kind: "report", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "list_report_visuals",
    module: "sempy_labs",
    alias: "labs",
    name: "list_report_visuals",
    description: "List all visuals in a Power BI report with page/position/type.",
    category: "Report",
    params: [
      { name: "report", kind: "report", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "report_rebind",
    module: "sempy_labs",
    alias: "labs",
    name: "report_rebind",
    description: "Rebind a report to a different semantic model.",
    category: "Report",
    params: [
      { name: "report", kind: "report", required: true },
      { name: "dataset", kind: "dataset", required: true, hint: "Target semantic model name." },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: false,
  },
  {
    id: "export_report",
    module: "sempy_labs",
    alias: "labs",
    name: "export_report",
    description: "Export a report (PNG / PDF / PPTX / …) via the Power BI Export API.",
    category: "Report",
    params: [
      { name: "report", kind: "report", required: true },
      { name: "export_format", kind: "text", required: true, default: "PNG", hint: "PNG | PDF | PPTX | DOCX | …" },
      { name: "file_name", kind: "text", required: false },
      { name: "page_name", kind: "text", required: false, hint: "Optional single page to export." },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: false,
  },
  {
    id: "clone_report",
    module: "sempy_labs",
    alias: "labs",
    name: "clone_report",
    description: "Clone a report into a new report (same workspace by default).",
    category: "Report",
    params: [
      { name: "report", kind: "report", required: true },
      { name: "cloned_report", kind: "text", required: true, hint: "Name of the new report." },
      { name: "workspace", kind: "workspace", required: false },
      { name: "target_workspace", kind: "text", required: false },
      { name: "target_dataset", kind: "text", required: false },
    ],
    returnsDataFrame: false,
  },

  // ── Refresh ────────────────────────────────────────────────────
  {
    id: "refresh_semantic_model",
    module: "sempy_labs",
    alias: "labs",
    name: "refresh_semantic_model",
    description: "Trigger a refresh of a semantic model (with optional refresh_type).",
    category: "Refresh",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
      { name: "refresh_type", kind: "text", required: false, default: "full", hint: "full | clearValues | calculate | dataOnly | …" },
    ],
    returnsDataFrame: false,
  },
  {
    id: "list_refresh_history",
    module: "sempy_labs",
    alias: "labs",
    name: "get_semantic_model_refresh_history",
    description: "Get the refresh history of a semantic model.",
    category: "Refresh",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "cancel_dataset_refresh",
    module: "sempy_labs",
    alias: "labs",
    name: "cancel_dataset_refresh",
    description: "Cancel an in-progress semantic model refresh.",
    category: "Refresh",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
      { name: "request_id", kind: "text", required: false },
    ],
    returnsDataFrame: false,
  },

  // ── Vertipaq / Performance ─────────────────────────────────────
  {
    id: "vertipaq_analyzer",
    module: "sempy_labs",
    alias: "labs",
    name: "vertipaq_analyzer",
    description: "Run Vertipaq Analyzer and return per-table / per-column storage metrics.",
    category: "Vertipaq",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
      { name: "export", kind: "text", required: false, hint: "zip | table | …" },
    ],
    returnsDataFrame: true,
  },
  {
    id: "get_semantic_model_size",
    module: "sempy_labs",
    alias: "labs",
    name: "get_semantic_model_size",
    description: "Return the total in-memory size of a semantic model.",
    category: "Vertipaq",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: false,
  },
  {
    id: "model_calc_dependencies",
    module: "sempy_labs",
    alias: "labs",
    name: "get_model_calc_dependencies",
    description: "Show DAX calculation dependencies between measures / columns.",
    category: "Vertipaq",
    params: [
      { name: "dataset", kind: "dataset", required: true },
      { name: "workspace", kind: "workspace", required: false },
    ],
    returnsDataFrame: true,
  },

  // ── Lakehouse ──────────────────────────────────────────────────
  {
    id: "list_lakehouses",
    module: "sempy_labs",
    alias: "labs",
    name: "list_lakehouses",
    description: "List lakehouses in a workspace.",
    category: "Lakehouse",
    params: [{ name: "workspace", kind: "workspace", required: false }],
    returnsDataFrame: true,
  },
  {
    id: "get_lakehouse_tables",
    module: "sempy_labs",
    alias: "labs",
    name: "get_lakehouse_tables",
    description: "List Delta tables inside a lakehouse with size / row count.",
    category: "Lakehouse",
    params: [
      { name: "lakehouse", kind: "lakehouse", required: false },
      { name: "workspace", kind: "workspace", required: false },
      { name: "extended", kind: "bool", required: false, default: false },
    ],
    returnsDataFrame: true,
  },
  {
    id: "list_warehouses",
    module: "sempy_labs",
    alias: "labs",
    name: "list_warehouses",
    description: "List warehouses in a workspace.",
    category: "Lakehouse",
    params: [{ name: "workspace", kind: "workspace", required: false }],
    returnsDataFrame: true,
  },

  // ── Misc / utility ─────────────────────────────────────────────
  {
    id: "list_apps",
    module: "sempy_labs",
    alias: "labs",
    name: "list_apps",
    description: "List Power BI apps in the tenant.",
    category: "Misc",
    params: [],
    returnsDataFrame: true,
  },
  {
    id: "list_dataflows",
    module: "sempy_labs",
    alias: "labs",
    name: "list_dataflows",
    description: "List dataflows in a workspace.",
    category: "Misc",
    params: [{ name: "workspace", kind: "workspace", required: false }],
    returnsDataFrame: true,
  },
  {
    id: "list_pipelines",
    module: "sempy_labs",
    alias: "labs",
    name: "list_data_pipelines",
    description: "List data pipelines in a workspace.",
    category: "Misc",
    params: [{ name: "workspace", kind: "workspace", required: false }],
    returnsDataFrame: true,
  },
];

/* -------------------------------------------------------------------- */
/* Code generation                                                      */
/* -------------------------------------------------------------------- */

export interface SempyArgValues {
  /** Map of param name → value as the user entered it. Empty / undefined
   *  values are skipped (so the call uses the function's own default). */
  [paramName: string]: string | number | boolean | undefined;
}

/** Render a Python literal for a single value. Strings get triple-quoted
 *  if they contain newlines so multi-line DAX stays readable. */
function pyRepr(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value === "number") return String(value);
  if (value.includes("\n")) {
    // Triple-quoted, escape any internal triple quotes.
    const safe = value.replace(/"""/g, '\\"\\"\\"');
    return `"""${safe}"""`;
  }
  // Single-line string — escape backslashes + double quotes.
  const safe = value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  return `"${safe}"`;
}

/** Build a Python call snippet for the given function + values. */
export function generateSempyCode(fn: SempyFunction, values: SempyArgValues): string {
  const importLine = `import ${fn.module}${fn.alias && fn.alias !== fn.module ? ` as ${fn.alias}` : ""}`;
  const callPrefix = fn.alias && fn.alias !== fn.module ? `${fn.alias}.${fn.name}` : `${fn.module}.${fn.name}`;

  const args: string[] = [];
  for (const p of fn.params) {
    const raw = values[p.name];
    const isEmpty =
      raw === undefined ||
      raw === null ||
      (typeof raw === "string" && raw.trim() === "");
    if (isEmpty) {
      // For required params, still emit so the user sees the slot.
      if (p.required) {
        args.push(`    ${p.name}=${pyRepr("")},  # TODO: required`);
      }
      continue;
    }
    let v: string | number | boolean = raw as any;
    if (p.kind === "number" && typeof v === "string") {
      const n = Number(v);
      if (!Number.isNaN(n)) v = n;
    }
    if (p.kind === "bool" && typeof v === "string") {
      v = v === "true" || v === "True" || v === "1";
    }
    args.push(`    ${p.name}=${pyRepr(v)},`);
  }

  const callBlock = args.length
    ? `${callPrefix}(\n${args.join("\n")}\n)`
    : `${callPrefix}()`;

  const lines = [
    `# ${fn.module}.${fn.name}`,
    `# ${fn.description}`,
    importLine,
    "",
    `result = ${callBlock}`,
  ];
  if (fn.returnsDataFrame) {
    lines.push("display(result)");
  } else {
    lines.push("print(result)");
  }
  return lines.join("\n");
}

/** Wrap the snippet into a Jupyter notebook (.ipynb v4) JSON string. */
export function codeToNotebookJson(code: string, title: string): string {
  const nb = {
    cells: [
      {
        cell_type: "markdown",
        metadata: {},
        source: [`# ${title}`, "", "Generated by **PBI Fixer ▸ Sempy Runner**.", "", "Click **Run all** to execute."],
      },
      {
        cell_type: "code",
        metadata: {},
        execution_count: null,
        outputs: [],
        source: code.split("\n").map((l, i, a) => (i === a.length - 1 ? l : l + "\n")),
      },
    ],
    metadata: {
      kernelspec: { display_name: "Synapse PySpark", language: "python", name: "synapse_pyspark" },
      language_info: { name: "python" },
      microsoft: { language: "python" },
    },
    nbformat: 4,
    nbformat_minor: 5,
  };
  return JSON.stringify(nb, null, 2);
}
