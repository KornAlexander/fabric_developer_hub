"""First-party Azure Resource Manager MCP server.

This server intentionally uses the user's Azure Resource Manager OBO token
injected by ``MCPClientManager`` as ``AZURE_MANAGEMENT_TOKEN``. We do not rely
on Azure CLI, managed identity, or host credentials because AgentHub sessions
are user-scoped and multi-tenant.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from mcp.server.fastmcp import FastMCP

from mcp_servers._common import format_http_error, shared_client

ARM_BASE = "https://management.azure.com"
RESOURCE_API_VERSION = "2021-04-01"
AUTHORIZATION_API_VERSION = "2022-04-01"
FABRIC_CAPACITY_API_VERSION = "2023-11-01"
ACTIVITY_LOG_API_VERSION = "2015-04-01"
RESOURCE_HEALTH_API_VERSION = "2022-10-01"

mcp = FastMCP("azure-management", log_level="WARNING")

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_RESOURCE_GROUP_RE = re.compile(r"^[\w.()\-]{1,90}$")
_CAPACITY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,62}$")


def _headers() -> dict[str, str]:
    token = os.environ.get("AZURE_MANAGEMENT_TOKEN", "")
    if not token:
        raise RuntimeError("AZURE_MANAGEMENT_TOKEN not set — user may not be authenticated for Azure management.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _validate_subscription_id(subscription_id: str) -> str:
    value = (subscription_id or "").strip()
    if not _GUID_RE.match(value):
        raise ValueError("subscription_id must be a GUID")
    return value


def _validate_resource_group(resource_group: str) -> str:
    value = (resource_group or "").strip()
    if not _RESOURCE_GROUP_RE.match(value):
        raise ValueError("resource_group contains unsupported characters")
    return value


def _validate_capacity_name(capacity_name: str) -> str:
    value = (capacity_name or "").strip()
    if not _CAPACITY_NAME_RE.match(value):
        raise ValueError("capacity_name contains unsupported characters")
    return value


def _validate_scope(scope: str) -> str:
    value = (scope or "").strip().rstrip("/")
    if not value.startswith("/subscriptions/"):
        raise ValueError("scope must be an Azure Resource Manager scope under /subscriptions/{id}")
    if ".." in value or "//" in value or "\x00" in value:
        raise ValueError("scope contains unsupported path segments")
    return value


def _validate_resource_id(resource_id: str) -> str:
    value = _validate_scope(resource_id)
    if "/providers/" not in value.lower():
        raise ValueError("resource_id must be a full Azure resource ID containing /providers/{namespace}")
    return value


def _q(value: str) -> str:
    return quote(value, safe="")


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


async def _arm_get(path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
    async with shared_client(30.0) as client:
        response = await client.get(f"{ARM_BASE}{path}", headers=_headers(), params=params)
    if response.status_code >= 400:
        raise RuntimeError(format_http_error(response, "calling Azure Resource Manager"))
    return response.json()


async def _arm_post(path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
    async with shared_client(60.0) as client:
        response = await client.post(f"{ARM_BASE}{path}", headers=_headers(), params=params)
    if response.status_code >= 400:
        raise RuntimeError(format_http_error(response, "calling Azure Resource Manager"))
    if not response.content:
        return {"statusCode": response.status_code}
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = {"body": response.text[:1000]}
    body.setdefault("statusCode", response.status_code)
    operation_location = response.headers.get("Azure-AsyncOperation") or response.headers.get("Location")
    if operation_location:
        body["operationLocation"] = operation_location
    return body


def _resource_summary(resource: dict[str, Any]) -> dict[str, Any]:
    sku = resource.get("sku") or {}
    properties = resource.get("properties") or {}
    return {
        "id": resource.get("id"),
        "name": resource.get("name"),
        "type": resource.get("type"),
        "location": resource.get("location"),
        "resourceGroup": _resource_group_from_id(resource.get("id") or ""),
        "sku": sku.get("name") if isinstance(sku, dict) else sku,
        "state": properties.get("state") or properties.get("provisioningState"),
        "provisioningState": properties.get("provisioningState"),
    }


def _role_assignment_summary(assignment: dict[str, Any]) -> dict[str, Any]:
    properties = assignment.get("properties") or {}
    return {
        "id": assignment.get("id"),
        "name": assignment.get("name"),
        "scope": properties.get("scope"),
        "roleDefinitionId": properties.get("roleDefinitionId"),
        "principalId": properties.get("principalId"),
        "principalType": properties.get("principalType"),
        "condition": properties.get("condition"),
        "createdOn": properties.get("createdOn"),
        "updatedOn": properties.get("updatedOn"),
    }


def _role_definition_summary(definition: dict[str, Any]) -> dict[str, Any]:
    properties = definition.get("properties") or {}
    permissions = properties.get("permissions") or []
    return {
        "id": definition.get("id"),
        "name": definition.get("name"),
        "roleName": properties.get("roleName"),
        "type": properties.get("type"),
        "description": properties.get("description"),
        "assignableScopes": properties.get("assignableScopes") or [],
        "permissions": [
            {
                "actions": (permission.get("actions") or [])[:100],
                "notActions": (permission.get("notActions") or [])[:100],
                "dataActions": (permission.get("dataActions") or [])[:100],
                "notDataActions": (permission.get("notDataActions") or [])[:100],
            }
            for permission in permissions[:10]
        ],
        "truncatedPermissions": len(permissions) > 10,
    }


def _activity_log_summary(event: dict[str, Any]) -> dict[str, Any]:
    def _localized(value: Any) -> Any:
        if isinstance(value, dict):
            return value.get("localizedValue") or value.get("value")
        return value

    claims = event.get("claims") or {}
    return {
        "eventTimestamp": event.get("eventTimestamp"),
        "submissionTimestamp": event.get("submissionTimestamp"),
        "operationName": _localized(event.get("operationName")),
        "status": _localized(event.get("status")),
        "subStatus": _localized(event.get("subStatus")),
        "level": event.get("level"),
        "resourceId": event.get("resourceId"),
        "resourceGroupName": event.get("resourceGroupName"),
        "caller": event.get("caller"),
        "callerObjectId": claims.get("http://schemas.microsoft.com/identity/claims/objectidentifier")
        or claims.get("oid"),
        "correlationId": event.get("correlationId"),
        "category": _localized(event.get("category")),
    }


def _resource_group_from_id(resource_id: str) -> str | None:
    parts = resource_id.split("/")
    for index, part in enumerate(parts):
        if part.lower() == "resourcegroups" and index + 1 < len(parts):
            return parts[index + 1]
    return None


def _matches(action_pattern: str, action: str) -> bool:
    return fnmatch.fnmatchcase(action.lower(), action_pattern.lower())


def _is_action_allowed(permissions: list[dict[str, Any]], action: str) -> bool:
    for permission in permissions:
        actions = permission.get("actions") or []
        not_actions = permission.get("notActions") or []
        if any(_matches(pattern, action) for pattern in actions):
            if not any(_matches(pattern, action) for pattern in not_actions):
                return True
    return False


@mcp.tool()
async def azure_list_subscriptions() -> str:
    """List Azure subscriptions visible to the authenticated user."""
    data = await _arm_get("/subscriptions", params={"api-version": "2022-12-01"})
    subscriptions = [
        {
            "subscriptionId": item.get("subscriptionId"),
            "displayName": item.get("displayName"),
            "state": item.get("state"),
            "tenantId": item.get("tenantId"),
        }
        for item in data.get("value", [])
    ]
    return _json({"subscriptions": subscriptions})


@mcp.tool()
async def azure_list_resource_groups(subscription_id: str) -> str:
    """List resource groups in an Azure subscription."""
    sub = _validate_subscription_id(subscription_id)
    data = await _arm_get(
        f"/subscriptions/{sub}/resourcegroups",
        params={"api-version": RESOURCE_API_VERSION},
    )
    groups = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "location": item.get("location"),
            "provisioningState": (item.get("properties") or {}).get("provisioningState"),
        }
        for item in data.get("value", [])
    ]
    return _json({"resourceGroups": groups})


@mcp.tool()
async def azure_list_resources(subscription_id: str, resource_group: str | None = None) -> str:
    """List Azure resources in a subscription or resource group."""
    sub = _validate_subscription_id(subscription_id)
    if resource_group:
        rg = _validate_resource_group(resource_group)
        path = f"/subscriptions/{sub}/resourceGroups/{_q(rg)}/resources"
    else:
        path = f"/subscriptions/{sub}/resources"
    data = await _arm_get(path, params={"api-version": RESOURCE_API_VERSION})
    resources = [_resource_summary(item) for item in data.get("value", [])]
    return _json({"resources": resources[:500], "truncated": len(resources) > 500})


@mcp.tool()
async def azure_get_resource(resource_id: str, api_version: str | None = None) -> str:
    """Get an Azure resource by full ARM resource ID.

    If the resource provider requires a specific API version, pass it through
    ``api_version``. Otherwise the generic ARM resources API version is used.
    """
    clean_id = _validate_resource_id(resource_id)
    version = (api_version or RESOURCE_API_VERSION).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}([a-zA-Z0-9.-]+)?$", version):
        raise ValueError("api_version must look like an ARM API version, for example 2021-04-01")
    data = await _arm_get(clean_id, params={"api-version": version})
    return _json({"resource": _resource_summary(data), "raw": data})


@mcp.tool()
async def azure_check_permissions(scope: str, actions: list[str] | None = None) -> str:
    """List effective Azure RBAC permissions at a scope and optionally check actions.

    Example actions:
    - Microsoft.Fabric/capacities/read
    - Microsoft.Fabric/capacities/resume/action
    - Microsoft.Authorization/roleAssignments/read
    """
    clean_scope = _validate_scope(scope)
    data = await _arm_get(
        f"{clean_scope}/providers/Microsoft.Authorization/permissions",
        params={"api-version": AUTHORIZATION_API_VERSION},
    )
    permissions = list(data.get("value", []))
    requested = [str(action).strip() for action in (actions or []) if str(action).strip()]
    checks = {
        action: _is_action_allowed(permissions, action)
        for action in requested[:50]
    }
    summary = []
    for permission in permissions[:20]:
        summary.append({
            "actions": (permission.get("actions") or [])[:50],
            "notActions": (permission.get("notActions") or [])[:50],
            "dataActions": (permission.get("dataActions") or [])[:50],
            "notDataActions": (permission.get("notDataActions") or [])[:50],
        })
    return _json({
        "scope": clean_scope,
        "checks": checks,
        "permissionSets": summary,
        "truncated": len(permissions) > 20,
    })


@mcp.tool()
async def azure_list_role_assignments(scope: str) -> str:
    """List Azure RBAC role assignments at an ARM scope."""
    clean_scope = _validate_scope(scope)
    data = await _arm_get(
        f"{clean_scope}/providers/Microsoft.Authorization/roleAssignments",
        params={"api-version": AUTHORIZATION_API_VERSION},
    )
    assignments = [_role_assignment_summary(item) for item in data.get("value", [])]
    return _json({"scope": clean_scope, "roleAssignments": assignments[:500], "truncated": len(assignments) > 500})


@mcp.tool()
async def azure_list_role_definitions(scope: str, role_name: str | None = None) -> str:
    """List Azure RBAC role definitions at an ARM scope, optionally by role name."""
    clean_scope = _validate_scope(scope)
    params = {"api-version": AUTHORIZATION_API_VERSION}
    if role_name:
        safe_name = role_name.strip().replace("'", "''")[:120]
        params["$filter"] = f"roleName eq '{safe_name}'"
    data = await _arm_get(
        f"{clean_scope}/providers/Microsoft.Authorization/roleDefinitions",
        params=params,
    )
    definitions = [_role_definition_summary(item) for item in data.get("value", [])]
    return _json({"scope": clean_scope, "roleDefinitions": definitions[:200], "truncated": len(definitions) > 200})


@mcp.tool()
async def azure_get_activity_log(
    subscription_id: str,
    resource_group: str | None = None,
    resource_id: str | None = None,
    hours: int = 24,
    max_events: int = 50,
) -> str:
    """Read recent Azure Activity Log management events."""
    sub = _validate_subscription_id(subscription_id)
    bounded_hours = max(1, min(int(hours or 24), 168))
    bounded_events = max(1, min(int(max_events or 50), 200))
    end = datetime.now(UTC)
    start = end - timedelta(hours=bounded_hours)
    filters = [
        f"eventTimestamp ge '{start.isoformat().replace('+00:00', 'Z')}'",
        f"eventTimestamp le '{end.isoformat().replace('+00:00', 'Z')}'",
    ]
    if resource_group:
        filters.append(f"resourceGroupName eq '{_validate_resource_group(resource_group)}'")
    if resource_id:
        filters.append(f"resourceUri eq '{_validate_resource_id(resource_id)}'")

    data = await _arm_get(
        f"/subscriptions/{sub}/providers/Microsoft.Insights/eventtypes/management/values",
        params={
            "api-version": ACTIVITY_LOG_API_VERSION,
            "$filter": " and ".join(filters),
            "$select": "eventTimestamp,operationName,status,subStatus,resourceId,resourceGroupName,caller,correlationId,level,category,claims,submissionTimestamp",
        },
    )
    events = [_activity_log_summary(item) for item in data.get("value", [])]
    return _json({"events": events[:bounded_events], "truncated": len(events) > bounded_events})


@mcp.tool()
async def azure_get_resource_health(resource_id: str) -> str:
    """Get Azure Resource Health availability status for a resource."""
    clean_id = _validate_resource_id(resource_id)
    data = await _arm_get(
        f"{clean_id}/providers/Microsoft.ResourceHealth/availabilityStatuses/current",
        params={"api-version": RESOURCE_HEALTH_API_VERSION},
    )
    properties = data.get("properties") or {}
    return _json({
        "id": data.get("id"),
        "name": data.get("name"),
        "type": data.get("type"),
        "availabilityState": properties.get("availabilityState"),
        "summary": properties.get("summary"),
        "reasonType": properties.get("reasonType"),
        "reasonChronicity": properties.get("reasonChronicity"),
        "reportedTime": properties.get("reportedTime"),
        "rawProperties": properties,
    })


@mcp.tool()
async def azure_list_fabric_capacities(subscription_id: str, resource_group: str | None = None) -> str:
    """List Microsoft Fabric capacity Azure resources."""
    raw = json.loads(await azure_list_resources(subscription_id, resource_group))
    capacities = [
        item for item in raw.get("resources", [])
        if str(item.get("type") or "").lower() == "microsoft.fabric/capacities"
    ]
    return _json({"capacities": capacities, "truncated": raw.get("truncated", False)})


@mcp.tool()
async def azure_get_fabric_capacity(subscription_id: str, resource_group: str, capacity_name: str) -> str:
    """Get a Microsoft Fabric capacity Azure resource and its current state."""
    sub = _validate_subscription_id(subscription_id)
    rg = _validate_resource_group(resource_group)
    name = _validate_capacity_name(capacity_name)
    data = await _arm_get(
        f"/subscriptions/{sub}/resourceGroups/{_q(rg)}/providers/Microsoft.Fabric/capacities/{_q(name)}",
        params={"api-version": FABRIC_CAPACITY_API_VERSION},
    )
    return _json({"capacity": _resource_summary(data), "rawProperties": data.get("properties") or {}})


@mcp.tool()
async def azure_resume_fabric_capacity(subscription_id: str, resource_group: str, capacity_name: str) -> str:
    """Resume/start an existing Microsoft Fabric capacity Azure resource.

    This is a non-destructive write operation, but it can have billing impact.
    The caller must have ``Microsoft.Fabric/capacities/resume/action`` on the capacity scope.
    """
    sub = _validate_subscription_id(subscription_id)
    rg = _validate_resource_group(resource_group)
    name = _validate_capacity_name(capacity_name)
    path = f"/subscriptions/{sub}/resourceGroups/{_q(rg)}/providers/Microsoft.Fabric/capacities/{_q(name)}/resume"
    data = await _arm_post(path, params={"api-version": FABRIC_CAPACITY_API_VERSION})
    data["message"] = "Fabric capacity resume requested. Poll azure_get_fabric_capacity for state."
    data["capacityResourceId"] = (
        f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Fabric/capacities/{name}"
    )
    return _json(data)


if __name__ == "__main__":
    mcp.run()
