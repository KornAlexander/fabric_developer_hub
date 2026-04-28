"""Custom Fabric MCP Server — provides Fabric/OneLake tools via the MCP protocol.

Why custom instead of the official @microsoft/fabric-mcp?
The official .NET server uses DefaultAzureCredential (designed for local 'az login').
In our multi-tenant backend, each request carries a different user's OBO token.
DefaultAzureCredential cannot accept raw bearer tokens, so we implement our own
server that reads tokens from environment variables injected per-request by
MCPClientManager.

Tools:
  Fabric REST API (requires FABRIC_API_TOKEN):
    - fabric_list_workspaces
    - fabric_create_workspace
    - fabric_list_items
        - fabric_list_folders
        - fabric_create_folder
    - fabric_create_item

  OneLake DFS API (requires ONELAKE_TOKEN):
    - fabric_list_files
    - fabric_read_file
    - fabric_write_file
    - fabric_delete_file
    - fabric_create_directory
"""

import asyncio
import base64
import json
import logging
import os
import sys
import time
from urllib.parse import urlencode

# When this file is spawned as a standalone script (``python fabric.py``),
# ``sys.path[0]`` is its own directory and the ``mcp_servers`` package is
# not importable. Insert the parent (``src/``) so the sibling import below
# resolves the same way it does when imported as a module under pytest.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from jose import jwt
from mcp.server.fastmcp import FastMCP

from mcp_servers._common import format_http_error, shared_client
from services.logging_categories import log_extra
from services.observability import bounded_text, stable_digest

# API endpoints
FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
POWERBI_API_BASE = "https://api.powerbi.com/v1.0/myorg"
ONELAKE_DFS_BASE = "https://onelake.dfs.fabric.microsoft.com"

# ``log_level="WARNING"`` stops FastMCP from installing a bare
# ``logging.basicConfig(level=INFO, format="%(message)s")`` handler that
# would emit unstructured ``Processing request of type ...`` lines on
# this subprocess's stderr (captured by Docker under ``backend-1 |``).
mcp = FastMCP("fabric", log_level="WARNING")
logger = logging.getLogger(__name__)


def _ensure_tool_logger() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - "
        "[log_category %(log_category)s req %(request_id)s u:%(user_id)s s:%(session_id)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _tool_extra(category: str) -> dict:
    extra = log_extra(category)  # type: ignore[arg-type]
    extra.setdefault("request_id", os.environ.get("AGENTHUB_REQUEST_ID", "-"))
    extra.setdefault("user_id", os.environ.get("AGENTHUB_USER_ID", "-"))
    extra.setdefault("session_id", os.environ.get("AGENTHUB_SESSION_ID", "-"))
    return extra


def _tool_actor_context_from_env() -> dict[str, str]:
    mapping = {
        "actorRole": "AGENTHUB_ACTOR_ROLE",
        "agentId": "AGENTHUB_AGENT_ID",
        "agentName": "AGENTHUB_AGENT_NAME",
        "agentSessionId": "AGENTHUB_AGENT_SESSION_ID",
        "runId": "AGENTHUB_RUN_ID",
        "taskId": "AGENTHUB_TASK_ID",
        "taskTitle": "AGENTHUB_TASK_TITLE",
        "toolCallId": "AGENTHUB_TOOL_CALL_ID",
    }
    context: dict[str, str] = {}
    for field_name, env_name in mapping.items():
        value = os.environ.get(env_name)
        if value:
            context[field_name] = bounded_text(value, max_chars=500)
    if context:
        context["source"] = "agenthub_mcp_subprocess"
    return context


def _tool_progress_record(tool_name: str, step: str, status: str, fields: dict) -> dict:
    row = {
        "toolName": tool_name,
        "step": step,
        "status": status,
        **_tool_actor_context_from_env(),
        **{key: value for key, value in fields.items() if value not in (None, "", [])},
    }
    if row.get("runId") and row.get("taskId"):
        row.setdefault("communicationPath", "generalist_context_pack_to_subagent_tool_call")
    return row


def _log_tool_progress(tool_name: str, step: str, status: str, **fields) -> None:
    try:
        _ensure_tool_logger()
        row = _tool_progress_record(tool_name, step, status, fields)
        digest = stable_digest(row)
        payload = json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
        logger.info(
            "[TOOL_PROGRESS:%s actorRole=%s agentId=%s agentSessionId=%s runId=%s taskId=%s toolCallId=%s step=%s status=%s digest=%s] %s",
            tool_name,
            row.get("actorRole", "-"),
            row.get("agentId", "-"),
            row.get("agentSessionId", "-"),
            row.get("runId", "-"),
            row.get("taskId", "-"),
            row.get("toolCallId", "-"),
            step,
            status,
            digest,
            bounded_text(payload, max_chars=1800),
            extra=_tool_extra("detailed"),
        )
    except Exception:
        pass


def _record_inventory_progress(
    progress: list[dict],
    started_at: float,
    step: str,
    status: str,
    **fields,
) -> None:
    row = {
        "step": step,
        "status": status,
        "elapsedMs": int((time.monotonic() - started_at) * 1000),
        **{key: value for key, value in fields.items() if value not in (None, "", [])},
    }
    progress.append(row)
    _log_tool_progress(
        "fabric_create_workspace_inventory_solution",
        step,
        status,
        elapsedMs=row.get("elapsedMs"),
        **{key: value for key, value in row.items() if key not in {"step", "status", "elapsedMs"}},
    )


ITEM_ROUTE_SEGMENTS = {
    "DataPipeline": "pipelines",
    "Lakehouse": "lakehouses",
    "Notebook": "notebooks",
    "Report": "reports",
    "Dashboard": "dashboards",
    "Warehouse": "warehouses",
    "SemanticModel": "semanticmodels",
    "SQLEndpoint": "sqlendpoints",
    "Eventstream": "eventstreams",
    "KQLDatabase": "kqldatabases",
    "KQLDashboard": "kqldashboards",
    "KQLQueryset": "kqlquerysets",
    "Environment": "environments",
    "SparkJobDefinition": "sparkjobdefinitions",
    "CopyJob": "copyjobs",
}


def _fabric_headers() -> dict:
    token = os.environ.get("FABRIC_API_TOKEN", "")
    if not token:
        raise RuntimeError("FABRIC_API_TOKEN not set — user may not be authenticated.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _powerbi_headers() -> dict:
    token = os.environ.get("POWERBI_API_TOKEN", "")
    if not token:
        raise RuntimeError("POWERBI_API_TOKEN not set — user may not be authenticated.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _powerbi_header_candidates() -> list[tuple[str, dict]]:
    candidates: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for env_name in ("POWERBI_API_TOKEN", "FABRIC_API_TOKEN"):
        token = os.environ.get(env_name, "")
        if not token or token in seen:
            continue
        seen.add(token)
        candidates.append((env_name, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}))
    if not candidates:
        raise RuntimeError("POWERBI_API_TOKEN or FABRIC_API_TOKEN not set — user may not be authenticated.")
    return candidates


def _onelake_headers() -> dict:
    token = os.environ.get("ONELAKE_TOKEN", "")
    if not token:
        raise RuntimeError("ONELAKE_TOKEN not set — user may not be authenticated.")
    return {
        "Authorization": f"Bearer {token}",
        "x-ms-version": "2023-11-03",
    }


def _get_item_route_segment(item_type: str) -> str:
    if item_type in ITEM_ROUTE_SEGMENTS:
        return ITEM_ROUTE_SEGMENTS[item_type]

    slug = []
    for index, char in enumerate(item_type):
        if index > 0 and char.isupper() and item_type[index - 1].islower():
            slug.append("-")
        slug.append(char.lower())
    return f"{''.join(slug)}s"


def _build_item_links(workspace_id: str, item_id: str, item_type: str) -> dict:
    route_segment = _get_item_route_segment(item_type)
    host_path = f"/groups/{workspace_id}/{route_segment}/{item_id}"
    query = ""

    token = os.environ.get("FABRIC_API_TOKEN", "")
    if token:
        try:
            claims = jwt.get_unverified_claims(token)
            tenant_id = claims.get("tid")
            if tenant_id:
                query = f"?ctid={tenant_id}&experience=fabric-developer"
        except Exception:
            query = ""

    return {
        "hostPath": host_path,
        "webUrl": f"https://app.powerbi.com{host_path}{query}",
    }


def _inline_json_part(path: str, payload: dict) -> dict:
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return {"path": path, "payload": encoded, "payloadType": "InlineBase64"}


def _inline_text_part(path: str, payload: str) -> dict:
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return {"path": path, "payload": encoded, "payloadType": "InlineBase64"}


def _passthrough_part(path: str, payload_b64: str) -> dict:
    """Forward an already-base64 payload from a getDefinition response back
    into a createItem call without round-tripping through json.loads (which
    can lose float precision and reorder dict keys)."""
    return {"path": path, "payload": payload_b64, "payloadType": "InlineBase64"}


def _m_string(value: object) -> str:
    return '"' + str(value or "").replace('"', '""').replace("\r", " ").replace("\n", " ")[:500] + '"'


def _dax_string_literal(value: object) -> str:
    return '"' + str(value or "").replace('"', '""').replace("\r", " ").replace("\n", " ")[:500] + '"'


def _inventory_rows_m(items: list[dict]) -> str:
    rows = []
    for item in items[:500]:
        rows.append(
            "{" + ", ".join(
                _m_string(value)
                for value in (
                    item.get("workspaceName"),
                    item.get("workspaceId"),
                    item.get("displayName") or item.get("name"),
                    item.get("type"),
                    item.get("id"),
                    item.get("folderId"),
                    item.get("webUrl"),
                )
            ) + "}"
        )
    row_block = ",\n        ".join(rows) or "{}"
    return (
        "let\n"
        "    Source = #table(\n"
        "        type table [WorkspaceName = text, WorkspaceId = text, ItemName = text, ItemType = text, ItemId = text, FolderId = text, WebUrl = text],\n"
        f"        {{{row_block}}}\n"
        "    )\n"
        "in\n"
        "    Source"
    )


def _inventory_rows_dax_datatable(items: list[dict]) -> str:
    """Build a DAX DATATABLE expression for use in a TMSL calculated
    partition. Calculated tables are evaluated when the model is first
    loaded (and on every model reload) so they do not require an
    explicit refresh through the Fabric/PBI refresh APIs — which is
    important because those APIs return 401/404 on Pro workspaces."""
    row_blocks = []
    for item in items[:500]:
        values = [
            _dax_string_literal(item.get("workspaceName")),
            _dax_string_literal(item.get("workspaceId")),
            _dax_string_literal(item.get("displayName") or item.get("name")),
            _dax_string_literal(item.get("type")),
            _dax_string_literal(item.get("id")),
            _dax_string_literal(item.get("folderId")),
            _dax_string_literal(item.get("webUrl")),
        ]
        row_blocks.append("{" + ",".join(values) + "}")
    if not row_blocks:
        row_blocks.append('{"","","","","","",""}')
    rows_dax = ",\n  ".join(row_blocks)
    return (
        'DATATABLE(\n'
        '  "WorkspaceName", STRING,\n'
        '  "WorkspaceId", STRING,\n'
        '  "ItemName", STRING,\n'
        '  "ItemType", STRING,\n'
        '  "ItemId", STRING,\n'
        '  "FolderId", STRING,\n'
        '  "WebUrl", STRING,\n'
        f'  {{\n  {rows_dax}\n  }}\n'
        ')'
    )


def _semantic_model_definition(items: list[dict]) -> dict:
    model_bim = {
        "compatibilityLevel": 1702,
        "model": {
            "culture": "en-US",
            "sourceQueryCulture": "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "discourageImplicitMeasures": True,
            "tables": [
                {
                    "name": "FabricItems",
                    "columns": [
                        {"name": "WorkspaceName", "dataType": "string", "sourceColumn": "WorkspaceName", "summarizeBy": "none"},
                        {"name": "WorkspaceId", "dataType": "string", "sourceColumn": "WorkspaceId", "summarizeBy": "none"},
                        {"name": "ItemName", "dataType": "string", "sourceColumn": "ItemName", "summarizeBy": "none", "isDefaultLabel": True},
                        {"name": "ItemType", "dataType": "string", "sourceColumn": "ItemType", "summarizeBy": "none"},
                        {"name": "ItemId", "dataType": "string", "sourceColumn": "ItemId", "summarizeBy": "none"},
                        {"name": "FolderId", "dataType": "string", "sourceColumn": "FolderId", "summarizeBy": "none"},
                        {"name": "WebUrl", "dataType": "string", "sourceColumn": "WebUrl", "summarizeBy": "none"},
                    ],
                    "measures": [
                        {"name": "Item Count", "expression": "COUNTROWS('FabricItems')", "formatString": "#,##0"}
                    ],
                    "partitions": [
                        {
                            "name": "FabricItems",
                            "source": {
                                "type": "calculated",
                                "expression": _inventory_rows_dax_datatable(items).splitlines(),
                            },
                        }
                    ],
                }
            ],
        },
    }
    definition_pbism = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "5.0",
        "settings": {"qnaEnabled": False},
    }
    return {
        "format": "TMSL",
        "parts": [
            _inline_json_part("model.bim", model_bim),
            _inline_json_part("definition.pbism", definition_pbism),
        ],
    }


def _source_lines(source: str) -> list[str]:
    lines = source.strip("\n").splitlines()
    return [f"{line}\n" for line in lines[:-1]] + ([lines[-1]] if lines else [])


def _fabric_notebook_source(
    *,
    workspace_id: str,
    lakehouse_id: str,
    lakehouse_name: str,
    code: str,
) -> str:
    notebook_metadata = {
        "kernel_info": {"name": "synapse_pyspark"},
        "dependencies": ({
            "lakehouse": {
                "default_lakehouse": lakehouse_id,
                "default_lakehouse_name": lakehouse_name,
                "default_lakehouse_workspace_id": workspace_id,
            }
        } if lakehouse_id else {}),
    }
    cell_metadata = {"language": "python", "language_group": "synapse_pyspark"}

    def metadata_lines(payload: dict) -> list[str]:
        return [f"# META {line}" for line in json.dumps(payload, indent=2).splitlines()]

    lines = [
        "# Fabric notebook source",
        "# METADATA ********************",
        *metadata_lines(notebook_metadata),
        "# CELL ********************",
        "# Fabric workspace inventory ingestion",
        "# Pulls Fabric workspace/item metadata through the Fabric REST API and writes Delta tables into the bound Lakehouse.",
        *code.strip("\n").splitlines(),
        "# METADATA ********************",
        *metadata_lines(cell_metadata),
        "",
    ]
    return "\n".join(lines)


def _inventory_notebook_definition(
    *,
    workspace_id: str,
    lakehouse_id: str,
    lakehouse_name: str,
    table_name: str,
    summary_table_name: str,
) -> dict:
    code = f'''
import json
import requests
from pyspark.sql import functions as F
from pyspark.sql.types import StructField, StructType, StringType

WORKSPACE_ID = "{workspace_id}"
LAKEHOUSE_ID = "{lakehouse_id}"
FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
TABLE_NAME = "{table_name}"
SUMMARY_TABLE_NAME = "{summary_table_name}"


def _notebookutils():
    try:
        import notebookutils  # type: ignore
        return notebookutils
    except Exception:
        try:
            from notebookutils import mssparkutils  # type: ignore
            return mssparkutils
        except Exception:
            import mssparkutils  # type: ignore
            return mssparkutils


utils = _notebookutils()
token = utils.credentials.getToken("pbi")
headers = {{"Authorization": f"Bearer {{token}}", "Content-Type": "application/json"}}


def _get_all_values(url):
    values = []
    next_url = url
    while next_url:
        response = requests.get(next_url, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json() if response.text else {{}}
        values.extend(data.get("value", []))
        continuation_uri = data.get("continuationUri")
        continuation_token = data.get("continuationToken")
        if continuation_uri:
            next_url = continuation_uri
        elif continuation_token:
            separator = "&" if "?" in url else "?"
            next_url = f"{{url}}{{separator}}continuationToken={{continuation_token}}"
        else:
            next_url = None
    return values


workspaces = _get_all_values(f"{{FABRIC_API_BASE}}/workspaces")
if not any(workspace.get("id") == WORKSPACE_ID for workspace in workspaces):
    workspaces.insert(0, {{"id": WORKSPACE_ID, "displayName": WORKSPACE_ID}})

rows = []
for workspace in workspaces:
    workspace_id = workspace.get("id")
    if not workspace_id:
        continue
    workspace_name = workspace.get("displayName") or workspace.get("name") or workspace_id
    try:
        items = _get_all_values(f"{{FABRIC_API_BASE}}/workspaces/{{workspace_id}}/items")
    except Exception as exc:
        rows.append({{
            "WorkspaceName": workspace_name,
            "WorkspaceId": workspace_id,
            "ItemName": f"Skipped workspace: {{exc}}"[:500],
            "ItemType": "InventoryWarning",
            "ItemId": "",
            "FolderId": "",
            "WebUrl": "",
        }})
        continue
    for item in items:
        item_id = item.get("id") or ""
        item_type = item.get("type") or "Item"
        rows.append({{
            "WorkspaceName": workspace_name,
            "WorkspaceId": workspace_id,
            "ItemName": item.get("displayName") or item.get("name") or "",
            "ItemType": item_type,
            "ItemId": item_id,
            "FolderId": item.get("folderId") or "",
            "WebUrl": f"https://app.powerbi.com/groups/{{workspace_id}}/{{item_type.lower()}}s/{{item_id}}" if item_id else "",
        }})

schema = StructType([
    StructField("WorkspaceName", StringType(), True),
    StructField("WorkspaceId", StringType(), True),
    StructField("ItemName", StringType(), True),
    StructField("ItemType", StringType(), True),
    StructField("ItemId", StringType(), True),
    StructField("FolderId", StringType(), True),
    StructField("WebUrl", StringType(), True),
])

inventory_df = spark.createDataFrame(rows, schema=schema)
summary_df = inventory_df.groupBy("ItemType").agg(F.count("*").alias("ItemCount")).orderBy(F.desc("ItemCount"), F.asc("ItemType"))

if LAKEHOUSE_ID:
    inventory_df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(TABLE_NAME)
    summary_df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(SUMMARY_TABLE_NAME)
else:
    print("No default Lakehouse was created in this workspace; skipping Delta table writes.")

result = {{
    "status": "completed",
    "tableName": TABLE_NAME if LAKEHOUSE_ID else None,
    "summaryTableName": SUMMARY_TABLE_NAME if LAKEHOUSE_ID else None,
    "rowCount": inventory_df.count(),
    "workspaceCount": len(workspaces),
}}
print(json.dumps(result, indent=2))

exit_fn = getattr(getattr(utils, "notebook", None), "exit", None)
if callable(exit_fn):
    exit_fn(json.dumps(result))
'''
    notebook_metadata = {
        "language_info": {"name": "python"},
        "kernel_info": {"name": "synapse_pyspark"},
        "dependencies": ({
            "lakehouse": {
                "default_lakehouse": lakehouse_id,
                "default_lakehouse_name": lakehouse_name,
                "default_lakehouse_workspace_id": workspace_id,
            }
        } if lakehouse_id else {}),
    }
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": [
            {
                "cell_type": "code",
                "source": _source_lines(code.strip("\n")),
                "execution_count": None,
                "outputs": [],
                "metadata": {"language": "python", "language_group": "synapse_pyspark"},
            }
        ],
        "metadata": notebook_metadata,
    }
    return {"format": "ipynb", "parts": [_inline_json_part("notebook-content.ipynb", notebook)]}


# ---------------------------------------------------------------------------
# Clone-existing-report PBIR template path
# ---------------------------------------------------------------------------

# These are the only paths whose payload we must keep verbatim from the
# source report so the Power BI render engine accepts the schema.
_REPORT_TEMPLATE_PASSTHROUGH = {"definition/report.json", "definition/version.json"}


def _decode_part_json(part: dict) -> dict | None:
    """Decode a getDefinition part payload to a dict, or None if not JSON."""
    payload = part.get("payload")
    if not isinstance(payload, str):
        return None
    try:
        raw = base64.b64decode(payload).decode("utf-8")
        return json.loads(raw)
    except Exception:
        return None


def _rewrite_visual_to_inventory(visual_json: dict, *, visual_name: str) -> dict:
    """Replace any source-report bindings with bindings against our
    ``FabricItems`` table so the cloned visual renders our data.

    Strategy: walk the JSON; for every ``Column.Property`` use
    ``ItemType``, for every ``Measure.Property`` use ``Item Count``,
    and for every ``SourceRef.Entity`` use ``FabricItems``. Also rewrite
    every ``queryRef`` accordingly so the binding stays internally
    consistent.
    """
    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if key == "Entity" and isinstance(value, str):
                    node[key] = "FabricItems"
                elif key == "Property" and isinstance(value, str):
                    parent = node  # `node` here is the {Expression, Property} dict
                    # We don't have direct access to the wrapping `Column`/`Measure`
                    # key, so use the sibling Expression to infer; if the parent has
                    # a `Measure` key on its grandparent we'd flip — but practically
                    # we cannot easily tell here. Use `_classify_property_owner`
                    # via a marker set externally.
                    node[key] = parent.pop("__inv_property_override", "ItemType")
                elif key == "queryRef" and isinstance(value, str):
                    if "count" in value.lower() or value.lower().endswith(".item count"):
                        node[key] = "FabricItems.Item Count"
                    else:
                        node[key] = "FabricItems.ItemType"
                elif key in ("Column", "Measure") and isinstance(value, dict):
                    # Tag the inner dict so the Property walk above knows
                    # whether this is a column (→ ItemType) or measure
                    # (→ Item Count) reference.
                    value["__inv_property_override"] = (
                        "Item Count" if key == "Measure" else "ItemType"
                    )
                    walk(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(visual_json)
    visual_json["name"] = visual_name
    return visual_json


def _rebind_pbir(definition_pbir: dict, target_model_id: str) -> dict:
    """Replace whatever dataset reference the source report uses with a
    direct ``byConnection`` reference to ``target_model_id``."""
    definition_pbir = dict(definition_pbir or {})
    definition_pbir["datasetReference"] = {
        "byConnection": {"connectionString": f"semanticmodelid={target_model_id}"}
    }
    return definition_pbir


async def _fetch_workspace_reports(client, workspace_id: str, headers: dict) -> list[dict]:
    items, _ = await _fabric_get_all_values(
        client, f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items?type=Report", headers,
    )
    return items


async def _get_report_definition_parts(
    client, workspace_id: str, report_id: str, headers: dict,
) -> list[dict] | None:
    """Call ``POST .../reports/{id}/getDefinition`` and return the raw
    ``parts`` list (each a ``{path, payload, payloadType}`` dict). Handles
    LRO 202 + result polling. Returns ``None`` on any failure."""
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/reports/{report_id}/getDefinition"
    _log_tool_progress("fabric_clone_report_definition", "get_definition", "started", sourceReportId=report_id)
    resp = await client.post(url, headers=headers)
    if resp.status_code == 200:
        body = resp.json() if resp.text else {}
        return body.get("definition", {}).get("parts") or None
    if resp.status_code != 202:
        _log_tool_progress(
            "fabric_clone_report_definition",
            "get_definition",
            "failed",
            sourceReportId=report_id,
            httpStatus=resp.status_code,
            bodyPreview=bounded_text(resp.text, max_chars=300),
        )
        return None
    location = resp.headers.get("Location", "")
    if not location:
        return None
    delay = min(max(int(resp.headers.get("Retry-After") or "3"), 1), 5)
    operation_url = location.rstrip("/")
    result_url = operation_url + "/result"
    for _ in range(20):
        await asyncio.sleep(delay)
        state = await client.get(operation_url, headers=headers)
        status = (state.json() if state.text else {}).get("status", "").lower() if state.status_code == 200 else ""
        if status in ("succeeded", "completed"):
            r = await client.get(result_url, headers=headers)
            if r.status_code == 200:
                body = r.json() if r.text else {}
                return body.get("definition", {}).get("parts") or None
            return None
        if status in ("failed", "cancelled", "canceled"):
            return None
    return None


def _build_inventory_report_from_template(
    source_parts: list[dict], target_model_id: str,
) -> list[dict] | None:
    """Mutate a source report's PBIR parts into a report that visualises
    our inventory model. Keeps the source's ``report.json`` and
    ``version.json`` verbatim (so the schema versions match what the
    renderer expects), rebinds the dataset reference, drops static
    resources, and rewrites every visual to bind against ``FabricItems``.
    """
    out: list[dict] = []
    saw_pbir = False
    saw_pages_metadata = False

    for part in source_parts:
        path = str(part.get("path") or "")
        if not path:
            continue
        # Drop static resources & diagram layout — they reference assets
        # that don't exist in our reduced report.
        if path.startswith("StaticResources/"):
            continue
        if path.endswith("semanticModelDiagramLayout.json"):
            continue
        if path.endswith("/mobile.json"):
            # Mobile layouts reference visuals by name; safer to drop.
            continue

        if path == "definition.pbir":
            decoded = _decode_part_json(part) or {}
            out.append(_inline_json_part(path, _rebind_pbir(decoded, target_model_id)))
            saw_pbir = True
            continue

        if path == "definition/pages/pages.json":
            decoded = _decode_part_json(part) or {}
            saw_pages_metadata = True
            out.append(_inline_json_part(path, decoded))
            continue

        if path.startswith("definition/pages/") and path.endswith("/visual.json"):
            decoded = _decode_part_json(part)
            if decoded is None:
                continue
            visual_name = decoded.get("name") or path.rsplit("/", 2)[-2]
            mutated = _rewrite_visual_to_inventory(decoded, visual_name=visual_name)
            out.append(_inline_json_part(path, mutated))
            continue

        # Anything else (report.json, version.json, page.json, bookmarks)
        # is forwarded byte-for-byte.
        out.append(_passthrough_part(path, part["payload"]))

    if not saw_pbir or not saw_pages_metadata:
        _log_tool_progress(
            "fabric_clone_report_definition",
            "template_validation",
            "failed",
            hasPbir=saw_pbir,
            hasPagesMetadata=saw_pages_metadata,
        )
        return None

    return out


async def _build_inventory_report_definition_from_clone(
    client, workspace_id: str, target_model_id: str, headers: dict, *,
    skip_folder_id: str | None = None,
) -> dict | None:
    """Find a working report in the workspace and reshape its PBIR into
    a report that renders our inventory model. Returns the ``definition``
    payload ready for a ``POST /workspaces/{ws}/items`` body, or ``None``
    if no usable template was found.

    To avoid picking up a previously-failed clone of our own, we skip
    any report sitting in folders whose displayName starts with ``tmp_``
    or matches ``skip_folder_id``.
    """
    reports = await _fetch_workspace_reports(client, workspace_id, headers)

    folders, _ = await _fabric_get_all_values(
        client, f"{FABRIC_API_BASE}/workspaces/{workspace_id}/folders", headers,
    )
    tmp_folder_ids = {
        f.get("id") for f in folders
        if str(f.get("displayName") or "").startswith("tmp_")
    }
    if skip_folder_id:
        tmp_folder_ids.add(skip_folder_id)

    candidates = [
        r for r in reports
        if r.get("id")
        and r.get("folderId") not in tmp_folder_ids
    ]
    _log_tool_progress(
        "fabric_clone_report_definition",
        "candidate_scan",
        "ok",
        reportCount=len(reports),
        candidateCount=len(candidates),
        skippedTempFolderCount=len(tmp_folder_ids),
    )
    for candidate in candidates:
        report_id = candidate["id"]
        parts = await _get_report_definition_parts(client, workspace_id, report_id, headers)
        if not parts:
            continue
        rebuilt = _build_inventory_report_from_template(parts, target_model_id)
        if rebuilt:
            _log_tool_progress(
                "fabric_clone_report_definition",
                "clone_template",
                "ok",
                sourceReportId=report_id,
                sourceReportName=candidate.get("displayName"),
                partsIn=len(parts),
                partsOut=len(rebuilt),
            )
            return {"format": "PBIR", "parts": rebuilt}
    return None


def _report_definition(semantic_model_id: str) -> dict:
    page_name = "InventoryOverview"
    visual_name = "ItemTypeSummary"
    report_json = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.1.0/schema.json",
        "themeCollection": {
            "baseTheme": {
                "name": "CY24SU10",
                "type": "SharedResources",
                "reportVersionAtImport": {
                    "visual": "2.5.0",
                    "report": "3.1.0",
                    "page": "2.3.0",
                },
            },
        },
        "settings": {
            "useStylableVisualContainerHeader": True,
            "useEnhancedTooltips": True,
        },
    }
    definition_pbir = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byConnection": {"connectionString": f"semanticmodelid={semantic_model_id}"}},
    }
    pages_json = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [page_name],
        "activePageName": page_name,
    }
    page_json = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json",
        "name": page_name,
        "displayName": "Fabric Items",
        "displayOption": "FitToPage",
        "height": 720,
        "width": 1280,
    }
    visual_json = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.0.0/schema.json",
        "name": visual_name,
        "position": {"x": 48, "y": 64, "z": 0, "height": 500, "width": 760, "tabOrder": 0},
        "visual": {
            "visualType": "barChart",
            "query": {
                "queryState": {
                    "Category": {
                        "projections": [
                            {
                                "field": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": "FabricItems"}},
                                        "Property": "ItemType",
                                    }
                                },
                                "queryRef": "FabricItems.ItemType",
                                "active": True,
                            }
                        ]
                    },
                    "Y": {
                        "projections": [
                            {
                                "field": {
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Entity": "FabricItems"}},
                                        "Property": "Item Count",
                                    }
                                },
                                "queryRef": "FabricItems.Item Count",
                            }
                        ]
                    },
                },
                "sortDefinition": {
                    "sort": [
                        {
                            "field": {
                                "Measure": {
                                    "Expression": {"SourceRef": {"Entity": "FabricItems"}},
                                    "Property": "Item Count",
                                }
                            },
                            "direction": "Descending",
                        }
                    ]
                },
            },
            "drillFilterOtherVisuals": True,
        },
    }
    version_json = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "1.0.0",
    }
    return {
        "format": "PBIR",
        "parts": [
            _inline_json_part("definition.pbir", definition_pbir),
            _inline_json_part("definition/report.json", report_json),
            _inline_json_part("definition/version.json", version_json),
            _inline_json_part("definition/pages/pages.json", pages_json),
            _inline_json_part(f"definition/pages/{page_name}/page.json", page_json),
            _inline_json_part(f"definition/pages/{page_name}/visuals/{visual_name}/visual.json", visual_json),
        ],
    }


async def _poll_lro_result(
    client,
    location: str,
    headers: dict,
    retry_after: str | None = None,
    *,
    fetch_result: bool = True,
) -> dict | None:
    if not location:
        return None
    delay = min(max(int(retry_after or "3"), 1), 5)
    operation_url = location.rstrip("/")
    result_url = operation_url + "/result"
    for _ in range(24):
        await asyncio.sleep(delay)
        state_resp = await client.get(operation_url, headers=headers)
        if state_resp.status_code == 200:
            state = state_resp.json() if state_resp.text else {}
            status = str(state.get("status", "")).lower()
            if status == "succeeded":
                if not fetch_result:
                    return state
                result_resp = await client.get(result_url, headers=headers)
                if result_resp.status_code == 200:
                    return result_resp.json() if result_resp.text else {}
                if result_resp.status_code == 204:
                    return state
                return {"error": format_http_error(result_resp, "getting operation result")}
            if status == "failed":
                return {"error": "Error polling operation: " + json.dumps(state)}
        elif state_resp.status_code not in (202, 204):
            return {"error": format_http_error(state_resp, "polling operation")}
    return None


async def _post_fabric_json(client, url: str, body: dict, op_name: str, *, fetch_lro_result: bool = True) -> dict | str:
    headers = _fabric_headers()
    resp = await client.post(url, json=body, headers=headers)
    if resp.status_code in (200, 201):
        return resp.json() if resp.text else {}
    if resp.status_code == 202:
        polled = await _poll_lro_result(
            client,
            resp.headers.get("Location", ""),
            headers,
            resp.headers.get("Retry-After"),
            fetch_result=fetch_lro_result,
        )
        if polled is not None and "error" not in polled:
            return polled
        if polled and "error" in polled:
            return polled["error"]
        return {"status": "accepted", "operationUrl": resp.headers.get("Location", "")}
    return format_http_error(resp, op_name)


def _safe_json(resp) -> dict:
    try:
        body = resp.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def _validate_workspace_capacity_active(client, workspace_id: str, headers: dict) -> dict | str:
    workspace_resp = await client.get(f"{FABRIC_API_BASE}/workspaces/{workspace_id}", headers=headers)
    if workspace_resp.status_code != 200:
        return format_http_error(workspace_resp, "checking workspace capacity assignment")
    workspace = _safe_json(workspace_resp)
    capacity_id = workspace.get("capacityId") or (workspace.get("capacity") or {}).get("id")
    if not capacity_id:
        return "Workspace is not assigned to a Fabric capacity; Power BI reports cannot be rendered reliably."

    capacities_resp = await client.get(f"{FABRIC_API_BASE}/capacities", headers=headers)
    if capacities_resp.status_code != 200:
        return format_http_error(capacities_resp, "checking Fabric capacity state")
    capacities = _safe_json(capacities_resp).get("value", [])
    capacity = next((item for item in capacities if str(item.get("id")) == str(capacity_id)), None)
    if not capacity:
        return f"Workspace capacity {capacity_id} was not visible to the current user; cannot verify it is Active."

    state = str(capacity.get("state") or capacity.get("status") or "").strip()
    if state.lower() != "active":
        name = capacity.get("displayName") or capacity.get("name") or capacity_id
        return f"Workspace capacity {name} ({capacity_id}) is {state or 'unknown'}, not Active; resume the capacity before creating report artifacts."

    return {
        "status": "active",
        "capacityId": str(capacity_id),
        "capacityName": capacity.get("displayName") or capacity.get("name"),
        "workspaceName": workspace.get("displayName") or workspace.get("name"),
    }


async def _delete_unverified_inventory_item(client, workspace_id: str, item_id: str, headers: dict) -> dict | str:
    resp = await client.delete(
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{item_id}?{urlencode({'hardDelete': 'true'})}",
        headers=headers,
    )
    if resp.status_code in (200, 204):
        return {"status": "deleted", "itemId": item_id, "via": "fabric_items_delete"}
    if resp.status_code == 202:
        polled = await _poll_lro_result(
            client,
            resp.headers.get("Location", ""),
            headers,
            resp.headers.get("Retry-After"),
            fetch_result=False,
        )
        if polled is not None and "error" not in polled:
            return {"status": "deleted", "itemId": item_id, "via": "fabric_items_delete_lro"}
        if polled and "error" in polled:
            return polled["error"]
    return format_http_error(resp, "deleting unverified inventory report")


async def _get_powerbi_report_metadata(client, url: str, headers: dict, token_name: str) -> dict | str:
    for attempt in range(12):
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            return _safe_json(resp)
        if resp.status_code in (401, 403):
            return format_http_error(resp, f"checking report metadata with {token_name}")
        if resp.status_code in (404, 409, 429, 500, 502, 503, 504) and attempt < 11:
            delay = min(max(int(resp.headers.get("Retry-After") or "5"), 1), 15)
            await asyncio.sleep(delay)
            continue
        return format_http_error(resp, f"checking report metadata with {token_name}")
    return f"Timed out checking report metadata with {token_name}."


async def _start_powerbi_report_export(client, url: str, headers: dict, token_name: str) -> dict | str:
    for attempt in range(8):
        resp = await client.post(f"{url}/ExportTo", headers=headers, json={"format": "PDF"})
        if resp.status_code in (200, 202):
            body = _safe_json(resp)
            if body.get("id"):
                body["retryAfter"] = resp.headers.get("Retry-After")
                return body
            if str(body.get("status") or "").lower() == "succeeded":
                return body
            return "Power BI ExportTo accepted the request but did not return an export id."
        if resp.status_code in (401, 403):
            return format_http_error(resp, f"starting report render export with {token_name}")
        if resp.status_code in (404, 409, 429, 500, 502, 503, 504) and attempt < 7:
            delay = min(max(int(resp.headers.get("Retry-After") or "5"), 1), 15)
            await asyncio.sleep(delay)
            continue
        return format_http_error(resp, f"starting report render export with {token_name}")
    return f"Timed out starting report render export with {token_name}."


async def _verify_report_renderable(
    client,
    workspace_id: str,
    report_id: str,
    semantic_model_id: str,
) -> dict | str:
    report_url = f"{POWERBI_API_BASE}/groups/{workspace_id}/reports/{report_id}"
    failures: list[str] = []
    for token_name, headers in _powerbi_header_candidates():
        metadata = await _get_powerbi_report_metadata(client, report_url, headers, token_name)
        if isinstance(metadata, str):
            failures.append(metadata)
            continue
        bound_dataset_id = metadata.get("datasetId") or metadata.get("datasetID")
        if bound_dataset_id and semantic_model_id and str(bound_dataset_id) != str(semantic_model_id):
            return (
                "Power BI report is not bound to the inventory semantic model: "
                f"expected {semantic_model_id}, got {bound_dataset_id}."
            )

        export = await _start_powerbi_report_export(client, report_url, headers, token_name)
        if isinstance(export, str):
            failures.append(export)
            continue
        if str(export.get("status") or "").lower() == "succeeded":
            return {
                "status": "rendered",
                "via": "powerbi_exportTo_pdf",
                "token": token_name,
                "reportId": report_id,
                "semanticModelId": semantic_model_id,
                "exportId": export.get("id"),
            }

        export_id = export.get("id")
        if not export_id:
            failures.append("Power BI ExportTo did not return an export id.")
            continue
        poll_url = f"{report_url}/exports/{export_id}"
        delay = min(max(int(export.get("retryAfter") or "5"), 1), 15)
        for _ in range(36):
            await asyncio.sleep(delay)
            state_resp = await client.get(poll_url, headers=headers)
            if state_resp.status_code == 429:
                delay = min(max(int(state_resp.headers.get("Retry-After") or str(delay)), 1), 30)
                continue
            if state_resp.status_code not in (200, 202):
                failures.append(format_http_error(state_resp, f"polling report render export with {token_name}"))
                break
            if state_resp.headers.get("Retry-After"):
                delay = min(max(int(state_resp.headers.get("Retry-After") or str(delay)), 1), 30)
            state = _safe_json(state_resp)
            status = str(state.get("status") or "").lower()
            if status == "succeeded":
                return {
                    "status": "rendered",
                    "via": "powerbi_exportTo_pdf",
                    "token": token_name,
                    "reportId": report_id,
                    "semanticModelId": semantic_model_id,
                    "exportId": export_id,
                }
            if status in ("failed", "cancelled", "canceled"):
                failures.append("Power BI report render export failed: " + json.dumps(state))
                break
        else:
            failures.append(f"Timed out waiting for Power BI report render export {export_id}.")

    return "Report render validation failed: " + "; ".join(failures)


def _inventory_expectations_from_task(expected_task: str | None) -> dict:
    text = (expected_task or "").lower()
    required_types: set[str] = set()
    if any(token in text for token in ("ingestion", "notebook", "transform", "transformation")):
        required_types.add("Notebook")
    if "semantic model" in text or "semantic modelling" in text or "semantic modeling" in text:
        required_types.add("SemanticModel")
    if "report" in text or "visual" in text or "visualization" in text or "visualisation" in text:
        required_types.add("Report")
    if "lakehouse" in text or "delta" in text or "table" in text:
        required_types.add("Lakehouse")
    if not required_types:
        required_types.update({"Notebook", "SemanticModel", "Report"})
    return {
        "expectedTask": (expected_task or "")[:1000],
        "requiredItemTypes": sorted(required_types),
    }


async def _items_in_folder(client, workspace_id: str, headers: dict, folder_id: str) -> tuple[list[dict], str | None]:
    items, error = await _fabric_get_all_values(
        client,
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items",
        headers,
    )
    if error:
        return [], error
    return [item for item in items if item.get("folderId") == folder_id], None


async def _update_notebook_definition(
    client,
    workspace_id: str,
    notebook_id: str,
    definition: dict,
) -> dict | str:
    return await _post_fabric_json(
        client,
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/notebooks/{notebook_id}/updateDefinition",
        {"definition": definition},
        "updating inventory notebook definition",
        fetch_lro_result=False,
    )


def _job_status(job: dict) -> str:
    return str(job.get("status") or "").lower()


def _job_is_terminal(job: dict) -> bool:
    return _job_status(job) in {"completed", "failed", "cancelled", "canceled", "deduped"}


async def _poll_item_job(
    client,
    location: str,
    headers: dict,
    retry_after: str | None = None,
) -> dict | str:
    if not location:
        return "Error running inventory notebook: Fabric did not return a job Location header"
    delay = min(max(int(retry_after or "10"), 5), 30)
    for _ in range(60):
        await asyncio.sleep(delay)
        resp = await client.get(location, headers=headers)
        if resp.status_code != 200:
            return format_http_error(resp, "polling inventory notebook job")
        job = resp.json() if resp.text else {}
        status = _job_status(job)
        if status == "completed":
            return job
        if status in {"failed", "cancelled", "canceled", "deduped"}:
            return "Error running inventory notebook: " + json.dumps(job, indent=2)
        delay = min(max(int(resp.headers.get("Retry-After", str(delay))), 5), 30)
    return "Error running inventory notebook: timed out waiting for Fabric job completion"


async def _run_inventory_notebook(
    client,
    workspace_id: str,
    notebook_id: str,
    headers: dict,
) -> dict | str:
    jobs, _ = await _fabric_get_all_values(
        client,
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{notebook_id}/jobs/instances",
        headers,
    )
    for job in jobs:
        if str(job.get("jobType", "")).lower() == "runnotebook" and not _job_is_terminal(job):
            job_id = job.get("id")
            if not job_id:
                break
            return await _poll_item_job(
                client,
                f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{notebook_id}/jobs/instances/{job_id}",
                headers,
            )

    resp = await client.post(
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{notebook_id}/jobs/RunNotebook/instances",
        headers=headers,
    )
    if resp.status_code != 202:
        return format_http_error(resp, "running inventory notebook")
    return await _poll_item_job(client, resp.headers.get("Location", ""), headers, resp.headers.get("Retry-After"))


async def _refresh_semantic_model(
    client,
    workspace_id: str,
    semantic_model_id: str,
    headers: dict,
) -> dict | str:
    """Trigger a Fabric semantic-model refresh and wait for completion.

    Without an explicit refresh, an import-mode semantic model created
    via the REST API has schema only \u2014 the report visuals stay empty
    because no partition data has been materialized yet.

    Tries the Power BI v1 refreshes endpoint first (works on Pro
    workspaces and starts immediately), falls back to the Fabric jobs
    endpoint if that returns a non-202.
    """
    # 1. Power BI v1 dataset refresh — fast, no LRO, works everywhere
    pbi_url = (
        f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"
        f"/datasets/{semantic_model_id}/refreshes"
    )
    _log_tool_progress(
        "fabric_refresh_semantic_model",
        "powerbi_v1_refresh",
        "started",
        workspaceId=workspace_id,
        semanticModelId=semantic_model_id,
        endpoint="powerbi_v1_refreshes",
    )
    pbi_last_status = None
    pbi_last_body = ""
    try:
        header_candidates = _powerbi_header_candidates()
    except RuntimeError as exc:
        header_candidates = []
        pbi_last_body = str(exc)
    for token_name, pbi_headers in header_candidates:
        for attempt in range(3):
            resp = await client.post(pbi_url, json={"type": "full"}, headers=pbi_headers)
            pbi_last_status = resp.status_code
            pbi_last_body = resp.text[:300] if resp.text else ""
            _log_tool_progress(
                "fabric_refresh_semantic_model",
                "powerbi_v1_refresh",
                "attempt",
                tokenSource=token_name,
                attempt=attempt,
                httpStatus=pbi_last_status,
                bodyPreview=bounded_text(pbi_last_body, max_chars=300),
            )
            if pbi_last_status == 202:
                return {"status": "refresh_triggered", "via": f"powerbi_v1:{token_name}"}
            if pbi_last_status == 404:
                await asyncio.sleep(2)
                continue
            if pbi_last_status in (401, 403):
                break
            break

    # 2. Fabric jobs API fallback (works on Premium/F-SKU only)
    refresh_url = (
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/semanticModels/{semantic_model_id}"
        "/jobs/instances?jobType=Refresh"
    )
    _log_tool_progress(
        "fabric_refresh_semantic_model",
        "fabric_jobs_refresh",
        "started",
        workspaceId=workspace_id,
        semanticModelId=semantic_model_id,
        endpoint="fabric_semantic_model_jobs",
    )
    resp = None
    last_status = None
    last_body = ""
    for attempt in range(3):
        resp = await client.post(refresh_url, headers=headers)
        last_status = resp.status_code
        last_body = resp.text[:400] if resp.text else ""
        _log_tool_progress(
            "fabric_refresh_semantic_model",
            "fabric_jobs_refresh",
            "attempt",
            attempt=attempt,
            httpStatus=resp.status_code,
            bodyPreview=bounded_text(last_body, max_chars=400),
        )
        if resp.status_code in (200, 202):
            break
        if resp.status_code == 404:
            await asyncio.sleep(2)
            continue
        break
    if resp is None or resp.status_code not in (200, 202):
        # Both refresh paths failed — that's OK with the calculated
        # DATATABLE partition because Power BI evaluates the table on
        # first model load. Surface the failure as a warning instead
        # of an error so the report still gets created.
        return (
            f"Refresh skipped (PBI v1 status={pbi_last_status}; "
            f"Fabric jobs status={last_status})"
        )
    location = resp.headers.get("Location", "")
    _log_tool_progress(
        "fabric_refresh_semantic_model",
        "fabric_jobs_refresh",
        "accepted",
        hasLocation=bool(location),
        retryAfter=resp.headers.get("Retry-After"),
    )
    if not location:
        return resp.json() if resp.text else {"status": "accepted"}
    polled = await _poll_item_job(client, location, headers, resp.headers.get("Retry-After"))
    _log_tool_progress(
        "fabric_refresh_semantic_model",
        "fabric_jobs_poll",
        "finished",
        resultPreview=bounded_text(polled, max_chars=400),
    )
    return polled


def _extract_powerbi_scalar(row: dict, name: str) -> object:
    for key, value in row.items():
        raw_key = str(key)
        clean_key = raw_key.strip("[]")
        if clean_key == name or clean_key.endswith(f".{name}"):
            return value
        if raw_key.endswith(f"[{name}]"):
            return value
    return None


async def _wait_for_inventory_model_data(
    client,
    workspace_id: str,
    semantic_model_id: str,
    *,
    expected_min_rows: int = 1,
) -> dict | str:
    """Prove the model is queryable through Power BI, without any backend fallback."""
    try:
        header_candidates = _powerbi_header_candidates()
    except RuntimeError as exc:
        return str(exc)

    url = (
        f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"
        f"/datasets/{semantic_model_id}/executeQueries"
    )
    count_query = "EVALUATE ROW(\"ItemCount\", COUNTROWS('FabricItems'))"
    types_query = """
EVALUATE
TOPN(
    20,
    SUMMARIZECOLUMNS('FabricItems'[ItemType], "Item Count", [Item Count]),
    [Item Count], DESC,
    'FabricItems'[ItemType], ASC
)
""".strip()

    last_error = ""
    for attempt in range(18):
        count_resp = None
        count_token_name = ""
        for token_name, headers in header_candidates:
            count_resp = await client.post(
                url,
                json={"queries": [{"query": count_query}], "serializerSettings": {"includeNulls": True}},
                headers=headers,
            )
            count_token_name = token_name
            if count_resp.status_code not in (401, 403):
                break
        if count_resp is None:
            return "Power BI executeQueries was not attempted."
        if count_resp.status_code == 200:
            try:
                tables = (count_resp.json().get("results") or [{}])[0].get("tables") or []
                rows = tables[0].get("rows") if tables else []
                row_count = int(_extract_powerbi_scalar(rows[0], "ItemCount") or 0) if rows else 0
            except Exception as exc:
                return f"Power BI executeQueries returned an unexpected inventory count shape: {exc}"
            if row_count >= expected_min_rows:
                types_resp = None
                for _, headers in header_candidates:
                    types_resp = await client.post(
                        url,
                        json={"queries": [{"query": types_query}], "serializerSettings": {"includeNulls": True}},
                        headers=headers,
                    )
                    if types_resp.status_code not in (401, 403):
                        break
                if types_resp is None:
                    return "Power BI executeQueries item-type query was not attempted."
                if types_resp.status_code != 200:
                    return format_http_error(types_resp, "querying inventory item types")
                try:
                    type_tables = (types_resp.json().get("results") or [{}])[0].get("tables") or []
                    type_rows = type_tables[0].get("rows") if type_tables else []
                    item_types = [
                        str(_extract_powerbi_scalar(row, "ItemType") or "").strip()
                        for row in type_rows
                    ]
                except Exception as exc:
                    return f"Power BI executeQueries returned an unexpected item-type shape: {exc}"
                item_types = [item_type for item_type in item_types if item_type]
                if item_types:
                    return {
                        "status": "queryable",
                        "via": "powerbi_executeQueries",
                        "rowCount": row_count,
                        "itemTypes": item_types,
                    }
                last_error = "Power BI executeQueries returned rows but no item types."
            else:
                last_error = f"Power BI executeQueries returned {row_count} inventory rows."
        else:
            last_error = f"{format_http_error(count_resp, 'querying inventory row count')} using {count_token_name}"
            if count_resp.status_code in (401, 403):
                return last_error
        await asyncio.sleep(5 if attempt < 6 else 10)
    return f"Timed out waiting for Power BI semantic model data. Last result: {last_error}"


async def _wait_for_lakehouse_tables(
    client,
    workspace_id: str,
    lakehouse_id: str,
    headers: dict,
    expected_table_names: set[str],
) -> dict | str:
    last_error = ""
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables"
    for attempt in range(18):
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            body = resp.json() if resp.text else {}
            tables = body.get("data") or body.get("value") or []
            if not isinstance(tables, list):
                return "Lakehouse tables response had an unexpected shape."
            table_names = {
                str(table.get("name") or table.get("displayName") or "")
                for table in tables
                if isinstance(table, dict)
            }
            expected_by_key = {name.casefold(): name for name in expected_table_names}
            observed_by_key = {name.casefold(): name for name in table_names if name}
            if set(expected_by_key).issubset(observed_by_key):
                row_counts: dict[str, int] = {}
                for table in tables:
                    if not isinstance(table, dict):
                        continue
                    name = str(table.get("name") or table.get("displayName") or "")
                    row_count = table.get("rowCount") or table.get("rowsCount")
                    if name and isinstance(row_count, int):
                        row_counts[name] = row_count
                return {
                    "status": "tables_found",
                    "via": "fabric_lakehouse_tables_api",
                    "tables": [observed_by_key[key] for key in sorted(expected_by_key)],
                    "rowCounts": row_counts,
                }
            last_error = f"Lakehouse tables not found yet; saw {sorted(table_names)}"
        else:
            last_error = format_http_error(resp, "listing lakehouse tables")
            if resp.status_code in (401, 403, 404):
                return last_error
        await asyncio.sleep(5 if attempt < 6 else 10)
    return f"Timed out waiting for Lakehouse Delta tables. Last result: {last_error}"


async def _find_workspace_item(
    client,
    workspace_id: str,
    headers: dict,
    *,
    display_name: str,
    item_type: str,
    folder_id: str | None = None,
) -> dict | None:
    items, error = await _fabric_get_all_values(
        client,
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items",
        headers,
    )
    if error:
        return None
    expected_type = item_type.lower()
    for item in items:
        item_name = item.get("displayName") or item.get("name")
        if item_name != display_name:
            continue
        if str(item.get("type", "")).lower() != expected_type:
            continue
        item_folder_id = item.get("folderId")
        if folder_id and item_folder_id and item_folder_id != folder_id:
            continue
        if item.get("id"):
            item.update(_build_item_links(workspace_id, item.get("id"), item.get("type", item_type)))
        return item | {"type": item.get("type", item_type), "folderId": item_folder_id or folder_id}
    return None


async def _create_or_reuse_inventory_item(
    client,
    workspace_id: str,
    headers: dict,
    *,
    display_name: str,
    item_type: str,
    description: str,
    folder_id: str,
    op_name: str,
    extra_body: dict | None = None,
) -> dict | str:
    body = {
        "displayName": display_name,
        "type": item_type,
        "description": description,
        "folderId": folder_id,
    }
    if extra_body:
        body.update(extra_body)
    result = await _post_fabric_json(
        client,
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items",
        body,
        op_name,
    )
    if not isinstance(result, str):
        if result.get("id"):
            result.update(_build_item_links(workspace_id, result.get("id"), result.get("type", item_type)))
        return result | {"type": result.get("type", item_type), "folderId": folder_id}
    if "itemdisplaynamealreadyinuse" not in result.lower() and "already in use" not in result.lower():
        return result
    existing = await _find_workspace_item(
        client,
        workspace_id,
        headers,
        display_name=display_name,
        item_type=item_type,
        folder_id=folder_id,
    )
    return existing if existing is not None else result


def _inventory_artifact_base(folder_name: str) -> str:
    folder_suffix = folder_name.removeprefix("tmp_")
    compact_suffix = "".join(ch for ch in folder_suffix if ch.isalnum()) or "Run"
    if compact_suffix.isdigit():
        compact_suffix = compact_suffix[-8:]
    else:
        compact_suffix = compact_suffix[:16]
    return f"Inv{compact_suffix}"


def _inventory_item_base(folder_name: str, folder_id: str) -> str:
    folder_suffix = "".join(ch for ch in folder_id if ch.isalnum())[:6]
    return f"{_inventory_artifact_base(folder_name)}{folder_suffix}"


async def _fabric_get_all_values(client, url: str, headers: dict) -> tuple[list[dict], str | None]:
    values: list[dict] = []
    next_url: str | None = url
    while next_url:
        resp = await client.get(next_url, headers=headers)
        if resp.status_code != 200:
            return values, format_http_error(resp, "listing Fabric inventory source data")
        data = resp.json() if resp.text else {}
        values.extend(data.get("value", []))
        continuation_uri = data.get("continuationUri")
        continuation_token = data.get("continuationToken")
        if continuation_uri:
            next_url = continuation_uri
        elif continuation_token:
            separator = "&" if "?" in url else "?"
            next_url = f"{url}{separator}{urlencode({'continuationToken': continuation_token})}"
        else:
            next_url = None
    return values, None


async def _collect_accessible_inventory_items(client, workspace_id: str, headers: dict) -> tuple[list[dict], list[str], int]:
    workspaces, workspace_error = await _fabric_get_all_values(client, f"{FABRIC_API_BASE}/workspaces", headers)
    warnings: list[str] = []
    if workspace_error:
        warnings.append(workspace_error)
        workspaces = [{"id": workspace_id, "displayName": workspace_id}]
    if not any(workspace.get("id") == workspace_id for workspace in workspaces):
        workspaces.insert(0, {"id": workspace_id, "displayName": workspace_id})

    semaphore = asyncio.Semaphore(8)

    async def collect_workspace(workspace: dict) -> tuple[list[dict], str | None]:
        source_workspace_id = workspace.get("id")
        if not source_workspace_id:
            return [], None
        workspace_name = workspace.get("displayName") or workspace.get("name") or source_workspace_id
        try:
            async with semaphore:
                workspace_items, item_error = await asyncio.wait_for(
                    _fabric_get_all_values(
                        client,
                        f"{FABRIC_API_BASE}/workspaces/{source_workspace_id}/items",
                        headers,
                    ),
                    timeout=45,
                )
        except TimeoutError:
            return [], f"Skipped workspace {workspace_name}: timed out listing items"
        except Exception as exc:
            return [], f"Skipped workspace {workspace_name}: {exc}"
        if item_error:
            return [], f"Skipped workspace {workspace_name}: {item_error}"

        rows: list[dict] = []
        for item in workspace_items:
            item_id = item.get("id")
            item_type = item.get("type", "Item")
            if item_id:
                item.update(_build_item_links(source_workspace_id, item_id, item_type))
            rows.append(item | {"workspaceId": source_workspace_id, "workspaceName": workspace_name})
        return rows, None

    inventory_rows: list[dict] = []
    results = await asyncio.gather(*(collect_workspace(workspace) for workspace in workspaces))
    for rows, warning in results:
        inventory_rows.extend(rows)
        if warning:
            warnings.append(warning)
    return inventory_rows, warnings, len(workspaces)


# ---------------------------------------------------------------------------
# Fabric REST API tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def fabric_list_workspaces() -> str:
    """List all Microsoft Fabric workspaces the user has access to.

    Returns a JSON list of workspaces with their IDs, display names, and types.
    Use this to discover available workspaces before operating on items.
    """
    async with shared_client(30.0) as client:
        resp = await client.get(
            f"{FABRIC_API_BASE}/workspaces",
            headers=_fabric_headers(),
        )
    if resp.status_code != 200:
        return format_http_error(resp, 'listing workspaces')
    data = resp.json()
    workspaces = data.get("value", [])
    result = []
    for ws in workspaces:
        result.append({
            "id": ws.get("id"),
            "displayName": ws.get("displayName"),
            "type": ws.get("type"),
            "capacityId": ws.get("capacityId"),
        })
    return json.dumps(result, indent=2)


@mcp.tool()
async def fabric_create_workspace(
    display_name: str,
    description: str | None = None,
    capacity_id: str | None = None,
) -> str:
    """Create a new Microsoft Fabric workspace.

    Args:
        display_name: Human-readable name for the new workspace. Must be
            unique within the tenant.
        description: Optional description.
        capacity_id: Optional capacity UUID to assign. If omitted, the
            workspace is created without an assigned capacity (user can
            attach one later in Fabric).
    """
    body: dict = {"displayName": display_name}
    if description:
        body["description"] = description
    if capacity_id:
        body["capacityId"] = capacity_id
    async with shared_client(30.0) as client:
        resp = await client.post(
            f"{FABRIC_API_BASE}/workspaces",
            json=body,
            headers=_fabric_headers(),
        )
    if resp.status_code not in (200, 201, 202):
        return format_http_error(resp, 'creating workspace')
    created = resp.json()
    return json.dumps({
        "id": created.get("id"),
        "displayName": created.get("displayName"),
        "type": created.get("type"),
        "capacityId": created.get("capacityId"),
    })


@mcp.tool()
async def fabric_list_items(workspace_id: str, item_type: str | None = None) -> str:
    """List items (lakehouses, notebooks, reports, etc.) in a Fabric workspace.

    Args:
        workspace_id: The workspace UUID (e.g. '8bdca8af-1db1-4fd8-9564-0c98b4dbdffc'). Use fabric_list_workspaces to get IDs.
        item_type: Optional filter — e.g. 'Lakehouse', 'Notebook', 'Report'.
    """
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items"
    if item_type:
        url += f"?type={item_type}"
    async with shared_client(30.0) as client:
        resp = await client.get(url, headers=_fabric_headers())
    if resp.status_code != 200:
        return format_http_error(resp, 'listing items')
    data = resp.json()
    items = data.get("value", [])
    result = []
    for item in items:
        item_id = item.get("id")
        item_type_value = item.get("type")
        result.append({
            "id": item_id,
            "displayName": item.get("displayName"),
            "type": item_type_value,
            "folderId": item.get("folderId"),
            **_build_item_links(workspace_id, item_id, item_type_value),
        })
    return json.dumps(result, indent=2)


@mcp.tool()
async def fabric_list_folders(workspace_id: str) -> str:
    """List folders in a Fabric workspace (top-level container structure).

    Folders are the Fabric workspace-view grouping shown alongside items
    (e.g. a "raw" folder sitting next to a Lakehouse). Returns an array
    of ``{id, displayName, parentFolderId}``.
    """
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/folders"
    async with shared_client(30.0) as client:
        resp = await client.get(url, headers=_fabric_headers())
    if resp.status_code != 200:
        return format_http_error(resp, 'listing folders')
    data = resp.json()
    folders = data.get("value", [])
    result = [
        {
            "id": f.get("id"),
            "displayName": f.get("displayName"),
            "parentFolderId": f.get("parentFolderId"),
        }
        for f in folders
    ]
    return json.dumps(result, indent=2)


@mcp.tool()
async def fabric_validate_workspace_capacity(workspace_id: str) -> str:
    """Verify that a Fabric workspace is assigned to an Active capacity.

    Verifier agents should call this before accepting report or semantic-model
    deliverables. Inactive or missing capacity means reports can be created but
    fail to open/render for users.

    Args:
        workspace_id: The workspace UUID.
    """
    async with shared_client(30.0) as client:
        outcome = await _validate_workspace_capacity_active(client, workspace_id, _fabric_headers())
    if isinstance(outcome, str):
        return json.dumps({"status": "failed", "errors": [outcome]}, indent=2)
    return json.dumps(outcome, indent=2)


@mcp.tool()
async def fabric_verify_report_renderable(
    workspace_id: str,
    report_id: str,
    semantic_model_id: str,
) -> str:
    """Verify a Power BI report is bound correctly and can render/export.

    This starts and polls a Power BI PDF export job. A Succeeded export is the
    server-side proof that the report renderer accepted the artifact and its
    semantic-model binding. The tool returns a JSON status and does not download
    the report bytes.

    Args:
        workspace_id: The workspace UUID.
        report_id: Report item UUID.
        semantic_model_id: Expected bound semantic model/dataset UUID.
    """
    async with shared_client(120.0) as client:
        outcome = await _verify_report_renderable(client, workspace_id, report_id, semantic_model_id)
    if isinstance(outcome, str):
        return json.dumps({"status": "failed", "errors": [outcome]}, indent=2)
    return json.dumps(outcome, indent=2)


@mcp.tool()
async def fabric_verify_workspace_inventory_solution(
    workspace_id: str,
    folder_id: str,
    expected_task: str | None = None,
    expected_min_inventory_rows: int = 1,
) -> str:
    """Verify an inventory solution folder against the original task.

    The verifier uses this after an agent claims it created the requested
    end-to-end Fabric item inventory solution. It checks capacity readiness,
    created item types in the run folder, semantic-model queryability with real
    inventory rows, and report render/export proof. The result is a JSON verdict
    suitable for routing repair follow-up tasks when anything is broken or
    mismatched.

    Args:
        workspace_id: The workspace UUID.
        folder_id: Run folder UUID containing the produced artifacts.
        expected_task: Original user task or acceptance criteria text.
        expected_min_inventory_rows: Minimum expected rows in the FabricItems table.
    """
    errors: list[str] = []
    warnings: list[str] = []
    capacity_validation: dict | None = None
    semantic_model_data_validation: dict | None = None
    report_render_validation: dict | None = None
    expectations = _inventory_expectations_from_task(expected_task)
    folder_items: list[dict] = []
    missing_types: list[str] = []

    async with shared_client(120.0) as client:
        headers = _fabric_headers()
        capacity_outcome = await _validate_workspace_capacity_active(client, workspace_id, headers)
        if isinstance(capacity_outcome, str):
            errors.append(capacity_outcome)
        else:
            capacity_validation = capacity_outcome

        folders, folder_error = await _fabric_get_all_values(
            client,
            f"{FABRIC_API_BASE}/workspaces/{workspace_id}/folders",
            headers,
        )
        if folder_error:
            errors.append(folder_error)
            folder = None
        else:
            folder = next((item for item in folders if item.get("id") == folder_id), None)
            if folder is None:
                errors.append(f"Expected run folder {folder_id} was not found in the workspace.")

        folder_items, item_error = await _items_in_folder(client, workspace_id, headers, folder_id)
        if item_error:
            errors.append(item_error)
        item_types = {str(item.get("type") or "") for item in folder_items}
        missing_types = [item_type for item_type in expectations["requiredItemTypes"] if item_type not in item_types]
        if missing_types:
            errors.append("Run folder is missing expected item types: " + ", ".join(missing_types))

        semantic_models = [item for item in folder_items if item.get("type") == "SemanticModel" and item.get("id")]
        reports = [item for item in folder_items if item.get("type") == "Report" and item.get("id")]
        if len(semantic_models) != 1:
            errors.append(f"Expected exactly one SemanticModel in the run folder, found {len(semantic_models)}.")
        if len(reports) != 1:
            errors.append(f"Expected exactly one Report in the run folder, found {len(reports)}.")

        if semantic_models:
            model_id = semantic_models[0]["id"]
            model_outcome = await _wait_for_inventory_model_data(
                client,
                workspace_id,
                model_id,
                expected_min_rows=max(1, expected_min_inventory_rows),
            )
            if isinstance(model_outcome, str):
                errors.append("Semantic model data validation failed: " + model_outcome)
            else:
                semantic_model_data_validation = model_outcome
        else:
            model_id = ""

        if reports and model_id:
            report_id = reports[0]["id"]
            report_outcome = await _verify_report_renderable(client, workspace_id, report_id, model_id)
            if isinstance(report_outcome, str):
                errors.append("Report render validation failed: " + report_outcome)
            else:
                report_render_validation = report_outcome

    result = {
        "status": "verified" if not errors else "failed",
        "workspaceId": workspace_id,
        "folderId": folder_id,
        "expectationCheck": {
            **expectations,
            "missingItemTypes": missing_types,
            "matched": not errors,
        },
        "capacityValidation": capacity_validation,
        "semanticModelDataValidation": semantic_model_data_validation,
        "reportRenderValidation": report_render_validation,
        "items": [
            {
                "id": item.get("id"),
                "displayName": item.get("displayName") or item.get("name"),
                "type": item.get("type"),
                **_build_item_links(workspace_id, item.get("id"), str(item.get("type") or "Item")),
            }
            for item in folder_items if item.get("id")
        ],
        "errors": errors,
        "warnings": warnings,
        "summary": (
            "Verified the inventory solution against the requested Fabric item inventory/report outcome."
            if not errors else
            "Inventory solution verification failed; route the errors to the producing agent as repair requirements."
        ),
    }
    return json.dumps(result, indent=2)


@mcp.tool()
async def fabric_create_folder(
    workspace_id: str,
    display_name: str,
    parent_folder_id: str | None = None,
) -> str:
    """Create a folder in a Fabric workspace.

    Args:
        workspace_id: The workspace UUID (e.g. '8bdca8af-1db1-4fd8-9564-0c98b4dbdffc'). NOT the workspace name.
        display_name: Display name for the new folder.
        parent_folder_id: Optional parent folder UUID. If omitted, the folder is created at workspace root.
    """
    body: dict = {"displayName": display_name}
    if parent_folder_id:
        body["parentFolderId"] = parent_folder_id
    async with shared_client(30.0) as client:
        resp = await client.post(
            f"{FABRIC_API_BASE}/workspaces/{workspace_id}/folders",
            json=body,
            headers=_fabric_headers(),
        )
    if resp.status_code not in (200, 201, 202):
        return format_http_error(resp, 'creating folder')
    return json.dumps(resp.json(), indent=2)


@mcp.tool()
async def fabric_create_item(
    workspace_id: str,
    display_name: str,
    item_type: str,
    description: str | None = None,
    folder_id: str | None = None,
) -> str:
    """Create a new item in a Fabric workspace.

    Args:
        workspace_id: The workspace UUID (e.g. '8bdca8af-1db1-4fd8-9564-0c98b4dbdffc'). NOT the workspace name.
        display_name: Display name for the new item.
        item_type: Item type — e.g. 'Lakehouse', 'Notebook', 'Warehouse'.
        description: Optional description for the item.
        folder_id: Optional folder UUID. If supplied, the item is created inside that folder.
    """
    body: dict = {"displayName": display_name, "type": item_type}
    if description:
        body["description"] = description
    if folder_id:
        body["folderId"] = folder_id
    async with shared_client(30.0) as client:
        resp = await client.post(
            f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items",
            json=body,
            headers=_fabric_headers(),
        )
    if resp.status_code not in (200, 201, 202):
        return format_http_error(resp, 'creating item')
    created_item = resp.json()
    created_item.update(_build_item_links(workspace_id, created_item.get("id"), created_item.get("type", item_type)))
    return json.dumps(created_item, indent=2)


@mcp.tool()
async def fabric_create_workspace_inventory_solution(
    workspace_id: str,
    folder_name: str,
    solution_name: str = "Fabric Items Inventory",
    description: str | None = None,
) -> str:
    """Create a complete Fabric item-inventory solution in one run folder.

    The tool lists accessible Fabric items in the selected workspace and creates
    a timestamped folder containing a Lakehouse, a Notebook with executable
    ingestion code, Delta tables written by a notebook run, a SemanticModel with
    one FabricItems table, and a Report bound to that model. Use this for prompts
    asking for an end-to-end solution that visualizes all Fabric items the user
    can access.

    Args:
        workspace_id: The workspace UUID. NOT the workspace name.
        folder_name: Timestamped run folder name, e.g. 'tmp_20260426174823'.
        solution_name: Base name used for the produced artifacts.
        description: Optional description for created Fabric items.
    """
    item_description = (description or "AgentHub generated inventory of accessible Fabric items.")[:256]
    produced: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    progress: list[dict] = []
    started_at = time.monotonic()
    cleanup: list[dict] = []
    capacity_validation: dict | None = None
    model_data_validation: dict | None = None
    report_render_validation: dict | None = None
    warehouse_id = ""
    warehouse_name = ""

    async with shared_client(120.0) as client:
        headers = _fabric_headers()
        _record_inventory_progress(progress, started_at, "capacity_validation", "started", workspaceId=workspace_id)
        capacity_outcome = await _validate_workspace_capacity_active(client, workspace_id, headers)
        if isinstance(capacity_outcome, str):
            _record_inventory_progress(progress, started_at, "capacity_validation", "blocked", error=capacity_outcome)
            result = {
                "status": "blocked",
                "workspaceId": workspace_id,
                "folderId": None,
                "folderName": folder_name,
                "errors": [capacity_outcome],
                "warnings": warnings,
                "progress": progress,
                "createdItems": produced,
                "summary": "Inventory solution creation was blocked before creating Fabric items because capacity readiness could not be verified.",
            }
            return "Error creating inventory solution: " + json.dumps(result, indent=2)
        capacity_validation = capacity_outcome
        _record_inventory_progress(progress, started_at, "capacity_validation", "ok", validation=capacity_validation)

        _record_inventory_progress(progress, started_at, "source_inventory", "started")
        source_items, source_warnings, source_workspace_count = await _collect_accessible_inventory_items(client, workspace_id, headers)
        warnings.extend(source_warnings)
        _record_inventory_progress(
            progress,
            started_at,
            "source_inventory",
            "ok" if source_items else "empty",
            sourceItemCount=len(source_items),
            sourceWorkspaceCount=source_workspace_count,
            warnings=len(source_warnings),
        )
        if not source_items:
            errors.append("No accessible Fabric items were returned for the inventory data set.")

        _record_inventory_progress(progress, started_at, "folder", "started", folderName=folder_name)
        folder_resp = await client.get(f"{FABRIC_API_BASE}/workspaces/{workspace_id}/folders", headers=headers)
        if folder_resp.status_code != 200:
            return format_http_error(folder_resp, "listing folders for inventory solution")
        existing_folder = next(
            (folder for folder in folder_resp.json().get("value", []) if folder.get("displayName") == folder_name),
            None,
        )
        if existing_folder:
            folder_result = existing_folder
        else:
            folder_result = await _post_fabric_json(
                client,
                f"{FABRIC_API_BASE}/workspaces/{workspace_id}/folders",
                {"displayName": folder_name},
                "creating inventory solution folder",
            )
            if isinstance(folder_result, str):
                return folder_result
        folder_id = folder_result.get("id")
        if not folder_id:
            return "Error creating inventory solution folder: Fabric did not return a folder id"
        produced.append({"displayName": folder_name, "type": "Folder", "id": folder_id, "workspaceId": workspace_id})
        _record_inventory_progress(
            progress,
            started_at,
            "folder",
            "reused" if existing_folder else "created",
            folderId=folder_id,
            folderName=folder_name,
        )
        item_base = _inventory_item_base(folder_name, folder_id)

        _record_inventory_progress(progress, started_at, "data_artifact", "started", preferredType="Lakehouse")
        lakehouse_result = await _create_or_reuse_inventory_item(
            client,
            workspace_id,
            headers,
            display_name=f"{item_base}LH",
            item_type="Lakehouse",
            description=item_description,
            folder_id=folder_id,
            op_name="creating inventory lakehouse",
        )
        lakehouse_id = ""
        lakehouse_name = ""
        if isinstance(lakehouse_result, str):
            warnings.append(f"Lakehouse creation without optional schema payload failed; retrying with schemas enabled. First error: {lakehouse_result}")
            lakehouse_result = await _create_or_reuse_inventory_item(
                client,
                workspace_id,
                headers,
                display_name=f"{item_base}LHS",
                item_type="Lakehouse",
                description=item_description,
                folder_id=folder_id,
                op_name="creating inventory lakehouse",
                extra_body={"creationPayload": {"enableSchemas": True}},
            )
        if isinstance(lakehouse_result, str):
            warehouse_result = await _create_or_reuse_inventory_item(
                client,
                workspace_id,
                headers,
                display_name=f"{item_base}WH",
                item_type="Warehouse",
                description=item_description,
                folder_id=folder_id,
                op_name="creating inventory warehouse fallback",
            )
            if isinstance(warehouse_result, str):
                errors.extend([lakehouse_result, warehouse_result])
            else:
                warehouse_id = warehouse_result.get("id", "")
                warehouse_name = warehouse_result.get("displayName") or warehouse_result.get("name") or f"{item_base}WH"
                warnings.append("Lakehouse creation was rejected; created a Warehouse data artifact instead.")
                produced.append(warehouse_result)
                _record_inventory_progress(progress, started_at, "data_artifact", "warehouse_fallback", itemId=warehouse_id, displayName=warehouse_name)
        else:
            lakehouse_id = lakehouse_result.get("id", "")
            lakehouse_name = lakehouse_result.get("displayName") or lakehouse_result.get("name") or f"{item_base}LH"
            produced.append(lakehouse_result)
            _record_inventory_progress(progress, started_at, "data_artifact", "created", itemId=lakehouse_id, displayName=lakehouse_name)

        table_name = f"{item_base}_FabricItems"
        summary_table_name = f"{item_base}_FabricItemsByType"
        notebook_execution: dict | None = None
        persistent_data_validation: dict | None = None
        _record_inventory_progress(progress, started_at, "notebook", "started")
        notebook_result = await _create_or_reuse_inventory_item(
            client,
            workspace_id,
            headers,
            display_name=f"{item_base}NB",
            item_type="Notebook",
            description=item_description,
            folder_id=folder_id,
            op_name="creating inventory notebook",
        )
        if isinstance(notebook_result, str):
            errors.append(notebook_result)
            _record_inventory_progress(progress, started_at, "notebook", "failed", error=notebook_result)
        else:
            produced.append(notebook_result)
            notebook_id = notebook_result.get("id", "")
            if not lakehouse_id:
                warnings.append(
                    "Lakehouse creation was rejected; the inventory notebook will run without Delta table writes, "
                    "and the SemanticModel and Report will be populated directly from live Fabric inventory data."
                )
            if not notebook_id:
                errors.append("Inventory notebook could not be executed because Fabric did not return a notebook id.")
                _record_inventory_progress(progress, started_at, "notebook", "failed", error="missing_notebook_id")
            else:
                _record_inventory_progress(progress, started_at, "notebook_definition", "started", notebookId=notebook_id)
                definition_result = await _update_notebook_definition(
                    client,
                    workspace_id,
                    notebook_id,
                    _inventory_notebook_definition(
                        workspace_id=workspace_id,
                        lakehouse_id=lakehouse_id,
                        lakehouse_name=lakehouse_name,
                        table_name=table_name,
                        summary_table_name=summary_table_name,
                    ),
                )
                if isinstance(definition_result, str):
                    errors.append(definition_result)
                    _record_inventory_progress(progress, started_at, "notebook_definition", "failed", notebookId=notebook_id, error=definition_result)
                else:
                    _record_inventory_progress(progress, started_at, "notebook_definition", "ok", notebookId=notebook_id)
                    produced.append({
                        "displayName": f"{item_base}NB definition",
                        "type": "NotebookDefinition",
                        "id": notebook_id,
                        "workspaceId": workspace_id,
                        "folderId": folder_id,
                    })
                    _record_inventory_progress(progress, started_at, "notebook_run", "started", notebookId=notebook_id)
                    run_result = await _run_inventory_notebook(client, workspace_id, notebook_id, headers)
                    if isinstance(run_result, str):
                        errors.append(run_result)
                        _record_inventory_progress(progress, started_at, "notebook_run", "failed", notebookId=notebook_id, error=run_result)
                    else:
                        notebook_execution = run_result
                        _record_inventory_progress(
                            progress,
                            started_at,
                            "notebook_run",
                            "ok",
                            notebookId=notebook_id,
                            runId=run_result.get("id"),
                            runStatus=run_result.get("status"),
                        )
                        produced.append({
                            "displayName": f"{item_base}NB run",
                            "type": "NotebookRun",
                            "id": run_result.get("id"),
                            "workspaceId": workspace_id,
                            "folderId": folder_id,
                            "status": run_result.get("status"),
                            "exitValue": run_result.get("exitValue"),
                        })
                        if lakehouse_id:
                            _record_inventory_progress(progress, started_at, "lakehouse_table_validation", "started", lakehouseId=lakehouse_id)
                            table_validation = await _wait_for_lakehouse_tables(
                                client,
                                workspace_id,
                                lakehouse_id,
                                headers,
                                {table_name, summary_table_name},
                            )
                            if isinstance(table_validation, str):
                                errors.append(
                                    "Notebook completed, but Lakehouse Delta tables were not verified: "
                                    f"{table_validation}"
                                )
                                _record_inventory_progress(progress, started_at, "lakehouse_table_validation", "failed", lakehouseId=lakehouse_id, error=table_validation)
                            else:
                                persistent_data_validation = table_validation
                                _record_inventory_progress(
                                    progress,
                                    started_at,
                                    "lakehouse_table_validation",
                                    "ok",
                                    lakehouseId=lakehouse_id,
                                    tables=table_validation.get("tables"),
                                    via=table_validation.get("via"),
                                )
                                produced.append({
                                    "displayName": f"{item_base}Lakehouse table validation",
                                    "type": "LakehouseTables",
                                    "id": lakehouse_id,
                                    "workspaceId": workspace_id,
                                    "status": table_validation.get("status"),
                                    "tables": table_validation.get("tables"),
                                    "via": table_validation.get("via"),
                                })

        _record_inventory_progress(progress, started_at, "semantic_model", "started")
        model_result = await _create_or_reuse_inventory_item(
            client,
            workspace_id,
            headers,
            display_name=f"{item_base}Model",
            item_type="SemanticModel",
            description=item_description,
            folder_id=folder_id,
            op_name="creating inventory semantic model",
            extra_body={
                "definition": _semantic_model_definition(source_items),
            },
        )
        if isinstance(model_result, str):
            errors.append(model_result)
            model_id = ""
            _record_inventory_progress(progress, started_at, "semantic_model", "failed", error=model_result)
        else:
            model_id = model_result.get("id", "")
            produced.append(model_result)
            _record_inventory_progress(progress, started_at, "semantic_model", "created", modelId=model_id)

        # Skip the explicit semantic model refresh — the workspace API
        # returns ItemNotFound for several minutes after creation, and
        # data is already materialised in the embedded `#table(...)` M
        # expression so the model can serve queries without it.

        # Trigger an import-mode refresh so the dataset materialises its
        # rows in storage. Power BI's report iframe will otherwise stay
        # on "Loading your report..." forever even though the model
        # definition contains the data inline.
        if model_id:
            _record_inventory_progress(progress, started_at, "semantic_model_refresh", "started", modelId=model_id)
            refresh_outcome = await _refresh_semantic_model(
                client, workspace_id, model_id, headers,
            )
            if isinstance(refresh_outcome, str):
                warnings.append(f"Refresh issue: {refresh_outcome}")
                _record_inventory_progress(progress, started_at, "semantic_model_refresh", "warning", modelId=model_id, error=refresh_outcome)
            else:
                _record_inventory_progress(
                    progress,
                    started_at,
                    "semantic_model_refresh",
                    "ok",
                    modelId=model_id,
                    refreshId=refresh_outcome.get("id") or refresh_outcome.get("via"),
                    refreshStatus=refresh_outcome.get("status"),
                )
                produced.append({
                    "displayName": f"{item_base}Model refresh",
                    "type": "SemanticModelRefresh",
                    "id": refresh_outcome.get("id") or refresh_outcome.get("via"),
                    "workspaceId": workspace_id,
                    "status": refresh_outcome.get("status"),
                })
            _record_inventory_progress(progress, started_at, "semantic_model_data_validation", "started", modelId=model_id)
            model_data_outcome = await _wait_for_inventory_model_data(
                client,
                workspace_id,
                model_id,
                expected_min_rows=max(1, min(len(source_items), 500)),
            )
            if isinstance(model_data_outcome, str):
                errors.append(
                    "Semantic model did not become queryable through Power BI executeQueries: "
                    f"{model_data_outcome}"
                )
                _record_inventory_progress(progress, started_at, "semantic_model_data_validation", "failed", modelId=model_id, error=model_data_outcome)
            else:
                model_data_validation = model_data_outcome
                _record_inventory_progress(
                    progress,
                    started_at,
                    "semantic_model_data_validation",
                    "ok",
                    modelId=model_id,
                    rowCount=model_data_outcome.get("rowCount"),
                    via=model_data_outcome.get("via"),
                )
                produced.append({
                    "displayName": f"{item_base}Model data validation",
                    "type": "SemanticModelData",
                    "id": model_id,
                    "workspaceId": workspace_id,
                    "status": model_data_outcome.get("status"),
                    "rowCount": model_data_outcome.get("rowCount"),
                    "itemTypes": model_data_outcome.get("itemTypes"),
                    "via": model_data_outcome.get("via"),
                })

        if model_id and model_data_validation:
            _record_inventory_progress(progress, started_at, "report_definition", "started", modelId=model_id)
            # Prefer cloning an existing report's PBIR shape (which the
            # render engine is already known to accept) and rebinding it
            # to our model. Falls back to the hand-rolled definition if
            # no template is available.
            report_definition = await _build_inventory_report_definition_from_clone(
                client, workspace_id, model_id, headers, skip_folder_id=folder_id,
            )
            if report_definition is None:
                report_definition = _report_definition(model_id)
                _record_inventory_progress(progress, started_at, "report_definition", "fallback", modelId=model_id)
            else:
                _record_inventory_progress(progress, started_at, "report_definition", "cloned", modelId=model_id)

            _record_inventory_progress(progress, started_at, "report", "started", modelId=model_id)
            report_result = await _create_or_reuse_inventory_item(
                client,
                workspace_id,
                headers,
                display_name=f"{item_base}Report",
                item_type="Report",
                description=item_description,
                folder_id=folder_id,
                op_name="creating inventory report",
                extra_body={
                    "definition": report_definition,
                },
            )
            if isinstance(report_result, str):
                errors.append(report_result)
                _record_inventory_progress(progress, started_at, "report", "failed", modelId=model_id, error=report_result)
            else:
                report_id = report_result.get("id", "")
                if not report_id:
                    errors.append("Report item creation did not return a report id, so report render validation could not run.")
                    _record_inventory_progress(progress, started_at, "report", "failed", modelId=model_id, error="missing_report_id")
                else:
                    _record_inventory_progress(progress, started_at, "report_render_validation", "started", reportId=report_id, modelId=model_id)
                    report_validation_outcome = await _verify_report_renderable(
                        client,
                        workspace_id,
                        report_id,
                        model_id,
                    )
                    if isinstance(report_validation_outcome, str):
                        _record_inventory_progress(progress, started_at, "report_render_validation", "failed", reportId=report_id, modelId=model_id, error=report_validation_outcome)
                        cleanup_outcome = await _delete_unverified_inventory_item(
                            client,
                            workspace_id,
                            report_id,
                            headers,
                        )
                        if isinstance(cleanup_outcome, str):
                            cleanup.append({"type": "Report", "id": report_id, "status": "cleanup_failed", "error": cleanup_outcome})
                            _record_inventory_progress(progress, started_at, "report_cleanup", "failed", reportId=report_id, error=cleanup_outcome)
                            errors.append(
                                "Report render validation failed and cleanup also failed; remove the unverified report manually: "
                                f"{report_validation_outcome}; cleanup: {cleanup_outcome}"
                            )
                        else:
                            cleanup.append({"type": "Report", "id": report_id, **cleanup_outcome})
                            _record_inventory_progress(progress, started_at, "report_cleanup", "ok", reportId=report_id, cleanup=cleanup_outcome)
                            errors.append(
                                "Report render validation failed; the unverified report item was deleted instead of being returned as a created artifact: "
                                f"{report_validation_outcome}"
                            )
                    else:
                        report_render_validation = report_validation_outcome
                        _record_inventory_progress(
                            progress,
                            started_at,
                            "report_render_validation",
                            "ok",
                            reportId=report_id,
                            modelId=model_id,
                            via=report_validation_outcome.get("via"),
                            exportId=report_validation_outcome.get("exportId"),
                        )
                        produced.append(report_result)
                        produced.append({
                            "displayName": f"{item_base}Report render validation",
                            "type": "ReportRenderValidation",
                            "id": report_id,
                            "workspaceId": workspace_id,
                            "status": report_validation_outcome.get("status"),
                            "via": report_validation_outcome.get("via"),
                            "exportId": report_validation_outcome.get("exportId"),
                        })

    report_created = any(item.get("type") == "Report" for item in produced)
    model_created = any(item.get("type") == "SemanticModel" for item in produced)
    semantic_model_queryable = model_created and bool(model_data_validation)
    notebook_writes_enabled = bool(lakehouse_id)
    persistent_data_written = bool(notebook_execution and lakehouse_id and persistent_data_validation)
    if not persistent_data_written:
        errors.append(
            "Inventory data was not persisted by the notebook because no Lakehouse-backed Delta table write completed."
        )
    if model_created and not semantic_model_queryable:
        errors.append("Semantic model data was not verified through the Power BI executeQueries API.")
    if semantic_model_queryable and not report_created:
        errors.append("Report was not delivered because report creation or render validation failed after model data validation.")
    if report_created and not report_render_validation:
        errors.append("Report item exists but was not verified by a Power BI render/export proof.")
    blocking_errors = errors
    _record_inventory_progress(
        progress,
        started_at,
        "final_summary",
        "created" if not blocking_errors else "partial",
        producedCount=len(produced),
        warningCount=len(warnings),
        errorCount=len(blocking_errors),
        reportCreated=report_created,
        modelQueryable=semantic_model_queryable,
        persistentDataWritten=persistent_data_written,
    )

    result = {
        "status": "created" if not blocking_errors else "partial",
        "workspaceId": workspace_id,
        "folderId": folder_id,
        "folderName": folder_name,
        "sourceItemCount": len(source_items),
        "sourceWorkspaceCount": source_workspace_count,
        "dataSource": "lakehouse_delta_tables" if persistent_data_written else "fabric_rest_import_model",
        "capacityValidation": capacity_validation,
        "notebookWritesEnabled": notebook_writes_enabled,
        "persistentDataWritten": persistent_data_written,
        "persistentDataStore": (
            {"type": "Lakehouse", "id": lakehouse_id, "displayName": lakehouse_name, "written": persistent_data_written, "validation": persistent_data_validation}
            if lakehouse_id else (
                {"type": "Warehouse", "id": warehouse_id, "displayName": warehouse_name, "written": False}
                if warehouse_id else None
            )
        ),
        "lakehouseTableName": table_name,
        "lakehouseSummaryTableName": summary_table_name,
        "notebookExecution": notebook_execution,
        "semanticModelDataValidation": model_data_validation,
        "reportRenderValidation": report_render_validation,
        "cleanup": cleanup,
        "errors": blocking_errors,
        "warnings": warnings,
        "progress": progress,
        "createdItems": produced,
        "summary": "Created and verified Fabric inventory solution with folder, Lakehouse tables, executed Notebook, queryable SemanticModel, and renderable Report." if not blocking_errors else "Created a partial Fabric inventory solution; inspect errors.",
    }
    if blocking_errors:
        return "Error creating inventory solution: " + json.dumps(result, indent=2)
    return json.dumps(result, indent=2)


@mcp.tool()
async def fabric_delete_item(
    workspace_id: str,
    item_id: str,
) -> str:
    """Delete an item from a Fabric workspace.

    This is a destructive operation. The item and its data will be permanently removed.

    Args:
        workspace_id: The workspace UUID (e.g. '8bdca8af-1db1-4fd8-9564-0c98b4dbdffc').
        item_id: The item UUID to delete.
    """
    async with shared_client(30.0) as client:
        resp = await client.delete(
            f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{item_id}",
            headers=_fabric_headers(),
        )
    if resp.status_code not in (200, 204):
        return format_http_error(resp, 'deleting item')
    return json.dumps({"status": "deleted", "item_id": item_id})


# ---------------------------------------------------------------------------
# OneLake DFS tools
# ---------------------------------------------------------------------------

def _validate_path(path: str) -> str:
    """Reject directory traversal attempts."""
    for segment in path.replace("\\", "/").split("/"):
        if segment.strip() in (".", "..", "~"):
            raise ValueError(f"Path traversal not allowed: {path}")
    return path.strip("/")


@mcp.tool()
async def fabric_list_files(
    workspace_id: str,
    item_id: str,
    path: str | None = None,
    recursive: bool = False,
) -> str:
    """List files and folders in a OneLake item (e.g. Lakehouse Files/).

    Args:
        workspace_id: Workspace UUID (e.g. '8bdca8af-1db1-4fd8-9564-0c98b4dbdffc').
        item_id: Item UUID (e.g. 'a325c1d9-53b5-4be9-8f80-06b7079ae289').
        path: Sub-path to list (defaults to root — shows Files/ and Tables/).
        recursive: If true, list all files recursively.
    """
    url = f"{ONELAKE_DFS_BASE}/{workspace_id}/{item_id}"
    if path:
        url += f"/{_validate_path(path)}"
    url += f"?resource=filesystem&recursive={str(recursive).lower()}"

    async with shared_client(30.0) as client:
        resp = await client.get(url, headers=_onelake_headers())

        if resp.status_code == 404:
            # Fall back: try listing Files/ and Tables/ separately
            if not path:
                results: list = []
                for folder in ("Files", "Tables"):
                    folder_url = (
                        f"{ONELAKE_DFS_BASE}/{workspace_id}/{item_id}/{folder}"
                        f"?resource=filesystem&recursive={str(recursive).lower()}"
                    )
                    try:
                        resp2 = await client.get(folder_url, headers=_onelake_headers())
                        if resp2.status_code == 200:
                            results.extend(resp2.json().get("paths", []))
                    except Exception:
                        continue
                return json.dumps(results, indent=2) if results else "No files found."
            return f"Not found: {resp.status_code}"
        if resp.status_code != 200:
            return format_http_error(resp, 'listing files')

        data = resp.json()
        paths = data.get("paths", [])
        result = []
        for p in paths:
            result.append({
                "name": p.get("name", "").rsplit("/", 1)[-1],
                "path": p.get("name"),
                "isDirectory": p.get("isDirectory", "false") == "true"
                               or str(p.get("isDirectory", False)).lower() == "true",
                "size": p.get("contentLength"),
                "lastModified": p.get("lastModified"),
            })
        return json.dumps(result, indent=2)


@mcp.tool()
async def fabric_read_file(
    workspace_id: str,
    item_id: str,
    file_path: str,
    max_bytes: int = 1_000_000,
) -> str:
    """Read the contents of a file from OneLake.

    For text files the content is returned directly. Binary files return
    base64-encoded content. Files larger than max_bytes are truncated.

    Args:
        workspace_id: Workspace UUID.
        item_id: Item UUID.
        file_path: Path within the item (e.g. 'Files/data.csv').
        max_bytes: Maximum bytes to read (default 1 MB).
    """
    clean_path = _validate_path(file_path)
    url = f"{ONELAKE_DFS_BASE}/{workspace_id}/{item_id}/{clean_path}"

    headers = _onelake_headers()
    if max_bytes:
        headers["Range"] = f"bytes=0-{max_bytes - 1}"

    async with shared_client(60.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code not in (200, 206):
        return format_http_error(resp, 'reading file')

    content_type = resp.headers.get("content-type", "")
    if "text" in content_type or "json" in content_type or "csv" in content_type or "xml" in content_type:
        return resp.text
    return f"[binary, {len(resp.content)} bytes, base64]\n{base64.b64encode(resp.content).decode()}"


@mcp.tool()
async def fabric_write_file(
    workspace_id: str,
    item_id: str,
    file_path: str,
    content: str,
    overwrite: bool = False,
) -> str:
    """Write content to a file in OneLake (create or overwrite).

    WARNING: This is a destructive action. Only call after confirming with the user.

    Args:
        workspace_id: Workspace GUID.
        item_id: Item GUID.
        file_path: Destination path (e.g. 'Files/output.csv').
        content: The text content to write.
        overwrite: Set to true to overwrite an existing file.
    """
    clean_path = _validate_path(file_path)
    base_url = f"{ONELAKE_DFS_BASE}/{workspace_id}/{item_id}/{clean_path}"
    headers = _onelake_headers()

    content_bytes = content.encode("utf-8")

    async with shared_client(60.0) as client:
        # Step 1: Create the file resource
        create_headers = {**headers, "Content-Length": "0"}
        if not overwrite:
            create_headers["If-None-Match"] = "*"
        resp = await client.put(f"{base_url}?resource=file", headers=create_headers, content=b"")
        if resp.status_code not in (200, 201):
            return format_http_error(resp, 'creating file')

        # Step 2: Append content
        resp = await client.patch(
            f"{base_url}?action=append&position=0",
            headers={**headers, "Content-Type": "application/octet-stream"},
            content=content_bytes,
        )
        if resp.status_code not in (200, 202):
            return format_http_error(resp, 'uploading content')

        # Step 3: Flush / commit
        resp = await client.patch(
            f"{base_url}?action=flush&position={len(content_bytes)}",
            headers=headers,
        )
        if resp.status_code not in (200, 202):
            return format_http_error(resp, 'flushing file')

    return json.dumps({"status": "ok", "path": clean_path, "bytes_written": len(content_bytes)})


@mcp.tool()
async def fabric_delete_file(workspace_id: str, item_id: str, file_path: str) -> str:
    """Delete a file from OneLake.

    WARNING: This is a destructive action. Only call after confirming with the user.

    Args:
        workspace_id: Workspace GUID.
        item_id: Item GUID.
        file_path: Path of file to delete (e.g. 'Files/old_data.csv').
    """
    clean_path = _validate_path(file_path)
    url = f"{ONELAKE_DFS_BASE}/{workspace_id}/{item_id}/{clean_path}"
    async with shared_client(30.0) as client:
        resp = await client.delete(url, headers=_onelake_headers())
    if resp.status_code not in (200, 202, 204):
        return format_http_error(resp, 'deleting file')
    return json.dumps({"status": "deleted", "path": clean_path})


@mcp.tool()
async def fabric_create_directory(
    workspace_id: str, item_id: str, directory_path: str
) -> str:
    """Create a directory in OneLake.

    Args:
        workspace_id: Workspace GUID.
        item_id: Item GUID.
        directory_path: Path for the new directory (e.g. 'Files/reports/2024').
    """
    clean_path = _validate_path(directory_path)
    url = f"{ONELAKE_DFS_BASE}/{workspace_id}/{item_id}/{clean_path}?resource=directory"
    async with shared_client(30.0) as client:
        resp = await client.put(url, headers=_onelake_headers())
    if resp.status_code not in (200, 201):
        return format_http_error(resp, 'creating directory')
    return json.dumps({"status": "created", "path": clean_path})


if __name__ == "__main__":
    mcp.run()
