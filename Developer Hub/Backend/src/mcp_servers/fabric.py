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
import re
import sys
import time
from collections import Counter
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


def _compact_inventory_check(check: object) -> object:
    if not isinstance(check, dict):
        return check
    compact = {
        key: check[key]
        for key in ("name", "status", "passed")
        if key in check
    }
    value = check.get("value")
    if isinstance(value, (str, int, float, bool)) or value is None:
        if "value" in check:
            compact["value"] = value
    elif isinstance(value, list):
        compact["value"] = value[:12]
        if len(value) > 12:
            compact["valueTruncated"] = len(value) - 12
    elif isinstance(value, dict):
        compact["valueKeys"] = sorted(str(key) for key in value.keys())[:12]
    return compact


def _compact_inventory_quality_validation(quality: object) -> object:
    if not isinstance(quality, dict):
        return quality

    compact: dict = {}
    if "status" in quality:
        compact["status"] = quality.get("status")
    checks = quality.get("checks")
    if isinstance(checks, list):
        compact["checks"] = [_compact_inventory_check(check) for check in checks]

    for section_name in ("semanticModel", "report", "notebookCode"):
        section = quality.get(section_name)
        if not isinstance(section, dict):
            continue
        section_compact = {
            key: section[key]
            for key in (
                "status",
                "storageMode",
                "measureNames",
                "columnNames",
                "pageCount",
                "visualCount",
                "visualTypes",
                "slicerCount",
                "chartCount",
                "cardCount",
                "modernReaderExperience",
                "designStandard",
                "readerScenario",
                "designRubric",
                "storyFlow3_30_300",
                "visibleNarrativeHeader",
                "visibleReaderPathSummary",
                "visibleSourceTransparency",
                "prominentAnalysisZones",
                "informationHierarchy",
                "usabilityInteractions",
                "scenarioNavigation",
                "methodologyTransparency",
                "customCardsAndTooltips",
                "accessibilityMetadata",
                "guidedTabOrder",
                "restrainedVisualDensity",
                "highContrastCanvas",
                "themeName",
                "palette",
                "visualStyleDefaults",
                "overlapCount",
                "classCount",
                "functionCount",
                "raisesRuntimeErrors",
                "tracksWarnings",
                "failsOnEmptyInventory",
                "failsWithoutLakehouse",
                "usesRequestTimeouts",
            )
            if key in section
        }
        section_checks = section.get("checks")
        if isinstance(section_checks, list):
            section_compact["checks"] = [_compact_inventory_check(check) for check in section_checks]
        compact[section_name] = section_compact
    return compact


def _compact_directlake_identity_diagnostics(diagnostics: object) -> object:
    if not isinstance(diagnostics, dict):
        return diagnostics
    compact = {
        key: diagnostics[key]
        for key in ("status", "ownerMismatch", "message")
        if key in diagnostics
    }
    for item_key in ("sourceItem", "semanticModelItem"):
        item = diagnostics.get(item_key)
        if isinstance(item, dict):
            compact[item_key] = {
                key: item[key]
                for key in ("id", "displayName", "type", "owner")
                if key in item
            }
    endpoint = diagnostics.get("sqlEndpoint")
    if isinstance(endpoint, dict):
        compact["sqlEndpoint"] = {
            key: endpoint[key]
            for key in ("id", "connectionString")
            if key in endpoint
        }
    identity = diagnostics.get("apiTokenIdentity")
    if isinstance(identity, dict):
        compact["apiTokenIdentity"] = {
            key: identity[key]
            for key in ("upn", "idtyp", "authMode", "hasDelegatedScopes", "hasApplicationRoles")
            if key in identity
        }
    return compact


def _compact_inventory_progress(progress: list[dict]) -> list[dict]:
    compact_rows: list[dict] = []
    for row in progress:
        compact_row: dict = {}
        for key, value in row.items():
            if key == "qualityValidation":
                if isinstance(value, dict):
                    compact_row[key] = {
                        "status": value.get("status"),
                        "semanticModelStatus": (value.get("semanticModel") or {}).get("status") if isinstance(value.get("semanticModel"), dict) else None,
                        "reportStatus": (value.get("report") or {}).get("status") if isinstance(value.get("report"), dict) else None,
                        "notebookCodeStatus": (value.get("notebookCode") or {}).get("status") if isinstance(value.get("notebookCode"), dict) else None,
                    }
                else:
                    compact_row[key] = value
            elif key == "diagnostics":
                compact_row[key] = _compact_directlake_identity_diagnostics(value)
            elif isinstance(value, (str, int, float, bool)) or value is None:
                compact_row[key] = value
            elif key in {"tables", "rowCounts", "validation", "namingConvention", "itemDisplayNames", "errors", "warnings", "cleanup"}:
                compact_row[key] = value
            elif isinstance(value, list):
                compact_row[key] = value[:12]
                if len(value) > 12:
                    compact_row[f"{key}Truncated"] = len(value) - 12
            elif isinstance(value, dict):
                try:
                    encoded = json.dumps(value, sort_keys=True, default=str)
                except Exception:
                    encoded = repr(value)
                if len(encoded) <= 1200:
                    compact_row[key] = value
                else:
                    compact_row[key] = {
                        "digest": stable_digest(encoded),
                        "preview": bounded_text(encoded, max_chars=600),
                    }
        compact_rows.append(compact_row)
    return compact_rows


def _compact_inventory_created_items(items: list[dict]) -> list[dict]:
    compact_items: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        compact_items.append({
            key: item[key]
            for key in (
                "displayName",
                "type",
                "id",
                "workspaceId",
                "folderId",
                "status",
                "rowCount",
                "itemTypes",
                "tables",
                "rowCounts",
                "via",
                "webUrl",
            )
            if key in item
        })
    return compact_items


def _compact_inventory_tool_result(result: dict) -> dict:
    summary_keys = (
        "status",
        "workspaceId",
        "folderId",
        "folderName",
        "sourceItemCount",
        "preCreationSourceItemCount",
        "sourceWorkspaceCount",
        "dataSource",
        "semanticModelStorageMode",
        "capacityValidation",
        "notebookWritesEnabled",
        "persistentDataWritten",
        "persistentDataStore",
        "lakehouseTableName",
        "lakehouseSummaryTableName",
        "notebookExecution",
        "semanticModelDataValidation",
        "reportRenderValidation",
        "namingConvention",
        "cleanup",
        "errors",
        "warnings",
        "summary",
    )
    compact = {key: result[key] for key in summary_keys if key in result}
    compact["qualityValidation"] = _compact_inventory_quality_validation(result.get("qualityValidation"))
    compact["directLakeIdentityDiagnostics"] = _compact_directlake_identity_diagnostics(result.get("directLakeIdentityDiagnostics"))
    item_names = result.get("itemDisplayNames")
    if isinstance(item_names, dict):
        compact["itemDisplayNames"] = {
            key: item_names[key]
            for key in ("folder", "lakehouse", "notebook", "semanticModel", "report")
            if key in item_names
        }
    progress = result.get("progress")
    if isinstance(progress, list):
        progress_window = progress[:8] + progress[-12:] if len(progress) > 20 else progress
        compact["progress"] = _compact_inventory_progress(progress_window)
        if len(progress) > len(progress_window):
            compact["progressTruncated"] = len(progress) - len(progress_window)
    created_items = result.get("createdItems")
    if isinstance(created_items, list):
        compact_items = _compact_inventory_created_items(created_items)
        compact["createdItems"] = compact_items[:24]
        if len(compact_items) > 24:
            compact["createdItemsTruncated"] = len(compact_items) - 24
    return compact


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


def _token_claims(token: str) -> dict | None:
    if not token:
        return None
    try:
        claims = jwt.get_unverified_claims(token)
    except Exception:
        return None
    return claims if isinstance(claims, dict) else None


def _token_auth_mode(claims: dict | None) -> str:
    if not claims:
        return "unknown"
    scp = claims.get("scp")
    idtyp = str(claims.get("idtyp") or "").lower()
    roles = claims.get("roles")
    if isinstance(scp, str) and scp.strip():
        return "delegated_user"
    if idtyp == "app" or (roles and not scp):
        return "application"
    return "unknown"


def _token_identity_from_claims(claims: dict | None) -> dict:
    if claims is None:
        return {"token": "unreadable"}
    identity = {
        key: claims.get(key)
        for key in (
            "name",
            "preferred_username",
            "upn",
            "appid",
            "azp",
            "oid",
            "tid",
            "idtyp",
            "app_displayname",
        )
        if claims.get(key)
    }
    identity["authMode"] = _token_auth_mode(claims)
    identity["hasDelegatedScopes"] = bool(str(claims.get("scp") or "").strip())
    identity["hasApplicationRoles"] = bool(claims.get("roles"))
    return identity


def _delegated_token_block_message(env_name: str, operation: str, identity: dict) -> str:
    principal = (
        identity.get("app_displayname")
        or identity.get("name")
        or identity.get("appid")
        or identity.get("azp")
        or "the app registration/service principal"
    )
    return (
        f"Blocked Fabric write for {operation}: {env_name} is an application/service-principal token "
        f"({principal}). AgentHub refuses to create or update mission artifacts with the app registration "
        "because those artifacts would be owned by the app instead of the user who submitted the mission. "
        "Re-authenticate through the delegated user/OBO flow and retry."
    )


def _require_delegated_user_token(env_name: str, operation: str) -> dict:
    token = os.environ.get(env_name, "")
    if not token:
        raise RuntimeError(f"{env_name} not set — user may not be authenticated.")
    claims = _token_claims(token)
    identity = _token_identity_from_claims(claims)
    if identity.get("authMode") == "application":
        raise RuntimeError(_delegated_token_block_message(env_name, operation, identity))
    return identity


def _fabric_headers(*, require_delegated: bool = False, operation: str = "Fabric API call") -> dict:
    token = os.environ.get("FABRIC_API_TOKEN", "")
    if not token:
        raise RuntimeError("FABRIC_API_TOKEN not set — user may not be authenticated.")
    if require_delegated:
        _require_delegated_user_token("FABRIC_API_TOKEN", operation)
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


def _decode_inline_json_part(part: dict) -> dict | None:
    """Reverse of ``_inline_json_part`` for in-process validation."""
    if not isinstance(part, dict):
        return None
    payload_type = str(part.get("payloadType") or "")
    payload = part.get("payload")
    if payload_type != "InlineBase64" or not isinstance(payload, str):
        return None
    try:
        decoded = base64.b64decode(payload).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return None


def _validate_semantic_model_definition(definition: dict) -> list[str]:
    """Structural pre-flight on a Fabric semantic-model definition.

    This catches the common Direct Lake TMSL mistakes deterministically
    *before* we ship to Fabric, where errors only surface much later as
    opaque ``0xC14700C7`` refresh failures or empty visuals. We do not
    aim for full schema coverage — we check the things that have
    actually broken in production:

    * ``compatibilityLevel`` >= 1604 (Direct Lake requirement).
    * ``model.expressions`` contains a named M expression for every
      ``expressionSource`` referenced by a Direct Lake partition.
    * Every Direct Lake partition has ``source.type == "entity"`` with a
      non-empty ``entityName``.
    * Every column has both ``dataType`` and ``sourceColumn``.
    * The Direct Lake shared expression refers to the SQL endpoint by
      GUID (a 36-char id), not by friendly name — Power BI requires
      this for Edit/refresh operations.

    Returns a list of human-readable error strings; empty if the
    definition passes. Caller decides whether to bail or continue.
    """
    errors: list[str] = []
    parts = definition.get("parts") if isinstance(definition, dict) else None
    if not isinstance(parts, list) or not parts:
        return ["Semantic model definition has no parts."]
    bim_part = next(
        (p for p in parts if isinstance(p, dict) and str(p.get("path") or "").endswith("model.bim")),
        None,
    )
    if not bim_part:
        return ["Semantic model definition is missing a model.bim part."]
    bim = _decode_inline_json_part(bim_part)
    if bim is None:
        return ["model.bim part could not be decoded as JSON."]

    compat = bim.get("compatibilityLevel")
    if not isinstance(compat, int) or compat < 1604:
        errors.append(
            f"compatibilityLevel must be >= 1604 for Direct Lake; got {compat!r}."
        )

    model = bim.get("model") if isinstance(bim, dict) else None
    if not isinstance(model, dict):
        return errors + ["model.bim is missing a top-level 'model' object."]

    expression_names: set[str] = set()
    for expr in model.get("expressions") or []:
        if isinstance(expr, dict) and expr.get("name"):
            expression_names.add(str(expr["name"]))
            # Direct Lake: M expression must reference SQL endpoint by GUID.
            raw = expr.get("expression")
            if isinstance(raw, list):
                raw_text = "\n".join(str(line) for line in raw)
            else:
                raw_text = str(raw or "")
            if "Sql.Database" in raw_text:
                # Extract the second argument (endpoint id) and confirm
                # it looks like a UUID. Tolerate whitespace/casing.
                m = re.search(
                    r'Sql\.Database\(\s*"([^"]+)"\s*,\s*"([^"]+)"',
                    raw_text,
                )
                if m:
                    endpoint_arg = m.group(2)
                    looks_like_guid = bool(
                        re.fullmatch(
                            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                            endpoint_arg,
                        )
                    )
                    if not looks_like_guid:
                        errors.append(
                            f"Direct Lake shared expression {expr.get('name')!r} "
                            f"must reference SQL endpoint by GUID; got "
                            f"{endpoint_arg!r}."
                        )

    tables = model.get("tables") or []
    if not isinstance(tables, list) or not tables:
        errors.append("model has no tables defined.")
    for table in tables:
        if not isinstance(table, dict):
            continue
        tname = table.get("name") or "<unnamed>"
        for column in table.get("columns") or []:
            if not isinstance(column, dict):
                continue
            cname = column.get("name") or "<unnamed>"
            if not column.get("dataType"):
                errors.append(f"Column {tname}.{cname} is missing dataType.")
            if not column.get("sourceColumn"):
                errors.append(f"Column {tname}.{cname} is missing sourceColumn.")
        partitions = table.get("partitions") or []
        if not partitions:
            errors.append(f"Table {tname!r} has no partitions defined.")
        for part in partitions:
            if not isinstance(part, dict):
                continue
            mode = str(part.get("mode") or "")
            source = part.get("source") if isinstance(part, dict) else None
            if mode == "directLake":
                if not isinstance(source, dict):
                    errors.append(
                        f"Direct Lake partition on table {tname!r} has no source object."
                    )
                    continue
                if str(source.get("type")) != "entity":
                    errors.append(
                        f"Direct Lake partition on table {tname!r} must have "
                        f"source.type='entity'; got {source.get('type')!r}."
                    )
                if not source.get("entityName"):
                    errors.append(
                        f"Direct Lake partition on table {tname!r} is missing "
                        f"source.entityName."
                    )
                expr_src = source.get("expressionSource")
                if not expr_src:
                    errors.append(
                        f"Direct Lake partition on table {tname!r} is missing "
                        f"source.expressionSource."
                    )
                elif expr_src not in expression_names:
                    errors.append(
                        f"Direct Lake partition on table {tname!r} references "
                        f"undefined shared expression {expr_src!r}. "
                        f"Defined expressions: {sorted(expression_names)}."
                    )
    return errors


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


def _inventory_semantic_columns() -> list[dict]:
    return [
        {"name": "WorkspaceName", "dataType": "string", "sourceColumn": "WorkspaceName", "summarizeBy": "none", "description": "Display name of the Fabric workspace that contains the item."},
        {"name": "WorkspaceId", "dataType": "string", "sourceColumn": "WorkspaceId", "summarizeBy": "none", "description": "Workspace GUID; used for uniqueness and drill-through links."},
        {"name": "ItemName", "dataType": "string", "sourceColumn": "ItemName", "summarizeBy": "none", "isDefaultLabel": True, "description": "Display name of the Fabric item."},
        {"name": "ItemType", "dataType": "string", "sourceColumn": "ItemType", "summarizeBy": "none", "description": "Fabric item type used for portfolio grouping."},
        {"name": "ItemId", "dataType": "string", "sourceColumn": "ItemId", "summarizeBy": "none", "description": "Fabric item GUID."},
        {"name": "FolderId", "dataType": "string", "sourceColumn": "FolderId", "summarizeBy": "none", "description": "Folder GUID when the item is organized in a Fabric folder."},
        {"name": "WebUrl", "dataType": "string", "sourceColumn": "WebUrl", "summarizeBy": "none", "description": "Portal URL for opening the item."},
    ]


def _inventory_semantic_measures() -> list[dict]:
    return [
        {"name": "Item Count", "expression": "COUNTROWS('FabricItems')", "formatString": "#,##0", "description": "Total accessible Fabric items in the inventory snapshot."},
        {"name": "Workspace Count", "expression": "DISTINCTCOUNT('FabricItems'[WorkspaceId])", "formatString": "#,##0", "description": "Distinct workspaces represented in the inventory snapshot."},
        {"name": "Item Type Count", "expression": "DISTINCTCOUNT('FabricItems'[ItemType])", "formatString": "#,##0", "description": "Distinct Fabric item types represented in the inventory snapshot."},
        {"name": "Report Count", "expression": "CALCULATE(COUNTROWS('FabricItems'), 'FabricItems'[ItemType] = \"Report\")", "formatString": "#,##0", "description": "Accessible Power BI report items."},
        {"name": "Notebook Count", "expression": "CALCULATE(COUNTROWS('FabricItems'), 'FabricItems'[ItemType] = \"Notebook\")", "formatString": "#,##0", "description": "Accessible Fabric notebook items."},
        {"name": "Semantic Model Count", "expression": "CALCULATE(COUNTROWS('FabricItems'), 'FabricItems'[ItemType] = \"SemanticModel\")", "formatString": "#,##0", "description": "Accessible Power BI semantic model items."},
        {"name": "Portfolio Overview", "expression": "\"Fabric Portfolio Inventory\"", "description": "Visible executive report heading for the inventory report."},
        {"name": "Reader Path", "expression": "\"3-30-300: KPIs -> filters -> item evidence\"", "description": "Visible reader-path summary for report consumers."},
        {"name": "Source Method", "expression": "\"Source: Fabric REST -> Lakehouse -> Direct Lake\"", "description": "Visible source and methodology note for professional report review."},
    ]


def _inventory_summary_columns() -> list[dict]:
    return [
        {"name": "ItemType", "dataType": "string", "sourceColumn": "ItemType", "summarizeBy": "none", "description": "Fabric item type."},
        {"name": "ItemCount", "dataType": "int64", "sourceColumn": "ItemCount", "summarizeBy": "sum", "description": "Pre-aggregated item count by type."},
    ]


def _semantic_model_definition_directlake(
    *,
    table_name: str,
    summary_table_name: str | None,
    sql_endpoint_connection_string: str,
    sql_endpoint_id: str,
    schema_name: str = "dbo",
) -> dict:
    """Build a Direct Lake semantic model bound to a Lakehouse SQL endpoint.

    Direct Lake is the preferred Power BI storage mode when a Fabric
    Lakehouse Delta table already holds the data:
      * No data duplication (the model reads the parquet on demand).
      * No scheduled refresh required (rows track Delta writes).
      * Native VertiPaq query performance.

    The TMSL shape mirrors what Microsoft's `semantic-link-labs` library
    (`directlake._generate_shared_expression`) produces:
      * A shared M expression named ``DatabaseQuery`` that calls
        ``Sql.Database(<sqlEPCS>, <sqlepid>)``.
      * One ``mode="directLake"`` partition per table whose ``source`` is
        a ``type="entity"`` reference pointing at the Delta table by name
        (``entityName``) and schema (``schemaName``), with
        ``expressionSource="DatabaseQuery"``.
    """
    m_expression_lines = [
        "let",
        f'    database = Sql.Database("{sql_endpoint_connection_string}", "{sql_endpoint_id}")',
        "in",
        "    database",
    ]

    def _direct_lake_table(table_internal_name: str, source_entity_name: str, columns: list[dict], measures: list[dict] | None = None) -> dict:
        return {
            "name": table_internal_name,
            "columns": columns,
            "measures": measures or [],
            "partitions": [
                {
                    "name": table_internal_name,
                    "mode": "directLake",
                    "source": {
                        "type": "entity",
                        "entityName": source_entity_name,
                        "schemaName": schema_name,
                        "expressionSource": "DatabaseQuery",
                    },
                }
            ],
        }

    inventory_columns = _inventory_semantic_columns()
    inventory_measures = _inventory_semantic_measures()
    tables = [_direct_lake_table("FabricItems", table_name, inventory_columns, inventory_measures)]

    if summary_table_name:
        summary_columns = _inventory_summary_columns()
        tables.append(_direct_lake_table("FabricItemsByType", summary_table_name, summary_columns))

    model_bim = {
        "compatibilityLevel": 1604,
        "model": {
            "culture": "en-US",
            "sourceQueryCulture": "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "discourageImplicitMeasures": True,
            "expressions": [
                {
                    "name": "DatabaseQuery",
                    "kind": "m",
                    "expression": m_expression_lines,
                }
            ],
            "tables": tables,
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


def _semantic_model_definition(items: list[dict]) -> dict:
    """Legacy DATATABLE-bound inventory model.

    Kept as a fallback for the case where no Lakehouse-backed Delta
    table exists (e.g. workspace ended up with a Warehouse fallback
    instead of a Lakehouse, or notebook execution did not write the
    Delta tables). Prefer
    :func:`_semantic_model_definition_directlake` whenever the
    Lakehouse SQL endpoint is reachable so the model reads live Delta
    data instead of an inlined snapshot of ``items``.
    """
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
                    "columns": _inventory_semantic_columns(),
                    "measures": _inventory_semantic_measures(),
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
from dataclasses import dataclass
from typing import Any

import requests
from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import StructField, StructType, StringType


@dataclass(frozen=True)
class InventoryConfig:
    workspace_id: str
    lakehouse_id: str
    fabric_api_base: str
    table_name: str
    summary_table_name: str
    request_timeout_seconds: int = 60

    def require_lakehouse_id(self) -> None:
        if not self.lakehouse_id:
            raise RuntimeError("Lakehouse ID is required; refusing to publish an inventory solution without persisted Delta tables.")


CONFIG = InventoryConfig(
    workspace_id="{workspace_id}",
    lakehouse_id="{lakehouse_id}",
    fabric_api_base="https://api.fabric.microsoft.com/v1",
    table_name="{table_name}",
    summary_table_name="{summary_table_name}",
)


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
CONFIG.require_lakehouse_id()


class FabricApiClient:
    def __init__(self, bearer_token: str, timeout_seconds: int) -> None:
        self.headers = {{"Authorization": f"Bearer {{bearer_token}}", "Content-Type": "application/json"}}
        self.timeout_seconds = timeout_seconds

    def get_all_values(self, url: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        next_url: str | None = url
        while next_url:
            try:
                response = requests.get(next_url, headers=self.headers, timeout=self.timeout_seconds)
                response.raise_for_status()
                data = response.json() if response.text else {{}}
            except requests.RequestException as exc:
                raise RuntimeError(f"Fabric API request failed for {{next_url}}: {{exc}}") from exc
            except ValueError as exc:
                raise RuntimeError(f"Fabric API returned non-JSON response for {{next_url}}") from exc
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


class InventoryBuilder:
    def __init__(self, config: InventoryConfig, client: FabricApiClient) -> None:
        self.config = config
        self.client = client

    def list_workspaces(self) -> list[dict[str, Any]]:
        workspaces = self.client.get_all_values(f"{{self.config.fabric_api_base}}/workspaces")
        if not any(workspace.get("id") == self.config.workspace_id for workspace in workspaces):
            workspaces.insert(0, {{"id": self.config.workspace_id, "displayName": self.config.workspace_id}})
        return workspaces

    def collect_rows(self) -> tuple[list[dict[str, str]], list[str], int]:
        workspaces = self.list_workspaces()
        rows: list[dict[str, str]] = []
        warnings: list[str] = []
        for workspace in workspaces:
            workspace_id = str(workspace.get("id") or "")
            if not workspace_id:
                continue
            workspace_name = str(workspace.get("displayName") or workspace.get("name") or workspace_id)
            try:
                items = self.client.get_all_values(f"{{self.config.fabric_api_base}}/workspaces/{{workspace_id}}/items")
            except RuntimeError as exc:
                if workspace_id == self.config.workspace_id:
                    raise RuntimeError(f"Cannot inventory selected workspace {{workspace_name}} ({{workspace_id}}): {{exc}}") from exc
                warnings.append(f"Skipped workspace {{workspace_name}} ({{workspace_id}}): {{exc}}")
                continue
            for item in items:
                item_id = str(item.get("id") or "")
                item_type = str(item.get("type") or "Item")
                rows.append({{
                    "WorkspaceName": workspace_name,
                    "WorkspaceId": workspace_id,
                    "ItemName": str(item.get("displayName") or item.get("name") or ""),
                    "ItemType": item_type,
                    "ItemId": item_id,
                    "FolderId": str(item.get("folderId") or ""),
                    "WebUrl": f"https://app.powerbi.com/groups/{{workspace_id}}/{{item_type.lower()}}s/{{item_id}}" if item_id else "",
                }})
        if not rows:
            raise RuntimeError("No Fabric items were collected; refusing to publish empty inventory Delta tables.")
        return rows, warnings, len(workspaces)


class DeltaInventoryWriter:
    def __init__(self, spark_session) -> None:
        self.spark = spark_session

    def schema_enabled(self) -> bool:
        try:
            return bool(self.spark.catalog.databaseExists("dbo"))
        except Exception:
            try:
                return any(str(getattr(db, "name", "")).lower() == "dbo" for db in self.spark.catalog.listDatabases())
            except Exception:
                return False

    def save_table(self, df: DataFrame, target_name: str) -> str:
        qualified_name = f"dbo.{{target_name}}" if self.schema_enabled() else target_name
        df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(qualified_name)
        return qualified_name

schema = StructType([
    StructField("WorkspaceName", StringType(), True),
    StructField("WorkspaceId", StringType(), True),
    StructField("ItemName", StringType(), True),
    StructField("ItemType", StringType(), True),
    StructField("ItemId", StringType(), True),
    StructField("FolderId", StringType(), True),
    StructField("WebUrl", StringType(), True),
])

def build_inventory_frames(raw_rows: list[dict[str, str]]) -> tuple[DataFrame, DataFrame]:
    inventory = spark.createDataFrame(raw_rows, schema=schema)
    if inventory.count() <= 0:
        raise RuntimeError("Inventory DataFrame is empty after collection; refusing to save empty Delta tables.")
    summary = inventory.groupBy("ItemType").agg(F.count("*").alias("ItemCount")).orderBy(F.desc("ItemCount"), F.asc("ItemType"))
    return inventory, summary


api_client = FabricApiClient(token, CONFIG.request_timeout_seconds)
builder = InventoryBuilder(CONFIG, api_client)
rows, warnings, workspace_count = builder.collect_rows()
inventory_df, summary_df = build_inventory_frames(rows)
writer = DeltaInventoryWriter(spark)
inventory_table_target = writer.save_table(inventory_df, CONFIG.table_name)
summary_table_target = writer.save_table(summary_df, CONFIG.summary_table_name)

result = {{
    "status": "completed",
    "tableName": inventory_table_target,
    "summaryTableName": summary_table_target,
    "rowCount": inventory_df.count(),
    "workspaceCount": workspace_count,
    "warningCount": len(warnings),
    "warnings": warnings[:20],
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
        # gets a targeted scrub. The biggest cause of "Loading your
        # report..." stuck states from cloned reports is dangling refs
        # to themes or custom visuals we dropped from StaticResources.
        # Decode and strip those references when present, then re-emit.
        if path == "definition/report.json":
            decoded = _decode_part_json(part)
            if isinstance(decoded, dict):
                # publicCustomVisuals points at custom-visual packages
                # under StaticResources/. We dropped those, so the
                # renderer waits forever for them. Same for
                # resourcePackages (themes, registered resources).
                if "publicCustomVisuals" in decoded:
                    decoded["publicCustomVisuals"] = []
                if "resourcePackages" in decoded:
                    decoded["resourcePackages"] = []
                # themeCollection.baseTheme refers to a SharedResources
                # entry that no longer exists; drop it so the renderer
                # falls back to the default theme instead of waiting.
                if isinstance(decoded.get("themeCollection"), dict):
                    decoded["themeCollection"] = {}
                out.append(_inline_json_part(path, decoded))
                continue
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


def _report_definition(semantic_model_id: str, *, style_profile: dict | None = None) -> dict:
    """Build a deterministic, polished PBIR-Legacy inventory report.

    This is intentionally PBIR-Legacy (single ``report.json`` file) and
    uses the EXACT XMLA-style connection that
    `sempy_labs.report.create_report_from_reportjson` uses:

    * ``definition.pbir.version = "1.0"`` (NOT 4.0)
    * ``byConnection.pbiServiceModelId = None``
    * ``byConnection.pbiModelVirtualServerName = "sobe_wowvirtualserver"``
    * ``byConnection.pbiModelDatabaseName = <semantic_model_id>``
    * ``byConnection.connectionType = "pbiServiceXmlaStyleLive"``
    * ``byConnection.name = "EntityDataSource"``

    Why PBIR-Legacy: the new PBIR (folder) format requires a careful
    mix of schema versions and resource packages. Multiple iterations
    of the folder format left the report stuck on
    "Loading your report..." in the embedded iframe. PBIR-Legacy is
    what `sempy_labs` (Microsoft's `semantic-link-labs`) uses to
    successfully create reports every day, including its own BPA
    report. By matching that proven shape exactly we get the same
    rendering behaviour.

    The report.json itself is the legacy single-file layout containing
    one executive overview page designed around the Power BI 3-30-300
    pattern: top-left KPI overview, filter/zoom charts and slicers,
    then details-on-demand bound to reusable semantic-model measures.
    """
    # The legacy report.json has its inner ``config`` and per-visual
    # ``config`` fields stored as JSON strings (doubly-encoded). The
    # outer keys are documented at:
    # https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/report-definition
    style_profile = style_profile or _modern_report_style_profile()
    page_id = "InventoryOverview"
    visual_id = "ItemCountCard"

    source_alias = "fi"

    def literal_expr(value: object) -> dict:
        if isinstance(value, bool):
            raw = "true" if value else "false"
        elif isinstance(value, int):
            raw = f"{value}L"
        elif isinstance(value, float):
            raw = f"{value}D"
        else:
            text = str(value).replace("'", "''")
            raw = f"'{text}'"
        return {"expr": {"Literal": {"Value": raw}}}

    def color_expr(value: str) -> dict:
        return {"solid": {"color": literal_expr(value)}}

    def measure_select(measure_name: str) -> dict:
        query_ref = f"Sum(FabricItems.{measure_name})"
        return {
            "Measure": {
                "Expression": {"SourceRef": {"Source": source_alias}},
                "Property": measure_name,
            },
            "Name": query_ref,
            "NativeReferenceName": measure_name,
        }

    def column_select(column_name: str, display_name: str | None = None) -> dict:
        query_ref = f"FabricItems.{column_name}"
        return {
            "Column": {
                "Expression": {"SourceRef": {"Source": source_alias}},
                "Property": column_name,
            },
            "Name": query_ref,
            "NativeReferenceName": display_name or column_name,
        }

    def visual_container(
        *,
        name: str,
        visual_type: str,
        x: float,
        y: float,
        width: float,
        height: float,
        tab_order: int,
        projections: dict,
        selects: list[dict],
        title: str | None = None,
        alt_text: str | None = None,
        surface_color: str | None = None,
        border_color: str | None = None,
        title_font_size: int = 11,
        value_font_size: int | None = None,
        category_font_size: int = 10,
    ) -> dict:
        surface_color = surface_color or style_profile["surfaceColor"]
        border_color = border_color or style_profile["borderColor"]
        visual_objects: dict[str, list[dict]] = {
            "general": [
                {
                    "properties": {
                        "altText": literal_expr(
                            alt_text
                            or title
                            or name
                        ),
                    }
                }
            ],
            "background": [
                {
                    "properties": {
                        "show": literal_expr(True),
                        "color": color_expr(surface_color),
                        "transparency": literal_expr(0),
                    }
                }
            ],
            "border": [
                {
                    "properties": {
                        "show": literal_expr(True),
                        "color": color_expr(border_color),
                        "transparency": literal_expr(0),
                    }
                }
            ],
        }
        visual_container_objects: dict[str, list[dict]] = {
            "background": [
                {
                    "properties": {
                        "show": literal_expr(True),
                        "color": color_expr(surface_color),
                        "transparency": literal_expr(0),
                    }
                }
            ],
            "border": [
                {
                    "properties": {
                        "show": literal_expr(True),
                        "color": color_expr(border_color),
                        "transparency": literal_expr(0),
                    }
                }
            ],
            "visualHeader": [{"properties": {"show": literal_expr(False)}}],
        }
        if title:
            title_object = [
                {
                    "properties": {
                        "show": literal_expr(True),
                        "text": literal_expr(title),
                        "fontColor": color_expr(style_profile["titleColor"]),
                        "fontSize": literal_expr(title_font_size),
                    }
                }
            ]
            visual_objects["title"] = title_object
            visual_container_objects["title"] = title_object
        if visual_type == "card":
            visible_value_font_size = value_font_size or 30
            visual_objects["labels"] = [{"properties": {"show": literal_expr(True), "color": color_expr(style_profile["titleColor"]), "fontSize": literal_expr(visible_value_font_size)}}]
            visual_objects["categoryLabels"] = [{"properties": {"show": literal_expr(True), "color": color_expr(style_profile["mutedTextColor"]), "fontSize": literal_expr(category_font_size)}}]
            visual_objects["calloutValue"] = [{"properties": {"color": color_expr(style_profile["titleColor"]), "fontSize": literal_expr(visible_value_font_size)}}]
        elif "Chart" in visual_type:
            visual_objects["dataPoint"] = [{"properties": {"defaultColor": color_expr(style_profile["accentColor"])}}]
            visual_objects["categoryAxis"] = [{"properties": {"show": literal_expr(True), "labelColor": color_expr(style_profile["mutedTextColor"]), "fontSize": literal_expr(9)}}]
            visual_objects["valueAxis"] = [{"properties": {"show": literal_expr(True), "labelColor": color_expr(style_profile["mutedTextColor"]), "fontSize": literal_expr(9)}}]
        visual_config = {
            "name": name,
            "layouts": [
                {
                    "id": 0,
                    "position": {
                        "x": x,
                        "y": y,
                        "z": tab_order,
                        "width": width,
                        "height": height,
                        "tabOrder": tab_order,
                    },
                }
            ],
            "objects": visual_container_objects,
            "visualContainerObjects": visual_container_objects,
            "singleVisual": {
                "visualType": visual_type,
                "projections": projections,
                "prototypeQuery": {
                    "Version": 2,
                    "From": [{"Name": source_alias, "Entity": "FabricItems", "Type": 0}],
                    "Select": selects,
                },
                "drillFilterOtherVisuals": True,
                "objects": visual_objects,
            },
        }
        return {
            "x": x,
            "y": y,
            "z": tab_order,
            "width": width,
            "height": height,
            "config": json.dumps(visual_config, separators=(",", ":")),
            "filters": "[]",
        }

    item_count_measure = measure_select("Item Count")
    workspace_count_measure = measure_select("Workspace Count")
    item_type_count_measure = measure_select("Item Type Count")
    report_count_measure = measure_select("Report Count")
    notebook_count_measure = measure_select("Notebook Count")
    semantic_model_count_measure = measure_select("Semantic Model Count")
    portfolio_heading_measure = measure_select("Portfolio Overview")
    reader_path_summary_measure = measure_select("Reader Path")
    source_transparency_note_measure = measure_select("Source Method")
    item_type_column = column_select("ItemType", "Item Type")
    workspace_column = column_select("WorkspaceName", "Workspace")
    item_name_column = column_select("ItemName", "Item")
    item_id_column = column_select("ItemId", "Item ID")

    visual_containers = [
        visual_container(
            name="PortfolioHeadlineCard",
            visual_type="card",
            x=40.0,
            y=24.0,
            width=660.0,
            height=76.0,
            tab_order=0,
            projections={"Values": [{"queryRef": "Sum(FabricItems.Portfolio Overview)", "active": True}]},
            selects=[portfolio_heading_measure],
            title="Portfolio at a Glance",
            alt_text="3-second top-left overview headline for the Fabric portfolio inventory report, designed as the first reader stop.",
            surface_color="#ECFEFF",
            border_color="#14B8A6",
            title_font_size=13,
            value_font_size=24,
            category_font_size=11,
        ),
        visual_container(
            name="ReaderPathSummaryCard",
            visual_type="card",
            x=720.0,
            y=24.0,
            width=470.0,
            height=76.0,
            tab_order=1,
            projections={"Values": [{"queryRef": "Sum(FabricItems.Reader Path)", "active": True}]},
            selects=[reader_path_summary_measure],
            title="3-30-300 Reader Path",
            alt_text="Visible methodology and source inventory summary: KPI strip, filter-and-zoom analysis, details on demand, and source transparency.",
            surface_color="#FFF7ED",
            border_color="#FDBA74",
            title_font_size=12,
            value_font_size=15,
            category_font_size=10,
        ),
        visual_container(
            name=visual_id,
            visual_type="card",
            x=40.0,
            y=122.0,
            width=160.0,
            height=102.0,
            tab_order=2,
            projections={"Values": [{"queryRef": "Sum(FabricItems.Item Count)", "active": True}]},
            selects=[item_count_measure],
            title="Total Items",
            alt_text="3-second top-left overview KPI sourced from live Fabric workspace inventory and showing total accessible item count.",
            surface_color="#FFFFFF",
            border_color="#99F6E4",
        ),
        visual_container(
            name="WorkspaceCountCard",
            visual_type="card",
            x=220.0,
            y=122.0,
            width=160.0,
            height=102.0,
            tab_order=3,
            projections={"Values": [{"queryRef": "Sum(FabricItems.Workspace Count)", "active": True}]},
            selects=[workspace_count_measure],
            title="Workspaces",
            alt_text="Executive overview KPI showing the number of accessible workspaces included in the inventory source.",
        ),
        visual_container(
            name="ItemTypeCountCard",
            visual_type="card",
            x=400.0,
            y=122.0,
            width=160.0,
            height=102.0,
            tab_order=4,
            projections={"Values": [{"queryRef": "Sum(FabricItems.Item Type Count)", "active": True}]},
            selects=[item_type_count_measure],
            title="Item Types",
            alt_text="Overview KPI showing breadth of Fabric item types found.",
        ),
        visual_container(
            name="ReportCountCard",
            visual_type="card",
            x=580.0,
            y=122.0,
            width=160.0,
            height=102.0,
            tab_order=5,
            projections={"Values": [{"queryRef": "Sum(FabricItems.Report Count)", "active": True}]},
            selects=[report_count_measure],
            title="Reports",
            alt_text="Overview KPI showing report count for immediate governance signal.",
        ),
        visual_container(
            name="SourceTransparencyCard",
            visual_type="card",
            x=760.0,
            y=122.0,
            width=430.0,
            height=102.0,
            tab_order=6,
            projections={"Values": [{"queryRef": "Sum(FabricItems.Source Method)", "active": True}]},
            selects=[source_transparency_note_measure],
            title="Methodology",
            alt_text="Visible methodology/source transparency card documenting the Fabric REST inventory, Lakehouse Delta persistence, and Direct Lake semantic model path.",
            surface_color="#F8FAFC",
            border_color="#CBD5E1",
            value_font_size=16,
            category_font_size=10,
        ),
        visual_container(
            name="ItemTypeSlicer",
            visual_type="slicer",
            x=970.0,
            y=256.0,
            width=240.0,
            height=108.0,
            tab_order=7,
            projections={"Values": [{"queryRef": "FabricItems.ItemType", "active": True}]},
            selects=[item_type_column],
            title="Item Type Focus",
            alt_text="30-second filter-and-zoom slicer for exploring Fabric item types after reading the KPI overview; use it to navigate the inventory scenario.",
        ),
        visual_container(
            name="WorkspaceSlicer",
            visual_type="slicer",
            x=970.0,
            y=384.0,
            width=240.0,
            height=108.0,
            tab_order=8,
            projections={"Values": [{"queryRef": "FabricItems.WorkspaceName", "active": True}]},
            selects=[workspace_column],
            title="Workspace Focus",
            alt_text="Interactive slicer for zooming into a workspace and cross-filtering charts, cards, and detail rows.",
        ),
        visual_container(
            name="ItemsByWorkspaceColumn",
            visual_type="clusteredColumnChart",
            x=40.0,
            y=256.0,
            width=590.0,
            height=245.0,
            tab_order=9,
            projections={
                "Category": [{"queryRef": "FabricItems.WorkspaceName", "active": True}],
                "Y": [{"queryRef": "Sum(FabricItems.Item Count)", "active": True}],
            },
            selects=[workspace_column, item_count_measure],
            title="Workspace Footprint",
            alt_text="30-second exploration column chart comparing workspace inventory size to highlight where governance or cleanup attention may be needed.",
            surface_color="#FFFFFF",
            border_color="#BFDBFE",
        ),
        visual_container(
            name="ItemsByTypeBar",
            visual_type="clusteredBarChart",
            x=650.0,
            y=256.0,
            width=300.0,
            height=245.0,
            tab_order=10,
            projections={
                "Category": [{"queryRef": "FabricItems.ItemType", "active": True}],
                "Y": [{"queryRef": "Sum(FabricItems.Item Count)", "active": True}],
            },
            selects=[item_type_column, item_count_measure],
            title="Item Mix by Type",
            alt_text="30-second exploration bar chart ranking item types so the reader can identify categories that deserve follow-up.",
            surface_color="#FFFFFF",
            border_color="#DDD6FE",
        ),
        visual_container(
            name="CoreAssetMixCard",
            visual_type="multiRowCard",
            x=970.0,
            y=526.0,
            width=240.0,
            height=150.0,
            tab_order=11,
            projections={
                "Values": [
                    {"queryRef": "Sum(FabricItems.Report Count)", "active": True},
                    {"queryRef": "Sum(FabricItems.Notebook Count)", "active": True},
                    {"queryRef": "Sum(FabricItems.Semantic Model Count)", "active": True},
                ]
            },
            selects=[report_count_measure, notebook_count_measure, semantic_model_count_measure],
            title="Core Asset Mix",
            alt_text="Custom reader card comparing reports, notebooks, and semantic models; methodology uses live source inventory counts and enhanced tooltips.",
            surface_color="#FDF2F8",
            border_color="#F9A8D4",
        ),
        visual_container(
            name="WorkspaceInventoryTable",
            visual_type="tableEx",
            x=40.0,
            y=526.0,
            width=910.0,
            height=150.0,
            tab_order=12,
            projections={
                "Values": [
                    {"queryRef": "FabricItems.WorkspaceName", "active": True},
                    {"queryRef": "FabricItems.ItemType", "active": True},
                    {"queryRef": "FabricItems.ItemName", "active": True},
                    {"queryRef": "FabricItems.ItemId", "active": True},
                    {"queryRef": "Sum(FabricItems.Item Count)", "active": True},
                ]
            },
            selects=[workspace_column, item_type_column, item_name_column, item_id_column, item_count_measure],
            title="Details on Demand",
            alt_text="300-second details-on-demand table that responds to filters and provides item-level workspace, type, name, id, and data-dictionary context.",
            surface_color="#FFFFFF",
            border_color="#99F6E4",
        ),
    ]

    page_background_objects = {
        "background": [
            {
                "properties": {
                    "color": color_expr(style_profile["canvasColor"]),
                    "transparency": literal_expr(0),
                }
            }
        ],
        "outspace": [
            {
                "properties": {
                    "color": color_expr(style_profile["canvasColor"]),
                    "transparency": literal_expr(0),
                }
            }
        ],
    }

    page_config_inner = {
        "name": page_id,
        "displayName": "Fabric Inventory Championship Overview",
        "objects": page_background_objects,
    }

    layout_section = {
        "id": 0,
        "name": page_id,
        "displayName": "Fabric Inventory Championship Overview",
        "displayOption": 1,  # 1 = FitToPage
        "height": 720.0,
        "width": 1280.0,
        "ordinal": 0,
        "visualContainers": visual_containers,
        "objects": page_background_objects,
        "config": json.dumps(page_config_inner, separators=(",", ":")),
        "filters": "[]",
    }

    report_config_inner = {
        "version": "5.43",
        "themeCollection": {"customTheme": style_profile["theme"]},
        "activeSectionIndex": 0,
        "defaultDrillFilterOtherVisuals": True,
        "settings": {
            "useStylableVisualContainerHeader": True,
            "exportDataMode": 1,
            "useNewFilterPaneExperience": True,
            "allowChangeFilterTypes": True,
            "useEnhancedTooltips": True,
        },
        "objects": {
            "background": [
                {
                    "properties": {
                        "color": color_expr(style_profile["canvasColor"]),
                        "transparency": literal_expr(0),
                    }
                }
            ]
        },
    }

    report_legacy = {
        "config": json.dumps(report_config_inner, separators=(",", ":")),
        "layoutOptimization": 0,
        "publicCustomVisuals": [],
        "resourcePackages": [],
        "sections": [layout_section],
    }

    # The exact connection block that sempy_labs uses in
    # `create_report_from_reportjson`. Power BI's renderer recognises
    # this XMLA-style live connection and binds it to the semantic
    # model identified by ``pbiModelDatabaseName``.
    definition_pbir = {
        "version": "1.0",
        "datasetReference": {
            "byPath": None,
            "byConnection": {
                "connectionString": None,
                "pbiServiceModelId": None,
                "pbiModelVirtualServerName": "sobe_wowvirtualserver",
                "pbiModelDatabaseName": semantic_model_id,
                "name": "EntityDataSource",
                "connectionType": "pbiServiceXmlaStyleLive",
            },
        },
    }

    return {
        "format": "PBIR-Legacy",
        "parts": [
            _inline_json_part("report.json", report_legacy),
            _inline_json_part("definition.pbir", definition_pbir),
        ],
    }


def _definition_part_json(definition: dict, path_name: str) -> dict | None:
    for part in definition.get("parts") or []:
        if isinstance(part, dict) and part.get("path") == path_name:
            return _decode_inline_json_part(part)
    return None


def _report_definition_inventory_quality(report_definition: dict) -> dict:
    report_json = _definition_part_json(report_definition, "report.json") or {}
    sections = report_json.get("sections") if isinstance(report_json, dict) else []
    visual_types: list[str] = []
    visual_positions: list[dict] = []
    visual_records: list[dict] = []
    visual_titles: list[str] = []
    visual_alt_texts: list[str] = []
    binding_text = ""
    try:
        report_config = json.loads(str(report_json.get("config") or "{}")) if isinstance(report_json, dict) else {}
    except Exception:
        report_config = {}
    def _literal_value_text(value: object) -> str:
        if not isinstance(value, dict):
            return ""
        raw = (((value.get("expr") or {}).get("Literal") or {}).get("Value"))
        if not isinstance(raw, str):
            return ""
        text = raw.strip()
        if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
            text = text[1:-1].replace("''", "'")
        return text.strip()

    def _visual_object_property(objects: object, object_name: str, property_name: str) -> str:
        if not isinstance(objects, dict):
            return ""
        candidates = objects.get(object_name)
        if not isinstance(candidates, list):
            return ""
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            properties = candidate.get("properties")
            if not isinstance(properties, dict):
                continue
            text = _literal_value_text(properties.get(property_name))
            if text:
                return text
        return ""

    for section in sections or []:
        if not isinstance(section, dict):
            continue
        for container in section.get("visualContainers") or []:
            if not isinstance(container, dict):
                continue
            visual_positions.append({
                "x": float(container.get("x") or 0),
                "y": float(container.get("y") or 0),
                "width": float(container.get("width") or 0),
                "height": float(container.get("height") or 0),
            })
            try:
                visual_config = json.loads(str(container.get("config") or "{}"))
            except Exception:
                visual_config = {}
            single_visual = visual_config.get("singleVisual") if isinstance(visual_config, dict) else None
            if not isinstance(single_visual, dict):
                continue
            visual_type = str(single_visual.get("visualType") or "")
            if visual_type:
                visual_types.append(visual_type)
            objects = single_visual.get("objects")
            title_text = _visual_object_property(objects, "title", "text")
            alt_text = _visual_object_property(objects, "general", "altText")
            if title_text:
                visual_titles.append(title_text)
            if alt_text:
                visual_alt_texts.append(alt_text)
            layouts = visual_config.get("layouts")
            position = layouts[0].get("position", {}) if isinstance(layouts, list) and layouts else {}
            visual_records.append({
                "name": str(visual_config.get("name") or ""),
                "visualType": visual_type,
                "x": float(container.get("x") or 0),
                "y": float(container.get("y") or 0),
                "width": float(container.get("width") or 0),
                "height": float(container.get("height") or 0),
                "tabOrder": int(position.get("tabOrder") if isinstance(position, dict) and position.get("tabOrder") is not None else len(visual_records)),
                "title": title_text,
                "altText": alt_text,
            })
            binding_text += json.dumps(single_visual, sort_keys=True)

    def _overlaps(left: dict, right: dict) -> bool:
        left_x2 = left["x"] + left["width"]
        left_y2 = left["y"] + left["height"]
        right_x2 = right["x"] + right["width"]
        right_y2 = right["y"] + right["height"]
        return left["x"] < right_x2 and left_x2 > right["x"] and left["y"] < right_y2 and left_y2 > right["y"]

    overlap_count = sum(
        1
        for index, left in enumerate(visual_positions)
        for right in visual_positions[index + 1:]
        if _overlaps(left, right)
    )
    lowered_visual_types = [visual_type.lower() for visual_type in visual_types]
    chart_count = sum(1 for visual_type in lowered_visual_types if "chart" in visual_type)
    slicer_count = sum(1 for visual_type in lowered_visual_types if visual_type == "slicer")
    card_count = sum(1 for visual_type in lowered_visual_types if "card" in visual_type)
    table_count = sum(1 for visual_type in lowered_visual_types if visual_type in {"table", "tableex", "matrix"})
    settings = report_config.get("settings") if isinstance(report_config, dict) else {}
    has_modern_reader_experience = bool(
        isinstance(settings, dict)
        and settings.get("useNewFilterPaneExperience") is True
        and settings.get("useEnhancedTooltips") is True
        and settings.get("useStylableVisualContainerHeader") is True
    )
    theme_collection = report_config.get("themeCollection") if isinstance(report_config, dict) else {}
    custom_theme = theme_collection.get("customTheme") if isinstance(theme_collection, dict) else {}
    palette_raw = custom_theme.get("dataColors") if isinstance(custom_theme, dict) else []
    palette = palette_raw if isinstance(palette_raw, list) else []
    palette_families = {
        str(color).strip().upper()[:3]
        for color in palette
        if isinstance(color, str) and color.startswith("#") and len(color) >= 4
    }
    visual_style_text = json.dumps(custom_theme.get("visualStyles") or {}, sort_keys=True) if isinstance(custom_theme, dict) else ""
    has_modern_theme = (
        isinstance(custom_theme, dict)
        and any(token in str(custom_theme.get("name") or "").lower() for token in ("modern", "championship"))
        and isinstance(palette, list)
        and len(palette) >= 5
        and len(palette_families) >= 4
    )
    has_visual_style_defaults = all(
        token in visual_style_text
        for token in ("background", "border", "title")
    )
    card_records = [record for record in visual_records if "card" in str(record.get("visualType") or "").lower()]
    slicer_records = [record for record in visual_records if str(record.get("visualType") or "").lower() == "slicer"]
    chart_records = [record for record in visual_records if "chart" in str(record.get("visualType") or "").lower()]
    table_records = [record for record in visual_records if str(record.get("visualType") or "").lower() in {"table", "tableex", "matrix"}]
    has_visible_narrative_header = any(
        record.get("name") == "PortfolioHeadlineCard"
        and float(record.get("x") or 0) <= 60
        and float(record.get("y") or 0) <= 40
        and float(record.get("width") or 0) >= 600
        and float(record.get("height") or 0) >= 60
        for record in card_records
    )
    top_left_kpis = [
        record for record in card_records
        if float(record.get("x") or 0) <= 760 and float(record.get("y") or 0) <= 140
    ]
    slicers_avoid_prime_overview = all(
        not (float(record.get("x") or 0) < 320 and float(record.get("y") or 0) < 220)
        for record in slicer_records
    )
    has_3_second_overview = (
        len(top_left_kpis) >= 4
        and has_visible_narrative_header
        and any(record.get("name") == "ItemCountCard" and int(record.get("tabOrder") or 99) <= 3 for record in top_left_kpis)
        and slicers_avoid_prime_overview
    )
    has_30_second_filter_zoom = (
        len(chart_records) >= 2
        and any(float(record.get("x") or 0) >= 900 and float(record.get("y") or 0) <= 340 for record in slicer_records)
        and all(180 <= float(record.get("y") or 0) <= 500 for record in chart_records[:2])
    )
    has_300_second_details_on_demand = any(
        float(record.get("y") or 0) >= 480 and float(record.get("width") or 0) >= 850
        for record in table_records
    )
    has_prominent_analysis_zones = (
        any(float(record.get("width") or 0) >= 520 and float(record.get("height") or 0) >= 220 for record in chart_records)
        and any(float(record.get("width") or 0) >= 850 and float(record.get("height") or 0) >= 140 for record in table_records)
        and any(float(record.get("x") or 0) >= 900 and 220 <= float(record.get("y") or 0) <= 420 for record in slicer_records)
    )
    tab_order_values = [int(record.get("tabOrder") or 0) for record in visual_records]
    has_guided_tab_order = sorted(tab_order_values) == list(range(len(tab_order_values)))
    has_accessibility_metadata = len(visual_alt_texts) == len(visual_records) and len(visual_titles) == len(visual_records)
    title_story_text = " ".join([*visual_titles, *visual_alt_texts, *(record.get("name") or "" for record in visual_records)]).lower()
    binding_text_lower = binding_text.lower()
    has_reader_path_summary = (
        ("Reader Path" in binding_text or "Reader Path Summary" in binding_text)
        and ("3-30-300 reader path" in title_story_text or "3-30-300" in title_story_text)
    )
    has_visible_source_transparency = (
        ("Source Method" in binding_text or "Source Transparency Note" in binding_text)
        and "methodology" in title_story_text
        and ("source" in title_story_text or "fabric rest" in binding_text_lower)
    )
    design_rubric = (
        "power_bi_championship_3_30_300"
        if all(token in title_story_text for token in ("3-second", "30-second", "300-second"))
        else ""
    )
    has_championship_story_flow = (
        has_3_second_overview
        and has_30_second_filter_zoom
        and has_300_second_details_on_demand
        and has_guided_tab_order
    )
    has_restrained_visual_density = 11 <= len(visual_records) <= 14
    has_championship_theme = (
        "championship" in str(custom_theme.get("name") or "").lower()
        and "3_30_300" in design_rubric
        and has_visible_narrative_header
    )
    has_high_contrast_canvas = (
        str(custom_theme.get("background") or "").upper() == "#F8FAFC"
        and str(custom_theme.get("foreground") or "").upper() == "#111827"
    )
    has_information_hierarchy = (
        has_3_second_overview
        and has_30_second_filter_zoom
        and has_300_second_details_on_demand
        and has_visible_narrative_header
        and has_prominent_analysis_zones
        and overlap_count == 0
        and any("portfolio" in title.lower() or "overview" in title.lower() for title in visual_titles)
    )
    has_usability_interactions = (
        slicer_count >= 2
        and has_30_second_filter_zoom
        and bool(isinstance(settings, dict) and settings.get("useEnhancedTooltips") is True)
        and bool(isinstance(report_config, dict) and report_config.get("defaultDrillFilterOtherVisuals") is True)
    )
    has_scenario_navigation = (
        "portfolio" in title_story_text
        and "workspace" in title_story_text
        and "focus" in title_story_text
        and "details on demand" in title_story_text
    )
    has_methodology_transparency = (
        "source inventory" in title_story_text
        or has_visible_source_transparency
        or ("methodology" in title_story_text and "data-dictionary" in title_story_text)
        or ("methodology" in title_story_text and "data dictionary" in title_story_text)
    )
    has_custom_cards_and_tooltips = (
        card_count >= 4
        and "multirowcard" in lowered_visual_types
        and bool(isinstance(settings, dict) and settings.get("useEnhancedTooltips") is True)
    )
    checks = [
        {"name": "report_has_overview_page", "passed": bool(sections)},
        {"name": "report_has_multiple_visuals", "passed": len(visual_types) >= 8, "value": len(visual_types)},
        {"name": "report_has_interactive_slicers", "passed": slicer_count >= 2, "value": slicer_count},
        {"name": "report_has_kpi_summary", "passed": card_count >= 4, "value": card_count},
        {"name": "report_has_distribution_charts", "passed": chart_count >= 2, "value": visual_types},
        {"name": "report_has_detail_table", "passed": table_count >= 1, "value": visual_types},
        {"name": "report_follows_3_30_300_story_flow", "passed": has_championship_story_flow},
        {"name": "report_has_top_left_kpi_overview", "passed": has_3_second_overview, "value": top_left_kpis},
        {"name": "report_slicers_do_not_occupy_prime_overview", "passed": slicers_avoid_prime_overview, "value": slicer_records},
        {"name": "report_has_filter_zoom_zone", "passed": has_30_second_filter_zoom},
        {"name": "report_has_details_on_demand_zone", "passed": has_300_second_details_on_demand},
        {"name": "report_has_visible_narrative_header", "passed": has_visible_narrative_header},
        {"name": "report_has_visible_reader_path_summary", "passed": has_reader_path_summary},
        {"name": "report_has_visible_source_transparency", "passed": has_visible_source_transparency},
        {"name": "report_has_prominent_analysis_zones", "passed": has_prominent_analysis_zones},
        {"name": "report_has_clear_information_hierarchy", "passed": has_information_hierarchy},
        {"name": "report_has_usability_interactions", "passed": has_usability_interactions},
        {"name": "report_has_scenario_navigation", "passed": has_scenario_navigation},
        {"name": "report_has_methodology_transparency", "passed": has_methodology_transparency},
        {"name": "report_has_custom_cards_and_tooltips", "passed": has_custom_cards_and_tooltips},
        {"name": "report_has_accessibility_alt_text_and_titles", "passed": has_accessibility_metadata, "value": {"titleCount": len(visual_titles), "altTextCount": len(visual_alt_texts)}},
        {"name": "report_has_guided_keyboard_tab_order", "passed": has_guided_tab_order, "value": tab_order_values},
        {"name": "report_uses_restrained_visual_density", "passed": has_restrained_visual_density, "value": len(visual_records)},
        {"name": "report_has_championship_design_rubric", "passed": has_championship_theme, "value": design_rubric},
        {"name": "report_has_high_contrast_canvas", "passed": has_high_contrast_canvas},
        {"name": "report_has_modern_reader_experience", "passed": has_modern_reader_experience},
        {"name": "report_has_modern_theme", "passed": has_modern_theme, "value": custom_theme.get("name") if isinstance(custom_theme, dict) else None},
        {"name": "report_has_multi_hue_palette", "passed": len(palette_families) >= 4, "value": palette},
        {"name": "report_has_visual_style_defaults", "passed": has_visual_style_defaults},
        {"name": "report_layout_has_no_overlaps", "passed": overlap_count == 0, "value": overlap_count},
        {"name": "report_binds_inventory_table", "passed": "FabricItems" in binding_text},
        {"name": "report_binds_item_type", "passed": "ItemType" in binding_text},
        {"name": "report_binds_workspace_filter", "passed": "WorkspaceName" in binding_text},
        {"name": "report_binds_item_count", "passed": "Item Count" in binding_text},
        {"name": "report_binds_visible_narrative_measures", "passed": "Portfolio Overview" in binding_text and "Reader Path" in binding_text and "Source Method" in binding_text},
    ]
    return {
        "status": "passed" if all(check["passed"] for check in checks) else "failed",
        "pageCount": len(sections or []),
        "visualCount": len(visual_types),
        "visualTypes": visual_types,
        "designStandard": "Power BI championship / Data Stories style, 3-30-300 information flow",
        "readerScenario": "Fabric portfolio governance inventory",
        "slicerCount": slicer_count,
        "chartCount": chart_count,
        "cardCount": card_count,
        "storyFlow3_30_300": has_championship_story_flow,
        "visibleNarrativeHeader": has_visible_narrative_header,
        "visibleReaderPathSummary": has_reader_path_summary,
        "visibleSourceTransparency": has_visible_source_transparency,
        "prominentAnalysisZones": has_prominent_analysis_zones,
        "informationHierarchy": has_information_hierarchy,
        "usabilityInteractions": has_usability_interactions,
        "scenarioNavigation": has_scenario_navigation,
        "methodologyTransparency": has_methodology_transparency,
        "customCardsAndTooltips": has_custom_cards_and_tooltips,
        "accessibilityMetadata": has_accessibility_metadata,
        "guidedTabOrder": has_guided_tab_order,
        "restrainedVisualDensity": has_restrained_visual_density,
        "highContrastCanvas": has_high_contrast_canvas,
        "modernReaderExperience": has_modern_reader_experience,
        "themeName": custom_theme.get("name") if isinstance(custom_theme, dict) else None,
        "designRubric": design_rubric,
        "palette": palette,
        "visualStyleDefaults": has_visual_style_defaults,
        "overlapCount": overlap_count,
        "checks": checks,
    }


def _semantic_model_inventory_quality(model_definition: dict, *, model_storage_mode: str) -> dict:
    model_bim = _definition_part_json(model_definition, "model.bim") or {}
    model = model_bim.get("model") if isinstance(model_bim, dict) else {}
    tables = model.get("tables") if isinstance(model, dict) else []
    fabric_items = next(
        (table for table in tables or [] if isinstance(table, dict) and table.get("name") == "FabricItems"),
        {},
    )
    measure_names = [str(measure.get("name")) for measure in fabric_items.get("measures") or [] if isinstance(measure, dict)]
    column_names = [str(column.get("name")) for column in fabric_items.get("columns") or [] if isinstance(column, dict)]
    checks = [
        {"name": "model_uses_explicit_measures", "passed": bool(model.get("discourageImplicitMeasures"))},
        {"name": "model_has_reusable_measures", "passed": len(measure_names) >= 4, "value": measure_names},
        {"name": "model_has_workspace_dimension", "passed": "WorkspaceName" in column_names and "WorkspaceId" in column_names},
        {"name": "model_has_item_type_dimension", "passed": "ItemType" in column_names},
        {"name": "model_prefers_persisted_data", "passed": model_storage_mode == "DirectLake", "value": model_storage_mode},
    ]
    return {
        "status": "passed" if all(check["passed"] for check in checks) else "failed",
        "measureNames": measure_names,
        "columnNames": column_names,
        "storageMode": model_storage_mode,
        "checks": checks,
    }


def _notebook_source_from_definition(notebook_definition: dict | None) -> str:
    if not isinstance(notebook_definition, dict):
        return ""
    for part in notebook_definition.get("parts") or []:
        if not isinstance(part, dict) or part.get("path") != "notebook-content.ipynb":
            continue
        notebook = _decode_inline_json_part(part) or {}
        cells = notebook.get("cells") if isinstance(notebook, dict) else []
        chunks: list[str] = []
        for cell in cells or []:
            if not isinstance(cell, dict) or cell.get("cell_type") != "code":
                continue
            source = cell.get("source") or []
            if isinstance(source, list):
                chunks.extend(str(line) for line in source)
            elif isinstance(source, str):
                chunks.append(source)
        return "".join(chunks)
    return ""


def _notebook_code_inventory_quality(notebook_definition: dict | None) -> dict:
    source = _notebook_source_from_definition(notebook_definition)
    class_count = len(re.findall(r"^\s*class\s+\w+", source, flags=re.MULTILINE))
    function_count = len(re.findall(r"^\s*def\s+\w+", source, flags=re.MULTILINE))
    checks = [
        {"name": "code_uses_config_class", "passed": "class InventoryConfig" in source},
        {"name": "code_uses_api_client_class", "passed": "class FabricApiClient" in source},
        {"name": "code_uses_writer_class", "passed": "class DeltaInventoryWriter" in source},
        {"name": "code_has_clear_function_boundaries", "passed": function_count >= 4, "value": function_count},
        {"name": "code_raises_explicit_runtime_errors", "passed": "raise RuntimeError" in source},
        {"name": "code_preserves_exception_cause", "passed": " from exc" in source},
        {"name": "code_tracks_partial_workspace_warnings", "passed": "warningCount" in source and "warnings" in source},
        {"name": "code_fails_without_lakehouse", "passed": "Lakehouse ID is required" in source},
        {"name": "code_fails_on_empty_inventory", "passed": "No Fabric items were collected" in source and "Inventory DataFrame is empty" in source},
        {"name": "code_uses_request_timeouts", "passed": "timeout=self.timeout_seconds" in source},
        {"name": "code_does_not_silently_skip_delta_writes", "passed": "skipping Delta table writes" not in source},
    ]
    return {
        "status": "passed" if source and all(check["passed"] for check in checks) else "failed",
        "classCount": class_count,
        "functionCount": function_count,
        "raisesRuntimeErrors": "raise RuntimeError" in source,
        "tracksWarnings": "warningCount" in source and "warnings" in source,
        "failsOnEmptyInventory": "No Fabric items were collected" in source and "Inventory DataFrame is empty" in source,
        "failsWithoutLakehouse": "Lakehouse ID is required" in source,
        "usesRequestTimeouts": "timeout=self.timeout_seconds" in source,
        "checks": checks,
    }


def _inventory_solution_quality_validation(
    *,
    model_definition: dict,
    report_definition: dict,
    notebook_definition: dict | None = None,
    model_storage_mode: str,
    persistent_data_written: bool,
) -> dict:
    model_quality = _semantic_model_inventory_quality(model_definition, model_storage_mode=model_storage_mode)
    report_quality = _report_definition_inventory_quality(report_definition)
    notebook_code_quality = _notebook_code_inventory_quality(notebook_definition)
    checks = [
        {"name": "data_persisted_to_lakehouse", "passed": persistent_data_written},
        {"name": "semantic_model_quality", "passed": model_quality["status"] == "passed", "detail": model_quality},
        {"name": "report_design_quality", "passed": report_quality["status"] == "passed", "detail": report_quality},
        {"name": "notebook_code_quality", "passed": notebook_code_quality["status"] == "passed", "detail": notebook_code_quality},
    ]
    return {
        "status": "passed" if all(check["passed"] for check in checks) else "failed",
        "checks": checks,
        "semanticModel": model_quality,
        "report": report_quality,
        "notebookCode": notebook_code_quality,
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


async def _post_fabric_json(
    client,
    url: str,
    body: dict,
    op_name: str,
    *,
    fetch_lro_result: bool = True,
    require_delegated: bool = False,
) -> dict | str:
    headers = _fabric_headers(require_delegated=require_delegated, operation=op_name)
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
    workspace_name = workspace.get("displayName") or workspace.get("name")
    if not capacity_id:
        return {
            "status": "unknown",
            "capacityId": None,
            "capacityName": None,
            "workspaceName": workspace_name,
            "warning": "Workspace metadata did not expose a capacity assignment; continuing and relying on artifact/render validation.",
        }

    capacities_resp = await client.get(f"{FABRIC_API_BASE}/capacities", headers=headers)
    if capacities_resp.status_code != 200:
        return {
            "status": "unknown",
            "capacityId": str(capacity_id),
            "capacityName": None,
            "workspaceName": workspace_name,
            "warning": "Workspace has a capacity assignment, but the current user could not list Fabric capacities to verify its state.",
            "verificationError": format_http_error(capacities_resp, "checking Fabric capacity state"),
        }
    capacities = _safe_json(capacities_resp).get("value", [])
    capacity = next((item for item in capacities if str(item.get("id")) == str(capacity_id)), None)
    if not capacity:
        return {
            "status": "unknown",
            "capacityId": str(capacity_id),
            "capacityName": None,
            "workspaceName": workspace_name,
            "warning": f"Workspace capacity {capacity_id} was not visible to the current user; continuing and relying on artifact/render validation.",
        }

    state = str(capacity.get("state") or capacity.get("status") or "").strip()
    if not state:
        return {
            "status": "unknown",
            "capacityId": str(capacity_id),
            "capacityName": capacity.get("displayName") or capacity.get("name"),
            "workspaceName": workspace_name,
            "warning": f"Workspace capacity {capacity_id} did not report a state; continuing and relying on artifact/render validation.",
        }
    if state.lower() != "active":
        name = capacity.get("displayName") or capacity.get("name") or capacity_id
        return f"Workspace capacity {name} ({capacity_id}) is {state or 'unknown'}, not Active; resume the capacity before creating report artifacts."

    return {
        "status": "active",
        "capacityId": str(capacity_id),
        "capacityName": capacity.get("displayName") or capacity.get("name"),
        "workspaceName": workspace_name,
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


async def _list_powerbi_report_pages(client, url: str, headers: dict, token_name: str) -> dict | str:
    """Return the report's `/pages` listing.

    A non-empty pages list is the strongest cheap signal that the
    PBIR-Legacy definition is well-formed *and* that the bound semantic
    model resolved successfully — Power BI rejects pages requests for
    reports whose dataset binding is broken.
    """
    for attempt in range(8):
        resp = await client.get(f"{url}/pages", headers=headers)
        if resp.status_code == 200:
            return _safe_json(resp)
        if resp.status_code in (401, 403):
            return format_http_error(resp, f"listing report pages with {token_name}")
        if resp.status_code in (404, 409, 429, 500, 502, 503, 504) and attempt < 7:
            delay = min(max(int(resp.headers.get("Retry-After") or "5"), 1), 15)
            await asyncio.sleep(delay)
            continue
        return format_http_error(resp, f"listing report pages with {token_name}")
    return f"Timed out listing report pages with {token_name}."


async def _verify_report_renderable(
    client,
    workspace_id: str,
    report_id: str,
    semantic_model_id: str,
) -> dict | str:
    """Validate a freshly-created Power BI report actually renders.

    Layered strategy (lightest → heaviest) because Power BI ExportTo is
    a Premium/Fabric capacity-gated operation that returns 202 with no
    export id on Pro/free SKUs — the previous "ExportTo or fail" path
    deleted perfectly working reports whenever the workspace happened
    to land on a non-Premium capacity.

    1. **Metadata** — `GET /groups/{ws}/reports/{id}` confirms the
       report exists and is bound to the expected semantic model.
    2. **Pages**    — `GET /pages` returns ``[{name, displayName, ...}]``
       only when the PBIR definition parses *and* the dataset binding
       resolves. A non-empty pages list is the strongest "this report
       will render in the user's browser" signal that does not require
       Premium capacity.
    3. **ExportTo (optional)** — best-effort PDF export. Success is
       reported as ``via=powerbi_exportTo_pdf``; failures, missing
       export ids, and timeouts are demoted to ``capacity_limited``
       and the verification passes on the strength of step 2 instead.
    """
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

        pages_outcome = await _list_powerbi_report_pages(client, report_url, headers, token_name)
        if isinstance(pages_outcome, str):
            failures.append(pages_outcome)
            continue
        pages = pages_outcome.get("value") if isinstance(pages_outcome, dict) else None
        page_count = len(pages) if isinstance(pages, list) else 0
        if page_count == 0:
            failures.append(
                "Power BI report has no pages — the PBIR definition was rejected "
                "or the bound semantic model failed to resolve."
            )
            continue
        first_page = pages[0] if isinstance(pages, list) and pages else {}

        # Step 3: best-effort ExportTo. Treat capacity-limited 202s and
        # missing export ids as a soft-warn rather than a hard failure.
        export_outcome: dict | None = None
        export_warning: str | None = None
        export = await _start_powerbi_report_export(client, report_url, headers, token_name)
        if isinstance(export, str):
            export_warning = export
        elif str(export.get("status") or "").lower() == "succeeded":
            export_outcome = {
                "via": "powerbi_exportTo_pdf",
                "exportId": export.get("id"),
            }
        else:
            export_id = export.get("id")
            if not export_id:
                export_warning = (
                    "Power BI ExportTo accepted the request but did not return an "
                    "export id — workspace capacity likely does not allow PDF export."
                )
            else:
                poll_url = f"{report_url}/exports/{export_id}"
                delay = min(max(int(export.get("retryAfter") or "5"), 1), 15)
                for _ in range(36):
                    await asyncio.sleep(delay)
                    state_resp = await client.get(poll_url, headers=headers)
                    if state_resp.status_code == 429:
                        delay = min(max(int(state_resp.headers.get("Retry-After") or str(delay)), 1), 30)
                        continue
                    if state_resp.status_code not in (200, 202):
                        export_warning = format_http_error(state_resp, f"polling report render export with {token_name}")
                        break
                    if state_resp.headers.get("Retry-After"):
                        delay = min(max(int(state_resp.headers.get("Retry-After") or str(delay)), 1), 30)
                    state = _safe_json(state_resp)
                    status = str(state.get("status") or "").lower()
                    if status == "succeeded":
                        export_outcome = {
                            "via": "powerbi_exportTo_pdf",
                            "exportId": export_id,
                        }
                        break
                    if status in ("failed", "cancelled", "canceled"):
                        export_warning = "Power BI report render export ended with status=" + status
                        break
                else:
                    export_warning = f"Power BI report render export {export_id} did not finish before the polling budget elapsed."

        result: dict = {
            "status": "rendered",
            "via": export_outcome["via"] if export_outcome else "powerbi_pages_metadata",
            "token": token_name,
            "reportId": report_id,
            "semanticModelId": semantic_model_id,
            "pageCount": page_count,
            "firstPage": {
                "name": first_page.get("name"),
                "displayName": first_page.get("displayName"),
            } if isinstance(first_page, dict) else None,
        }
        if export_outcome:
            result["exportId"] = export_outcome.get("exportId")
        if export_warning:
            result["exportWarning"] = export_warning
            result["exportStatus"] = "capacity_limited"
        return result

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
        require_delegated=True,
    )


# Per-item-type updateDefinition routes. The generic
# /items/{id}/updateDefinition endpoint is preview-only and not
# enabled in every tenant, so we use the typed routes which are GA.
_ITEM_UPDATE_DEFINITION_ROUTES = {
    "semanticmodel": "semanticModels",
    "report": "reports",
    "notebook": "notebooks",
    "datapipeline": "dataPipelines",
    "dataflow": "dataflows",
}


async def _update_item_definition(
    client,
    workspace_id: str,
    item_id: str,
    item_type: str,
    definition: dict,
    op_name: str,
) -> dict | str:
    """Push a new definition into an existing Fabric item.

    Used by ``_create_or_reuse_inventory_item`` when an item with the
    target name already exists — this lets us recover from a partial
    or broken previous run by overwriting the definition rather than
    deleting and recreating (which would invalidate downstream report
    bindings and lose item history).
    """
    route = _ITEM_UPDATE_DEFINITION_ROUTES.get(item_type.lower())
    if not route:
        return f"updateDefinition not supported for item type {item_type!r}"
    return await _post_fabric_json(
        client,
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{route}/{item_id}/updateDefinition",
        {"definition": definition},
        op_name,
        fetch_lro_result=False,
        require_delegated=True,
    )


def _job_status(job: dict) -> str:
    return str(job.get("status") or "").lower()


def _job_is_terminal(job: dict) -> bool:
    return _job_status(job) in {"completed", "failed", "cancelled", "canceled", "deduped"}


def _as_positive_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _notebook_exit_payload(job: dict | None) -> dict:
    if not isinstance(job, dict):
        return {}
    raw = job.get("exitValue")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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


async def _wait_for_powerbi_refresh_completion(
    client,
    workspace_id: str,
    semantic_model_id: str,
    *,
    max_attempts: int = 30,
    poll_interval_seconds: int = 6,
) -> dict:
    """Poll Power BI's refreshes endpoint until the latest refresh finishes.

    Without this, ``_refresh_semantic_model`` returns as soon as a 202
    is accepted, hiding refresh failures like ``0xC14700C7`` ("We cannot
    access the source Delta table ..."). The verifier and the user only
    discover the failure later when the report renders empty.

    Returns a dict with keys:
      - ``status``: ``"Completed"`` | ``"Failed"`` | ``"Disabled"`` |
        ``"Unknown"`` (timed out while still in progress) | ``"NoHistory"``
      - ``errorMessage``: human-readable error from
        ``serviceExceptionJson`` when status is ``"Failed"``
      - ``raw``: the underlying refresh history entry for diagnostics
    """
    try:
        header_candidates = _powerbi_header_candidates()
    except RuntimeError as exc:
        return {"status": "Unknown", "errorMessage": str(exc), "raw": None}
    url = (
        f"{POWERBI_API_BASE}/groups/{workspace_id}"
        f"/datasets/{semantic_model_id}/refreshes?$top=1"
    )
    last_entry: dict | None = None
    last_status_code: int | None = None
    for attempt in range(max_attempts):
        resp = None
        for _, headers in header_candidates:
            resp = await client.get(url, headers=headers)
            last_status_code = resp.status_code
            if resp.status_code not in (401, 403):
                break
        if resp is None:
            break
        if resp.status_code == 200:
            try:
                body = resp.json()
            except Exception:
                body = {}
            entries = (body or {}).get("value") or []
            if entries and isinstance(entries[0], dict):
                last_entry = entries[0]
                status = str(last_entry.get("status") or "")
                if status and status != "Unknown":
                    error_message = ""
                    if status == "Failed":
                        raw_err = last_entry.get("serviceExceptionJson") or ""
                        if isinstance(raw_err, str) and raw_err:
                            try:
                                parsed = json.loads(raw_err)
                                error_message = (
                                    parsed.get("errorDescription")
                                    or parsed.get("errorMessage")
                                    or raw_err
                                )
                            except Exception:
                                error_message = raw_err
                        else:
                            error_message = (
                                "Refresh failed but no serviceExceptionJson was provided."
                            )
                    return {
                        "status": status,
                        "errorMessage": error_message,
                        "raw": last_entry,
                    }
            elif not entries:
                last_entry = None
        elif resp.status_code in (401, 403):
            return {
                "status": "Unknown",
                "errorMessage": format_http_error(resp, "polling refresh history"),
                "raw": None,
            }
        await asyncio.sleep(poll_interval_seconds)
    if last_entry is None:
        return {
            "status": "NoHistory",
            "errorMessage": (
                f"Refresh status endpoint never returned a refresh entry "
                f"(last HTTP {last_status_code})."
            ),
            "raw": None,
        }
    return {
        "status": "Unknown",
        "errorMessage": (
            f"Refresh did not finish within "
            f"{max_attempts * poll_interval_seconds}s."
        ),
        "raw": last_entry,
    }


async def _validate_semantic_model_structure(
    client,
    workspace_id: str,
    semantic_model_id: str,
    expected_table_names: set[str],
) -> dict | str:
    """Verify the deployed semantic model contains the expected tables.

    Uses the Power BI ``executeQueries`` endpoint with the DAX info
    function ``INFO.TABLES()`` (a.k.a. ``$SYSTEM.TMSCHEMA_TABLES``) to
    enumerate the tables the model itself knows about, independently
    of the Lakehouse SQL endpoint catalog. This catches deploy-time
    silent rewrites where Fabric accepts a TMSL definition but ends
    up with no tables (or different names than we sent).

    The previous implementation called ``GET /datasets/{id}/tables``
    which is the Push API endpoint and returns 404 for non-Push
    datasets ("Dataset is not Push API dataset"). DAX
    ``EVALUATE INFO.TABLES()`` is the modern, universal way to read
    a model's table list.
    """
    try:
        header_candidates = _powerbi_header_candidates()
    except RuntimeError as exc:
        return str(exc)
    url = (
        f"{POWERBI_API_BASE}/groups/{workspace_id}"
        f"/datasets/{semantic_model_id}/executeQueries"
    )
    body = {
        "queries": [{"query": "EVALUATE SELECTCOLUMNS(INFO.TABLES(), \"Name\", [Name])"}],
        "serializerSettings": {"includeNulls": True},
    }
    last_error = ""
    for attempt in range(8):
        last_resp = None
        for _, headers in header_candidates:
            resp = await client.post(url, json=body, headers=headers)
            last_resp = resp
            if resp.status_code not in (401, 403):
                break
        if last_resp is None:
            return "Power BI executeQueries endpoint was not attempted."
        if last_resp.status_code == 200:
            try:
                payload = last_resp.json()
            except Exception as exc:
                return f"Power BI executeQueries response was not JSON: {exc}"
            results = (payload or {}).get("results") or []
            rows: list[dict] = []
            if results and isinstance(results[0], dict):
                tables = results[0].get("tables") or []
                if tables and isinstance(tables[0], dict):
                    rows = tables[0].get("rows") or []
            observed_names = sorted({
                str(_extract_powerbi_scalar(r, "Name") or "")
                for r in rows
                if isinstance(r, dict)
            })
            observed_names = [n for n in observed_names if n]
            missing = sorted(expected_table_names.difference(observed_names))
            if not missing:
                return {
                    "status": "structurally_valid",
                    "via": "powerbi_executeQueries_INFO_TABLES",
                    "observedTables": observed_names,
                    "expectedTables": sorted(expected_table_names),
                    "attempts": attempt + 1,
                }
            last_error = (
                f"Power BI semantic model is missing expected table(s) {missing}; "
                f"observed only {observed_names}. The TMSL definition was accepted but "
                "Fabric did not materialise the table — usually a malformed Direct Lake "
                "partition or DataSource block."
            )
            return last_error
        if last_resp.status_code in (401, 403):
            return format_http_error(last_resp, "running INFO.TABLES() against semantic model")
        if last_resp.status_code in (404, 409, 429, 500, 502, 503, 504):
            last_error = format_http_error(last_resp, "running INFO.TABLES() against semantic model")
            await asyncio.sleep(5 if attempt < 4 else 10)
            continue
        return format_http_error(last_resp, "running INFO.TABLES() against semantic model")
    return f"Timed out enumerating semantic model tables. Last error: {last_error}"


async def _wait_for_inventory_model_data(
    client,
    workspace_id: str,
    semantic_model_id: str,
    *,
    expected_min_rows: int = 1,
    sql_endpoint_id: str | None = None,
    fabric_headers: dict | None = None,
) -> dict | str:
    """Hard-validate the semantic model is queryable end-to-end.

    DirectLake-bound models depend on the Lakehouse SQL endpoint
    catalog knowing about the freshly-written Delta tables. Spark
    notebook writes land on OneLake immediately but the SQL endpoint
    catalog can lag for several minutes — sometimes longer — before
    DAX queries against the bound table succeed. Without the bound
    table being queryable, any report we create on top of the model
    will render an empty/error visual in the user's browser, so we
    must not declare success early.

    Strategy:
      * Up to 18 attempts (~10 minutes) of the row-count DAX query.
      * Between attempts we re-trigger
        ``POST /sqlEndpoints/{id}/refreshMetadata`` (when an endpoint
        id is provided) to force the catalog to re-scan OneLake.
      * On success we also fetch item types for richer downstream
        context.
      * On failure we return an actionable error — the report build
        path will skip report creation and the verifier will mark
        the mission as failed.
    """
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

    async def _execute_dax(query: str) -> tuple[object | None, str, str]:
        last_resp = None
        last_token = ""
        for token_name, headers in header_candidates:
            resp = await client.post(
                url,
                json={"queries": [{"query": query}], "serializerSettings": {"includeNulls": True}},
                headers=headers,
            )
            last_resp = resp
            last_token = token_name
            if resp.status_code not in (401, 403):
                break
        if last_resp is None:
            return None, "", "Power BI executeQueries was not attempted."
        if last_resp.status_code == 200:
            try:
                return last_resp.json(), last_token, ""
            except Exception as exc:
                return None, last_token, f"Power BI executeQueries returned an unparseable response: {exc}"
        return None, last_token, f"{format_http_error(last_resp, 'querying semantic model')} using {last_token}"

    last_error = ""
    last_refresh_at = 0  # forced re-refresh interval bookkeeping
    # Up to 18 attempts: 6×10s + 12×30s ≈ 6 minutes catalog patience.
    # The proactive refresh kicks in every 60 seconds.
    for attempt in range(18):
        # Periodically force the SQL endpoint to re-scan OneLake so
        # the catalog catches up with the recent Spark write.
        now = attempt
        if (
            sql_endpoint_id
            and fabric_headers
            and (attempt == 0 or attempt - last_refresh_at >= 2)
        ):
            refresh_outcome = await _refresh_sql_endpoint_metadata(
                client, workspace_id, sql_endpoint_id, fabric_headers,
            )
            last_refresh_at = now
            if isinstance(refresh_outcome, str):
                last_error = (
                    last_error or refresh_outcome
                )

        count_body, count_token, count_err = await _execute_dax(count_query)
        if count_body is None:
            last_error = count_err
            if "401" in count_err or "403" in count_err:
                return count_err
        else:
            try:
                tables = (count_body.get("results") or [{}])[0].get("tables") or []
                rows = tables[0].get("rows") if tables else []
                row_count = int(_extract_powerbi_scalar(rows[0], "ItemCount") or 0) if rows else 0
            except Exception as exc:
                return f"Power BI executeQueries returned an unexpected inventory count shape: {exc}"
            if row_count >= expected_min_rows:
                types_body, _, types_err = await _execute_dax(types_query)
                item_types: list[str] = []
                if types_body is not None:
                    try:
                        type_tables = (types_body.get("results") or [{}])[0].get("tables") or []
                        type_rows = type_tables[0].get("rows") if type_tables else []
                        item_types = [
                            str(_extract_powerbi_scalar(row, "ItemType") or "").strip()
                            for row in type_rows
                        ]
                        item_types = [it for it in item_types if it]
                    except Exception:
                        pass
                return {
                    "status": "queryable",
                    "via": "powerbi_executeQueries",
                    "token": count_token,
                    "rowCount": row_count,
                    "itemTypes": item_types,
                    "attempts": attempt + 1,
                }
            last_error = f"Power BI executeQueries returned {row_count} inventory rows."
        await asyncio.sleep(10 if attempt < 6 else 30)

    return (
        "Power BI semantic model never became queryable for the bound table. "
        "DirectLake catalog sync did not propagate the freshly-written Delta "
        "table within the validation budget (~6 minutes). Last error: "
        + last_error
    )


async def _wait_for_lakehouse_tables(
    client,
    workspace_id: str,
    lakehouse_id: str,
    headers: dict,
    expected_table_names: set[str],
    expected_non_empty_table_names: set[str] | None = None,
) -> dict | str:
    last_error = ""
    expected_non_empty_by_key = {
        name.casefold(): name for name in (expected_non_empty_table_names or set())
    }
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
                    row_count = table.get("rowCount")
                    if row_count is None:
                        row_count = table.get("rowsCount")
                    if name and isinstance(row_count, int):
                        row_counts[name] = row_count
                empty_expected_tables = [
                    observed_by_key[key]
                    for key in sorted(expected_by_key)
                    if row_counts.get(observed_by_key[key]) == 0
                ]
                if empty_expected_tables:
                    last_error = (
                        "Lakehouse Delta table(s) exist but are empty: "
                        f"{empty_expected_tables}. Empty tables are not accepted as persisted inventory data."
                    )
                    await asyncio.sleep(5 if attempt < 6 else 10)
                    continue
                empty_required_tables = [
                    observed_by_key[key]
                    for key in sorted(expected_non_empty_by_key)
                    if key in observed_by_key and row_counts.get(observed_by_key[key]) == 0
                ]
                if empty_required_tables:
                    last_error = (
                        "Lakehouse inventory table(s) exist but have zero rows: "
                        f"{empty_required_tables}. Empty tables are not accepted as persisted inventory data."
                    )
                    await asyncio.sleep(5 if attempt < 6 else 10)
                    continue
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


def _observed_lakehouse_table_name(validation: dict | None, expected_name: str) -> str:
    """Return the exact table name Fabric reports for a case-insensitive match."""
    if not isinstance(validation, dict) or not expected_name:
        return expected_name
    tables = validation.get("tables")
    if not isinstance(tables, list):
        return expected_name
    for table in tables:
        if isinstance(table, str) and table.casefold() == expected_name.casefold():
            return table
    return expected_name


async def _refresh_sql_endpoint_metadata(
    client,
    workspace_id: str,
    sql_endpoint_id: str,
    headers: dict,
) -> dict | str:
    """Force the Lakehouse SQL endpoint to re-scan OneLake for new tables.

    The DirectLake DAX validation step (``_wait_for_inventory_model_data``)
    fails with ``Invalid object name 'dbo.<table>'`` whenever the SQL
    endpoint's table catalog has not yet propagated a Delta table that
    a Spark notebook just wrote — even though the Delta files are on
    OneLake and the lakehouse-tables API can list them. Microsoft's
    ``sempy_labs.lakehouse.refresh_sql_endpoint_metadata`` calls
    ``POST /v1/workspaces/{ws}/sqlEndpoints/{id}/refreshMetadata?preview=true``
    to bypass that propagation lag; we mirror that here. Failures are
    non-fatal because the catalog will eventually catch up on its own;
    we simply lose the early sync benefit.
    """
    url = (
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/sqlEndpoints/"
        f"{sql_endpoint_id}/refreshMetadata?preview=true"
    )
    resp = await client.post(url, headers=headers, json={})
    if resp.status_code in (200, 201):
        return {"status": "refreshed", "via": "fabric_sql_endpoint_refresh"}
    if resp.status_code == 202:
        location = resp.headers.get("Location") or ""
        if not location:
            return {"status": "accepted", "via": "fabric_sql_endpoint_refresh"}
        retry_after = min(max(int(resp.headers.get("Retry-After") or "5"), 1), 10)
        for _ in range(24):
            await asyncio.sleep(retry_after)
            poll = await client.get(location, headers=headers)
            if poll.status_code == 429:
                retry_after = min(max(int(poll.headers.get("Retry-After") or str(retry_after)), 1), 30)
                continue
            if poll.status_code in (200, 201):
                return {"status": "refreshed", "via": "fabric_sql_endpoint_refresh"}
            if poll.status_code != 202:
                return format_http_error(poll, "polling SQL endpoint metadata refresh")
            if poll.headers.get("Retry-After"):
                retry_after = min(max(int(poll.headers.get("Retry-After") or str(retry_after)), 1), 30)
        return "SQL endpoint metadata refresh did not complete within the polling budget."
    return format_http_error(resp, "triggering SQL endpoint metadata refresh")


async def _fetch_lakehouse_sql_endpoint(
    client,
    workspace_id: str,
    lakehouse_id: str,
    headers: dict,
) -> dict | str:
    """Resolve the Lakehouse's SQL endpoint connection string + id.

    Returns ``{"connectionString": ..., "id": ..., "provisioningStatus": ...}``
    on success or a descriptive error string when the lookup fails or
    the SQL endpoint is not yet provisioned.

    Mirrors the call sempy_labs makes in
    `directlake._generate_shared_expression`:
        GET /v1/workspaces/{ws}/lakehouses/{lh}
        body.properties.sqlEndpointProperties.{connectionString, id, provisioningStatus}
    """
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}"
    last_error = ""
    for attempt in range(12):
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            body = resp.json() if resp.text else {}
            sqlprop = (body.get("properties") or {}).get("sqlEndpointProperties") or {}
            cs = sqlprop.get("connectionString")
            sql_id = sqlprop.get("id")
            prov = sqlprop.get("provisioningStatus")
            if cs and sql_id and prov != "InProgress":
                return {
                    "connectionString": cs,
                    "id": sql_id,
                    "provisioningStatus": prov,
                }
            last_error = (
                f"SQL endpoint not ready (provisioningStatus={prov}, "
                f"hasConnectionString={bool(cs)}, hasId={bool(sql_id)})."
            )
        else:
            last_error = format_http_error(resp, "fetching lakehouse SQL endpoint")
            if resp.status_code in (401, 403, 404):
                return last_error
        await asyncio.sleep(5 if attempt < 6 else 10)
    return f"Timed out waiting for Lakehouse SQL endpoint. Last result: {last_error}"


async def _find_workspace_item(
    client,
    workspace_id: str,
    headers: dict,
    *,
    display_name: str,
    item_type: str,
    folder_id: str | None = None,
) -> dict | None:
    matches = await _find_workspace_items(
        client,
        workspace_id,
        headers,
        display_name=display_name,
        item_type=item_type,
        folder_id=folder_id,
    )
    return matches[0] if matches else None


async def _find_workspace_items(
    client,
    workspace_id: str,
    headers: dict,
    *,
    display_name: str,
    item_type: str,
    folder_id: str | None = None,
) -> list[dict]:
    """Return ALL workspace items matching name+type+folder.

    Fabric's REST API has been observed to silently create duplicate
    items (especially semantic models) under iteration retry loops —
    each POST returns 201 with a fresh GUID instead of returning a
    409 "already in use". We therefore have to enumerate the workspace
    and dedupe ourselves rather than trusting the create response.
    """
    items, error = await _fabric_get_all_values(
        client,
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items",
        headers,
    )
    if error:
        return []
    expected_type = item_type.lower()
    matches: list[dict] = []
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
        matches.append(item | {"type": item.get("type", item_type), "folderId": item_folder_id or folder_id})
    # Sort by createdDate (oldest first) so the canonical "kept" item
    # is always the first one Fabric created. Items without a creation
    # date sort last.
    def _sort_key(it: dict) -> tuple[int, str]:
        created = str(it.get("createdDate") or it.get("lastUpdatedDate") or "")
        return (0 if created else 1, created)
    matches.sort(key=_sort_key)
    return matches


async def _delete_workspace_item(
    client,
    workspace_id: str,
    headers: dict,
    item_id: str,
) -> bool:
    """Best-effort delete of a workspace item. Returns True on success."""
    if not item_id:
        return False
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{item_id}"
    try:
        resp = await client.delete(url, headers=headers)
    except Exception:
        return False
    return resp.status_code in (200, 202, 204)


def _principal_name_hint(value) -> str | None:
    if isinstance(value, dict):
        for key in ("displayName", "userPrincipalName", "name", "email", "id"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _item_owner_hint(item: dict | None) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in (
        "owner",
        "ownerName",
        "createdBy",
        "createdByUser",
        "creator",
        "lastModifiedBy",
        "modifiedBy",
    ):
        hint = _principal_name_hint(item.get(key))
        if hint:
            return hint
    return None


def _item_owner_identity_hint(item: dict | None) -> dict | None:
    if not isinstance(item, dict):
        return None
    for key in (
        "owner",
        "ownerName",
        "createdBy",
        "createdByUser",
        "creator",
        "lastModifiedBy",
        "modifiedBy",
    ):
        raw = item.get(key)
        if not raw:
            continue
        if isinstance(raw, dict):
            return {
                "sourceField": key,
                "id": raw.get("id") or raw.get("objectId") or raw.get("principalId"),
                "displayName": raw.get("displayName") or raw.get("name"),
                "userPrincipalName": raw.get("userPrincipalName") or raw.get("upn"),
                "email": raw.get("email") or raw.get("mail"),
                "principalType": raw.get("principalType") or raw.get("type"),
                "raw": raw,
            }
        if isinstance(raw, str) and raw.strip():
            return {"sourceField": key, "displayName": raw.strip()}
    return None


def _token_identity_hint() -> dict:
    token = os.environ.get("FABRIC_API_TOKEN", "")
    if not token:
        return {"token": "missing"}
    return _token_identity_from_claims(_token_claims(token))


def _token_user_owner_hints(identity: dict | None = None) -> set[str]:
    identity = identity or _token_identity_hint()
    if identity.get("authMode") != "delegated_user":
        return set()
    hints: set[str] = set()
    for key in ("name", "preferred_username", "upn", "oid"):
        value = identity.get(key)
        if isinstance(value, str) and value.strip():
            hints.add(value.strip().lower())
    return hints


def _owner_matches_token_user(owner: str | None, identity: dict | None = None) -> bool | None:
    if not owner:
        return None
    hints = _token_user_owner_hints(identity)
    if not hints:
        return None
    return owner.strip().lower() in hints


def _owner_mismatch_block_message(item: dict, operation: str, identity: dict | None = None) -> str | None:
    owner = _item_owner_hint(item)
    owner_match = _owner_matches_token_user(owner, identity)
    if owner_match is not False:
        return None
    item_name = item.get("displayName") or item.get("name") or item.get("id") or "item"
    return (
        f"Blocked {operation}: existing or newly-created item '{item_name}' is owned by '{owner}', "
        "which does not match the delegated mission user token. AgentHub will not reuse or keep "
        "app-owned/mismatched Fabric mission artifacts because Direct Lake models can inherit that "
        "owner/effective identity and lose access to the user's data."
    )


async def _workspace_item_metadata(
    client,
    workspace_id: str,
    headers: dict,
    item_id: str,
) -> dict:
    if not item_id:
        return {}
    try:
        resp = await client.get(
            f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items/{item_id}",
            headers=headers,
        )
    except Exception:
        return {}
    if resp.status_code != 200:
        return {}
    try:
        return resp.json() if resp.text else {}
    except Exception:
        return {}


async def _hydrate_workspace_item(
    client,
    workspace_id: str,
    headers: dict,
    item: dict,
) -> dict:
    item_id = str(item.get("id") or "")
    metadata = await _workspace_item_metadata(client, workspace_id, headers, item_id)
    if metadata:
        merged = {**item, **metadata}
        if item_id:
            merged.update(_build_item_links(workspace_id, item_id, merged.get("type", item.get("type", ""))))
        return merged
    return item


def _same_owner_hint(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.strip().lower() == right.strip().lower()


async def _directlake_identity_diagnostics(
    client,
    workspace_id: str,
    headers: dict,
    *,
    lakehouse_item: dict | None,
    semantic_model_item: dict | None,
    sql_endpoint: dict | None,
) -> dict:
    """Collect owner/effective-identity evidence for Direct Lake models.

    Direct Lake failures often look like table/catalog issues even when the
    real cause is that Power BI frames the model as the semantic model owner,
    and that principal cannot access the Lakehouse Delta table. Fabric's item
    API does not always return owner fields, so this is best-effort evidence;
    when it does find a mismatch we surface it before the long refresh loop.
    """
    lakehouse = lakehouse_item or {}
    model = semantic_model_item or {}
    if lakehouse.get("id"):
        lakehouse = await _hydrate_workspace_item(client, workspace_id, headers, lakehouse)
    if model.get("id"):
        model = await _hydrate_workspace_item(client, workspace_id, headers, model)

    source_owner = _item_owner_hint(lakehouse)
    model_owner = _item_owner_hint(model)
    owner_mismatch = bool(source_owner and model_owner and not _same_owner_hint(source_owner, model_owner))
    message = None
    if owner_mismatch:
        message = (
            "Direct Lake identity risk: the Lakehouse/data owner appears to be "
            f"'{source_owner}', but the SemanticModel owner appears to be '{model_owner}'. "
            "Power BI may frame Direct Lake using the SemanticModel owner/effective identity; "
            "that principal must have access to the Lakehouse Delta tables/SQL endpoint."
        )
    return {
        "status": "owner_mismatch" if owner_mismatch else ("ok" if source_owner and model_owner else "unknown"),
        "ownerMismatch": owner_mismatch,
        "sourceItem": {
            "id": lakehouse.get("id"),
            "displayName": lakehouse.get("displayName") or lakehouse.get("name"),
            "type": lakehouse.get("type") or "Lakehouse",
            "owner": source_owner,
        },
        "semanticModelItem": {
            "id": model.get("id"),
            "displayName": model.get("displayName") or model.get("name"),
            "type": model.get("type") or "SemanticModel",
            "owner": model_owner,
        },
        "sqlEndpoint": {
            "id": (sql_endpoint or {}).get("id"),
            "connectionString": (sql_endpoint or {}).get("connectionString"),
        },
        "apiTokenIdentity": _token_identity_hint(),
        "message": message,
    }


def _directlake_identity_failure_hint(identity_diagnostics: dict | None) -> str:
    if not isinstance(identity_diagnostics, dict) or not identity_diagnostics.get("ownerMismatch"):
        return ""
    message = identity_diagnostics.get("message")
    return " " + str(message or "Direct Lake owner/effective-identity mismatch detected.")


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
    """Create the item OR update an existing one with the same name.

    Strategy (avoids the Fabric duplicate-item bug while never
    discarding the canonical item unnecessarily):

    1. List existing items in the workspace with the same name+type+
       folder. If one exists, reuse its id and ``updateDefinition``
       with the new payload (when a definition was provided). Older
       attempts have shown Fabric's POST ``/items`` endpoint silently
       creating a fresh duplicate every iteration loop; pre-checking
       short-circuits that.
    2. After a fresh create returns 201, re-list and detect any
       duplicates Fabric created in spite of our request. Keep the
       oldest (canonical) item and DELETE the strict duplicates only
       — never the canonical one. Deleting is the only resolution
       because two semantic models can't be merged.
    """
    extra_body = extra_body or {}
    new_definition = extra_body.get("definition")

    pre_existing = await _find_workspace_items(
        client,
        workspace_id,
        headers,
        display_name=display_name,
        item_type=item_type,
        folder_id=folder_id,
    )
    if pre_existing:
        token_identity = _require_delegated_user_token("FABRIC_API_TOKEN", f"{op_name} (reuse existing item)")
        canonical = pre_existing[0]
        if _token_user_owner_hints(token_identity):
            canonical = await _hydrate_workspace_item(client, workspace_id, headers, canonical)
            owner_block = _owner_mismatch_block_message(canonical, f"{op_name} (reuse existing item)", token_identity)
            if owner_block:
                return owner_block
        canonical_id = canonical.get("id") or ""
        # If the caller provided a definition, push it into the
        # already-existing item so retries actually heal broken state.
        if canonical_id and new_definition is not None:
            update_outcome = await _update_item_definition(
                client,
                workspace_id,
                canonical_id,
                item_type,
                new_definition,
                f"{op_name} (updateDefinition on reuse)",
            )
            if isinstance(update_outcome, str):
                # Surface the update error so callers know reuse failed.
                return update_outcome
        # Sweep any silent duplicates from prior runs.
        for dup in pre_existing[1:]:
            dup_id = dup.get("id") or ""
            if dup_id and dup_id != canonical_id:
                await _delete_workspace_item(client, workspace_id, headers, dup_id)
        return canonical

    use_typed_lakehouse_create = item_type.lower() == "lakehouse"
    body = {
        "displayName": display_name,
        "description": description,
        "folderId": folder_id,
    }
    if not use_typed_lakehouse_create:
        body["type"] = item_type
    body.update(extra_body)
    create_url = (
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/lakehouses"
        if use_typed_lakehouse_create
        else f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items"
    )
    result = await _post_fabric_json(
        client,
        create_url,
        body,
        op_name,
        require_delegated=True,
    )
    if isinstance(result, str):
        if (
            "itemdisplaynamealreadyinuse" not in result.lower()
            and "already in use" not in result.lower()
        ):
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

    created_id = result.get("id") or ""
    if created_id:
        result.update(_build_item_links(workspace_id, created_id, result.get("type", item_type)))
        token_identity = _token_identity_hint()
        if _token_user_owner_hints(token_identity):
            hydrated = await _hydrate_workspace_item(client, workspace_id, headers, result)
            owner_block = _owner_mismatch_block_message(hydrated, op_name, token_identity)
            if owner_block:
                await _delete_workspace_item(client, workspace_id, headers, created_id)
                return owner_block
    # Sweep silent duplicates Fabric may have produced in spite of
    # our pre-check — keep the one we just created (or the oldest if
    # ours has gone missing) and delete the rest.
    post_matches = await _find_workspace_items(
        client,
        workspace_id,
        headers,
        display_name=display_name,
        item_type=item_type,
        folder_id=folder_id,
    )
    if len(post_matches) > 1:
        keep_id = created_id or (post_matches[0].get("id") or "")
        for match in post_matches:
            match_id = match.get("id") or ""
            if match_id and match_id != keep_id:
                await _delete_workspace_item(client, workspace_id, headers, match_id)
    return result | {"type": result.get("type", item_type), "folderId": folder_id}


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


def _inventory_run_token(folder_name: str, folder_id: str = "") -> str:
    folder_suffix = folder_name.removeprefix("tmp_")
    compact_suffix = "".join(ch for ch in folder_suffix if ch.isalnum())
    if compact_suffix.isdigit() and len(compact_suffix) >= 6:
        return compact_suffix[-6:]
    if compact_suffix:
        return compact_suffix[:8]
    return "".join(ch for ch in folder_id if ch.isalnum())[:6] or "Run"


def _title_words(value: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+", value or "")
    return [word[:1].upper() + word[1:] for word in words]


def _compact_pascal(value: str) -> str:
    return "".join(_title_words(value)) or "FabricInventory"


def _snake_case(value: str) -> str:
    words = [word.lower() for word in re.findall(r"[A-Za-z0-9]+", value or "")]
    return "_".join(words) or "fabric_inventory"


def _kebab_case(value: str) -> str:
    return _snake_case(value).replace("_", "-")


def _name_style(display_name: str) -> str | None:
    name = (display_name or "").strip()
    if not name:
        return None
    if name.lower() in {
        "report",
        "dashboard",
        "lakehouse",
        "warehouse",
        "notebook",
        "semanticmodel",
        "semantic model",
        "sqlendpoint",
        "sql endpoint",
    }:
        return None
    if "_" in name and " " not in name:
        return "snake_case"
    if "-" in name and " " not in name:
        return "kebab_case"
    if " " in name:
        return "title_case_spaces"
    if re.match(r"^[A-Z][A-Za-z0-9]+$", name) and any(ch.islower() for ch in name):
        return "compact_pascal"
    return None


def _looks_like_generated_inventory_name(display_name: str) -> bool:
    name = (display_name or "").strip()
    return bool(re.match(r"^Inv[A-Za-z0-9]{6,}(LH|LHS|WH|NB|Model|Report)?$", name))


def _infer_inventory_naming_convention(
    *,
    source_items: list[dict],
    folders: list[dict],
    workspace_id: str,
    folder_name: str,
    folder_id: str,
    solution_name: str,
) -> dict:
    tmp_folder_ids = {
        str(folder.get("id") or "")
        for folder in folders
        if str(folder.get("displayName") or "").startswith("tmp_")
    }
    candidate_names: list[str] = []
    for item in source_items:
        if str(item.get("workspaceId") or "") != str(workspace_id):
            continue
        if str(item.get("folderId") or "") in tmp_folder_ids:
            continue
        name = str(item.get("displayName") or item.get("name") or "").strip()
        if not name or _looks_like_generated_inventory_name(name):
            continue
        candidate_names.append(name)
    for folder in folders:
        name = str(folder.get("displayName") or "").strip()
        if not name or name.startswith("tmp_") or _looks_like_generated_inventory_name(name):
            continue
        candidate_names.append(name)

    style_counts = Counter(
        style for name in candidate_names if (style := _name_style(name))
    )
    preferred_style = "title_case_spaces"
    if style_counts:
        preferred_style = style_counts.most_common(1)[0][0]
    base_display_name = " ".join(_title_words(solution_name)) or "Fabric Items Inventory"
    run_token = _inventory_run_token(folder_name, folder_id)
    return {
        "status": "inferred" if candidate_names else "defaulted",
        "preferredStyle": preferred_style,
        "baseDisplayName": base_display_name,
        "runToken": run_token,
        "sampleSize": len(candidate_names),
        "styleCounts": dict(style_counts),
        "examples": candidate_names[:8],
        "ignoredTempFolderCount": len(tmp_folder_ids),
    }


_INVENTORY_ITEM_TYPE_LABELS = {
    "Lakehouse": "Lakehouse",
    "Warehouse": "Warehouse",
    "Notebook": "Notebook",
    "SemanticModel": "Semantic Model",
    "Report": "Report",
}


def _inventory_display_name(naming: dict, item_type: str) -> str:
    label = _INVENTORY_ITEM_TYPE_LABELS.get(item_type, item_type)
    if item_type in {"Lakehouse", "Warehouse"}:
        return _inventory_internal_artifact_name(naming, label, sql_identifier=True)
    base = str(naming.get("baseDisplayName") or "Fabric Items Inventory")
    token = str(naming.get("runToken") or "Run")
    style = str(naming.get("preferredStyle") or "title_case_spaces")
    if style == "compact_pascal":
        name = f"{_compact_pascal(base)}{token}{_compact_pascal(label)}"
    elif style == "snake_case":
        name = _snake_case(f"{base} {token} {label}")
    elif style == "kebab_case":
        name = _kebab_case(f"{base} {token} {label}")
    else:
        name = f"{base} {token} {label}"
    return name[:120]


def _inventory_internal_artifact_name(
    naming: dict,
    label: str,
    *,
    sql_identifier: bool = False,
) -> str:
    base = str(naming.get("baseDisplayName") or "Fabric Items Inventory")
    token = str(naming.get("runToken") or "Run")
    style = str(naming.get("preferredStyle") or "title_case_spaces")
    value = f"{base} {token} {label}"
    if sql_identifier:
        if style in {"snake_case", "kebab_case"}:
            name = _snake_case(value)
        else:
            name = _compact_pascal(value)
        name = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_")
        if not name or not re.match(r"^[A-Za-z_]", name):
            name = f"Inventory_{name or _compact_pascal(label)}"
    elif style == "compact_pascal":
        name = f"{_compact_pascal(base)}{token}{_compact_pascal(label)}"
    elif style == "snake_case":
        name = _snake_case(value)
    elif style == "kebab_case":
        name = _kebab_case(value)
    else:
        name = value
    return name[:120]


def _modern_report_style_profile() -> dict:
    palette = ["#0F766E", "#2563EB", "#7C3AED", "#F97316", "#16A34A", "#DB2777", "#0891B2"]
    theme = {
        "name": "AgentHub Championship Analytics",
        "dataColors": palette,
        "background": "#F8FAFC",
        "foreground": "#111827",
        "tableAccent": "#0F766E",
        "visualStyles": {
            "*": {
                "*": {
                    "title": [{"show": True, "fontColor": {"solid": {"color": "#111827"}}, "fontSize": 11}],
                    "background": [{"show": True, "color": {"solid": {"color": "#FFFFFF"}}, "transparency": 0}],
                    "border": [{"show": True, "color": {"solid": {"color": "#E5E7EB"}}, "transparency": 0}],
                    "visualHeader": [{"show": False}],
                }
            }
        },
    }
    return {
        "name": theme["name"],
        "palette": palette,
        "canvasColor": "#F8FAFC",
        "surfaceColor": "#FFFFFF",
        "borderColor": "#E5E7EB",
        "titleColor": "#111827",
        "mutedTextColor": "#64748B",
        "accentColor": "#0F766E",
        "theme": theme,
    }


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
            headers=_fabric_headers(require_delegated=True, operation="creating workspace"),
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
async def fabric_get_semantic_model_refresh_history(
    workspace_id: str,
    semantic_model_id: str,
    top: int = 5,
) -> str:
    """Return the most recent refresh attempts for a semantic model.

    Use this to check whether a semantic-model refresh actually
    succeeded — many DirectLake/Import errors only surface in the
    refresh history (e.g. ``0xC14700C7`` "We cannot access the source
    Delta table ..."). When ``status == "Failed"``, the response
    surfaces ``serviceExceptionJson`` so the caller can read the
    underlying error description verbatim.

    Args:
        workspace_id: The workspace UUID.
        semantic_model_id: The semantic-model (dataset) UUID.
        top: Number of recent refreshes to return (default 5).
    """
    top = max(1, min(top, 50))
    url = (
        f"{POWERBI_API_BASE}/groups/{workspace_id}"
        f"/datasets/{semantic_model_id}/refreshes?$top={top}"
    )
    try:
        header_candidates = _powerbi_header_candidates()
    except RuntimeError as exc:
        return json.dumps({"status": "error", "error": str(exc)}, indent=2)
    async with shared_client(60.0) as client:
        last_resp = None
        for token_name, headers in header_candidates:
            resp = await client.get(url, headers=headers)
            last_resp = resp
            if resp.status_code in (401, 403):
                continue
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except Exception as exc:
                    return json.dumps(
                        {"status": "error", "error": f"non-JSON response: {exc}"},
                        indent=2,
                    )
                entries = (body or {}).get("value") or []
                # Surface decoded error description for any failed
                # entry so verifiers don't have to re-parse the JSON.
                normalized: list[dict] = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    out = dict(entry)
                    raw_err = entry.get("serviceExceptionJson")
                    if isinstance(raw_err, str) and raw_err:
                        try:
                            parsed = json.loads(raw_err)
                            out["errorDescription"] = (
                                parsed.get("errorDescription")
                                or parsed.get("errorMessage")
                                or raw_err
                            )
                            out["errorCode"] = parsed.get("errorCode")
                        except Exception:
                            out["errorDescription"] = raw_err
                    normalized.append(out)
                return json.dumps(
                    {
                        "status": "ok",
                        "via": f"powerbi:{token_name}",
                        "refreshes": normalized,
                    },
                    indent=2,
                )
            return json.dumps(
                {"status": "error", "error": format_http_error(resp, "fetching refresh history")},
                indent=2,
            )
        return json.dumps(
            {
                "status": "error",
                "error": (
                    "All token candidates were rejected (401/403) when fetching refresh history. "
                    f"Last status: {getattr(last_resp, 'status_code', 'n/a')}"
                ),
            },
            indent=2,
        )


@mcp.tool()
async def fabric_diagnose_workspace_artifacts(
    workspace_id: str,
    folder_id: str | None = None,
    folder_name: str | None = None,
    item_ids: list[str] | None = None,
    include_refresh_history: bool = True,
    include_powerbi_diagnostics: bool = True,
    include_lakehouse_diagnostics: bool = True,
    include_definition_diagnostics: bool = False,
) -> str:
    """Collect read-only diagnostics for Fabric artifacts in a workspace.

    Use this after any create/update/refresh/report/render failure before
    retrying the same mutating operation. The tool gathers the evidence agents
    usually need for root cause analysis: workspace/capacity state, folder and
    item metadata, owner hints, workspace role assignments, semantic-model
    refresh history, Power BI dataset/report metadata, DAX metadata probes,
    Lakehouse table/catalog evidence, token auth mode, and common cross-artifact
    risks such as a SemanticModel owner differing from the Lakehouse/Warehouse
    owner in the same folder.

    Args:
        workspace_id: Workspace UUID.
        folder_id: Optional folder UUID to scope diagnostics.
        folder_name: Optional folder display name to resolve and scope.
        item_ids: Optional explicit item UUIDs to inspect.
        include_refresh_history: Include recent Power BI refresh history for
            SemanticModel items when possible.
        include_powerbi_diagnostics: Include Power BI dataset/report metadata
            and DAX INFO.VIEW diagnostics for SemanticModel/Report items.
        include_lakehouse_diagnostics: Include Fabric Lakehouse metadata and
            table catalog diagnostics for Lakehouse items.
        include_definition_diagnostics: Include Fabric getDefinition probes for
            semantic models. This can be larger/slower, so defaults to false.
    """
    headers = _fabric_headers()
    diagnostics: dict = {
        "status": "ok",
        "workspaceId": workspace_id,
        "requested": {
            "folderId": folder_id,
            "folderName": folder_name,
            "itemIds": item_ids or [],
            "includeRefreshHistory": include_refresh_history,
            "includePowerBIDiagnostics": include_powerbi_diagnostics,
            "includeLakehouseDiagnostics": include_lakehouse_diagnostics,
            "includeDefinitionDiagnostics": include_definition_diagnostics,
        },
        "tokenIdentity": _token_identity_hint(),
        "tokenIdentities": {
            "fabric": _token_identity_from_claims(_token_claims(os.environ.get("FABRIC_API_TOKEN", ""))) if os.environ.get("FABRIC_API_TOKEN") else {"token": "missing"},
            "powerbi": _token_identity_from_claims(_token_claims(os.environ.get("POWERBI_API_TOKEN", ""))) if os.environ.get("POWERBI_API_TOKEN") else {"token": "missing"},
            "onelake": _token_identity_from_claims(_token_claims(os.environ.get("ONELAKE_TOKEN", ""))) if os.environ.get("ONELAKE_TOKEN") else {"token": "missing"},
        },
        "diagnosticCoverage": [
            "Fabric workspace metadata and capacity readiness",
            "Fabric folder and item metadata with owner hints",
            "Workspace role assignments",
            "Token auth mode and principal hints for Fabric, Power BI, and OneLake",
            "SemanticModel refresh history and Power BI dataset metadata",
            "SemanticModel DAX metadata/queryability probes through executeQueries",
            "Report metadata, pages, and datasource binding evidence",
            "Lakehouse metadata and table catalog evidence",
            "Cross-artifact owner/effective-identity mismatch detection",
            "Owner identity hints suitable for Entra owner/access diagnostics",
        ],
        "warnings": [],
        "suspectedIssues": [],
        "recommendedNextChecks": [],
    }
    async with shared_client(60.0) as client:
        capacity = await _validate_workspace_capacity_active(client, workspace_id, headers)
        diagnostics["capacity"] = (
            {"status": "error", "error": capacity}
            if isinstance(capacity, str)
            else capacity
        )
        folders, folder_error = await _fabric_get_all_values(
            client, f"{FABRIC_API_BASE}/workspaces/{workspace_id}/folders", headers,
        )
        if folder_error:
            diagnostics["warnings"].append(f"Could not list folders: {folder_error}")
            folders = []
        if folder_name and not folder_id:
            match = next((f for f in folders if f.get("displayName") == folder_name), None)
            if match:
                folder_id = match.get("id")
        diagnostics["folder"] = next(
            (f for f in folders if (folder_id and f.get("id") == folder_id) or (folder_name and f.get("displayName") == folder_name)),
            None,
        )

        items, item_error = await _fabric_get_all_values(
            client, f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items", headers,
        )
        if item_error:
            return json.dumps({**diagnostics, "status": "error", "error": item_error}, indent=2)

        explicit_ids = {str(item_id) for item_id in (item_ids or []) if item_id}
        selected: list[dict] = []
        for item in items:
            item_id = str(item.get("id") or "")
            item_folder_id = item.get("folderId")
            if explicit_ids and item_id not in explicit_ids:
                continue
            if folder_id and item_folder_id != folder_id:
                continue
            if not explicit_ids and not folder_id and len(selected) >= 50:
                continue
            selected.append(item)

        hydrated: list[dict] = []
        for item in selected:
            full = await _hydrate_workspace_item(client, workspace_id, headers, item)
            item_id = full.get("id")
            item_type = full.get("type")
            row = {
                "id": item_id,
                "displayName": full.get("displayName") or full.get("name"),
                "type": item_type,
                "folderId": full.get("folderId"),
                "owner": _item_owner_hint(full),
                "ownerIdentity": _item_owner_identity_hint(full),
                "createdBy": full.get("createdBy"),
                "lastModifiedBy": full.get("lastModifiedBy"),
                "createdDate": full.get("createdDate"),
                "lastUpdatedDate": full.get("lastUpdatedDate"),
                "webUrl": full.get("webUrl"),
                **_build_item_links(workspace_id, item_id, item_type),
            }
            if include_refresh_history and str(item_type or "").lower().replace(" ", "") == "semanticmodel" and item_id:
                row["refreshHistory"] = await _recent_refresh_history(client, workspace_id, str(item_id), top=3)
            normalized_type = str(item_type or "").lower().replace(" ", "")
            if include_powerbi_diagnostics and normalized_type == "semanticmodel" and item_id:
                row["semanticModelDiagnostics"] = await _semantic_model_diagnostics(
                    client,
                    workspace_id,
                    str(item_id),
                    include_definition=include_definition_diagnostics,
                )
            if include_powerbi_diagnostics and normalized_type == "report" and item_id:
                row["reportDiagnostics"] = await _report_diagnostics(client, workspace_id, str(item_id))
            if include_lakehouse_diagnostics and normalized_type == "lakehouse" and item_id:
                row["lakehouseDiagnostics"] = await _lakehouse_diagnostics(client, workspace_id, str(item_id), headers)
            hydrated.append(row)

        role_resp = await client.get(
            f"{FABRIC_API_BASE}/workspaces/{workspace_id}/roleAssignments",
            headers=headers,
        )
        if role_resp.status_code == 200:
            roles = (role_resp.json() if role_resp.text else {}).get("value", [])
            diagnostics["workspaceRoles"] = {"status": "ok", "count": len(roles), "roles": roles[:100]}
        else:
            diagnostics["workspaceRoles"] = {
                "status": "unavailable",
                "error": format_http_error(role_resp, "listing workspace role assignments"),
            }
        diagnostics["workspace"] = await _diagnostic_http_json(
            client,
            "GET",
            f"{FABRIC_API_BASE}/workspaces/{workspace_id}",
            headers,
            "fabric_workspace_metadata",
        )

    diagnostics["items"] = hydrated

    source_items = [
        item for item in hydrated
        if str(item.get("type") or "").lower() in {"lakehouse", "warehouse", "sqleanalyticsendpoint", "sqlendpoint"}
    ]
    semantic_items = [
        item for item in hydrated
        if str(item.get("type") or "").lower().replace(" ", "") == "semanticmodel"
    ]
    for model in semantic_items:
        owner_match = _owner_matches_token_user(model.get("owner"), diagnostics.get("tokenIdentity"))
        if owner_match is False:
            diagnostics["suspectedIssues"].append({
                "code": "SEMANTIC_MODEL_OWNER_NOT_MISSION_USER",
                "severity": "error",
                "message": (
                    "SemanticModel owner does not match the delegated mission user token. "
                    "Direct Lake and refresh operations may run under the wrong effective identity."
                ),
                "semanticModel": {"id": model.get("id"), "displayName": model.get("displayName"), "owner": model.get("owner")},
                "tokenIdentity": diagnostics.get("tokenIdentity"),
            })
        for source in source_items:
            if model.get("folderId") and source.get("folderId") and model.get("folderId") != source.get("folderId"):
                continue
            model_owner = model.get("owner")
            source_owner = source.get("owner")
            if model_owner and source_owner and not _same_owner_hint(str(model_owner), str(source_owner)):
                diagnostics["suspectedIssues"].append({
                    "code": "DIRECTLAKE_OWNER_MISMATCH_RISK",
                    "severity": "warning",
                    "message": (
                        "SemanticModel owner differs from a likely data-source item owner. "
                        "For Direct Lake, the model owner/effective identity may need explicit access "
                        "to the Lakehouse/Warehouse tables and SQL endpoint."
                    ),
                    "semanticModel": {"id": model.get("id"), "displayName": model.get("displayName"), "owner": model_owner},
                    "sourceItem": {"id": source.get("id"), "displayName": source.get("displayName"), "type": source.get("type"), "owner": source_owner},
                })

    for model in semantic_items:
        for refresh in (model.get("refreshHistory") or {}).get("refreshes", []) if isinstance(model.get("refreshHistory"), dict) else []:
            if str(refresh.get("status") or "").lower() == "failed":
                diagnostics["suspectedIssues"].append({
                    "code": "SEMANTIC_MODEL_REFRESH_FAILED",
                    "severity": "error",
                    "semanticModelId": model.get("id"),
                    "errorCode": refresh.get("errorCode"),
                    "errorDescription": refresh.get("errorDescription") or refresh.get("serviceExceptionJson"),
                })

    diagnostics["recommendedNextChecks"] = [
        "If any token identity reports authMode=application, stop mutating Fabric/Power BI and repair OBO/delegated-token acquisition first.",
        "If a source/model owner mismatch appears, confirm the semantic model owner/effective identity has workspace or item access to the data source.",
        "For every suspicious ownerIdentity, call entra_diagnose_principal_access with id, UPN, email, appId, or displayName to confirm the owner still exists, is enabled, and has the needed memberships/roles.",
        "Use semanticModelDiagnostics.daxMetadata to distinguish model schema/partition failures from data-source permission failures.",
        "Use reportDiagnostics.powerbiReport pages/datasources to verify report-to-model binding before trying browser rendering.",
        "Use lakehouseDiagnostics.tables plus SQL endpoint metadata to verify that Delta tables exist before blaming the semantic model.",
        "If refresh history contains a failed serviceExceptionJson, treat that exact text as the root-cause evidence before retrying.",
        "If metadata is missing, call get_item/list_workspace_roles or sl_list_workspace_users with the same workspace id.",
        "Do not recreate duplicate artifacts until diagnostics explain whether the failure is schema, permissions, capacity, catalog propagation, or browser rendering.",
    ]
    return json.dumps(diagnostics, indent=2, default=str)


async def _recent_refresh_history(client, workspace_id: str, semantic_model_id: str, *, top: int = 3) -> dict:
    top = max(1, min(top, 10))
    url = (
        f"{POWERBI_API_BASE}/groups/{workspace_id}"
        f"/datasets/{semantic_model_id}/refreshes?$top={top}"
    )
    try:
        header_candidates = _powerbi_header_candidates()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}
    last_status = None
    for token_name, headers in header_candidates:
        resp = await client.get(url, headers=headers)
        last_status = resp.status_code
        if resp.status_code in (401, 403):
            continue
        if resp.status_code != 200:
            return {"status": "error", "error": format_http_error(resp, "fetching refresh history")}
        body = resp.json() if resp.text else {}
        normalized: list[dict] = []
        for entry in body.get("value", []) or []:
            if not isinstance(entry, dict):
                continue
            out = dict(entry)
            raw_err = entry.get("serviceExceptionJson")
            if isinstance(raw_err, str) and raw_err:
                try:
                    parsed = json.loads(raw_err)
                    out["errorDescription"] = parsed.get("errorDescription") or parsed.get("errorMessage") or raw_err
                    out["errorCode"] = parsed.get("errorCode")
                except Exception:
                    out["errorDescription"] = raw_err
            normalized.append(out)
        return {"status": "ok", "via": f"powerbi:{token_name}", "refreshes": normalized}
    return {"status": "error", "error": f"All token candidates rejected; last status={last_status}"}


def _bounded_diagnostic_value(value, *, depth: int = 0, max_list: int = 25, max_str: int = 2000):
    if depth >= 5:
        return "..."
    if isinstance(value, str):
        return bounded_text(value, max_chars=max_str)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        items = [_bounded_diagnostic_value(item, depth=depth + 1, max_list=max_list, max_str=max_str) for item in value[:max_list]]
        if len(value) > max_list:
            items.append({"truncated": len(value) - max_list})
        return items
    if isinstance(value, dict):
        return {
            str(key): _bounded_diagnostic_value(val, depth=depth + 1, max_list=max_list, max_str=max_str)
            for key, val in list(value.items())[:80]
        }
    return bounded_text(str(value), max_chars=max_str)


async def _diagnostic_http_json(
    client,
    method: str,
    url: str,
    headers: dict,
    label: str,
    *,
    json_body: dict | None = None,
) -> dict:
    try:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, json=json_body)
        else:
            return {"status": "error", "label": label, "error": f"Unsupported diagnostic method {method}"}
    except Exception as exc:
        return {"status": "error", "label": label, "error": str(exc)}

    row: dict = {
        "status": "ok" if 200 <= resp.status_code < 300 else "error",
        "label": label,
        "httpStatus": resp.status_code,
    }
    for header_name in ("x-ms-request-id", "requestid", "activityid", "Retry-After", "Location"):
        value = resp.headers.get(header_name) if hasattr(resp, "headers") else None
        if value:
            row[header_name] = value
    if resp.text:
        try:
            row["body"] = _bounded_diagnostic_value(resp.json())
        except Exception:
            row["text"] = bounded_text(resp.text, max_chars=2000)
    return row


async def _diagnostic_execute_dax(
    client,
    workspace_id: str,
    semantic_model_id: str,
    query: str,
    label: str,
) -> dict:
    try:
        header_candidates = _powerbi_header_candidates()
    except RuntimeError as exc:
        return {"status": "error", "label": label, "error": str(exc)}
    url = (
        f"{POWERBI_API_BASE}/groups/{workspace_id}"
        f"/datasets/{semantic_model_id}/executeQueries"
    )
    body = {"queries": [{"query": query}], "serializerSettings": {"includeNulls": True}}
    last: dict | None = None
    for token_name, headers in header_candidates:
        outcome = await _diagnostic_http_json(
            client,
            "POST",
            url,
            headers,
            label,
            json_body=body,
        )
        outcome["via"] = f"powerbi:{token_name}"
        last = outcome
        if outcome.get("httpStatus") not in (401, 403):
            return outcome
    return last or {"status": "error", "label": label, "error": "Power BI executeQueries was not attempted."}


async def _semantic_model_diagnostics(client, workspace_id: str, semantic_model_id: str, *, include_definition: bool) -> dict:
    diagnostics: dict = {
        "powerbiDataset": [],
        "daxMetadata": [],
        "definition": None,
    }
    try:
        pbi_headers = _powerbi_headers()
    except RuntimeError as exc:
        diagnostics["powerbiDataset"].append({"status": "error", "error": str(exc)})
        pbi_headers = None
    if pbi_headers:
        dataset_base = f"{POWERBI_API_BASE}/groups/{workspace_id}/datasets/{semantic_model_id}"
        for suffix, label in (
            ("", "powerbi_dataset_metadata"),
            ("/datasources", "powerbi_dataset_datasources"),
            ("/refreshSchedule", "powerbi_dataset_refresh_schedule"),
            ("/users", "powerbi_dataset_users"),
        ):
            diagnostics["powerbiDataset"].append(
                await _diagnostic_http_json(client, "GET", dataset_base + suffix, pbi_headers, label)
            )
    dax_queries = [
        (
            "semantic_model_scope_counts",
            """
EVALUATE
ROW(
    "TableCount", COUNTROWS(INFO.VIEW.TABLES()),
    "ColumnCount", COUNTROWS(INFO.VIEW.COLUMNS()),
    "MeasureCount", COUNTROWS(INFO.VIEW.MEASURES()),
    "RelationshipCount", COUNTROWS(INFO.VIEW.RELATIONSHIPS())
)
""".strip(),
        ),
        (
            "semantic_model_tables",
            "EVALUATE TOPN(50, SELECTCOLUMNS(INFO.VIEW.TABLES(), \"Name\", [Name], \"IsHidden\", [IsHidden]), [Name], ASC)",
        ),
        (
            "semantic_model_measures",
            "EVALUATE TOPN(50, SELECTCOLUMNS(INFO.VIEW.MEASURES(), \"Table\", [Table], \"Name\", [Name], \"Expression\", [Expression]), [Table], ASC, [Name], ASC)",
        ),
        (
            "semantic_model_partitions",
            "EVALUATE TOPN(50, SELECTCOLUMNS(INFO.PARTITIONS(), \"TableID\", [TableID], \"Name\", [Name], \"Mode\", [Mode], \"SourceType\", [SourceType], \"ExpressionSourceID\", [ExpressionSourceID]), [Name], ASC)",
        ),
    ]
    for label, query in dax_queries:
        diagnostics["daxMetadata"].append(await _diagnostic_execute_dax(client, workspace_id, semantic_model_id, query, label))

    if include_definition:
        headers = _fabric_headers()
        definition = await _diagnostic_http_json(
            client,
            "POST",
            f"{FABRIC_API_BASE}/workspaces/{workspace_id}/semanticModels/{semantic_model_id}/getDefinition",
            headers,
            "fabric_semantic_model_get_definition",
        )
        diagnostics["definition"] = definition
    return diagnostics


async def _lakehouse_diagnostics(client, workspace_id: str, lakehouse_id: str, headers: dict) -> dict:
    base = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}"
    return {
        "metadata": await _diagnostic_http_json(client, "GET", base, headers, "fabric_lakehouse_metadata"),
        "tables": await _diagnostic_http_json(client, "GET", f"{base}/tables", headers, "fabric_lakehouse_tables"),
    }


async def _report_diagnostics(client, workspace_id: str, report_id: str) -> dict:
    diagnostics: dict = {"powerbiReport": [], "fabricDefinition": None}
    try:
        pbi_headers = _powerbi_headers()
    except RuntimeError as exc:
        diagnostics["powerbiReport"].append({"status": "error", "error": str(exc)})
        pbi_headers = None
    if pbi_headers:
        report_base = f"{POWERBI_API_BASE}/groups/{workspace_id}/reports/{report_id}"
        for suffix, label in (
            ("", "powerbi_report_metadata"),
            ("/pages", "powerbi_report_pages"),
            ("/datasources", "powerbi_report_datasources"),
        ):
            diagnostics["powerbiReport"].append(
                await _diagnostic_http_json(client, "GET", report_base + suffix, pbi_headers, label)
            )
    return diagnostics


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
            headers=_fabric_headers(require_delegated=True, operation="creating folder"),
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
            headers=_fabric_headers(require_delegated=True, operation="creating item"),
        )
    if resp.status_code not in (200, 201, 202):
        return format_http_error(resp, 'creating item')
    created_item = resp.json()
    item_id = created_item.get("id") or ""
    if item_id:
        token_identity = _token_identity_hint()
        if _token_user_owner_hints(token_identity):
            headers = _fabric_headers(require_delegated=True, operation="validating created item owner")
            hydrated = await _hydrate_workspace_item(client, workspace_id, headers, created_item)
            owner_block = _owner_mismatch_block_message(hydrated, "creating item", token_identity)
            if owner_block:
                await _delete_workspace_item(client, workspace_id, headers, str(item_id))
                return owner_block
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
    quality_validation: dict | None = None
    directlake_identity_diagnostics: dict | None = None
    warehouse_id = ""
    warehouse_name = ""

    async with shared_client(120.0) as client:
        headers = _fabric_headers(require_delegated=True, operation="creating inventory solution artifacts")
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
        if isinstance(capacity_validation, dict) and capacity_validation.get("warning"):
            warnings.append(str(capacity_validation["warning"]))
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
        folders = folder_resp.json().get("value", [])
        if existing_folder:
            folder_result = existing_folder
        else:
            folder_result = await _post_fabric_json(
                client,
                f"{FABRIC_API_BASE}/workspaces/{workspace_id}/folders",
                {"displayName": folder_name},
                "creating inventory solution folder",
                require_delegated=True,
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
        naming_convention = _infer_inventory_naming_convention(
            source_items=source_items,
            folders=folders,
            workspace_id=workspace_id,
            folder_name=folder_name,
            folder_id=folder_id,
            solution_name=solution_name,
        )
        item_display_names = {
            item_type: _inventory_display_name(naming_convention, item_type)
            for item_type in ("Lakehouse", "Warehouse", "Notebook", "SemanticModel", "Report")
        }
        _record_inventory_progress(
            progress,
            started_at,
            "naming_convention",
            naming_convention["status"],
            namingConvention=naming_convention,
            itemDisplayNames=item_display_names,
        )

        _record_inventory_progress(progress, started_at, "data_artifact", "started", preferredType="Lakehouse")
        lakehouse_result = await _create_or_reuse_inventory_item(
            client,
            workspace_id,
            headers,
            display_name=item_display_names["Lakehouse"],
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
                display_name=item_display_names["Lakehouse"],
                item_type="Lakehouse",
                description=item_description,
                folder_id=folder_id,
                op_name="creating inventory lakehouse",
                extra_body={"creationPayload": {"enableSchemas": True}},
            )
        if isinstance(lakehouse_result, str):
            errors.append(
                "Inventory solution requires a Lakehouse-backed Delta ingestion path; "
                f"Warehouse/import-model fallback is not accepted. Lakehouse error: {lakehouse_result}"
            )
            _record_inventory_progress(progress, started_at, "data_artifact", "failed", preferredType="Lakehouse", error=lakehouse_result)
        else:
            lakehouse_id = lakehouse_result.get("id", "")
            lakehouse_name = lakehouse_result.get("displayName") or lakehouse_result.get("name") or f"{item_base}LH"
            lakehouse_result = await _hydrate_workspace_item(client, workspace_id, headers, lakehouse_result)
            produced.append(lakehouse_result)
            _record_inventory_progress(progress, started_at, "data_artifact", "created", itemId=lakehouse_id, displayName=lakehouse_name)

        table_name = _inventory_internal_artifact_name(
            naming_convention,
            "Fabric Items",
            sql_identifier=True,
        )
        summary_table_name = _inventory_internal_artifact_name(
            naming_convention,
            "Fabric Items By Type",
            sql_identifier=True,
        )
        notebook_execution: dict | None = None
        notebook_definition: dict | None = None
        persistent_data_validation: dict | None = None
        if not lakehouse_id:
            _record_inventory_progress(progress, started_at, "notebook", "skipped", reason="missing_lakehouse")
        else:
            _record_inventory_progress(progress, started_at, "notebook", "started")
            notebook_result = await _create_or_reuse_inventory_item(
                client,
                workspace_id,
                headers,
                display_name=item_display_names["Notebook"],
                item_type="Notebook",
                description=item_description,
                folder_id=folder_id,
                op_name="creating inventory notebook",
            )
            if isinstance(notebook_result, str):
                errors.append(notebook_result)
                _record_inventory_progress(progress, started_at, "notebook", "failed", error=notebook_result)
                notebook_id = ""
            else:
                produced.append(notebook_result)
                notebook_id = notebook_result.get("id", "")
            if not notebook_id:
                errors.append("Inventory notebook could not be executed because Fabric did not return a notebook id.")
                _record_inventory_progress(progress, started_at, "notebook", "failed", error="missing_notebook_id")
            else:
                _record_inventory_progress(progress, started_at, "notebook_definition", "started", notebookId=notebook_id)
                notebook_definition = _inventory_notebook_definition(
                    workspace_id=workspace_id,
                    lakehouse_id=lakehouse_id,
                    lakehouse_name=lakehouse_name,
                    table_name=table_name,
                    summary_table_name=summary_table_name,
                )
                definition_result = await _update_notebook_definition(
                    client,
                    workspace_id,
                    notebook_id,
                    notebook_definition,
                )
                if isinstance(definition_result, str):
                    errors.append(definition_result)
                    _record_inventory_progress(progress, started_at, "notebook_definition", "failed", notebookId=notebook_id, error=definition_result)
                else:
                    _record_inventory_progress(progress, started_at, "notebook_definition", "ok", notebookId=notebook_id)
                    produced.append({
                        "displayName": _inventory_internal_artifact_name(
                            naming_convention,
                            "Notebook Definition",
                        ),
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
                        _record_inventory_progress(progress, started_at, "lakehouse_table_validation", "started", lakehouseId=lakehouse_id)
                        table_validation = await _wait_for_lakehouse_tables(
                            client,
                            workspace_id,
                            lakehouse_id,
                            headers,
                            {table_name, summary_table_name},
                            expected_non_empty_table_names={table_name},
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
                                rowCounts=table_validation.get("rowCounts"),
                            )
                            produced.append({
                                "displayName": f"{item_base}Lakehouse table validation",
                                "type": "LakehouseTables",
                                "id": lakehouse_id,
                                "workspaceId": workspace_id,
                                "status": table_validation.get("status"),
                                "tables": table_validation.get("tables"),
                                "rowCounts": table_validation.get("rowCounts"),
                                "via": table_validation.get("via"),
                            })

        # Resolve Direct Lake binding inputs whenever a Lakehouse-backed
        # Delta table is available. Direct Lake is the preferred storage
        # mode (no refresh, no duplication, live data from OneLake) and
        # avoids the calculated-DATATABLE anti-pattern that bakes a
        # snapshot of `source_items` into the model BIM. If the SQL
        # endpoint is not yet provisioned we fall back to the legacy
        # DATATABLE-bound model so the build still succeeds.
        directlake_endpoint: dict | None = None
        if lakehouse_id and persistent_data_validation:
            _record_inventory_progress(
                progress, started_at, "lakehouse_sql_endpoint", "started",
                lakehouseId=lakehouse_id,
            )
            sql_outcome = await _fetch_lakehouse_sql_endpoint(
                client, workspace_id, lakehouse_id, headers,
            )
            if isinstance(sql_outcome, str):
                warnings.append(
                    "Direct Lake binding unavailable; falling back to inline DATATABLE: "
                    f"{sql_outcome}"
                )
                _record_inventory_progress(
                    progress, started_at, "lakehouse_sql_endpoint", "warning",
                    lakehouseId=lakehouse_id, error=sql_outcome,
                )
            else:
                directlake_endpoint = sql_outcome
                _record_inventory_progress(
                    progress, started_at, "lakehouse_sql_endpoint", "ok",
                    lakehouseId=lakehouse_id,
                    sqlEndpointId=sql_outcome.get("id"),
                    provisioningStatus=sql_outcome.get("provisioningStatus"),
                )

                # Force the SQL endpoint to re-scan OneLake for new
                # Delta tables. Without this the DirectLake DAX
                # validation hits "Invalid object name 'dbo.<table>'"
                # because the SQL catalog has not yet seen the
                # notebook-written tables. Failures are non-fatal —
                # the catalog will catch up eventually.
                _record_inventory_progress(
                    progress, started_at, "sql_endpoint_metadata_refresh", "started",
                    lakehouseId=lakehouse_id,
                    sqlEndpointId=sql_outcome.get("id"),
                )
                refresh_outcome = await _refresh_sql_endpoint_metadata(
                    client, workspace_id, sql_outcome["id"], headers,
                )
                if isinstance(refresh_outcome, str):
                    _record_inventory_progress(
                        progress, started_at, "sql_endpoint_metadata_refresh", "warning",
                        lakehouseId=lakehouse_id,
                        sqlEndpointId=sql_outcome.get("id"),
                        error=refresh_outcome,
                    )
                else:
                    _record_inventory_progress(
                        progress, started_at, "sql_endpoint_metadata_refresh", "ok",
                        lakehouseId=lakehouse_id,
                        sqlEndpointId=sql_outcome.get("id"),
                        via=refresh_outcome.get("via"),
                    )

        if directlake_endpoint is not None:
            directlake_table_name = _observed_lakehouse_table_name(persistent_data_validation, table_name)
            directlake_summary_table_name = _observed_lakehouse_table_name(persistent_data_validation, summary_table_name)
            model_definition = _semantic_model_definition_directlake(
                table_name=directlake_table_name,
                summary_table_name=directlake_summary_table_name,
                sql_endpoint_connection_string=directlake_endpoint["connectionString"],
                sql_endpoint_id=directlake_endpoint["id"],
            )
            model_storage_mode = "DirectLake"
        else:
            model_definition = None
            model_storage_mode = "Unavailable"
            if lakehouse_id and persistent_data_validation:
                errors.append(
                    "Direct Lake binding was not verified because the Lakehouse SQL endpoint was unavailable; "
                    "import/DATATABLE semantic-model fallback is not accepted for the inventory solution."
                )

        # Pre-flight structural validation. Catches the common Direct
        # Lake TMSL mistakes locally before we ship to Fabric, where
        # errors only surface much later as opaque refresh failures.
        _record_inventory_progress(
            progress, started_at, "semantic_model_definition_validation", "started",
            storageMode=model_storage_mode,
        )
        definition_errors = _validate_semantic_model_definition(model_definition)
        if definition_errors:
            joined = "; ".join(definition_errors[:10])
            errors.append(
                f"Semantic model definition failed pre-flight validation: {joined}"
            )
            _record_inventory_progress(
                progress, started_at, "semantic_model_definition_validation", "failed",
                storageMode=model_storage_mode,
                errorCount=len(definition_errors),
                errors=definition_errors[:10],
            )
            # The definition is broken — skip publishing it. Better to
            # fail fast than ship a model that won't refresh.
            model_definition = None
        else:
            _record_inventory_progress(
                progress, started_at, "semantic_model_definition_validation", "ok",
                storageMode=model_storage_mode,
            )

        if model_definition is None:
            model_id = ""
            model_result = "skipped: Direct Lake semantic model definition was not available"
        else:
            _record_inventory_progress(
                progress, started_at, "semantic_model", "started",
                storageMode=model_storage_mode,
            )
            model_result = await _create_or_reuse_inventory_item(
                client,
                workspace_id,
                headers,
                display_name=item_display_names["SemanticModel"],
                item_type="SemanticModel",
                description=item_description,
                folder_id=folder_id,
                op_name="creating inventory semantic model",
                extra_body={
                    "definition": model_definition,
                },
            )
        if isinstance(model_result, str):
            errors.append(model_result)
            model_id = ""
            _record_inventory_progress(progress, started_at, "semantic_model", "failed", error=model_result)
        else:
            model_id = model_result.get("id", "")
            model_result = await _hydrate_workspace_item(client, workspace_id, headers, model_result)
            produced.append(model_result)
            _record_inventory_progress(progress, started_at, "semantic_model", "created", modelId=model_id)

        if model_id and directlake_endpoint is not None and lakehouse_id:
            _record_inventory_progress(
                progress, started_at, "directlake_identity_validation", "started",
                modelId=model_id, lakehouseId=lakehouse_id,
            )
            directlake_identity_diagnostics = await _directlake_identity_diagnostics(
                client,
                workspace_id,
                headers,
                lakehouse_item=lakehouse_result if isinstance(lakehouse_result, dict) else None,
                semantic_model_item=model_result if isinstance(model_result, dict) else None,
                sql_endpoint=directlake_endpoint,
            )
            identity_status = str(directlake_identity_diagnostics.get("status") or "unknown")
            progress_status = "warning" if identity_status == "owner_mismatch" else identity_status
            _record_inventory_progress(
                progress, started_at, "directlake_identity_validation", progress_status,
                modelId=model_id,
                lakehouseId=lakehouse_id,
                diagnostics=directlake_identity_diagnostics,
            )
            if directlake_identity_diagnostics.get("ownerMismatch"):
                warnings.append(str(directlake_identity_diagnostics.get("message")))

        # Structural validation via INFO.TABLES() is intentionally NOT
        # called here. Direct Lake tables are unprocessed until the
        # first refresh frames them, and unprocessed Direct Lake
        # models reject every DAX query — including INFO.TABLES() —
        # with a 400 DatasetExecuteQueriesError. The pre-flight
        # ``_validate_semantic_model_definition`` already catches the
        # structural mistakes (bad partitions, missing expressions,
        # non-GUID endpoint) locally before deploy, and the refresh-
        # status + data-validation steps below catch any remaining
        # issues. Adding the validation here would only inject a
        # false-positive failure on every healthy model.

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

            # Verify the refresh actually completed (not just that the
            # job was accepted). Without this we silently miss errors
            # like 0xC14700C7 ("We cannot access the source Delta
            # table ...") which only surface when the user opens the
            # report and sees an empty visual.
            #
            # When that specific error fires, it almost always means the
            # SQL analytics endpoint catalog has not yet picked up the
            # Delta table the notebook just wrote. We re-refresh the
            # endpoint metadata (the same call sempy_labs uses) and
            # re-trigger the Power BI refresh up to a few times before
            # giving up.
            DELTA_TABLE_ACCESS_MARKERS = (
                "0xc14700c7",
                "we cannot access the source delta table",
                "source delta table does not exist",
            )
            refresh_status: dict = {}
            refresh_state = ""
            refresh_error = ""
            sql_endpoint_id = (directlake_endpoint or {}).get("id")
            for attempt in range(3):
                _record_inventory_progress(
                    progress, started_at, "semantic_model_refresh_status", "started",
                    modelId=model_id, attempt=attempt + 1,
                )
                refresh_status = await _wait_for_powerbi_refresh_completion(
                    client, workspace_id, model_id,
                )
                refresh_state = str(refresh_status.get("status") or "")
                refresh_error = str(refresh_status.get("errorMessage") or "")
                if refresh_state == "Completed":
                    break
                if refresh_state != "Failed":
                    break  # In-progress / Disabled / NoHistory — handled below.
                lower_err = refresh_error.lower()
                is_delta_access_error = any(
                    marker in lower_err for marker in DELTA_TABLE_ACCESS_MARKERS
                )
                if is_delta_access_error and (directlake_identity_diagnostics or {}).get("ownerMismatch"):
                    # This is not catalog propagation. The source and model
                    # owners differ, so retrying SQL endpoint metadata refresh
                    # just burns minutes before showing the same access error.
                    break
                if not is_delta_access_error or attempt == 2 or not sql_endpoint_id:
                    break
                # Recoverable: catalog propagation lag. Re-refresh the
                # SQL endpoint catalog and retry the model refresh.
                _record_inventory_progress(
                    progress, started_at, "semantic_model_refresh_status", "retrying",
                    modelId=model_id, attempt=attempt + 1,
                    reason="delta_table_not_visible_to_sql_endpoint",
                    error=refresh_error[:240],
                )
                await _refresh_sql_endpoint_metadata(
                    client, workspace_id, sql_endpoint_id, headers,
                )
                # Give the catalog a few extra seconds to settle, then
                # re-trigger the model refresh.
                await asyncio.sleep(8 + attempt * 6)
                retry_outcome = await _refresh_semantic_model(
                    client, workspace_id, model_id, headers,
                )
                if isinstance(retry_outcome, str):
                    # Retry kick-off failed — surface and bail to the
                    # status check so the error message is captured.
                    warnings.append(f"Refresh retry kick-off issue: {retry_outcome}")
            if refresh_state == "Completed":
                _record_inventory_progress(
                    progress, started_at, "semantic_model_refresh_status", "ok",
                    modelId=model_id, refreshState=refresh_state,
                )
            elif refresh_state == "Failed":
                errors.append(
                    "Semantic model refresh FAILED — Power BI reported: "
                    f"{refresh_error or 'no error description provided.'}"
                    f"{_directlake_identity_failure_hint(directlake_identity_diagnostics)}"
                )
                _record_inventory_progress(
                    progress, started_at, "semantic_model_refresh_status", "failed",
                    modelId=model_id, refreshState=refresh_state,
                    error=refresh_error,
                )
                # If refresh failed, the data is not loaded — skip
                # the queryability check and report creation. The
                # verifier will see this error on the response.
                model_id = ""
            else:
                # In-progress / Disabled / NoHistory — surface as a
                # warning so we still attempt downstream steps but the
                # verifier knows refresh status was inconclusive.
                warnings.append(
                    f"Semantic model refresh status was {refresh_state!r}: "
                    f"{refresh_error or 'no details available.'}"
                )
                _record_inventory_progress(
                    progress, started_at, "semantic_model_refresh_status", "warning",
                    modelId=model_id, refreshState=refresh_state,
                    error=refresh_error,
                )

        if model_id:
            _record_inventory_progress(progress, started_at, "semantic_model_data_validation", "started", modelId=model_id)
            model_data_outcome = await _wait_for_inventory_model_data(
                client,
                workspace_id,
                model_id,
                expected_min_rows=max(1, min(len(source_items), 500)),
                sql_endpoint_id=(directlake_endpoint or {}).get("id"),
                fabric_headers=headers,
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
            # Prefer cloning a known-good report when one exists in the
            # workspace; otherwise generate the documented PBIR-Legacy
            # shape and let render validation decide whether it is safe
            # to keep. This delivers a real report from the beginning
            # while preserving the verifier's strict gate.
            report_definition = await _build_inventory_report_definition_from_clone(
                client, workspace_id, model_id, headers, skip_folder_id=folder_id,
            )
            if report_definition is None:
                report_definition = _report_definition(model_id)
                _record_inventory_progress(
                    progress, started_at, "report_definition", "generated",
                    modelId=model_id,
                    note=(
                        "No clonable working Power BI report was found in workspace "
                        f"{workspace_id}; generated a PBIR-Legacy report definition "
                        "and will keep it only if render validation succeeds."
                    ),
                )
            else:
                _record_inventory_progress(
                    progress, started_at, "report_definition", "cloned",
                    modelId=model_id,
                )

            quality_validation = _inventory_solution_quality_validation(
                model_definition=model_definition,
                report_definition=report_definition,
                notebook_definition=notebook_definition,
                model_storage_mode=model_storage_mode,
                persistent_data_written=bool(notebook_execution and lakehouse_id and persistent_data_validation),
            )
            if quality_validation.get("status") != "passed" and report_definition.get("format") != "PBIR-Legacy":
                warnings.append(
                    "Cloned report template did not meet the inventory design-quality rubric; "
                    "using the generated executive PBIR-Legacy fallback instead."
                )
                report_definition = _report_definition(model_id)
                quality_validation = _inventory_solution_quality_validation(
                    model_definition=model_definition,
                    report_definition=report_definition,
                    notebook_definition=notebook_definition,
                    model_storage_mode=model_storage_mode,
                    persistent_data_written=bool(notebook_execution and lakehouse_id and persistent_data_validation),
                )
                _record_inventory_progress(
                    progress, started_at, "report_definition", "generated_quality_fallback",
                    modelId=model_id,
                    qualityValidation=quality_validation,
                )
            elif quality_validation.get("status") == "passed":
                _record_inventory_progress(
                    progress, started_at, "solution_quality_validation", "ok",
                    qualityValidation=quality_validation,
                )
            else:
                errors.append(
                    "Generated inventory solution failed the professional quality rubric: "
                    + json.dumps(quality_validation, sort_keys=True)[:1200]
                )
                _record_inventory_progress(
                    progress, started_at, "solution_quality_validation", "failed",
                    qualityValidation=quality_validation,
                )
                report_definition = None

            if report_definition is not None:
                _record_inventory_progress(progress, started_at, "report", "started", modelId=model_id)
                report_result = await _create_or_reuse_inventory_item(
                    client,
                    workspace_id,
                    headers,
                    display_name=item_display_names["Report"],
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
    directlake_model_bound = model_storage_mode == "DirectLake" and directlake_endpoint is not None
    notebook_writes_enabled = bool(lakehouse_id)
    notebook_exit_payload = _notebook_exit_payload(notebook_execution)
    notebook_row_count = _as_positive_int(notebook_exit_payload.get("rowCount"))
    notebook_warning_count = _as_positive_int(notebook_exit_payload.get("warningCount"))
    if notebook_warning_count:
        warning_preview = notebook_exit_payload.get("warnings")
        warnings.append(
            "Inventory notebook completed with "
            f"{notebook_warning_count} workspace warning(s): "
            + bounded_text(json.dumps(warning_preview, default=str), max_chars=500)
        )
    model_row_count = _as_positive_int((model_data_validation or {}).get("rowCount"))
    persistent_data_written = bool(
        notebook_execution
        and lakehouse_id
        and persistent_data_validation
        and directlake_model_bound
        and (notebook_row_count or model_row_count)
    )
    pre_creation_source_item_count = len(source_items)
    persisted_source_item_count = (
        model_row_count
        or _as_positive_int(notebook_exit_payload.get("rowCount"))
        or pre_creation_source_item_count
    )
    if model_storage_mode != "DirectLake":
        errors.append(
            f"Semantic model storage mode was {model_storage_mode!r}; accepted inventory solutions must bind the report to Lakehouse Delta tables through Direct Lake."
        )
    if notebook_exit_payload and not notebook_row_count:
        errors.append(
            "Inventory notebook completed but did not report a positive rowCount in its exit payload."
        )
    if not persistent_data_written:
        errors.append(
            "Inventory data was not proven to be persisted by the notebook into non-empty Lakehouse Delta tables."
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
        "sourceItemCount": persisted_source_item_count,
        "preCreationSourceItemCount": pre_creation_source_item_count,
        "sourceWorkspaceCount": source_workspace_count,
        "dataSource": "lakehouse_delta_tables" if persistent_data_written else "unverified_or_import_fallback",
        "semanticModelStorageMode": model_storage_mode,
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
        "directLakeIdentityDiagnostics": directlake_identity_diagnostics,
        "semanticModelDataValidation": model_data_validation,
        "reportRenderValidation": report_render_validation,
        "qualityValidation": quality_validation,
        "namingConvention": naming_convention,
        "itemDisplayNames": item_display_names,
        "cleanup": cleanup,
        "errors": blocking_errors,
        "warnings": warnings,
        "progress": progress,
        "createdItems": produced,
        "summary": "Created and verified Fabric inventory solution with folder, Lakehouse tables, executed Notebook, queryable SemanticModel, and renderable Report." if not blocking_errors else "Created a partial Fabric inventory solution; inspect errors.",
    }
    if blocking_errors:
        return "Error creating inventory solution: " + json.dumps(_compact_inventory_tool_result(result), indent=2)
    return json.dumps(_compact_inventory_tool_result(result), indent=2)


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
