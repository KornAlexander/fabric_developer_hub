"""First-party Azure Resource Manager MCP server.

This server intentionally uses the user's Azure Resource Manager OBO token
injected by ``MCPClientManager`` as ``AZURE_MANAGEMENT_TOKEN``. We do not rely
on Azure CLI, managed identity, or host credentials because AgentHub sessions
are user-scoped and multi-tenant.
"""
from __future__ import annotations

import fnmatch
import base64
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
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
RESOURCE_API_VERSION = "2021-04-01"
AUTHORIZATION_API_VERSION = "2022-04-01"
FABRIC_CAPACITY_API_VERSION = "2023-11-01"
ACTIVITY_LOG_API_VERSION = "2015-04-01"
RESOURCE_HEALTH_API_VERSION = "2022-10-01"
DIAGNOSTIC_SETTINGS_API_VERSION = "2021-05-01-preview"
METRICS_API_VERSION = "2018-01-01"

mcp = FastMCP("azure-management", log_level="WARNING")

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_RESOURCE_GROUP_RE = re.compile(r"^[\w.()\-]{1,90}$")
_CAPACITY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,62}$")
_GRAPH_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9@._+\-#=]{1,256}$")
_GRAPH_SEARCH_TEXT_RE = re.compile(r"^[\w @._+\-#=()]{1,256}$")
_NETWORK_RESOURCE_TYPES = {
    "microsoft.network/applicationgateways",
    "microsoft.network/azurefirewalls",
    "microsoft.network/bastionhosts",
    "microsoft.network/connections",
    "microsoft.network/dnszones",
    "microsoft.network/expressroutecircuits",
    "microsoft.network/loadbalancers",
    "microsoft.network/localnetworkgateways",
    "microsoft.network/natgateways",
    "microsoft.network/networkinterfaces",
    "microsoft.network/networksecuritygroups",
    "microsoft.network/networkwatchers",
    "microsoft.network/privatednszones",
    "microsoft.network/privateendpoints",
    "microsoft.network/publicipaddresses",
    "microsoft.network/routetables",
    "microsoft.network/virtualnetworkgateways",
    "microsoft.network/virtualnetworks",
}


def _headers() -> dict[str, str]:
    token = os.environ.get("AZURE_MANAGEMENT_TOKEN", "")
    if not token:
        raise RuntimeError("AZURE_MANAGEMENT_TOKEN not set — user may not be authenticated for Azure management.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _graph_headers() -> dict[str, str]:
    token = os.environ.get("GRAPH_API_TOKEN", "")
    if not token:
        raise RuntimeError("GRAPH_API_TOKEN not set — user may not be authenticated for Microsoft Graph / Entra ID.")
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


def _validate_graph_identifier(identifier: str) -> str:
    value = (identifier or "").strip()
    if not _GRAPH_IDENTIFIER_RE.match(value) or ".." in value or "/" in value or "\x00" in value:
        raise ValueError("identifier must be an Entra object id, app id, or user principal name")
    return value


def _validate_graph_search_text(identifier: str) -> str:
    value = (identifier or "").strip()
    if not _GRAPH_SEARCH_TEXT_RE.match(value) or ".." in value or "/" in value or "\x00" in value:
        raise ValueError("identifier must be an Entra object id, app id, display name, or user principal name")
    return value


def _odata_quote(value: str) -> str:
    return value.replace("'", "''")


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


async def _graph_get(path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
    async with shared_client(30.0) as client:
        response = await client.get(f"{GRAPH_BASE}{path}", headers=_graph_headers(), params=params)
    if response.status_code >= 400:
        raise RuntimeError(format_http_error(response, "calling Microsoft Graph"))
    return response.json()


async def _try(label: str, awaitable: Any) -> dict[str, Any]:
    try:
        return {"ok": True, "label": label, "data": await awaitable}
    except Exception as exc:
        return {"ok": False, "label": label, "error": str(exc)}


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


def _subscription_from_id(resource_id: str) -> str | None:
    parts = resource_id.split("/")
    for index, part in enumerate(parts):
        if part.lower() == "subscriptions" and index + 1 < len(parts):
            candidate = parts[index + 1]
            return candidate if _GUID_RE.match(candidate) else None
    return None


def _principal_summary(principal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": principal.get("id"),
        "displayName": principal.get("displayName"),
        "userPrincipalName": principal.get("userPrincipalName"),
        "mail": principal.get("mail"),
        "accountEnabled": principal.get("accountEnabled"),
        "userType": principal.get("userType"),
        "appId": principal.get("appId"),
        "servicePrincipalType": principal.get("servicePrincipalType"),
        "appOwnerOrganizationId": principal.get("appOwnerOrganizationId"),
    }


def _dedupe_principals(principals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for principal in principals:
        principal_id = str(principal.get("id") or "")
        if principal_id and principal_id in seen:
            continue
        if principal_id:
            seen.add(principal_id)
        out.append(principal)
    return out


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        claims = json.loads(decoded.decode("utf-8"))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def _token_summary(env_name: str, token: str, *, include_claims: bool = False) -> dict[str, Any]:
    claims = _decode_jwt_claims(token)
    now = datetime.now(UTC).timestamp()
    exp = claims.get("exp")
    scopes = str(claims.get("scp") or "").split()
    roles = claims.get("roles") or []
    summary: dict[str, Any] = {
        "envName": env_name,
        "present": bool(token),
        "audience": claims.get("aud"),
        "tenantId": claims.get("tid"),
        "objectId": claims.get("oid"),
        "userPrincipalName": claims.get("upn") or claims.get("preferred_username"),
        "appId": claims.get("appid") or claims.get("azp"),
        "identityType": "delegated" if scopes else "app-only" if roles else "unknown",
        "scopes": scopes[:80],
        "roles": roles[:80] if isinstance(roles, list) else roles,
        "expiresUtc": datetime.fromtimestamp(exp, UTC).isoformat().replace("+00:00", "Z") if isinstance(exp, int) else None,
        "expired": bool(isinstance(exp, int) and exp <= now),
    }
    if include_claims:
        summary["claims"] = claims
    return summary


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
async def azure_list_diagnostic_settings(resource_id: str) -> str:
    """List Azure Monitor diagnostic settings for a resource.

    Useful when failures are hard to explain because the resource is not
    sending logs or metrics to Log Analytics, Event Hubs, or Storage.
    """
    clean_id = _validate_resource_id(resource_id)
    data = await _arm_get(
        f"{clean_id}/providers/microsoft.insights/diagnosticSettings",
        params={"api-version": DIAGNOSTIC_SETTINGS_API_VERSION},
    )
    settings = []
    for item in data.get("value", []):
        properties = item.get("properties") or {}
        settings.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "workspaceId": properties.get("workspaceId"),
            "eventHubAuthorizationRuleId": properties.get("eventHubAuthorizationRuleId"),
            "eventHubName": properties.get("eventHubName"),
            "storageAccountId": properties.get("storageAccountId"),
            "logs": [
                {"category": log.get("category"), "enabled": log.get("enabled")}
                for log in properties.get("logs", [])
            ],
            "metrics": [
                {"category": metric.get("category"), "enabled": metric.get("enabled")}
                for metric in properties.get("metrics", [])
            ],
        })
    return _json({"resourceId": clean_id, "diagnosticSettings": settings})


@mcp.tool()
async def azure_list_metric_definitions(resource_id: str) -> str:
    """List Azure Monitor metric definitions available for a resource."""
    clean_id = _validate_resource_id(resource_id)
    data = await _arm_get(
        f"{clean_id}/providers/microsoft.insights/metricDefinitions",
        params={"api-version": METRICS_API_VERSION},
    )
    metrics = []
    for item in data.get("value", []):
        name = item.get("name") or {}
        metrics.append({
            "name": name.get("value") or item.get("name"),
            "displayName": name.get("localizedValue"),
            "unit": item.get("unit"),
            "primaryAggregationType": item.get("primaryAggregationType"),
            "supportedAggregationTypes": item.get("supportedAggregationTypes") or [],
            "dimensions": [
                (dimension.get("name") or {}).get("value") or dimension.get("value")
                for dimension in item.get("dimensions", [])
            ],
        })
    return _json({"resourceId": clean_id, "metricDefinitions": metrics[:300], "truncated": len(metrics) > 300})


@mcp.tool()
async def azure_query_metrics(
    resource_id: str,
    metric_names: list[str] | None = None,
    timespan_minutes: int = 60,
    interval: str = "PT5M",
    aggregation: str = "Average,Minimum,Maximum,Total,Count",
) -> str:
    """Query recent Azure Monitor metrics for a resource and return compact series summaries."""
    clean_id = _validate_resource_id(resource_id)
    bounded_minutes = max(5, min(int(timespan_minutes or 60), 1440))
    end = datetime.now(UTC)
    start = end - timedelta(minutes=bounded_minutes)
    params = {
        "api-version": METRICS_API_VERSION,
        "timespan": f"{start.isoformat().replace('+00:00', 'Z')}/{end.isoformat().replace('+00:00', 'Z')}",
        "interval": (interval or "PT5M")[:20],
        "aggregation": (aggregation or "Average")[:120],
    }
    names = [str(name).strip() for name in (metric_names or []) if str(name).strip()]
    if names:
        params["metricnames"] = ",".join(names[:20])
    data = await _arm_get(
        f"{clean_id}/providers/microsoft.insights/metrics",
        params=params,
    )
    summaries = []
    for metric in data.get("value", []):
        name = metric.get("name") or {}
        series = []
        for timeseries in metric.get("timeseries", [])[:20]:
            points = [point for point in timeseries.get("data", []) if any(
                point.get(field) is not None for field in ("average", "minimum", "maximum", "total", "count")
            )]
            last = points[-1] if points else None
            series.append({
                "metadataValues": timeseries.get("metadatavalues") or [],
                "pointCount": len(points),
                "lastPoint": last,
            })
        summaries.append({
            "name": name.get("value") or metric.get("name"),
            "displayName": name.get("localizedValue"),
            "unit": metric.get("unit"),
            "series": series,
        })
    return _json({"resourceId": clean_id, "timespanMinutes": bounded_minutes, "metrics": summaries})


@mcp.tool()
async def azure_network_inventory(subscription_id: str, resource_group: str | None = None) -> str:
    """List network-relevant Azure resources for connectivity diagnostics.

    This is read-only and intentionally broad: VNets, subnets, NSGs, route
    tables, private endpoints, private DNS zones, load balancers, gateways,
    firewalls, public IPs, and related resources.
    """
    raw = json.loads(await azure_list_resources(subscription_id, resource_group))
    network_resources = [
        item for item in raw.get("resources", [])
        if str(item.get("type") or "").lower() in _NETWORK_RESOURCE_TYPES
    ]
    by_type: dict[str, int] = {}
    for item in network_resources:
        typ = str(item.get("type") or "unknown")
        by_type[typ] = by_type.get(typ, 0) + 1
    return _json({
        "subscriptionId": _validate_subscription_id(subscription_id),
        "resourceGroup": _validate_resource_group(resource_group) if resource_group else None,
        "summaryByType": by_type,
        "networkResources": network_resources[:500],
        "truncated": len(network_resources) > 500 or bool(raw.get("truncated")),
    })


@mcp.tool()
async def azure_diagnose_resource(resource_id: str, actions: list[str] | None = None) -> str:
    """Run a read-only Azure diagnostic bundle for a resource.

    Collects resource metadata, Resource Health, effective permissions,
    diagnostic settings, metric definitions, and recent Activity Log events.
    Individual probes are allowed to fail so the caller still receives a
    partial diagnostic report with concrete missing-permission evidence.
    """
    clean_id = _validate_resource_id(resource_id)
    subscription_id = _subscription_from_id(clean_id)
    if not subscription_id:
        raise ValueError("resource_id must include a valid subscription GUID")
    requested_actions = actions or [
        "*/read",
        "Microsoft.Authorization/roleAssignments/read",
        "Microsoft.Insights/diagnosticSettings/read",
        "Microsoft.Insights/metricDefinitions/read",
        "Microsoft.Insights/metrics/read",
    ]
    probes = [
        await _try("resource", _arm_get(clean_id, params={"api-version": RESOURCE_API_VERSION})),
        await _try("resourceHealth", _arm_get(
            f"{clean_id}/providers/Microsoft.ResourceHealth/availabilityStatuses/current",
            params={"api-version": RESOURCE_HEALTH_API_VERSION},
        )),
        await _try("permissions", _arm_get(
            f"{clean_id}/providers/Microsoft.Authorization/permissions",
            params={"api-version": AUTHORIZATION_API_VERSION},
        )),
        await _try("diagnosticSettings", _arm_get(
            f"{clean_id}/providers/microsoft.insights/diagnosticSettings",
            params={"api-version": DIAGNOSTIC_SETTINGS_API_VERSION},
        )),
        await _try("metricDefinitions", _arm_get(
            f"{clean_id}/providers/microsoft.insights/metricDefinitions",
            params={"api-version": METRICS_API_VERSION},
        )),
        await _try("activityLog", _arm_get(
            f"/subscriptions/{subscription_id}/providers/Microsoft.Insights/eventtypes/management/values",
            params={
                "api-version": ACTIVITY_LOG_API_VERSION,
                "$filter": " and ".join([
                    f"eventTimestamp ge '{(datetime.now(UTC) - timedelta(hours=24)).isoformat().replace('+00:00', 'Z')}'",
                    f"eventTimestamp le '{datetime.now(UTC).isoformat().replace('+00:00', 'Z')}'",
                    f"resourceUri eq '{clean_id}'",
                ]),
                "$select": "eventTimestamp,operationName,status,subStatus,resourceId,resourceGroupName,caller,correlationId,level,category,claims,submissionTimestamp",
            },
        )),
    ]
    diagnostics: dict[str, Any] = {probe["label"]: probe for probe in probes}
    permission_data = diagnostics.get("permissions", {}).get("data") if diagnostics.get("permissions", {}).get("ok") else None
    permission_sets = list((permission_data or {}).get("value", []))
    checks = {action: _is_action_allowed(permission_sets, action) for action in requested_actions[:50]}
    findings = []
    if diagnostics.get("resourceHealth", {}).get("ok"):
        health_props = (diagnostics["resourceHealth"].get("data") or {}).get("properties") or {}
        state = health_props.get("availabilityState")
        if state and state != "Available":
            findings.append({"severity": "warning", "category": "resourceHealth", "message": f"Resource Health reports {state}."})
    else:
        findings.append({"severity": "info", "category": "resourceHealth", "message": diagnostics.get("resourceHealth", {}).get("error")})
    if diagnostics.get("diagnosticSettings", {}).get("ok"):
        settings = (diagnostics["diagnosticSettings"].get("data") or {}).get("value") or []
        if not settings:
            findings.append({"severity": "warning", "category": "observability", "message": "No Azure Monitor diagnostic settings are configured for this resource."})
    if permission_data is not None and not checks.get("*/read", False):
        findings.append({"severity": "warning", "category": "permissions", "message": "Caller does not appear to have broad read permission at this resource scope."})
    return _json({
        "resourceId": clean_id,
        "subscriptionId": subscription_id,
        "permissionChecks": checks,
        "findings": findings,
        "diagnostics": diagnostics,
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


@mcp.tool()
async def entra_token_diagnostics(include_claims: bool = False) -> str:
    """Summarize available delegated tokens without exposing token values.

    Helps diagnose wrong audience, missing OBO token, app-only identity,
    expired token, or missing Microsoft Graph / Azure Management consent.
    """
    env_names = [
        "FABRIC_API_TOKEN",
        "POWERBI_API_TOKEN",
        "ONELAKE_TOKEN",
        "AZURE_MANAGEMENT_TOKEN",
        "GRAPH_API_TOKEN",
    ]
    summaries = []
    for env_name in env_names:
        token = os.environ.get(env_name, "")
        if token:
            summaries.append(_token_summary(env_name, token, include_claims=bool(include_claims)))
        else:
            summaries.append({"envName": env_name, "present": False})
    return _json({"tokens": summaries})


@mcp.tool()
async def entra_get_signed_in_user() -> str:
    """Get the Microsoft Graph / Entra profile for the delegated mission user."""
    data = await _graph_get(
        "/me",
        params={"$select": "id,displayName,userPrincipalName,mail,accountEnabled,userType"},
    )
    return _json({"user": _principal_summary(data), "raw": data})


@mcp.tool()
async def entra_get_user(user_id_or_upn: str) -> str:
    """Get an Entra user by object id or user principal name."""
    identifier = _validate_graph_identifier(user_id_or_upn)
    data = await _graph_get(
        f"/users/{_q(identifier)}",
        params={"$select": "id,displayName,userPrincipalName,mail,accountEnabled,userType"},
    )
    return _json({"user": _principal_summary(data), "raw": data})


@mcp.tool()
async def entra_get_service_principal(identifier: str) -> str:
    """Get an Entra service principal by object id, app id, or exact display name."""
    clean = _validate_graph_identifier(identifier)
    errors: list[str] = []
    if _GUID_RE.match(clean):
        try:
            data = await _graph_get(
                f"/servicePrincipals/{_q(clean)}",
                params={"$select": "id,displayName,appId,servicePrincipalType,accountEnabled,appOwnerOrganizationId"},
            )
            return _json({"servicePrincipal": _principal_summary(data), "lookup": "objectId", "raw": data})
        except Exception as exc:
            errors.append(str(exc))
        data = await _graph_get(
            "/servicePrincipals",
            params={
                "$filter": f"appId eq '{clean}'",
                "$select": "id,displayName,appId,servicePrincipalType,accountEnabled,appOwnerOrganizationId",
                "$top": "10",
            },
        )
        return _json({"servicePrincipals": [_principal_summary(item) for item in data.get("value", [])], "lookup": "appId", "errors": errors})

    safe = clean.replace("'", "''")
    data = await _graph_get(
        "/servicePrincipals",
        params={
            "$filter": f"displayName eq '{safe}'",
            "$select": "id,displayName,appId,servicePrincipalType,accountEnabled,appOwnerOrganizationId",
            "$top": "10",
        },
    )
    return _json({"servicePrincipals": [_principal_summary(item) for item in data.get("value", [])], "lookup": "displayName"})


@mcp.tool()
async def entra_diagnose_principal_access(
    identifier: str,
    principal_type: str = "auto",
    azure_scope: str | None = None,
) -> str:
    """Diagnose whether a user/service-principal owner can still access a mission target.

    This is the owner/permission triage tool agents should use when a Fabric
    or Power BI item may be broken because its owner left the org, was disabled,
    lost group membership, is the wrong app/service principal, or lacks Azure
    RBAC at a related scope. ``identifier`` may be an object id, app id, UPN,
    mail address, or exact display name. ``azure_scope`` is optional and, when
    provided, role assignments at that ARM scope are matched against the
    principal and its transitive group memberships.
    """
    clean = _validate_graph_search_text(identifier)
    requested_type = (principal_type or "auto").strip().lower()
    if requested_type not in {"auto", "user", "serviceprincipal", "service_principal", "sp"}:
        raise ValueError("principal_type must be auto, user, or servicePrincipal")
    safe = _odata_quote(clean)
    candidate_rows: list[dict[str, Any]] = []
    lookup_errors: list[dict[str, str]] = []

    async def _add_user_candidates() -> None:
        if " " not in clean and "/" not in clean:
            probe = await _try("user_by_id_or_upn", _graph_get(
                f"/users/{_q(clean)}",
                params={"$select": "id,displayName,userPrincipalName,mail,accountEnabled,userType"},
            ))
            if probe["ok"]:
                candidate_rows.append({"principalType": "User", **_principal_summary(probe["data"]), "raw": probe["data"]})
            else:
                lookup_errors.append({"label": probe["label"], "error": probe["error"]})
        probe = await _try("user_by_exact_fields", _graph_get(
            "/users",
            params={
                "$filter": f"userPrincipalName eq '{safe}' or mail eq '{safe}' or displayName eq '{safe}'",
                "$select": "id,displayName,userPrincipalName,mail,accountEnabled,userType",
                "$top": "10",
            },
        ))
        if probe["ok"]:
            for item in (probe["data"].get("value") or []):
                candidate_rows.append({"principalType": "User", **_principal_summary(item), "raw": item})
        else:
            lookup_errors.append({"label": probe["label"], "error": probe["error"]})

    async def _add_service_principal_candidates() -> None:
        if _GUID_RE.match(clean):
            probe = await _try("service_principal_by_object_id", _graph_get(
                f"/servicePrincipals/{_q(clean)}",
                params={"$select": "id,displayName,appId,servicePrincipalType,accountEnabled,appOwnerOrganizationId"},
            ))
            if probe["ok"]:
                candidate_rows.append({"principalType": "ServicePrincipal", **_principal_summary(probe["data"]), "raw": probe["data"]})
            else:
                lookup_errors.append({"label": probe["label"], "error": probe["error"]})
            probe = await _try("service_principal_by_app_id", _graph_get(
                "/servicePrincipals",
                params={
                    "$filter": f"appId eq '{safe}'",
                    "$select": "id,displayName,appId,servicePrincipalType,accountEnabled,appOwnerOrganizationId",
                    "$top": "10",
                },
            ))
            if probe["ok"]:
                for item in (probe["data"].get("value") or []):
                    candidate_rows.append({"principalType": "ServicePrincipal", **_principal_summary(item), "raw": item})
            else:
                lookup_errors.append({"label": probe["label"], "error": probe["error"]})
        probe = await _try("service_principal_by_display_name", _graph_get(
            "/servicePrincipals",
            params={
                "$filter": f"displayName eq '{safe}'",
                "$select": "id,displayName,appId,servicePrincipalType,accountEnabled,appOwnerOrganizationId",
                "$top": "10",
            },
        ))
        if probe["ok"]:
            for item in (probe["data"].get("value") or []):
                candidate_rows.append({"principalType": "ServicePrincipal", **_principal_summary(item), "raw": item})
        else:
            lookup_errors.append({"label": probe["label"], "error": probe["error"]})

    if requested_type in {"auto", "user"}:
        await _add_user_candidates()
    if requested_type in {"auto", "serviceprincipal", "service_principal", "sp"}:
        await _add_service_principal_candidates()

    candidates = _dedupe_principals(candidate_rows)
    findings: list[dict[str, Any]] = []
    if not candidates:
        findings.append({
            "severity": "error",
            "category": "ownerIdentity",
            "message": "No active Entra user or service principal matched the owner/effective-identity hint. The owner may have left the org, been deleted, or may only be visible with higher directory permissions.",
        })

    membership_by_principal: dict[str, dict[str, Any]] = {}
    app_roles_by_principal: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        principal_id = candidate.get("id")
        if not principal_id:
            continue
        if candidate.get("accountEnabled") is False:
            findings.append({
                "severity": "error",
                "category": "ownerIdentity",
                "principalId": principal_id,
                "message": "Matched principal exists but accountEnabled=false. Items owned by this identity can fail refresh, Direct Lake, or API calls until ownership/access is repaired.",
            })
        membership_probe = await _try("transitive_memberships", _graph_get(
            f"/directoryObjects/{_q(str(principal_id))}/transitiveMemberOf",
            params={"$select": "id,displayName,description", "$top": "100"},
        ))
        if membership_probe["ok"]:
            memberships = [
                {
                    "id": item.get("id"),
                    "displayName": item.get("displayName"),
                    "odataType": item.get("@odata.type"),
                }
                for item in (membership_probe["data"].get("value") or [])
            ]
            membership_by_principal[str(principal_id)] = {"status": "ok", "memberships": memberships, "truncated": bool(membership_probe["data"].get("@odata.nextLink"))}
        else:
            membership_by_principal[str(principal_id)] = {"status": "unavailable", "error": membership_probe["error"]}

        collection = "servicePrincipals" if candidate.get("principalType") == "ServicePrincipal" else "users"
        app_role_probe = await _try("app_role_assignments", _graph_get(
            f"/{collection}/{_q(str(principal_id))}/appRoleAssignments",
            params={"$top": "100"},
        ))
        if app_role_probe["ok"]:
            app_roles_by_principal[str(principal_id)] = {
                "status": "ok",
                "appRoleAssignments": [
                    {
                        "resourceDisplayName": item.get("resourceDisplayName"),
                        "resourceId": item.get("resourceId"),
                        "appRoleId": item.get("appRoleId"),
                    }
                    for item in (app_role_probe["data"].get("value") or [])
                ],
                "truncated": bool(app_role_probe["data"].get("@odata.nextLink")),
            }
        else:
            app_roles_by_principal[str(principal_id)] = {"status": "unavailable", "error": app_role_probe["error"]}

    azure_rbac: dict[str, Any] | None = None
    if azure_scope:
        clean_scope = _validate_scope(azure_scope)
        assignments_probe = await _try("azure_role_assignments", _arm_get(
            f"{clean_scope}/providers/Microsoft.Authorization/roleAssignments",
            params={"api-version": AUTHORIZATION_API_VERSION},
        ))
        if assignments_probe["ok"]:
            candidate_ids = {str(candidate.get("id")) for candidate in candidates if candidate.get("id")}
            membership_ids = {
                str(membership.get("id"))
                for data in membership_by_principal.values()
                for membership in data.get("memberships", [])
                if membership.get("id")
            }
            effective_ids = candidate_ids | membership_ids
            matches = []
            for assignment in assignments_probe["data"].get("value", []):
                summary = _role_assignment_summary(assignment)
                if str(summary.get("principalId")) in effective_ids:
                    matches.append(summary)
            azure_rbac = {"status": "ok", "scope": clean_scope, "matchingAssignments": matches, "checkedPrincipalAndGroupIds": sorted(effective_ids)}
            if candidates and not matches:
                findings.append({
                    "severity": "warning",
                    "category": "azureRbac",
                    "message": "No Azure RBAC role assignment at the requested scope matched the principal or its discovered groups. If this scope backs the Fabric capacity or dependent Azure resource, permission may be the blocker.",
                })
        else:
            azure_rbac = {"status": "unavailable", "scope": clean_scope, "error": assignments_probe["error"]}

    return _json({
        "identifier": clean,
        "principalTypeRequested": requested_type,
        "candidates": candidates,
        "memberships": membership_by_principal,
        "appRoleAssignments": app_roles_by_principal,
        "azureRbac": azure_rbac,
        "findings": findings,
        "lookupErrors": lookup_errors[:10],
        "recommendedNextChecks": [
            "Compare the candidate id/displayName to Fabric item createdBy/owner and workspaceRoles from fabric_diagnose_workspace_artifacts.",
            "If the owner is disabled, deleted, or app-only, transfer ownership or recreate the artifact under the delegated mission user before retrying refresh/render/write operations.",
            "If Azure RBAC is missing at a capacity/resource scope, grant the least-privilege role to the user or a stable group rather than relying on a departed owner.",
            "If Graph membership lookup is unavailable, request Directory.Read.All/User.Read.All consent or ask a tenant admin to confirm group membership.",
        ],
    })


@mcp.tool()
async def entra_list_group_memberships(principal_id: str, transitive: bool = True) -> str:
    """List direct or transitive Entra group/directory-role memberships for a principal."""
    principal = _validate_graph_identifier(principal_id)
    relation = "transitiveMemberOf" if transitive else "memberOf"
    data = await _graph_get(
        f"/directoryObjects/{_q(principal)}/{relation}",
        params={"$select": "id,displayName,description", "$top": "100"},
    )
    memberships = [
        {
            "id": item.get("id"),
            "displayName": item.get("displayName"),
            "description": item.get("description"),
            "odataType": item.get("@odata.type"),
        }
        for item in data.get("value", [])
    ]
    return _json({"principalId": principal, "transitive": transitive, "memberships": memberships, "truncated": bool(data.get("@odata.nextLink"))})


@mcp.tool()
async def entra_list_app_role_assignments(principal_id: str, principal_type: str = "servicePrincipal") -> str:
    """List Graph app-role assignments for a user, group, or service principal."""
    principal = _validate_graph_identifier(principal_id)
    normalized_type = (principal_type or "servicePrincipal").strip().lower()
    path_by_type = {
        "serviceprincipal": "servicePrincipals",
        "serviceprincipals": "servicePrincipals",
        "user": "users",
        "users": "users",
        "group": "groups",
        "groups": "groups",
    }
    collection = path_by_type.get(normalized_type)
    if not collection:
        raise ValueError("principal_type must be servicePrincipal, user, or group")
    data = await _graph_get(
        f"/{collection}/{_q(principal)}/appRoleAssignments",
        params={"$top": "100"},
    )
    assignments = [
        {
            "id": item.get("id"),
            "resourceDisplayName": item.get("resourceDisplayName"),
            "resourceId": item.get("resourceId"),
            "appRoleId": item.get("appRoleId"),
            "principalDisplayName": item.get("principalDisplayName"),
            "principalId": item.get("principalId"),
            "principalType": item.get("principalType"),
        }
        for item in data.get("value", [])
    ]
    return _json({"principalId": principal, "principalType": collection, "appRoleAssignments": assignments, "truncated": bool(data.get("@odata.nextLink"))})


if __name__ == "__main__":
    mcp.run()
