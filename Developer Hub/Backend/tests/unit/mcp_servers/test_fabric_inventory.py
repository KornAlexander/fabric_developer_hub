import base64
import json

import pytest

from mcp_servers import fabric as fabric_module
from mcp_servers.fabric import (
    _create_or_reuse_inventory_item,
    fabric_create_workspace_inventory_solution,
    fabric_verify_workspace_inventory_solution,
    _inventory_artifact_base,
    _inventory_item_base,
    _inventory_notebook_definition,
    _inventory_rows_m,
    _refresh_semantic_model,
    _report_definition,
    _run_inventory_notebook,
    _semantic_model_definition,
    _wait_for_lakehouse_tables,
    _wait_for_inventory_model_data,
)

from .conftest import FakeAsyncClient, install_fake_client, make_response


def test_inventory_artifact_base_is_short_and_alphanumeric() -> None:
    assert _inventory_artifact_base("tmp_20260426183320") == "Inv26183320"


def test_inventory_artifact_base_handles_non_timestamp_folder() -> None:
    assert _inventory_artifact_base("tmp_run folder!") == "Invrunfolder"


def test_inventory_item_base_includes_folder_id_suffix() -> None:
    assert _inventory_item_base("tmp_20260426183320", "abc-123-def") == "Inv26183320abc123"


def test_inventory_rows_include_workspace_metadata_and_item_data() -> None:
    expression = _inventory_rows_m([
        {
            "workspaceName": "Finance Workspace",
            "workspaceId": "workspace-1",
            "displayName": "Executive Report",
            "type": "Report",
            "id": "report-1",
            "folderId": "folder-1",
            "webUrl": "https://app.powerbi.com/groups/workspace-1/reports/report-1",
        }
    ])

    assert "WorkspaceName = text" in expression
    assert "WorkspaceId = text" in expression
    assert '"Finance Workspace", "workspace-1", "Executive Report", "Report"' in expression


def test_semantic_model_definition_contains_queryable_inventory_table() -> None:
    definition = _semantic_model_definition([
        {"workspaceName": "Workspace", "workspaceId": "workspace-1", "displayName": "Lake", "type": "Lakehouse", "id": "item-1"}
    ])
    model_part = next(part for part in definition["parts"] if part["path"] == "model.bim")
    model_bim = json.loads(base64.b64decode(model_part["payload"]).decode("utf-8"))
    table = model_bim["model"]["tables"][0]

    assert table["name"] == "FabricItems"
    assert {column["name"] for column in table["columns"]} >= {"WorkspaceName", "WorkspaceId", "ItemName", "ItemType"}
    assert table["measures"][0]["expression"] == "COUNTROWS('FabricItems')"
    partition = table["partitions"][0]
    assert partition["source"]["type"] == "calculated"
    expression = partition["source"]["expression"]
    if isinstance(expression, list):
        expression = "\n".join(expression)
    assert "DATATABLE" in expression
    assert "Lakehouse" in expression


def test_inventory_notebook_definition_contains_executable_ingestion_code() -> None:
    definition = _inventory_notebook_definition(
        workspace_id="workspace-1",
        lakehouse_id="lakehouse-1",
        lakehouse_name="InventoryLakehouse",
        table_name="Inv_FabricItems",
        summary_table_name="Inv_FabricItemsByType",
    )
    notebook_part = next(part for part in definition["parts"] if part["path"] == "notebook-content.ipynb")
    notebook = json.loads(base64.b64decode(notebook_part["payload"]).decode("utf-8"))
    code = "".join(notebook["cells"][0]["source"])

    assert definition["format"] == "ipynb"
    assert notebook["metadata"]["dependencies"]["lakehouse"]["default_lakehouse"] == "lakehouse-1"
    assert notebook["cells"][0]["cell_type"] == "code"
    assert "utils.credentials.getToken(\"pbi\")" in code
    assert "requests.get(next_url" in code
    assert "saveAsTable(TABLE_NAME)" in code
    assert "saveAsTable(SUMMARY_TABLE_NAME)" in code
    assert "exit_fn(json.dumps(result))" in code


def test_report_definition_binds_to_semantic_model_and_inventory_visual() -> None:
    definition = _report_definition("semantic-model-1")
    definition_pbir = next(part for part in definition["parts"] if part["path"] == "definition.pbir")
    visual_part = next(part for part in definition["parts"] if part["path"].endswith("/visual.json"))
    pbir = json.loads(base64.b64decode(definition_pbir["payload"]).decode("utf-8"))
    visual = json.loads(base64.b64decode(visual_part["payload"]).decode("utf-8"))

    assert definition["format"] == "PBIR"
    assert pbir["datasetReference"]["byConnection"]["connectionString"] == "semanticmodelid=semantic-model-1"
    assert visual["visual"]["visualType"] == "barChart"
    visual_payload = json.dumps(visual)
    assert "FabricItems" in visual_payload
    assert "ItemType" in visual_payload
    assert "Item Count" in visual_payload


@pytest.mark.asyncio
async def test_create_or_reuse_inventory_item_reuses_conflict_when_folder_id_is_omitted(fabric_token_env) -> None:
    fake = FakeAsyncClient(
        responses_by_method={
            "POST": [
                make_response(
                    409,
                    text='{"errorCode":"ItemDisplayNameAlreadyInUse","message":"item already exists"}',
                )
            ],
            "GET": [
                make_response(
                    200,
                    json_body={
                        "value": [
                            {
                                "id": "notebook-1",
                                "displayName": "Inv26183320abc123NB",
                                "type": "Notebook",
                            }
                        ]
                    },
                    text="{}",
                )
            ],
        }
    )

    result = await _create_or_reuse_inventory_item(
        fake,
        "workspace-1",
        {"Authorization": "Bearer fake-fabric-token"},
        display_name="Inv26183320abc123NB",
        item_type="Notebook",
        description="Inventory notebook",
        folder_id="abc-123-def",
        op_name="creating inventory notebook",
    )

    assert isinstance(result, dict)
    assert result["id"] == "notebook-1"
    assert result["folderId"] == "abc-123-def"
    assert fake.calls[0][0] == "POST"
    assert fake.calls[1][0] == "GET"


@pytest.mark.asyncio
async def test_run_inventory_notebook_triggers_and_polls_job(monkeypatch, fabric_token_env) -> None:
    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"value": []}, text="{}"),
                make_response(
                    200,
                    json_body={
                        "id": "job-1",
                        "itemId": "notebook-1",
                        "jobType": "RunNotebook",
                        "status": "Completed",
                        "exitValue": '{"rowCount": 12}',
                    },
                    text="{}",
                ),
            ],
            "POST": [
                make_response(
                    202,
                    headers={
                        "Location": "https://api.fabric.microsoft.com/v1/workspaces/workspace-1/items/notebook-1/jobs/instances/job-1",
                        "Retry-After": "5",
                    },
                )
            ],
        }
    )

    result = await _run_inventory_notebook(
        fake,
        "workspace-1",
        "notebook-1",
        {"Authorization": "Bearer fake-fabric-token"},
    )

    assert isinstance(result, dict)
    assert result["status"] == "Completed"
    assert fake.calls[0][0] == "GET"
    assert fake.calls[1][0] == "POST"
    assert fake.calls[1][1].endswith("/jobs/RunNotebook/instances")
    assert fake.calls[2][0] == "GET"


@pytest.mark.asyncio
async def test_refresh_semantic_model_uses_powerbi_api_token(fabric_token_env) -> None:
    fake = FakeAsyncClient(
        responses_by_method={
            "POST": [make_response(202)],
        }
    )

    result = await _refresh_semantic_model(
        fake,
        "workspace-1",
        "semantic-model-1",
        {"Authorization": "Bearer fake-fabric-token", "Content-Type": "application/json"},
    )

    assert result == {"status": "refresh_triggered", "via": "powerbi_v1:POWERBI_API_TOKEN"}
    assert fake.calls[0][1].endswith("/datasets/semantic-model-1/refreshes")
    assert fake.calls[0][2]["headers"]["Authorization"] == "Bearer fake-powerbi-token"


@pytest.mark.asyncio
async def test_wait_for_inventory_model_data_falls_back_to_fabric_token(monkeypatch, fabric_token_env) -> None:
    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)
    fake = FakeAsyncClient(
        responses_by_method={
            "POST": [
                make_response(401),
                make_response(
                    200,
                    json_body={"results": [{"tables": [{"rows": [{"[ItemCount]": 3}]}]}]},
                    text="{}",
                ),
                make_response(401),
                make_response(
                    200,
                    json_body={"results": [{"tables": [{"rows": [{"FabricItems[ItemType]": "Report"}]}]}]},
                    text="{}",
                ),
            ]
        }
    )

    result = await _wait_for_inventory_model_data(
        fake,
        "workspace-1",
        "semantic-model-1",
        expected_min_rows=3,
    )

    assert isinstance(result, dict)
    assert result["status"] == "queryable"
    assert fake.calls[0][2]["headers"]["Authorization"] == "Bearer fake-powerbi-token"
    assert fake.calls[1][2]["headers"]["Authorization"] == "Bearer fake-fabric-token"


@pytest.mark.asyncio
async def test_wait_for_lakehouse_tables_requires_expected_tables(monkeypatch, fabric_token_env) -> None:
    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"data": [{"name": "Other"}]}, text="{}"),
                make_response(
                    200,
                    json_body={"data": [{"name": "Inventory"}, {"name": "Summary", "rowCount": 4}]},
                    text="{}",
                ),
            ]
        }
    )

    result = await _wait_for_lakehouse_tables(
        fake,
        "workspace-1",
        "lakehouse-1",
        {"Authorization": "Bearer fake-fabric-token"},
        {"Inventory", "Summary"},
    )

    assert result == {
        "status": "tables_found",
        "via": "fabric_lakehouse_tables_api",
        "tables": ["Inventory", "Summary"],
        "rowCounts": {"Summary": 4},
    }


@pytest.mark.asyncio
async def test_wait_for_lakehouse_tables_matches_fabric_lowercase_names(monkeypatch, fabric_token_env) -> None:
    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(
                    200,
                    json_body={
                        "data": [
                            {"name": "inv_fabricitems", "rowCount": 3},
                            {"name": "inv_fabricitemsbytype", "rowCount": 2},
                        ]
                    },
                    text="{}",
                ),
            ]
        }
    )

    result = await _wait_for_lakehouse_tables(
        fake,
        "workspace-1",
        "lakehouse-1",
        {"Authorization": "Bearer fake-fabric-token"},
        {"Inv_FabricItems", "Inv_FabricItemsByType"},
    )

    assert result == {
        "status": "tables_found",
        "via": "fabric_lakehouse_tables_api",
        "tables": ["inv_fabricitems", "inv_fabricitemsbytype"],
        "rowCounts": {"inv_fabricitems": 3, "inv_fabricitemsbytype": 2},
    }


@pytest.mark.asyncio
async def test_wait_for_inventory_model_data_uses_execute_queries_without_fallback(monkeypatch, fabric_token_env) -> None:
    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)
    fake = FakeAsyncClient(
        responses_by_method={
            "POST": [
                make_response(
                    200,
                    json_body={
                        "results": [
                            {"tables": [{"rows": [{"[ItemCount]": 3}]}]}
                        ]
                    },
                    text="{}",
                ),
                make_response(
                    200,
                    json_body={
                        "results": [
                            {"tables": [{"rows": [{"FabricItems[ItemType]": "Report"}, {"FabricItems[ItemType]": "Lakehouse"}]}]}
                        ]
                    },
                    text="{}",
                ),
            ]
        }
    )

    result = await _wait_for_inventory_model_data(
        fake,
        "workspace-1",
        "semantic-model-1",
        expected_min_rows=3,
    )

    assert result == {
        "status": "queryable",
        "via": "powerbi_executeQueries",
        "rowCount": 3,
        "itemTypes": ["Report", "Lakehouse"],
    }
    assert all(call[1].endswith("/datasets/semantic-model-1/executeQueries") for call in fake.calls)
    assert all(call[2]["headers"]["Authorization"] == "Bearer fake-powerbi-token" for call in fake.calls)


@pytest.mark.asyncio
async def test_inventory_solution_handles_model_validation_failure_without_unbound_local(monkeypatch, fabric_token_env) -> None:
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"id": "workspace-1", "capacityId": "capacity-1"}, text="{}"),
                make_response(200, json_body={"value": [{"id": "capacity-1", "state": "Active"}]}, text="{}"),
                make_response(200, json_body={"value": []}, text="{}"),
            ],
            "POST": [make_response(201, json_body={"id": "folder-1", "displayName": "tmp_run"}, text="{}")],
        }
    )
    install_fake_client(monkeypatch, fabric_module, fake)

    async def collect_items(_client, workspace_id: str, _headers: dict):
        return [
            {
                "workspaceName": "Workspace",
                "workspaceId": workspace_id,
                "displayName": "Report",
                "type": "Report",
                "id": "report-1",
            }
        ], [], 1

    async def create_item(_client, _workspace_id: str, _headers: dict, *, display_name: str, item_type: str, folder_id: str, **_kwargs):
        return {"id": f"{item_type.lower()}-1", "displayName": display_name, "type": item_type, "folderId": folder_id}

    async def ok_definition(*_args, **_kwargs):
        return {"status": "accepted"}

    async def ok_notebook_run(*_args, **_kwargs):
        return {"id": "job-1", "status": "Completed", "exitValue": '{"rowCount":1}'}

    async def ok_lakehouse_tables(*_args, **_kwargs):
        return {"status": "tables_found", "via": "fabric_lakehouse_tables_api", "tables": ["Inventory", "Summary"]}

    async def ok_refresh(*_args, **_kwargs):
        return {"status": "refresh_triggered", "via": "powerbi_v1"}

    async def model_not_ready(*_args, **_kwargs):
        return "Power BI executeQueries returned 0 inventory rows."

    monkeypatch.setattr(fabric_module, "_collect_accessible_inventory_items", collect_items)
    monkeypatch.setattr(fabric_module, "_create_or_reuse_inventory_item", create_item)
    monkeypatch.setattr(fabric_module, "_update_notebook_definition", ok_definition)
    monkeypatch.setattr(fabric_module, "_run_inventory_notebook", ok_notebook_run)
    monkeypatch.setattr(fabric_module, "_wait_for_lakehouse_tables", ok_lakehouse_tables)
    monkeypatch.setattr(fabric_module, "_refresh_semantic_model", ok_refresh)
    monkeypatch.setattr(fabric_module, "_wait_for_inventory_model_data", model_not_ready)

    result = await fabric_create_workspace_inventory_solution("workspace-1", "tmp_run")

    assert result.startswith("Error creating inventory solution: ")
    body = json.loads(result.removeprefix("Error creating inventory solution: "))
    assert body["status"] == "partial"
    assert body["semanticModelDataValidation"] is None
    assert any("Power BI executeQueries" in error for error in body["errors"])


@pytest.mark.asyncio
async def test_inventory_solution_blocks_when_capacity_is_inactive(monkeypatch, fabric_token_env) -> None:
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"id": "workspace-1", "capacityId": "capacity-1"}, text="{}"),
                make_response(
                    200,
                    json_body={"value": [{"id": "capacity-1", "displayName": "Paused Capacity", "state": "Inactive"}]},
                    text="{}",
                ),
            ]
        }
    )
    install_fake_client(monkeypatch, fabric_module, fake)

    result = await fabric_create_workspace_inventory_solution("workspace-1", "tmp_run")

    assert result.startswith("Error creating inventory solution: ")
    body = json.loads(result.removeprefix("Error creating inventory solution: "))
    assert body["status"] == "blocked"
    assert body["createdItems"] == []
    assert any("not Active" in error for error in body["errors"])
    assert not any(call[0] == "POST" for call in fake.calls)


@pytest.mark.asyncio
async def test_inventory_solution_deletes_report_when_render_validation_fails(monkeypatch, fabric_token_env) -> None:
    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"id": "workspace-1", "capacityId": "capacity-1"}, text="{}"),
                make_response(200, json_body={"value": [{"id": "capacity-1", "state": "Active"}]}, text="{}"),
                make_response(200, json_body={"value": []}, text="{}"),
                make_response(200, json_body={"id": "report-1", "datasetId": "semanticmodel-1"}, text="{}"),
                make_response(200, json_body={"status": "Failed", "error": {"message": "visual binding failed"}}, text="{}"),
            ],
            "POST": [
                make_response(201, json_body={"id": "folder-1", "displayName": "tmp_run"}, text="{}"),
                make_response(202, json_body={"id": "export-1", "status": "Running"}, text="{}"),
            ],
            "DELETE": [make_response(204)],
        }
    )
    install_fake_client(monkeypatch, fabric_module, fake)

    async def collect_items(_client, workspace_id: str, _headers: dict):
        return [
            {
                "workspaceName": "Workspace",
                "workspaceId": workspace_id,
                "displayName": "Report",
                "type": "Report",
                "id": "source-report-1",
            }
        ], [], 1

    async def create_item(_client, _workspace_id: str, _headers: dict, *, display_name: str, item_type: str, folder_id: str, **_kwargs):
        item_id = "semanticmodel-1" if item_type == "SemanticModel" else f"{item_type.lower()}-1"
        return {"id": item_id, "displayName": display_name, "type": item_type, "folderId": folder_id}

    async def ok_definition(*_args, **_kwargs):
        return {"status": "accepted"}

    async def ok_notebook_run(*_args, **_kwargs):
        return {"id": "job-1", "status": "Completed", "exitValue": '{"rowCount":1}'}

    async def ok_lakehouse_tables(*_args, **_kwargs):
        return {"status": "tables_found", "via": "fabric_lakehouse_tables_api", "tables": ["Inventory", "Summary"]}

    async def ok_refresh(*_args, **_kwargs):
        return {"status": "refresh_triggered", "via": "powerbi_v1"}

    async def ok_model_data(*_args, **_kwargs):
        return {"status": "queryable", "via": "powerbi_executeQueries", "rowCount": 1, "itemTypes": ["Report"]}

    async def no_clone(*_args, **_kwargs):
        return None

    monkeypatch.setattr(fabric_module, "_collect_accessible_inventory_items", collect_items)
    monkeypatch.setattr(fabric_module, "_create_or_reuse_inventory_item", create_item)
    monkeypatch.setattr(fabric_module, "_update_notebook_definition", ok_definition)
    monkeypatch.setattr(fabric_module, "_run_inventory_notebook", ok_notebook_run)
    monkeypatch.setattr(fabric_module, "_wait_for_lakehouse_tables", ok_lakehouse_tables)
    monkeypatch.setattr(fabric_module, "_refresh_semantic_model", ok_refresh)
    monkeypatch.setattr(fabric_module, "_wait_for_inventory_model_data", ok_model_data)
    monkeypatch.setattr(fabric_module, "_build_inventory_report_definition_from_clone", no_clone)

    result = await fabric_create_workspace_inventory_solution("workspace-1", "tmp_run")

    assert result.startswith("Error creating inventory solution: ")
    body = json.loads(result.removeprefix("Error creating inventory solution: "))
    assert body["status"] == "partial"
    assert body["reportRenderValidation"] is None
    assert body["cleanup"][0]["id"] == "report-1"
    assert body["cleanup"][0]["status"] == "deleted"
    assert not any(item.get("type") == "Report" for item in body["createdItems"])
    assert any(call[0] == "DELETE" and "/items/report-1" in call[1] for call in fake.calls)
    assert any("Report render validation failed" in error for error in body["errors"])


@pytest.mark.asyncio
async def test_inventory_solution_succeeds_only_after_report_render_validation(monkeypatch, fabric_token_env) -> None:
    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"id": "workspace-1", "capacityId": "capacity-1"}, text="{}"),
                make_response(200, json_body={"value": [{"id": "capacity-1", "state": "Active"}]}, text="{}"),
                make_response(200, json_body={"value": []}, text="{}"),
                make_response(200, json_body={"id": "report-1", "datasetId": "semanticmodel-1"}, text="{}"),
                make_response(200, json_body={"status": "Succeeded"}, text="{}"),
            ],
            "POST": [
                make_response(201, json_body={"id": "folder-1", "displayName": "tmp_run"}, text="{}"),
                make_response(202, json_body={"id": "export-1", "status": "Running"}, text="{}"),
            ],
        }
    )
    install_fake_client(monkeypatch, fabric_module, fake)

    async def collect_items(_client, workspace_id: str, _headers: dict):
        return [
            {
                "workspaceName": "Workspace",
                "workspaceId": workspace_id,
                "displayName": "Report",
                "type": "Report",
                "id": "source-report-1",
            }
        ], [], 1

    async def create_item(_client, _workspace_id: str, _headers: dict, *, display_name: str, item_type: str, folder_id: str, **_kwargs):
        item_id = "semanticmodel-1" if item_type == "SemanticModel" else f"{item_type.lower()}-1"
        return {"id": item_id, "displayName": display_name, "type": item_type, "folderId": folder_id}

    async def ok_definition(*_args, **_kwargs):
        return {"status": "accepted"}

    async def ok_notebook_run(*_args, **_kwargs):
        return {"id": "job-1", "status": "Completed", "exitValue": '{"rowCount":1}'}

    async def ok_lakehouse_tables(*_args, **_kwargs):
        return {"status": "tables_found", "via": "fabric_lakehouse_tables_api", "tables": ["Inventory", "Summary"]}

    async def ok_refresh(*_args, **_kwargs):
        return {"status": "refresh_triggered", "via": "powerbi_v1"}

    async def ok_model_data(*_args, **_kwargs):
        return {"status": "queryable", "via": "powerbi_executeQueries", "rowCount": 1, "itemTypes": ["Report"]}

    async def no_clone(*_args, **_kwargs):
        return None

    monkeypatch.setattr(fabric_module, "_collect_accessible_inventory_items", collect_items)
    monkeypatch.setattr(fabric_module, "_create_or_reuse_inventory_item", create_item)
    monkeypatch.setattr(fabric_module, "_update_notebook_definition", ok_definition)
    monkeypatch.setattr(fabric_module, "_run_inventory_notebook", ok_notebook_run)
    monkeypatch.setattr(fabric_module, "_wait_for_lakehouse_tables", ok_lakehouse_tables)
    monkeypatch.setattr(fabric_module, "_refresh_semantic_model", ok_refresh)
    monkeypatch.setattr(fabric_module, "_wait_for_inventory_model_data", ok_model_data)
    monkeypatch.setattr(fabric_module, "_build_inventory_report_definition_from_clone", no_clone)

    result = await fabric_create_workspace_inventory_solution("workspace-1", "tmp_run")

    body = json.loads(result)
    assert body["status"] == "created"
    assert body["capacityValidation"]["status"] == "active"
    assert body["semanticModelDataValidation"]["status"] == "queryable"
    assert body["reportRenderValidation"] == {
        "status": "rendered",
        "via": "powerbi_exportTo_pdf",
        "token": "POWERBI_API_TOKEN",
        "reportId": "report-1",
        "semanticModelId": "semanticmodel-1",
        "exportId": "export-1",
    }
    assert any(item.get("type") == "Report" for item in body["createdItems"])
    assert any(item.get("type") == "ReportRenderValidation" for item in body["createdItems"])
    assert body["cleanup"] == []


@pytest.mark.asyncio
async def test_verify_workspace_inventory_solution_checks_items_data_and_report(monkeypatch, fabric_token_env) -> None:
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"id": "workspace-1", "capacityId": "capacity-1"}, text="{}"),
                make_response(200, json_body={"value": [{"id": "capacity-1", "state": "Active"}]}, text="{}"),
                make_response(200, json_body={"value": [{"id": "folder-1", "displayName": "tmp_run"}]}, text="{}"),
                make_response(
                    200,
                    json_body={
                        "value": [
                            {"id": "notebook-1", "displayName": "NB", "type": "Notebook", "folderId": "folder-1"},
                            {"id": "model-1", "displayName": "Model", "type": "SemanticModel", "folderId": "folder-1"},
                            {"id": "report-1", "displayName": "Report", "type": "Report", "folderId": "folder-1"},
                        ]
                    },
                    text="{}",
                ),
            ]
        }
    )
    install_fake_client(monkeypatch, fabric_module, fake)

    async def ok_model_data(*_args, **_kwargs):
        return {"status": "queryable", "via": "powerbi_executeQueries", "rowCount": 12, "itemTypes": ["Report"]}

    async def ok_report_render(*_args, **_kwargs):
        return {"status": "rendered", "via": "powerbi_exportTo_pdf", "exportId": "export-1"}

    monkeypatch.setattr(fabric_module, "_wait_for_inventory_model_data", ok_model_data)
    monkeypatch.setattr(fabric_module, "_verify_report_renderable", ok_report_render)

    result = await fabric_verify_workspace_inventory_solution(
        "workspace-1",
        "folder-1",
        expected_task="Create an end to end ingestion, transformation, semantic modelling and report visualization.",
        expected_min_inventory_rows=1,
    )

    body = json.loads(result)
    assert body["status"] == "verified"
    assert body["expectationCheck"]["missingItemTypes"] == []
    assert body["semanticModelDataValidation"]["status"] == "queryable"
    assert body["reportRenderValidation"]["status"] == "rendered"
    assert {item["type"] for item in body["items"]} == {"Notebook", "SemanticModel", "Report"}