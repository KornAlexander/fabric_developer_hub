"""Unit tests for the first-party Azure Management MCP server."""
from __future__ import annotations

import json
import base64

import pytest

from mcp_servers import azure_management as azm


def test_headers_requires_azure_management_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_MANAGEMENT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="AZURE_MANAGEMENT_TOKEN not set"):
        azm._headers()


def test_headers_uses_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_MANAGEMENT_TOKEN", "arm-token")

    assert azm._headers()["Authorization"] == "Bearer arm-token"


def test_graph_headers_requires_graph_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPH_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="GRAPH_API_TOKEN not set"):
        azm._graph_headers()


def test_graph_headers_uses_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_API_TOKEN", "graph-token")

    assert azm._graph_headers()["Authorization"] == "Bearer graph-token"


def _fake_jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).rstrip(b"=").decode("ascii")
    return f"header.{payload}.signature"


def test_validate_subscription_id_rejects_non_guid() -> None:
    with pytest.raises(ValueError, match="subscription_id"):
        azm._validate_subscription_id("not-a-subscription")


def test_validate_scope_requires_subscription_scope() -> None:
    with pytest.raises(ValueError, match="scope"):
        azm._validate_scope("/tenants/abc")


def test_validate_resource_id_requires_provider_path() -> None:
    with pytest.raises(ValueError, match="resource_id"):
        azm._validate_resource_id("/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-one")


def test_permission_action_matching_honors_not_actions() -> None:
    permissions = [{
        "actions": ["Microsoft.Fabric/capacities/*"],
        "notActions": ["Microsoft.Fabric/capacities/delete"],
    }]

    assert azm._is_action_allowed(permissions, "Microsoft.Fabric/capacities/resume/action") is True
    assert azm._is_action_allowed(permissions, "Microsoft.Fabric/capacities/delete") is False


@pytest.mark.asyncio
async def test_get_resource_uses_generic_resource_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def fake_get(path: str, *, params: dict[str, str] | None = None) -> dict:
        calls.append((path, params))
        return {
            "id": path,
            "name": "cap-one",
            "type": "Microsoft.Fabric/capacities",
            "location": "westeurope",
            "properties": {"provisioningState": "Succeeded", "state": "Paused"},
        }

    monkeypatch.setattr(azm, "_arm_get", fake_get)

    resource_id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-one/providers/Microsoft.Fabric/capacities/cap-one"
    raw = await azm.azure_get_resource(resource_id)
    body = json.loads(raw)

    assert calls == [(resource_id, {"api-version": "2021-04-01"})]
    assert body["resource"]["state"] == "Paused"


@pytest.mark.asyncio
async def test_list_role_assignments_summarizes_principals(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(path: str, *, params: dict[str, str] | None = None) -> dict:
        assert path.endswith("/providers/Microsoft.Authorization/roleAssignments")
        assert params == {"api-version": "2022-04-01"}
        return {"value": [{"id": "ra-id", "name": "ra-name", "properties": {
            "scope": "/subscriptions/11111111-1111-1111-1111-111111111111",
            "roleDefinitionId": "role-id",
            "principalId": "principal-id",
            "principalType": "User",
        }}]}

    monkeypatch.setattr(azm, "_arm_get", fake_get)

    raw = await azm.azure_list_role_assignments("/subscriptions/11111111-1111-1111-1111-111111111111")
    body = json.loads(raw)

    assert body["roleAssignments"][0]["principalId"] == "principal-id"


@pytest.mark.asyncio
async def test_list_role_definitions_filters_by_role_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def fake_get(path: str, *, params: dict[str, str] | None = None) -> dict:
        calls.append((path, params))
        return {"value": [{"id": "role-id", "name": "guid", "properties": {
            "roleName": "Reader",
            "type": "BuiltInRole",
            "permissions": [{"actions": ["*/read"], "notActions": []}],
        }}]}

    monkeypatch.setattr(azm, "_arm_get", fake_get)

    raw = await azm.azure_list_role_definitions(
        "/subscriptions/11111111-1111-1111-1111-111111111111",
        "Reader",
    )
    body = json.loads(raw)

    assert calls[0][1] == {"api-version": "2022-04-01", "$filter": "roleName eq 'Reader'"}
    assert body["roleDefinitions"][0]["roleName"] == "Reader"


@pytest.mark.asyncio
async def test_get_activity_log_builds_recent_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def fake_get(path: str, *, params: dict[str, str] | None = None) -> dict:
        calls.append((path, params))
        return {"value": [{
            "eventTimestamp": "2026-04-26T12:00:00Z",
            "operationName": {"localizedValue": "Resume capacity"},
            "status": {"localizedValue": "Succeeded"},
            "resourceId": "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-one/providers/Microsoft.Fabric/capacities/cap-one",
            "resourceGroupName": "rg-one",
        }]}

    monkeypatch.setattr(azm, "_arm_get", fake_get)

    raw = await azm.azure_get_activity_log(
        "11111111-1111-1111-1111-111111111111",
        resource_group="rg-one",
        hours=1,
    )
    body = json.loads(raw)

    assert calls[0][0].endswith("/providers/Microsoft.Insights/eventtypes/management/values")
    assert "resourceGroupName eq 'rg-one'" in calls[0][1]["$filter"]
    assert body["events"][0]["operationName"] == "Resume capacity"


@pytest.mark.asyncio
async def test_get_resource_health_reads_current_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def fake_get(path: str, *, params: dict[str, str] | None = None) -> dict:
        calls.append((path, params))
        return {"id": "health-id", "name": "current", "type": "availabilityStatuses", "properties": {
            "availabilityState": "Available",
            "summary": "Healthy",
        }}

    monkeypatch.setattr(azm, "_arm_get", fake_get)

    resource_id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-one/providers/Microsoft.Fabric/capacities/cap-one"
    raw = await azm.azure_get_resource_health(resource_id)
    body = json.loads(raw)

    assert calls == [(
        f"{resource_id}/providers/Microsoft.ResourceHealth/availabilityStatuses/current",
        {"api-version": "2022-10-01"},
    )]
    assert body["availabilityState"] == "Available"


@pytest.mark.asyncio
async def test_list_diagnostic_settings_summarizes_destinations(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def fake_get(path: str, *, params: dict[str, str] | None = None) -> dict:
        calls.append((path, params))
        return {"value": [{"id": "diag-id", "name": "send-to-law", "properties": {
            "workspaceId": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law",
            "logs": [{"category": "Audit", "enabled": True}],
            "metrics": [{"category": "AllMetrics", "enabled": True}],
        }}]}

    monkeypatch.setattr(azm, "_arm_get", fake_get)

    resource_id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-one/providers/Microsoft.Fabric/capacities/cap-one"
    raw = await azm.azure_list_diagnostic_settings(resource_id)
    body = json.loads(raw)

    assert calls == [(f"{resource_id}/providers/microsoft.insights/diagnosticSettings", {"api-version": "2021-05-01-preview"})]
    assert body["diagnosticSettings"][0]["workspaceId"].endswith("/workspaces/law")
    assert body["diagnosticSettings"][0]["logs"] == [{"category": "Audit", "enabled": True}]


@pytest.mark.asyncio
async def test_network_inventory_filters_network_resource_types(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list_resources(subscription_id: str, resource_group: str | None = None) -> str:
        return json.dumps({
            "resources": [
                {"name": "vnet-a", "type": "Microsoft.Network/virtualNetworks"},
                {"name": "nsg-a", "type": "Microsoft.Network/networkSecurityGroups"},
                {"name": "vm-a", "type": "Microsoft.Compute/virtualMachines"},
            ],
            "truncated": False,
        })

    monkeypatch.setattr(azm, "azure_list_resources", fake_list_resources)

    raw = await azm.azure_network_inventory("11111111-1111-1111-1111-111111111111")
    body = json.loads(raw)

    assert [item["name"] for item in body["networkResources"]] == ["vnet-a", "nsg-a"]
    assert body["summaryByType"] == {
        "Microsoft.Network/virtualNetworks": 1,
        "Microsoft.Network/networkSecurityGroups": 1,
    }


@pytest.mark.asyncio
async def test_diagnose_resource_returns_partial_probe_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    resource_id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-one/providers/Microsoft.Fabric/capacities/cap-one"

    async def fake_get(path: str, *, params: dict[str, str] | None = None) -> dict:
        if path == resource_id:
            return {"id": path, "name": "cap-one", "type": "Microsoft.Fabric/capacities", "properties": {"state": "Active"}}
        if "availabilityStatuses" in path:
            return {"properties": {"availabilityState": "Unavailable"}}
        if path.endswith("/providers/Microsoft.Authorization/permissions"):
            return {"value": [{"actions": ["Microsoft.Insights/*/read"], "notActions": []}]}
        if path.endswith("/providers/microsoft.insights/diagnosticSettings"):
            return {"value": []}
        if path.endswith("/providers/microsoft.insights/metricDefinitions"):
            raise RuntimeError("metric definitions denied")
        if "eventtypes/management/values" in path:
            return {"value": []}
        raise AssertionError(path)

    monkeypatch.setattr(azm, "_arm_get", fake_get)

    raw = await azm.azure_diagnose_resource(resource_id)
    body = json.loads(raw)

    assert body["diagnostics"]["metricDefinitions"]["ok"] is False
    assert body["permissionChecks"]["Microsoft.Insights/metrics/read"] is True
    assert {finding["category"] for finding in body["findings"]} >= {"resourceHealth", "observability", "permissions"}


@pytest.mark.asyncio
async def test_list_fabric_capacities_filters_resource_type(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list_resources(subscription_id: str, resource_group: str | None = None) -> str:
        assert subscription_id == "11111111-1111-1111-1111-111111111111"
        assert resource_group == "rg-one"
        return json.dumps({
            "resources": [
                {"name": "cap-a", "type": "Microsoft.Fabric/capacities"},
                {"name": "vm-a", "type": "Microsoft.Compute/virtualMachines"},
            ],
            "truncated": False,
        })

    monkeypatch.setattr(azm, "azure_list_resources", fake_list_resources)

    raw = await azm.azure_list_fabric_capacities(
        "11111111-1111-1111-1111-111111111111",
        "rg-one",
    )
    body = json.loads(raw)

    assert body["capacities"] == [{"name": "cap-a", "type": "Microsoft.Fabric/capacities"}]


@pytest.mark.asyncio
async def test_resume_fabric_capacity_uses_resume_action(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def fake_post(path: str, *, params: dict[str, str] | None = None) -> dict:
        calls.append((path, params))
        return {"statusCode": 202}

    monkeypatch.setattr(azm, "_arm_post", fake_post)

    raw = await azm.azure_resume_fabric_capacity(
        "11111111-1111-1111-1111-111111111111",
        "rg-one",
        "cap-one",
    )
    body = json.loads(raw)

    assert calls == [(
        "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-one/providers/Microsoft.Fabric/capacities/cap-one/resume",
        {"api-version": "2023-11-01"},
    )]
    assert body["statusCode"] == 202
    assert body["capacityResourceId"].endswith("/Microsoft.Fabric/capacities/cap-one")


@pytest.mark.asyncio
async def test_entra_token_diagnostics_summarizes_claims_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FABRIC_API_TOKEN", _fake_jwt({
        "aud": "https://api.fabric.microsoft.com",
        "tid": "tenant-id",
        "oid": "user-id",
        "preferred_username": "user@example.com",
        "scp": "Workspace.ReadWrite.All Item.ReadWrite.All",
        "exp": 1893456000,
    }))
    monkeypatch.delenv("GRAPH_API_TOKEN", raising=False)

    raw = await azm.entra_token_diagnostics()
    body = json.loads(raw)
    fabric = next(item for item in body["tokens"] if item["envName"] == "FABRIC_API_TOKEN")
    graph = next(item for item in body["tokens"] if item["envName"] == "GRAPH_API_TOKEN")

    assert fabric["present"] is True
    assert fabric["identityType"] == "delegated"
    assert fabric["userPrincipalName"] == "user@example.com"
    assert "claims" not in fabric
    assert graph == {"envName": "GRAPH_API_TOKEN", "present": False}


@pytest.mark.asyncio
async def test_entra_get_signed_in_user_uses_graph_me(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    async def fake_graph_get(path: str, *, params: dict[str, str] | None = None) -> dict:
        calls.append((path, params))
        return {"id": "user-id", "displayName": "Ada", "userPrincipalName": "ada@example.com"}

    monkeypatch.setattr(azm, "_graph_get", fake_graph_get)

    raw = await azm.entra_get_signed_in_user()
    body = json.loads(raw)

    assert calls == [("/me", {"$select": "id,displayName,userPrincipalName,mail,accountEnabled,userType"})]
    assert body["user"]["userPrincipalName"] == "ada@example.com"


@pytest.mark.asyncio
async def test_entra_diagnose_principal_access_flags_disabled_owner_and_missing_rbac(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_graph_get(path: str, *, params: dict[str, str] | None = None) -> dict:
        if path == "/users/owner%40example.com":
            return {
                "id": "owner-id",
                "displayName": "Departed Owner",
                "userPrincipalName": "owner@example.com",
                "accountEnabled": False,
                "userType": "Member",
            }
        if path == "/users":
            return {"value": []}
        if path == "/directoryObjects/owner-id/transitiveMemberOf":
            return {"value": [{"id": "group-id", "displayName": "Fabric Contributors", "@odata.type": "#microsoft.graph.group"}]}
        if path == "/users/owner-id/appRoleAssignments":
            return {"value": []}
        raise RuntimeError(f"unexpected Graph path {path}")

    async def fake_arm_get(path: str, *, params: dict[str, str] | None = None) -> dict:
        assert path.endswith("/providers/Microsoft.Authorization/roleAssignments")
        return {"value": [{"id": "ra-id", "properties": {"principalId": "other-id", "scope": path.removesuffix("/providers/Microsoft.Authorization/roleAssignments")}}]}

    monkeypatch.setattr(azm, "_graph_get", fake_graph_get)
    monkeypatch.setattr(azm, "_arm_get", fake_arm_get)

    raw = await azm.entra_diagnose_principal_access(
        "owner@example.com",
        principal_type="user",
        azure_scope="/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-one",
    )
    body = json.loads(raw)

    assert body["candidates"][0]["accountEnabled"] is False
    assert body["memberships"]["owner-id"]["memberships"][0]["id"] == "group-id"
    assert body["azureRbac"]["matchingAssignments"] == []
    assert {finding["category"] for finding in body["findings"]} >= {"ownerIdentity", "azureRbac"}
