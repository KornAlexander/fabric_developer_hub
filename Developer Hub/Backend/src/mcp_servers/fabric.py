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
    - fabric_create_item

  OneLake DFS API (requires ONELAKE_TOKEN):
    - fabric_list_files
    - fabric_read_file
    - fabric_write_file
    - fabric_delete_file
    - fabric_create_directory
"""

import base64
import json
import os
import sys

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

# API endpoints
FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
ONELAKE_DFS_BASE = "https://onelake.dfs.fabric.microsoft.com"

# ``log_level="WARNING"`` stops FastMCP from installing a bare
# ``logging.basicConfig(level=INFO, format="%(message)s")`` handler that
# would emit unstructured ``Processing request of type ...`` lines on
# this subprocess's stderr (captured by Docker under ``backend-1 |``).
mcp = FastMCP("fabric", log_level="WARNING")


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
async def fabric_create_item(
    workspace_id: str,
    display_name: str,
    item_type: str,
    description: str | None = None,
) -> str:
    """Create a new item in a Fabric workspace.

    Args:
        workspace_id: The workspace UUID (e.g. '8bdca8af-1db1-4fd8-9564-0c98b4dbdffc'). NOT the workspace name.
        display_name: Display name for the new item.
        item_type: Item type — e.g. 'Lakehouse', 'Notebook', 'Warehouse'.
        description: Optional description for the item.
    """
    body: dict = {"displayName": display_name, "type": item_type}
    if description:
        body["description"] = description
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
