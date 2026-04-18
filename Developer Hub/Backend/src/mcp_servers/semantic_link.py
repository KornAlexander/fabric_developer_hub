"""Semantic Link MCP Server — Fabric semantic model, report, lakehouse,
Direct Lake, admin, and governance operations via the MCP protocol.

Complements the core Fabric MCP server (workspace/item CRUD + OneLake I/O)
with higher-level data-platform operations inspired by the Semantic Link
(sempy) and Semantic Link Labs (sempy_labs) Python libraries.

All tools use direct Fabric REST API calls with bearer tokens injected
per-request by MCPClientManager — no Fabric notebook context needed.

Token env vars (set by MCPClientManager):
  FABRIC_API_TOKEN  — Fabric REST API (api.fabric.microsoft.com)
  ONELAKE_TOKEN     — OneLake DFS API  (onelake.dfs.fabric.microsoft.com)
"""

from __future__ import annotations

import json
import os
import time

import httpx
from mcp.server.fastmcp import FastMCP

# ── Constants ────────────────────────────────────────────────────────

FABRIC_API = "https://api.fabric.microsoft.com/v1"
PBI_API = "https://api.powerbi.com/v1.0/myorg"
XMLA_ENDPOINT = "https://analysis.windows.net/powerbi/api"

mcp = FastMCP(
    "semantic-link",
    version="0.1.0",
    description=(
        "Semantic model, report, Direct Lake, lakehouse, refresh, "
        "admin, and governance operations for Microsoft Fabric"
    ),
)


def _headers() -> dict:
    token = os.environ.get("FABRIC_API_TOKEN", "")
    if not token:
        raise RuntimeError("FABRIC_API_TOKEN not set")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _pbi_headers() -> dict:
    """Power BI REST API shares the same Fabric token."""
    return _headers()


async def _poll_lro(client: httpx.AsyncClient, location: str, hdrs: dict,
                    max_wait: int = 120) -> dict | None:
    """Poll a Fabric long-running operation until completion."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        resp = await client.get(location, headers=hdrs)
        if resp.status_code == 200:
            body = resp.json()
            status = body.get("status", "").lower()
            if status in ("succeeded", "completed"):
                return body
            if status in ("failed", "cancelled"):
                return body
        await _async_sleep(3)
    return None


async def _async_sleep(seconds: float):
    import asyncio
    await asyncio.sleep(seconds)


# ═══════════════════════════════════════════════════════════════════════
#  SEMANTIC MODEL — query & metadata
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_evaluate_dax(
    workspace_id: str,
    dataset_id: str,
    dax_query: str,
) -> str:
    """Execute a DAX query against a semantic model and return results as JSON.

    Args:
        workspace_id: Workspace UUID.
        dataset_id: Semantic model (dataset) UUID.
        dax_query: DAX query string, e.g. 'EVALUATE SUMMARIZE(Sales, Sales[Region])'.
    """
    body = {"queries": [{"query": dax_query}], "serializerSettings": {"includeNulls": True}}
    url = f"{PBI_API}/groups/{workspace_id}/datasets/{dataset_id}/executeQueries"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=body, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    data = resp.json()
    results = data.get("results", [])
    if results:
        tables = results[0].get("tables", [])
        if tables:
            return json.dumps(tables[0].get("rows", []), indent=2)
    return json.dumps(data, indent=2)


@mcp.tool()
async def sl_get_semantic_model_definition(
    workspace_id: str,
    dataset_id: str,
) -> str:
    """Get the full definition (TMDL/BIM files) of a semantic model.

    Returns a JSON list of {path, payload} parts (base64-encoded content).

    Args:
        workspace_id: Workspace UUID.
        dataset_id: Semantic model UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/semanticModels/{dataset_id}/getDefinition"
    hdrs = _headers()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=hdrs)
        if resp.status_code == 200:
            return json.dumps(resp.json().get("definition", {}).get("parts", []), indent=2)
        if resp.status_code == 202:
            location = resp.headers.get("Location", "")
            if not location:
                return "Error: 202 but no Location header"
            result_url = location.rstrip("/") + "/result"
            await _async_sleep(int(resp.headers.get("Retry-After", "5")))
            resp2 = await client.get(result_url, headers=hdrs)
            if resp2.status_code == 200:
                return json.dumps(resp2.json().get("definition", {}).get("parts", []), indent=2)
            return f"Error polling: {resp2.status_code} — {resp2.text[:500]}"
    return f"Error: {resp.status_code} — {resp.text[:500]}"


@mcp.tool()
async def sl_list_semantic_models(
    workspace_id: str,
) -> str:
    """List all semantic models in a workspace with key metadata.

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{PBI_API}/groups/{workspace_id}/datasets"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    datasets = resp.json().get("value", [])
    result = []
    for ds in datasets:
        result.append({
            "id": ds.get("id"),
            "name": ds.get("name"),
            "configuredBy": ds.get("configuredBy"),
            "isRefreshable": ds.get("isRefreshable"),
            "isEffectiveIdentityRequired": ds.get("isEffectiveIdentityRequired"),
            "targetStorageMode": ds.get("targetStorageMode"),
        })
    return json.dumps(result, indent=2)


@mcp.tool()
async def sl_get_semantic_model_tables(
    workspace_id: str,
    dataset_id: str,
) -> str:
    """Discover tables, columns, measures, and relationships in a semantic model via DAX INFO functions.

    Args:
        workspace_id: Workspace UUID.
        dataset_id: Semantic model UUID.
    """
    dax = """
    EVALUATE
    UNION(
        ROW("section", "tables",    "data", CONCATENATEX(INFO.VIEW.TABLES(), [Name] & " (" & [Description] & ")", ", ")),
        ROW("section", "measures",  "data", CONCATENATEX(INFO.VIEW.MEASURES(), [TableName] & ".[" & [Name] & "] = " & [Expression], " ||| ")),
        ROW("section", "columns",   "data", CONCATENATEX(INFO.VIEW.COLUMNS(), [TableName] & ".[" & [Name] & "] " & [DataType], " ||| ")),
        ROW("section", "relations", "data", CONCATENATEX(INFO.VIEW.RELATIONSHIPS(), [FromTableName] & ".[" & [FromColumnName] & "] -> " & [ToTableName] & ".[" & [ToColumnName] & "]", " ||| "))
    )
    """
    return await sl_evaluate_dax(workspace_id, dataset_id, dax)


# ═══════════════════════════════════════════════════════════════════════
#  SEMANTIC MODEL — refresh & cache
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_refresh_semantic_model(
    workspace_id: str,
    dataset_id: str,
    refresh_type: str = "full",
) -> str:
    """Trigger a refresh of a semantic model.

    Args:
        workspace_id: Workspace UUID.
        dataset_id: Semantic model UUID.
        refresh_type: 'full' or 'automatic' (default: full).
    """
    body = {"type": refresh_type}
    url = f"{PBI_API}/groups/{workspace_id}/datasets/{dataset_id}/refreshes"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=_pbi_headers())
    if resp.status_code == 202:
        return json.dumps({"status": "refresh_triggered", "dataset_id": dataset_id})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


@mcp.tool()
async def sl_get_refresh_history(
    workspace_id: str,
    dataset_id: str,
    top: int = 10,
) -> str:
    """Get refresh history for a semantic model.

    Args:
        workspace_id: Workspace UUID.
        dataset_id: Semantic model UUID.
        top: Number of recent refreshes to return (default 10).
    """
    url = f"{PBI_API}/groups/{workspace_id}/datasets/{dataset_id}/refreshes?$top={top}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


@mcp.tool()
async def sl_cancel_refresh(
    workspace_id: str,
    dataset_id: str,
    refresh_id: str,
) -> str:
    """Cancel an in-progress semantic model refresh.

    Args:
        workspace_id: Workspace UUID.
        dataset_id: Semantic model UUID.
        refresh_id: The refresh request ID to cancel.
    """
    url = f"{PBI_API}/groups/{workspace_id}/datasets/{dataset_id}/refreshes/{refresh_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(url, headers=_pbi_headers())
    if resp.status_code in (200, 204):
        return json.dumps({"status": "cancelled", "refresh_id": refresh_id})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ═══════════════════════════════════════════════════════════════════════
#  REPORT operations
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_reports(
    workspace_id: str,
) -> str:
    """List all reports in a workspace.

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{PBI_API}/groups/{workspace_id}/reports"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    reports = resp.json().get("value", [])
    result = []
    for r in reports:
        result.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "datasetId": r.get("datasetId"),
            "reportType": r.get("reportType"),
            "webUrl": r.get("webUrl"),
        })
    return json.dumps(result, indent=2)


@mcp.tool()
async def sl_get_report_definition(
    workspace_id: str,
    report_id: str,
) -> str:
    """Get the full PBIR definition of a report (all files).

    Args:
        workspace_id: Workspace UUID.
        report_id: Report UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/reports/{report_id}/getDefinition"
    hdrs = _headers()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=hdrs)
        if resp.status_code == 200:
            return json.dumps(resp.json().get("definition", {}).get("parts", []), indent=2)
        if resp.status_code == 202:
            location = resp.headers.get("Location", "")
            if not location:
                return "Error: 202 but no Location header"
            result_url = location.rstrip("/") + "/result"
            await _async_sleep(int(resp.headers.get("Retry-After", "5")))
            resp2 = await client.get(result_url, headers=hdrs)
            if resp2.status_code == 200:
                return json.dumps(resp2.json().get("definition", {}).get("parts", []), indent=2)
            return f"Error polling: {resp2.status_code} — {resp2.text[:500]}"
    return f"Error: {resp.status_code} — {resp.text[:500]}"


@mcp.tool()
async def sl_clone_report(
    workspace_id: str,
    report_id: str,
    cloned_report_name: str,
    target_workspace_id: str | None = None,
    target_dataset_id: str | None = None,
) -> str:
    """Clone a report within or across workspaces.

    Args:
        workspace_id: Source workspace UUID.
        report_id: Source report UUID.
        cloned_report_name: Name for the cloned report.
        target_workspace_id: Target workspace UUID (defaults to same workspace).
        target_dataset_id: Target semantic model UUID to rebind to.
    """
    body: dict = {"name": cloned_report_name}
    if target_workspace_id:
        body["targetWorkspaceId"] = target_workspace_id
    if target_dataset_id:
        body["targetModelId"] = target_dataset_id
    url = f"{PBI_API}/groups/{workspace_id}/reports/{report_id}/Clone"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=_pbi_headers())
    if resp.status_code in (200, 201):
        return json.dumps(resp.json(), indent=2)
    return f"Error: {resp.status_code} — {resp.text[:500]}"


@mcp.tool()
async def sl_rebind_report(
    workspace_id: str,
    report_id: str,
    target_dataset_id: str,
) -> str:
    """Rebind a report to a different semantic model.

    Args:
        workspace_id: Workspace UUID.
        report_id: Report UUID.
        target_dataset_id: New semantic model UUID to bind to.
    """
    body = {"datasetId": target_dataset_id}
    url = f"{PBI_API}/groups/{workspace_id}/reports/{report_id}/Rebind"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=_pbi_headers())
    if resp.status_code in (200, 204):
        return json.dumps({"status": "rebound", "report_id": report_id, "dataset_id": target_dataset_id})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


@mcp.tool()
async def sl_export_report(
    workspace_id: str,
    report_id: str,
    export_format: str = "PDF",
) -> str:
    """Export a report to PDF, PPTX, or PNG. Returns the export status.

    This triggers an async export. Poll with the returned URL.

    Args:
        workspace_id: Workspace UUID.
        report_id: Report UUID.
        export_format: 'PDF', 'PPTX', or 'PNG'.
    """
    body = {"format": export_format}
    url = f"{PBI_API}/groups/{workspace_id}/reports/{report_id}/ExportTo"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=_pbi_headers())
    if resp.status_code == 202:
        return json.dumps(resp.json(), indent=2)
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ═══════════════════════════════════════════════════════════════════════
#  LAKEHOUSE operations
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_lakehouses(
    workspace_id: str,
) -> str:
    """List all lakehouses in a workspace.

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


@mcp.tool()
async def sl_get_lakehouse_tables(
    workspace_id: str,
    lakehouse_id: str,
) -> str:
    """List all tables in a lakehouse with metadata.

    Args:
        workspace_id: Workspace UUID.
        lakehouse_id: Lakehouse UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("data", []), indent=2)


@mcp.tool()
async def sl_run_table_maintenance(
    workspace_id: str,
    lakehouse_id: str,
    table_name: str,
    optimize: bool = True,
    vacuum: bool = True,
    v_order: bool = True,
    retention_hours: int = 168,
) -> str:
    """Run maintenance (OPTIMIZE and/or VACUUM) on a lakehouse delta table.

    Args:
        workspace_id: Workspace UUID.
        lakehouse_id: Lakehouse UUID.
        table_name: Name of the delta table.
        optimize: Run OPTIMIZE (default True).
        vacuum: Run VACUUM (default True).
        v_order: Use V-Order during OPTIMIZE (default True).
        retention_hours: VACUUM retention period in hours (default 168 = 7 days).
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/jobs/instances?jobType=TableMaintenance"
    config = {
        "tableName": table_name,
        "optimizeSettings": {"vOrder": v_order} if optimize else None,
        "vacuumSettings": {"retentionPeriod": f"{retention_hours}:00:00"} if vacuum else None,
    }
    config = {k: v for k, v in config.items() if v is not None}
    body = {"executionData": {"configuration": config}}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=_headers())
    if resp.status_code in (200, 202):
        return json.dumps({"status": "maintenance_triggered", "table": table_name})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ═══════════════════════════════════════════════════════════════════════
#  SHORTCUTS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_shortcuts(
    workspace_id: str,
    item_id: str,
    parent_path: str | None = None,
) -> str:
    """List OneLake shortcuts in a lakehouse or warehouse.

    Args:
        workspace_id: Workspace UUID.
        item_id: Lakehouse or warehouse UUID.
        parent_path: Optional parent path filter (e.g. 'Tables').
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/items/{item_id}/shortcuts"
    if parent_path:
        url += f"?parentPath={parent_path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


@mcp.tool()
async def sl_create_shortcut(
    workspace_id: str,
    item_id: str,
    shortcut_name: str,
    parent_path: str,
    source_workspace_id: str,
    source_item_id: str,
    source_path: str,
) -> str:
    """Create a OneLake shortcut pointing to another lakehouse/warehouse.

    Args:
        workspace_id: Target workspace UUID.
        item_id: Target lakehouse/warehouse UUID.
        shortcut_name: Name for the shortcut.
        parent_path: Parent path (e.g. 'Tables' or 'Files').
        source_workspace_id: Source workspace UUID.
        source_item_id: Source item UUID.
        source_path: Path in the source item.
    """
    body = {
        "path": parent_path,
        "name": shortcut_name,
        "target": {
            "oneLake": {
                "workspaceId": source_workspace_id,
                "itemId": source_item_id,
                "path": source_path,
            }
        },
    }
    url = f"{FABRIC_API}/workspaces/{workspace_id}/items/{item_id}/shortcuts"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=_headers())
    if resp.status_code in (200, 201):
        return json.dumps(resp.json(), indent=2)
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ═══════════════════════════════════════════════════════════════════════
#  WORKSPACE — users & capacity
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_workspace_users(
    workspace_id: str,
) -> str:
    """List users and their roles in a workspace.

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/roleAssignments"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


@mcp.tool()
async def sl_add_workspace_user(
    workspace_id: str,
    principal_id: str,
    principal_type: str,
    role: str,
) -> str:
    """Add a user or group to a workspace.

    Args:
        workspace_id: Workspace UUID.
        principal_id: User/group/service principal UUID.
        principal_type: 'User', 'Group', or 'ServicePrincipal'.
        role: 'Admin', 'Member', 'Contributor', or 'Viewer'.
    """
    body = {
        "principal": {"id": principal_id, "type": principal_type},
        "role": role,
    }
    url = f"{FABRIC_API}/workspaces/{workspace_id}/roleAssignments"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=_headers())
    if resp.status_code in (200, 201):
        return json.dumps({"status": "added", "principal_id": principal_id, "role": role})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


@mcp.tool()
async def sl_assign_workspace_to_capacity(
    workspace_id: str,
    capacity_id: str,
) -> str:
    """Assign a workspace to a Fabric capacity.

    Args:
        workspace_id: Workspace UUID.
        capacity_id: Capacity UUID.
    """
    body = {"capacityId": capacity_id}
    url = f"{FABRIC_API}/workspaces/{workspace_id}/assignToCapacity"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=_headers())
    if resp.status_code in (200, 202):
        return json.dumps({"status": "assigned", "workspace_id": workspace_id, "capacity_id": capacity_id})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ═══════════════════════════════════════════════════════════════════════
#  GIT integration
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_get_git_connection(
    workspace_id: str,
) -> str:
    """Get the git connection details for a workspace.

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/git/connection"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json(), indent=2)


@mcp.tool()
async def sl_get_git_status(
    workspace_id: str,
) -> str:
    """Get git sync status for a workspace (changes, conflicts).

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/git/status"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=_headers())
    if resp.status_code == 200:
        return json.dumps(resp.json(), indent=2)
    if resp.status_code == 202:
        location = resp.headers.get("Location", "")
        if location:
            result_url = location.rstrip("/") + "/result"
            await _async_sleep(3)
            resp2 = await client.get(result_url, headers=_headers())
            if resp2.status_code == 200:
                return json.dumps(resp2.json(), indent=2)
    return f"Error: {resp.status_code} — {resp.text[:500]}"


@mcp.tool()
async def sl_commit_to_git(
    workspace_id: str,
    comment: str,
) -> str:
    """Commit workspace changes to the connected git repository.

    Args:
        workspace_id: Workspace UUID.
        comment: Commit message.
    """
    body = {"mode": "All", "comment": comment}
    url = f"{FABRIC_API}/workspaces/{workspace_id}/git/commitToGit"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=body, headers=_headers())
    if resp.status_code in (200, 202):
        return json.dumps({"status": "committed", "comment": comment})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


@mcp.tool()
async def sl_update_from_git(
    workspace_id: str,
) -> str:
    """Pull the latest changes from git into the workspace.

    Args:
        workspace_id: Workspace UUID.
    """
    body = {"conflictResolution": {"conflictResolutionType": "Workspace"}}
    url = f"{FABRIC_API}/workspaces/{workspace_id}/git/updateFromGit"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=body, headers=_headers())
    if resp.status_code in (200, 202):
        return json.dumps({"status": "update_triggered"})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ═══════════════════════════════════════════════════════════════════════
#  DEPLOYMENT — semantic model
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_deploy_semantic_model(
    source_workspace_id: str,
    source_dataset_id: str,
    target_workspace_id: str,
    target_dataset_name: str,
) -> str:
    """Deploy (copy) a semantic model from one workspace to another.

    Uses getDefinition + updateDefinition for a full TMDL-based copy.

    Args:
        source_workspace_id: Source workspace UUID.
        source_dataset_id: Source semantic model UUID.
        target_workspace_id: Target workspace UUID.
        target_dataset_name: Display name in the target workspace.
    """
    hdrs = _headers()
    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. Get source definition
        resp = await client.post(
            f"{FABRIC_API}/workspaces/{source_workspace_id}/semanticModels/{source_dataset_id}/getDefinition",
            headers=hdrs,
        )
        if resp.status_code == 202:
            loc = resp.headers.get("Location", "")
            await _async_sleep(int(resp.headers.get("Retry-After", "5")))
            resp = await client.get(loc.rstrip("/") + "/result", headers=hdrs)
        if resp.status_code != 200:
            return f"Error getting definition: {resp.status_code} — {resp.text[:300]}"

        definition = resp.json().get("definition", {})

        # 2. Create in target workspace
        body = {
            "displayName": target_dataset_name,
            "type": "SemanticModel",
            "definition": definition,
        }
        resp2 = await client.post(
            f"{FABRIC_API}/workspaces/{target_workspace_id}/items",
            json=body,
            headers=hdrs,
        )
        if resp2.status_code in (200, 201, 202):
            return json.dumps(resp2.json(), indent=2)
        return f"Error creating model: {resp2.status_code} — {resp2.text[:300]}"


# ═══════════════════════════════════════════════════════════════════════
#  ADMIN — governance & audit
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_admin_list_workspaces(
    top: int = 100,
    state: str | None = None,
) -> str:
    """[Admin] List all workspaces across the tenant.

    Requires Power BI admin permissions.

    Args:
        top: Number of workspaces to return (max 5000).
        state: Filter by state — 'Active', 'Deleted', etc.
    """
    url = f"{PBI_API}/admin/groups?$top={top}"
    if state:
        url += f"&$filter=state eq '{state}'"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    workspaces = resp.json().get("value", [])
    result = []
    for ws in workspaces:
        result.append({
            "id": ws.get("id"),
            "name": ws.get("name"),
            "state": ws.get("state"),
            "type": ws.get("type"),
            "capacityId": ws.get("capacityId"),
        })
    return json.dumps(result, indent=2)


@mcp.tool()
async def sl_admin_list_datasets(
    top: int = 100,
) -> str:
    """[Admin] List all semantic models across the tenant.

    Requires Power BI admin permissions.

    Args:
        top: Number of datasets to return.
    """
    url = f"{PBI_API}/admin/datasets?$top={top}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


@mcp.tool()
async def sl_admin_get_activity_events(
    start_date: str,
    end_date: str,
    activity_type: str | None = None,
) -> str:
    """[Admin] Get audit/activity events for the tenant.

    Requires Power BI admin permissions.

    Args:
        start_date: ISO 8601 start datetime (e.g. '2026-04-01T00:00:00Z').
        end_date: ISO 8601 end datetime.
        activity_type: Optional filter — e.g. 'ViewReport', 'CreateReport'.
    """
    filter_str = f"activityDateTime ge {start_date} and activityDateTime le {end_date}"
    if activity_type:
        filter_str += f" and Activity eq '{activity_type}'"
    url = f"{PBI_API}/admin/activityevents?startDateTime='{start_date}'&endDateTime='{end_date}'"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json(), indent=2)


@mcp.tool()
async def sl_admin_list_workspace_users(
    workspace_id: str,
) -> str:
    """[Admin] List users with access to a specific workspace (admin view).

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{PBI_API}/admin/groups/{workspace_id}/users"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


@mcp.tool()
async def sl_admin_list_dataset_users(
    dataset_id: str,
) -> str:
    """[Admin] List users with access to a specific semantic model.

    Args:
        dataset_id: Semantic model UUID.
    """
    url = f"{PBI_API}/admin/datasets/{dataset_id}/users"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


# ═══════════════════════════════════════════════════════════════════════
#  CONNECTIONS & GATEWAYS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_connections() -> str:
    """List all connections the user has access to."""
    url = f"{FABRIC_API}/connections"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


@mcp.tool()
async def sl_list_gateways() -> str:
    """List all on-premises data gateways the user has access to."""
    url = f"{PBI_API}/gateways"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


# ═══════════════════════════════════════════════════════════════════════
#  NOTEBOOKS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_get_notebook_definition(
    workspace_id: str,
    notebook_id: str,
) -> str:
    """Get the definition (content) of a Fabric notebook.

    Args:
        workspace_id: Workspace UUID.
        notebook_id: Notebook UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/notebooks/{notebook_id}/getDefinition"
    hdrs = _headers()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=hdrs)
        if resp.status_code == 200:
            return json.dumps(resp.json().get("definition", {}).get("parts", []), indent=2)
        if resp.status_code == 202:
            location = resp.headers.get("Location", "")
            if not location:
                return "Error: 202 but no Location header"
            result_url = location.rstrip("/") + "/result"
            await _async_sleep(int(resp.headers.get("Retry-After", "5")))
            resp2 = await client.get(result_url, headers=hdrs)
            if resp2.status_code == 200:
                return json.dumps(resp2.json().get("definition", {}).get("parts", []), indent=2)
            return f"Error polling: {resp2.status_code} — {resp2.text[:500]}"
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ═══════════════════════════════════════════════════════════════════════
#  DATA PIPELINES
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_data_pipelines(
    workspace_id: str,
) -> str:
    """List all data pipelines in a workspace.

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/dataPipelines"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


@mcp.tool()
async def sl_run_data_pipeline(
    workspace_id: str,
    pipeline_id: str,
) -> str:
    """Trigger a data pipeline run.

    Args:
        workspace_id: Workspace UUID.
        pipeline_id: Data pipeline UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/items/{pipeline_id}/jobs/instances?jobType=Pipeline"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=_headers())
    if resp.status_code in (200, 202):
        return json.dumps({"status": "pipeline_triggered", "pipeline_id": pipeline_id})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ═══════════════════════════════════════════════════════════════════════
#  WAREHOUSES
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_warehouses(
    workspace_id: str,
) -> str:
    """List all warehouses in a workspace.

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/warehouses"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


# ═══════════════════════════════════════════════════════════════════════
#  SQL ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_sql_endpoints(
    workspace_id: str,
) -> str:
    """List all SQL endpoints in a workspace.

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/sqlEndpoints"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


# ═══════════════════════════════════════════════════════════════════════
#  CAPACITIES
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_capacities() -> str:
    """List all Fabric/Power BI capacities the user has access to."""
    url = f"{PBI_API}/capacities"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_pbi_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


# ═══════════════════════════════════════════════════════════════════════
#  MIRRORED DATABASES
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_mirrored_databases(
    workspace_id: str,
) -> str:
    """List all mirrored databases in a workspace.

    Args:
        workspace_id: Workspace UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/mirroredDatabases"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


@mcp.tool()
async def sl_get_mirroring_status(
    workspace_id: str,
    mirrored_db_id: str,
) -> str:
    """Get the mirroring status of a mirrored database.

    Args:
        workspace_id: Workspace UUID.
        mirrored_db_id: Mirrored database UUID.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/mirroredDatabases/{mirrored_db_id}/getMirroringStatus"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json(), indent=2)


# ═══════════════════════════════════════════════════════════════════════
#  JOB SCHEDULER
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_list_item_schedules(
    workspace_id: str,
    item_id: str,
    job_type: str = "DefaultJob",
) -> str:
    """List scheduled jobs for a Fabric item.

    Args:
        workspace_id: Workspace UUID.
        item_id: Item UUID.
        job_type: Job type (default 'DefaultJob').
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/items/{item_id}/jobs/{job_type}/schedules"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code != 200:
        return f"Error: {resp.status_code} — {resp.text[:500]}"
    return json.dumps(resp.json().get("value", []), indent=2)


@mcp.tool()
async def sl_run_item_job(
    workspace_id: str,
    item_id: str,
    job_type: str = "DefaultJob",
) -> str:
    """Run an on-demand job for a Fabric item (e.g. notebook, pipeline, semantic model refresh).

    Args:
        workspace_id: Workspace UUID.
        item_id: Item UUID.
        job_type: Job type — 'DefaultJob', 'Pipeline', 'RunNotebook', etc.
    """
    url = f"{FABRIC_API}/workspaces/{workspace_id}/items/{item_id}/jobs/instances?jobType={job_type}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=_headers())
    if resp.status_code in (200, 202):
        return json.dumps({"status": "job_triggered", "item_id": item_id, "job_type": job_type})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ═══════════════════════════════════════════════════════════════════════
#  ENDORSEMENT & CATALOG
# ═══════════════════════════════════════════════════════════════════════

@mcp.tool()
async def sl_set_endorsement(
    workspace_id: str,
    item_id: str,
    endorsement: str,
) -> str:
    """Set endorsement (Promoted/Certified/None) on a Fabric item.

    Args:
        workspace_id: Workspace UUID.
        item_id: Item UUID.
        endorsement: 'Promoted', 'Certified', or 'None'.
    """
    body = {"endorsementDetails": {"endorsement": endorsement}}
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Try the endorsement API
        resp = await client.patch(
            f"{FABRIC_API}/workspaces/{workspace_id}/items/{item_id}",
            json=body,
            headers=_headers(),
        )
    if resp.status_code in (200, 204):
        return json.dumps({"status": "endorsed", "item_id": item_id, "endorsement": endorsement})
    return f"Error: {resp.status_code} — {resp.text[:500]}"


# ═══════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run()
