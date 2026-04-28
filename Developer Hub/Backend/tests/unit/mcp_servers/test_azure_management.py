"""Unit tests for the first-party Azure Management MCP server."""
from __future__ import annotations

import json

import pytest

from mcp_servers import azure_management as azm


def test_headers_requires_azure_management_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_MANAGEMENT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="AZURE_MANAGEMENT_TOKEN not set"):
        azm._headers()


def test_headers_uses_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_MANAGEMENT_TOKEN", "arm-token")

    assert azm._headers()["Authorization"] == "Bearer arm-token"


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
