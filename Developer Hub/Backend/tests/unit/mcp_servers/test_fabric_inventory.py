import base64
import json

import pytest

from mcp_servers import fabric as fabric_module
from mcp_servers.fabric import (
    _compact_inventory_tool_result,
    _create_or_reuse_inventory_item,
    _require_delegated_user_token,
    fabric_create_workspace_inventory_solution,
    fabric_create_item,
    fabric_diagnose_workspace_artifacts,
    fabric_verify_workspace_inventory_solution,
    _inventory_artifact_base,
    _infer_inventory_naming_convention,
    _inventory_display_name,
    _inventory_internal_artifact_name,
    _inventory_item_base,
    _inventory_notebook_definition,
    _inventory_solution_quality_validation,
    _observed_lakehouse_table_name,
    _inventory_rows_m,
    _refresh_semantic_model,
    _report_definition,
    _run_inventory_notebook,
    _semantic_model_definition,
    _semantic_model_definition_directlake,
    _wait_for_lakehouse_tables,
    _wait_for_inventory_model_data,
    _validate_semantic_model_structure,
)

from .conftest import FakeAsyncClient, install_fake_client, make_response


def _fake_jwt(claims: dict) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def encode(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode("ascii").rstrip("=")

    return f"{encode(header)}.{encode(claims)}.sig"


def test_inventory_artifact_base_is_short_and_alphanumeric() -> None:
    assert _inventory_artifact_base("tmp_20260426183320") == "Inv26183320"


def test_inventory_artifact_base_handles_non_timestamp_folder() -> None:
    assert _inventory_artifact_base("tmp_run folder!") == "Invrunfolder"


def test_inventory_item_base_includes_folder_id_suffix() -> None:
    assert _inventory_item_base("tmp_20260426183320", "abc-123-def") == "Inv26183320abc123"


def test_inventory_naming_convention_prefers_existing_readable_workspace_names() -> None:
    naming = _infer_inventory_naming_convention(
        source_items=[
            {"workspaceId": "workspace-1", "displayName": "Sales Performance Report", "type": "Report"},
            {"workspaceId": "workspace-1", "displayName": "Customer Lakehouse", "type": "Lakehouse"},
            {"workspaceId": "workspace-1", "displayName": "Inv291440583d4141Report", "type": "Report", "folderId": "tmp-folder"},
            {"workspaceId": "other", "displayName": "other_workspace_report", "type": "Report"},
        ],
        folders=[{"id": "tmp-folder", "displayName": "tmp_20260429144058"}],
        workspace_id="workspace-1",
        folder_name="tmp_20260429144058",
        folder_id="folder-123456",
        solution_name="Fabric Items Inventory",
    )

    assert naming["preferredStyle"] == "title_case_spaces"
    assert naming["sampleSize"] == 2
    assert _inventory_display_name(naming, "Report") == "Fabric Items Inventory 144058 Report"
    assert _inventory_display_name(naming, "SemanticModel") == "Fabric Items Inventory 144058 Semantic Model"
    assert _inventory_display_name(naming, "Lakehouse") == "FabricItemsInventory144058Lakehouse"


def test_inventory_naming_convention_preserves_compact_pascal_when_dominant() -> None:
    naming = _infer_inventory_naming_convention(
        source_items=[
            {"workspaceId": "workspace-1", "displayName": "SalesLakehouse", "type": "Lakehouse"},
            {"workspaceId": "workspace-1", "displayName": "SalesModel", "type": "SemanticModel"},
        ],
        folders=[],
        workspace_id="workspace-1",
        folder_name="tmp_20260429144058",
        folder_id="folder-123456",
        solution_name="Fabric Items Inventory",
    )

    assert naming["preferredStyle"] == "compact_pascal"
    assert _inventory_display_name(naming, "Report") == "FabricItemsInventory144058Report"
    assert _inventory_display_name(naming, "Lakehouse") == "FabricItemsInventory144058Lakehouse"


def test_inventory_internal_table_names_follow_readable_convention() -> None:
    naming = _infer_inventory_naming_convention(
        source_items=[
            {"workspaceId": "workspace-1", "displayName": "Sales Performance Report", "type": "Report"},
            {"workspaceId": "workspace-1", "displayName": "Customer Lakehouse", "type": "Lakehouse"},
        ],
        folders=[],
        workspace_id="workspace-1",
        folder_name="tmp_wukasz",
        folder_id="d7e52e-folder",
        solution_name="Fabric Items Inventory",
    )

    assert _inventory_internal_artifact_name(naming, "Fabric Items", sql_identifier=True) == "FabricItemsInventoryWukaszFabricItems"
    assert (
        _inventory_internal_artifact_name(naming, "Fabric Items By Type", sql_identifier=True)
        == "FabricItemsInventoryWukaszFabricItemsByType"
    )


def test_inventory_internal_table_names_follow_snake_case_convention() -> None:
    naming = _infer_inventory_naming_convention(
        source_items=[
            {"workspaceId": "workspace-1", "displayName": "sales_performance_report", "type": "Report"},
            {"workspaceId": "workspace-1", "displayName": "customer_lakehouse", "type": "Lakehouse"},
        ],
        folders=[],
        workspace_id="workspace-1",
        folder_name="tmp_20260429144058",
        folder_id="folder-123456",
        solution_name="Fabric Items Inventory",
    )

    assert _inventory_internal_artifact_name(naming, "Fabric Items", sql_identifier=True) == "fabric_items_inventory_144058_fabric_items"
    assert (
        _inventory_internal_artifact_name(naming, "Fabric Items By Type", sql_identifier=True)
        == "fabric_items_inventory_144058_fabric_items_by_type"
    )


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
    assert "@dataclass(frozen=True)" in code
    assert "class InventoryConfig" in code
    assert "class FabricApiClient" in code
    assert "class InventoryBuilder" in code
    assert "class DeltaInventoryWriter" in code
    assert "raise RuntimeError" in code
    assert " from exc" in code
    assert "No Fabric items were collected" in code
    assert "Lakehouse ID is required" in code
    assert "utils.credentials.getToken(\"pbi\")" in code
    assert "requests.get(next_url" in code
    assert "timeout=self.timeout_seconds" in code
    assert "saveAsTable(qualified_name)" in code
    assert "writer.save_table(" in code
    assert "warningCount" in code
    assert "exit_fn(json.dumps(result))" in code


def test_observed_lakehouse_table_name_preserves_fabric_case() -> None:
    validation = {
        "status": "tables_found",
        "tables": ["inv28213732e4cbdc_fabricitems", "inv28213732e4cbdc_fabricitemsbytype"],
    }

    assert (
        _observed_lakehouse_table_name(validation, "Inv28213732e4cbdc_FabricItems")
        == "inv28213732e4cbdc_fabricitems"
    )
    assert _observed_lakehouse_table_name(validation, "OtherTable") == "OtherTable"


@pytest.mark.asyncio
async def test_diagnose_workspace_artifacts_surfaces_owner_mismatch_and_refresh_failure(monkeypatch, fabric_token_env) -> None:
    service_exception = json.dumps({
        "errorCode": "0xC14700C7",
        "errorDescription": "We cannot access the source Delta table because of access permissions.",
    })
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
                            {"id": "lakehouse-1", "displayName": "InventoryLakehouse", "type": "Lakehouse", "folderId": "folder-1"},
                            {"id": "model-1", "displayName": "InventoryModel", "type": "SemanticModel", "folderId": "folder-1"},
                        ]
                    },
                    text="{}",
                ),
                make_response(200, json_body={"id": "lakehouse-1", "type": "Lakehouse", "createdBy": {"displayName": "Lukasz Obst"}}, text="{}"),
                make_response(200, json_body={"id": "model-1", "type": "SemanticModel", "createdBy": {"displayName": "Fabric ClawHub"}}, text="{}"),
                make_response(
                    200,
                    json_body={"value": [{"status": "Failed", "serviceExceptionJson": service_exception}]},
                    text="{}",
                ),
                make_response(200, json_body={"value": [{"principal": {"displayName": "Fabric ClawHub"}, "role": "Contributor"}]}, text="{}"),
            ]
        }
    )
    install_fake_client(monkeypatch, fabric_module, fake)

    result = await fabric_diagnose_workspace_artifacts(
        "workspace-1",
        folder_name="tmp_run",
        include_powerbi_diagnostics=False,
        include_lakehouse_diagnostics=False,
    )
    body = json.loads(result)

    assert body["status"] == "ok"
    assert body["folder"]["id"] == "folder-1"
    assert {issue["code"] for issue in body["suspectedIssues"]} >= {
        "DIRECTLAKE_OWNER_MISMATCH_RISK",
        "SEMANTIC_MODEL_REFRESH_FAILED",
    }
    refresh_issue = next(issue for issue in body["suspectedIssues"] if issue["code"] == "SEMANTIC_MODEL_REFRESH_FAILED")
    assert refresh_issue["errorCode"] == "0xC14700C7"
    assert "access permissions" in refresh_issue["errorDescription"]
    assert body["workspaceRoles"]["status"] == "ok"
    assert body["items"][0]["ownerIdentity"]["displayName"] == "Lukasz Obst"
    assert "entra_diagnose_principal_access" in " ".join(body["recommendedNextChecks"])


def test_delegated_token_guard_blocks_application_token(monkeypatch) -> None:
    monkeypatch.setenv(
        "FABRIC_API_TOKEN",
        _fake_jwt({
            "idtyp": "app",
            "roles": ["Item.ReadWrite.All"],
            "appid": "app-1",
            "app_displayname": "Fabric ClawHub",
        }),
    )

    with pytest.raises(RuntimeError, match="application/service-principal token"):
        _require_delegated_user_token("FABRIC_API_TOKEN", "creating item")


def test_delegated_token_guard_allows_user_delegated_token(monkeypatch) -> None:
    monkeypatch.setenv(
        "FABRIC_API_TOKEN",
        _fake_jwt({
            "scp": "Item.ReadWrite.All Dataset.ReadWrite.All",
            "name": "Lukasz Obst",
            "preferred_username": "lukasz@example.test",
            "oid": "user-1",
        }),
    )

    identity = _require_delegated_user_token("FABRIC_API_TOKEN", "creating item")
    assert identity["authMode"] == "delegated_user"
    assert identity["name"] == "Lukasz Obst"


@pytest.mark.asyncio
async def test_create_item_blocks_app_token_before_post(monkeypatch) -> None:
    monkeypatch.setenv(
        "FABRIC_API_TOKEN",
        _fake_jwt({
            "idtyp": "app",
            "roles": ["Item.ReadWrite.All"],
            "appid": "app-1",
            "app_displayname": "Fabric ClawHub",
        }),
    )
    fake = FakeAsyncClient()
    install_fake_client(monkeypatch, fabric_module, fake)

    with pytest.raises(RuntimeError, match="refuses to create or update mission artifacts"):
        await fabric_create_item("workspace-1", "Model", "SemanticModel")

    assert fake.calls == []


@pytest.mark.asyncio
async def test_diagnose_workspace_artifacts_collects_powerbi_lakehouse_and_report_evidence(monkeypatch, fabric_token_env) -> None:
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
                            {"id": "lakehouse-1", "displayName": "InventoryLakehouse", "type": "Lakehouse", "folderId": "folder-1"},
                            {"id": "model-1", "displayName": "InventoryModel", "type": "SemanticModel", "folderId": "folder-1"},
                            {"id": "report-1", "displayName": "InventoryReport", "type": "Report", "folderId": "folder-1"},
                        ]
                    },
                    text="{}",
                ),
                make_response(200, json_body={"id": "lakehouse-1", "type": "Lakehouse", "createdBy": {"displayName": "Lukasz Obst"}}, text="{}"),
                make_response(200, json_body={"properties": {"sqlEndpointProperties": {"id": "sql-1", "provisioningStatus": "Succeeded"}}}, text="{}"),
                make_response(200, json_body={"data": [{"name": "FabricItems", "format": "delta"}]}, text="{}"),
                make_response(200, json_body={"id": "model-1", "type": "SemanticModel", "createdBy": {"displayName": "Lukasz Obst"}}, text="{}"),
                make_response(200, json_body={"value": [{"status": "Completed"}]}, text="{}"),
                make_response(200, json_body={"id": "model-1", "name": "InventoryModel", "isRefreshable": True}, text="{}"),
                make_response(200, json_body={"value": [{"datasourceType": "Extension"}]}, text="{}"),
                make_response(200, json_body={"enabled": True}, text="{}"),
                make_response(200, json_body={"value": [{"identifier": "lukasz@example.test", "principalType": "User"}]}, text="{}"),
                make_response(200, json_body={"id": "report-1", "type": "Report", "createdBy": {"displayName": "Lukasz Obst"}}, text="{}"),
                make_response(200, json_body={"id": "report-1", "datasetId": "model-1"}, text="{}"),
                make_response(200, json_body={"value": [{"name": "ReportSection", "displayName": "Overview"}]}, text="{}"),
                make_response(200, json_body={"value": [{"datasourceType": "PowerBI"}]}, text="{}"),
                make_response(200, json_body={"value": [{"principal": {"displayName": "Lukasz Obst"}, "role": "Admin"}]}, text="{}"),
                make_response(200, json_body={"id": "workspace-1", "displayName": "Workspace"}, text="{}"),
            ],
            "POST": [
                make_response(200, json_body={"results": [{"tables": [{"rows": [{"[TableCount]": 1}]}]}]}, text="{}"),
                make_response(200, json_body={"results": [{"tables": [{"rows": [{"[Name]": "FabricItems"}]}]}]}, text="{}"),
                make_response(200, json_body={"results": [{"tables": [{"rows": [{"[Name]": "Item Count"}]}]}]}, text="{}"),
                make_response(200, json_body={"results": [{"tables": [{"rows": [{"[Name]": "FabricItems"}]}]}]}, text="{}"),
            ],
        }
    )
    install_fake_client(monkeypatch, fabric_module, fake)

    result = await fabric_diagnose_workspace_artifacts("workspace-1", folder_name="tmp_run")
    body = json.loads(result)

    assert "SemanticModel DAX metadata/queryability probes through executeQueries" in body["diagnosticCoverage"]
    model = next(item for item in body["items"] if item["type"] == "SemanticModel")
    report = next(item for item in body["items"] if item["type"] == "Report")
    lakehouse = next(item for item in body["items"] if item["type"] == "Lakehouse")
    assert model["semanticModelDiagnostics"]["powerbiDataset"][0]["label"] == "powerbi_dataset_metadata"
    assert model["semanticModelDiagnostics"]["daxMetadata"][0]["label"] == "semantic_model_scope_counts"
    assert report["reportDiagnostics"]["powerbiReport"][1]["label"] == "powerbi_report_pages"
    assert lakehouse["lakehouseDiagnostics"]["tables"]["label"] == "fabric_lakehouse_tables"


def test_report_definition_binds_to_semantic_model_and_inventory_visual() -> None:
    """The report uses the proven PBIR-Legacy 2-part format with the
    `pbiServiceXmlaStyleLive` connection that `sempy_labs` uses
    successfully every day. The single ``report.json`` (legacy
    single-file format) carries the layout, sections, and visual
    bindings, while ``definition.pbir`` carries only the dataset
    reference."""
    definition = _report_definition("semantic-model-1")
    assert definition["format"] == "PBIR-Legacy"
    definition_pbir = next(part for part in definition["parts"] if part["path"] == "definition.pbir")
    report_part = next(part for part in definition["parts"] if part["path"] == "report.json")
    pbir = json.loads(base64.b64decode(definition_pbir["payload"]).decode("utf-8"))
    report_legacy = json.loads(base64.b64decode(report_part["payload"]).decode("utf-8"))

    # PBIR-Legacy uses the XMLA-style live connection from sempy_labs.
    assert pbir["version"] == "1.0"
    by_conn = pbir["datasetReference"]["byConnection"]
    assert by_conn["pbiModelDatabaseName"] == "semantic-model-1"
    assert by_conn["pbiModelVirtualServerName"] == "sobe_wowvirtualserver"
    assert by_conn["connectionType"] == "pbiServiceXmlaStyleLive"

    # The legacy single-file report has an executive overview page with
    # interactive slicers, multiple analytical visuals, and bindings to
    # the FabricItems entity and reusable measures.
    assert report_legacy["sections"], "report should have at least one page section"
    section = report_legacy["sections"][0]
    assert len(section["visualContainers"]) >= 8
    visual_inner = json.loads(section["visualContainers"][0]["config"])
    single_visual = visual_inner["singleVisual"]
    assert single_visual["visualType"] == "card"
    assert visual_inner["layouts"][0]["position"]["x"] == 40.0
    assert "Portfolio at a Glance" in json.dumps(single_visual)
    assert "3-second top-left overview" in json.dumps(single_visual)
    all_visuals = [json.loads(container["config"])["singleVisual"] for container in section["visualContainers"]]
    visual_types = {visual["visualType"] for visual in all_visuals}
    assert sum(1 for visual in all_visuals if visual["visualType"] == "slicer") >= 2
    assert "clusteredBarChart" in visual_types
    assert "clusteredColumnChart" in visual_types
    assert "multiRowCard" in visual_types
    assert "tableEx" in visual_types
    serialized = json.dumps(all_visuals)
    assert "Item Mix by Type" in serialized
    assert "30-second filter-and-zoom" in serialized
    assert "Details on Demand" in serialized
    assert "300-second details-on-demand" in serialized
    assert "altText" in serialized
    report_config = json.loads(report_legacy["config"])
    assert report_config["settings"]["useNewFilterPaneExperience"] is True
    assert report_config["settings"]["useEnhancedTooltips"] is True
    custom_theme = report_config["themeCollection"]["customTheme"]
    assert custom_theme["name"] == "AgentHub Championship Analytics"
    assert len(custom_theme["dataColors"]) >= 5
    assert "visualStyles" in custom_theme
    assert report_legacy["resourcePackages"] == []
    assert len(definition["parts"]) == 2
    assert "FabricItems" in serialized
    assert "Item Count" in serialized
    assert "Workspace Count" in serialized
    assert "ItemType" in serialized
    assert "WorkspaceName" in serialized

    model_definition = _semantic_model_definition_directlake(
        table_name="Inventory_FabricItems",
        summary_table_name="Inventory_FabricItemsByType",
        sql_endpoint_connection_string="tcp:test.example.fabric.microsoft.com,1433",
        sql_endpoint_id="12345678-1234-1234-1234-1234567890ab",
    )
    notebook_definition = _inventory_notebook_definition(
        workspace_id="workspace-1",
        lakehouse_id="lakehouse-1",
        lakehouse_name="InventoryLakehouse",
        table_name="Inventory_FabricItems",
        summary_table_name="Inventory_FabricItemsByType",
    )
    quality = _inventory_solution_quality_validation(
        model_definition=model_definition,
        report_definition=definition,
        notebook_definition=notebook_definition,
        model_storage_mode="DirectLake",
        persistent_data_written=True,
    )
    assert quality["status"] == "passed"
    assert quality["report"]["visualCount"] >= 8
    assert quality["report"]["slicerCount"] >= 2
    assert quality["report"]["chartCount"] >= 2
    assert quality["report"]["storyFlow3_30_300"] is True
    assert quality["report"]["accessibilityMetadata"] is True
    assert quality["report"]["guidedTabOrder"] is True
    assert quality["report"]["restrainedVisualDensity"] is True
    assert quality["report"]["highContrastCanvas"] is True
    assert quality["report"]["designRubric"] == "power_bi_championship_3_30_300"
    assert quality["report"]["modernReaderExperience"] is True
    assert quality["report"]["themeName"] == "AgentHub Championship Analytics"
    assert quality["report"]["visualStyleDefaults"] is True
    assert quality["report"]["overlapCount"] == 0
    assert len(quality["semanticModel"]["measureNames"]) >= 4
    assert quality["notebookCode"]["status"] == "passed"
    assert quality["notebookCode"]["classCount"] >= 3
    assert quality["notebookCode"]["raisesRuntimeErrors"] is True
    assert quality["notebookCode"]["tracksWarnings"] is True


def test_inventory_solution_result_compaction_preserves_proof_under_runtime_cap() -> None:
    checks = [
        {"name": f"check_{index}", "passed": True, "detail": {"large": "x" * 2000}}
        for index in range(60)
    ]
    result = {
        "status": "created",
        "workspaceId": "workspace-1",
        "folderId": "folder-1",
        "folderName": "tmp_run",
        "sourceItemCount": 3,
        "dataSource": "lakehouse_delta_tables",
        "semanticModelStorageMode": "DirectLake",
        "notebookWritesEnabled": True,
        "persistentDataWritten": True,
        "persistentDataStore": {"type": "Lakehouse", "id": "lakehouse-1", "written": True},
        "semanticModelDataValidation": {"status": "queryable", "via": "powerbi_executeQueries", "rowCount": 3},
        "reportRenderValidation": {"status": "rendered", "via": "powerbi_pages_metadata", "pageCount": 1},
        "qualityValidation": {
            "status": "passed",
            "checks": checks,
            "semanticModel": {"status": "passed", "storageMode": "DirectLake", "checks": checks},
            "report": {
                "status": "passed",
                "visualCount": 10,
                "themeName": "AgentHub Modern Analytics",
                "storyFlow3_30_300": True,
                "accessibilityMetadata": True,
                "guidedTabOrder": True,
                "restrainedVisualDensity": True,
                "highContrastCanvas": True,
                "designRubric": "power_bi_championship_3_30_300",
                "checks": checks,
            },
            "notebookCode": {"status": "passed", "classCount": 3, "functionCount": 4, "raisesRuntimeErrors": True, "tracksWarnings": True, "checks": checks},
        },
        "directLakeIdentityDiagnostics": {
            "status": "unknown",
            "ownerMismatch": False,
            "apiTokenIdentity": {"upn": "user@example.com", "appid": "x" * 4000, "hasDelegatedScopes": True},
        },
        "itemDisplayNames": {
            "folder": "tmp_run",
            "lakehouse": "Inventory Lakehouse",
            "notebook": "Inventory Notebook",
            "semanticModel": "Inventory Semantic Model",
            "report": "Inventory Report",
            **{f"sourceItem{idx}": "x" * 200 for idx in range(250)},
        },
        "progress": [
            {"step": f"step_{idx}", "status": "ok", "qualityValidation": {"status": "passed", "checks": checks}, "diagnostics": {"status": "unknown", "apiTokenIdentity": {"appid": "x" * 4000}}}
            for idx in range(50)
        ],
        "createdItems": [
            {"displayName": "Inventory Report", "type": "Report", "id": "report-1", "definition": "x" * 10000},
            *[
                {"displayName": f"Source Item {idx}", "type": "Report", "id": f"source-{idx}", "definition": "x" * 1000}
                for idx in range(60)
            ],
        ],
        "errors": [],
    }

    compact = _compact_inventory_tool_result(result)
    encoded = json.dumps(compact, indent=2)

    assert len(encoded) < 40_000
    assert compact["status"] == "created"
    assert compact["dataSource"] == "lakehouse_delta_tables"
    assert compact["semanticModelStorageMode"] == "DirectLake"
    assert compact["qualityValidation"]["status"] == "passed"
    assert compact["qualityValidation"]["report"]["visualCount"] == 10
    assert compact["qualityValidation"]["report"]["designRubric"] == "power_bi_championship_3_30_300"
    assert compact["qualityValidation"]["report"]["storyFlow3_30_300"] is True
    assert compact["qualityValidation"]["report"]["accessibilityMetadata"] is True
    assert compact["qualityValidation"]["notebookCode"]["classCount"] == 3
    assert compact["qualityValidation"]["notebookCode"]["raisesRuntimeErrors"] is True
    assert compact["itemDisplayNames"] == {
        "folder": "tmp_run",
        "lakehouse": "Inventory Lakehouse",
        "notebook": "Inventory Notebook",
        "semanticModel": "Inventory Semantic Model",
        "report": "Inventory Report",
    }
    assert compact["progressTruncated"] == 30
    assert compact["createdItems"][0] == {"displayName": "Inventory Report", "type": "Report", "id": "report-1"}
    assert compact["createdItemsTruncated"] == 37


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
                # Pre-check: existing item lives in a different folder so it's
                # filtered out — pre-check returns nothing and the POST is
                # attempted, which then hits 409.
                make_response(
                    200,
                    json_body={
                        "value": [
                            {
                                "id": "notebook-1",
                                "displayName": "Inv26183320abc123NB",
                                "type": "Notebook",
                                "folderId": "different-folder",
                            }
                        ]
                    },
                    text="{}",
                ),
                # 409 fallback: same listing without folder filter resolves
                # to the existing item.
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
                ),
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
    assert fake.calls[0][0] == "GET"  # pre-check
    assert fake.calls[1][0] == "POST"  # create attempt
    assert fake.calls[2][0] == "GET"  # 409 fallback lookup


@pytest.mark.asyncio
async def test_create_or_reuse_inventory_item_updates_existing_definition(fabric_token_env) -> None:
    """When an item already exists, push the new definition into it
    instead of creating a duplicate."""
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                # Pre-check finds an existing semantic model in the same folder.
                make_response(
                    200,
                    json_body={
                        "value": [
                            {
                                "id": "model-1",
                                "displayName": "Inv26183320abc123Model",
                                "type": "SemanticModel",
                                "folderId": "abc-123-def",
                                "createdDate": "2026-04-28T18:00:00Z",
                            }
                        ]
                    },
                    text="{}",
                ),
            ],
            "POST": [
                # updateDefinition response.
                make_response(200, json_body={"status": "updated"}, text="{}"),
            ],
        }
    )

    result = await _create_or_reuse_inventory_item(
        fake,
        "workspace-1",
        {"Authorization": "Bearer fake-fabric-token"},
        display_name="Inv26183320abc123Model",
        item_type="SemanticModel",
        description="Inventory semantic model",
        folder_id="abc-123-def",
        op_name="creating inventory semantic model",
        extra_body={"definition": {"parts": [{"path": "model.bim", "payload": "e30="}]}},
    )

    assert isinstance(result, dict)
    assert result["id"] == "model-1"
    # Must have updated existing — not POSTed to /items.
    assert fake.calls[0][0] == "GET"
    assert fake.calls[1][0] == "POST"
    assert "/semanticModels/model-1/updateDefinition" in fake.calls[1][1]


@pytest.mark.asyncio
async def test_create_or_reuse_inventory_item_uses_typed_lakehouse_create(fabric_token_env) -> None:
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"value": []}, text="{}"),
                make_response(200, json_body={"value": [{"id": "lakehouse-1", "displayName": "Inventory Lakehouse", "type": "Lakehouse", "folderId": "folder-1"}]}, text="{}"),
            ],
            "POST": [
                make_response(
                    201,
                    json_body={"id": "lakehouse-1", "displayName": "Inventory Lakehouse", "type": "Lakehouse"},
                    text="{}",
                )
            ],
        }
    )

    result = await _create_or_reuse_inventory_item(
        fake,
        "workspace-1",
        {"Authorization": "Bearer fake-fabric-token"},
        display_name="Inventory Lakehouse",
        item_type="Lakehouse",
        description="Inventory data store",
        folder_id="folder-1",
        op_name="creating inventory lakehouse",
        extra_body={"creationPayload": {"enableSchemas": True}},
    )

    assert isinstance(result, dict)
    assert result["id"] == "lakehouse-1"
    assert fake.calls[1][0] == "POST"
    assert fake.calls[1][1].endswith("/workspaces/workspace-1/lakehouses")
    assert fake.calls[1][2]["json"] == {
        "displayName": "Inventory Lakehouse",
        "description": "Inventory data store",
        "folderId": "folder-1",
        "creationPayload": {"enableSchemas": True},
    }


@pytest.mark.asyncio
async def test_create_or_reuse_inventory_item_deletes_silent_duplicates(fabric_token_env) -> None:
    """When Fabric silently creates a duplicate after a successful POST,
    the canonical item is kept and duplicates are deleted."""
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                # Pre-check: no existing item.
                make_response(200, json_body={"value": []}, text="{}"),
                # Post-create sweep: Fabric silently created two items
                # for our single POST. Older one wins.
                make_response(
                    200,
                    json_body={
                        "value": [
                            {
                                "id": "model-old",
                                "displayName": "Inv26183320abc123Model",
                                "type": "SemanticModel",
                                "folderId": "abc-123-def",
                                "createdDate": "2026-04-28T18:00:00Z",
                            },
                            {
                                "id": "model-dup",
                                "displayName": "Inv26183320abc123Model",
                                "type": "SemanticModel",
                                "folderId": "abc-123-def",
                                "createdDate": "2026-04-28T18:00:01Z",
                            },
                        ]
                    },
                    text="{}",
                ),
            ],
            "POST": [
                make_response(
                    201,
                    json_body={
                        "id": "model-old",
                        "displayName": "Inv26183320abc123Model",
                        "type": "SemanticModel",
                    },
                    text="{}",
                ),
            ],
            "DELETE": [make_response(204)],
        }
    )

    result = await _create_or_reuse_inventory_item(
        fake,
        "workspace-1",
        {"Authorization": "Bearer fake-fabric-token"},
        display_name="Inv26183320abc123Model",
        item_type="SemanticModel",
        description="Inventory semantic model",
        folder_id="abc-123-def",
        op_name="creating inventory semantic model",
    )

    assert isinstance(result, dict)
    assert result["id"] == "model-old"
    delete_calls = [c for c in fake.calls if c[0] == "DELETE"]
    assert len(delete_calls) == 1
    assert "/items/model-dup" in delete_calls[0][1]


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
async def test_wait_for_lakehouse_tables_rejects_empty_inventory_table(monkeypatch, fabric_token_env) -> None:
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
                            {"name": "FabricItemsInventoryRunFabricItems", "rowCount": 0},
                            {"name": "FabricItemsInventoryRunFabricItemsByType", "rowCount": 0},
                        ]
                    },
                    text="{}",
                )
                for _ in range(18)
            ]
        }
    )

    result = await _wait_for_lakehouse_tables(
        fake,
        "workspace-1",
        "lakehouse-1",
        {"Authorization": "Bearer fake-fabric-token"},
        {"FabricItemsInventoryRunFabricItems", "FabricItemsInventoryRunFabricItemsByType"},
        expected_non_empty_table_names={"FabricItemsInventoryRunFabricItems"},
    )

    assert isinstance(result, str)
    assert "Empty tables are not accepted" in result


@pytest.mark.asyncio
async def test_wait_for_inventory_model_data_uses_execute_queries_without_fallback(monkeypatch, fabric_token_env) -> None:
    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)
    fake = FakeAsyncClient(
        responses_by_method={
            "POST": [
                # Row count query — must succeed for the model to be
                # considered queryable. The strict validator does not
                # use a separate liveness probe; it requires actual
                # data from the bound table.
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
        "token": "POWERBI_API_TOKEN",
        "attempts": 1,
    }
    assert all(call[1].endswith("/datasets/semantic-model-1/executeQueries") for call in fake.calls)
    assert all(call[2]["headers"]["Authorization"] == "Bearer fake-powerbi-token" for call in fake.calls)


@pytest.mark.asyncio
async def test_wait_for_inventory_model_data_passes_liveness_when_directlake_catalog_lags(monkeypatch, fabric_token_env) -> None:
    """When the bound DirectLake table never becomes queryable within
    the validation budget, the helper must return a hard error string
    so the inventory tool blocks report creation. Previously a soft
    fallback declared the model 'queryable' which caused us to ship
    a broken report. The verifier now relies on this signal to fail
    the SEMANTIC_MODEL_QUERYABLE step instead of waiting until the
    browser stage to discover the empty visual.
    """
    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)
    invalid_object_error = {
        "error": {
            "code": "DatasetExecuteQueriesError",
            "pbi.error": {
                "details": [
                    {"code": "DetailsMessage", "detail": {"value": "Invalid object name 'dbo.X_FabricItems'."}}
                ],
            },
        }
    }
    fake = FakeAsyncClient(
        responses_by_method={
            "POST": [
                # Every count attempt fails with Invalid object name
                # because the SQL endpoint catalog has not synced.
                *(make_response(400, json_body=invalid_object_error, text="bound table missing") for _ in range(40)),
            ]
        }
    )

    result = await _wait_for_inventory_model_data(
        fake, "workspace-1", "semantic-model-1", expected_min_rows=1,
    )

    assert isinstance(result, str)
    assert "never became queryable" in result
    assert "DirectLake catalog sync" in result


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
        return {"id": "job-1", "status": "Completed", "exitValue": '{"rowCount":3}'}

    async def ok_lakehouse_tables(*_args, **_kwargs):
        return {"status": "tables_found", "via": "fabric_lakehouse_tables_api", "tables": ["Inventory", "Summary"]}

    async def ok_refresh(*_args, **_kwargs):
        return {"status": "refresh_triggered", "via": "powerbi_v1"}

    async def model_not_ready(*_args, **_kwargs):
        return "Power BI executeQueries returned 0 inventory rows."

    async def ok_sql_endpoint(*_args, **_kwargs):
        return {
            "id": "1f5d2b3a-cccc-4dec-9d4e-0123456789ab",
            "connectionString": "tcp:test.example.fabric.microsoft.com,1433",
            "via": "fabric_lakehouse_sql_endpoint",
        }

    monkeypatch.setattr(fabric_module, "_collect_accessible_inventory_items", collect_items)
    monkeypatch.setattr(fabric_module, "_create_or_reuse_inventory_item", create_item)
    monkeypatch.setattr(fabric_module, "_update_notebook_definition", ok_definition)
    monkeypatch.setattr(fabric_module, "_run_inventory_notebook", ok_notebook_run)
    monkeypatch.setattr(fabric_module, "_wait_for_lakehouse_tables", ok_lakehouse_tables)
    monkeypatch.setattr(fabric_module, "_fetch_lakehouse_sql_endpoint", ok_sql_endpoint)
    monkeypatch.setattr(fabric_module, "_refresh_semantic_model", ok_refresh)
    monkeypatch.setattr(fabric_module, "_wait_for_inventory_model_data", model_not_ready)
    async def ok_structure(*_args, **_kwargs):
        return {"status": "structurally_valid", "via": "powerbi_metadata_tables", "observedTables": ["FabricItems", "FabricItemsByType"], "expectedTables": ["FabricItems", "FabricItemsByType"], "attempts": 1}
    monkeypatch.setattr(fabric_module, "_validate_semantic_model_structure", ok_structure)
    async def _ok_refresh_status(*_a, **_k):
        return {"status": "Completed", "errorMessage": "", "raw": {"status": "Completed"}}
    monkeypatch.setattr(fabric_module, "_wait_for_powerbi_refresh_completion", _ok_refresh_status)

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
async def test_inventory_solution_continues_when_workspace_capacity_assignment_is_not_exposed(monkeypatch, fabric_token_env) -> None:
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"id": "workspace-1", "displayName": "Workspace"}, text="{}"),
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

    async def create_item(*_args, item_type: str, **_kwargs):
        if item_type == "Lakehouse":
            return {"id": "lakehouse-1", "displayName": "Fabric Inventory Lakehouse", "type": "Lakehouse"}
        return "blocked after proving missing capacity metadata did not stop folder creation"

    monkeypatch.setattr(fabric_module, "_collect_accessible_inventory_items", collect_items)
    monkeypatch.setattr(fabric_module, "_create_or_reuse_inventory_item", create_item)

    result = await fabric_create_workspace_inventory_solution("workspace-1", "tmp_run")

    assert result.startswith("Error creating inventory solution: ")
    body = json.loads(result.removeprefix("Error creating inventory solution: "))
    assert body["status"] == "partial"
    assert body["capacityValidation"]["status"] == "unknown"
    assert body["capacityValidation"]["capacityId"] is None
    assert any("did not expose" in warning for warning in body["warnings"])
    assert any(call[0] == "POST" and call[1].endswith("/folders") for call in fake.calls)


@pytest.mark.asyncio
async def test_inventory_solution_continues_when_capacity_is_not_list_visible(monkeypatch, fabric_token_env) -> None:
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"id": "workspace-1", "capacityId": "capacity-1"}, text="{}"),
                make_response(200, json_body={"value": []}, text="{}"),
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

    async def create_item(*_args, item_type: str, **_kwargs):
        if item_type == "Lakehouse":
            return {"id": "lakehouse-1", "displayName": "Fabric Inventory Lakehouse", "type": "Lakehouse"}
        return "blocked after proving capacity visibility did not stop folder creation"

    monkeypatch.setattr(fabric_module, "_collect_accessible_inventory_items", collect_items)
    monkeypatch.setattr(fabric_module, "_create_or_reuse_inventory_item", create_item)

    result = await fabric_create_workspace_inventory_solution("workspace-1", "tmp_run")

    assert result.startswith("Error creating inventory solution: ")
    body = json.loads(result.removeprefix("Error creating inventory solution: "))
    assert body["status"] == "partial"
    assert body["capacityValidation"]["status"] == "unknown"
    assert any("not visible" in warning for warning in body["warnings"])
    assert any(call[0] == "POST" and call[1].endswith("/folders") for call in fake.calls)


@pytest.mark.asyncio
async def test_inventory_solution_rejects_lakehouse_failure_without_warehouse_fallback(monkeypatch, fabric_token_env) -> None:
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
                "id": "source-report-1",
            }
        ], [], 1

    created_item_types: list[str] = []

    async def lakehouse_creation_fails(
        _client,
        _workspace_id: str,
        _headers: dict,
        *,
        item_type: str,
        **_kwargs,
    ):
        created_item_types.append(item_type)
        return "Error creating inventory lakehouse: Lakehouse creation was rejected"

    monkeypatch.setattr(fabric_module, "_collect_accessible_inventory_items", collect_items)
    monkeypatch.setattr(fabric_module, "_create_or_reuse_inventory_item", lakehouse_creation_fails)

    result = await fabric_create_workspace_inventory_solution("workspace-1", "tmp_run")

    assert result.startswith("Error creating inventory solution: ")
    body = json.loads(result.removeprefix("Error creating inventory solution: "))
    assert body["status"] == "partial"
    assert body["persistentDataWritten"] is False
    assert body["notebookExecution"] is None
    assert body["semanticModelStorageMode"] == "Unavailable"
    assert not any(item.get("type") == "Warehouse" for item in body["createdItems"])
    assert created_item_types == ["Lakehouse", "Lakehouse"]
    assert any("Warehouse/import-model fallback is not accepted" in error for error in body["errors"])


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
                make_response(200, json_body={"id": "report-1", "datasetId": "semanticmodel-1"}, text="{}"),
                # Empty pages list signals the dataset binding is broken
                # (PBIR cannot resolve the model) → render validation must fail
                # and the unverified report must be cleaned up.
                make_response(200, json_body={"value": []}, text="{}"),
            ],
            "POST": [
                make_response(201, json_body={"id": "folder-1", "displayName": "tmp_run"}, text="{}"),
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
        return {"id": "job-1", "status": "Completed", "exitValue": '{"rowCount":3}'}

    async def ok_lakehouse_tables(*_args, **_kwargs):
        return {"status": "tables_found", "via": "fabric_lakehouse_tables_api", "tables": ["Inventory", "Summary"]}

    async def ok_refresh(*_args, **_kwargs):
        return {"status": "refresh_triggered", "via": "powerbi_v1"}

    async def ok_model_data(*_args, **_kwargs):
        return {"status": "queryable", "via": "powerbi_executeQueries", "rowCount": 3, "itemTypes": ["Report"]}

    async def no_clone(*_args, **_kwargs):
        return None

    monkeypatch.setattr(fabric_module, "_collect_accessible_inventory_items", collect_items)
    monkeypatch.setattr(fabric_module, "_create_or_reuse_inventory_item", create_item)
    monkeypatch.setattr(fabric_module, "_update_notebook_definition", ok_definition)
    monkeypatch.setattr(fabric_module, "_run_inventory_notebook", ok_notebook_run)
    monkeypatch.setattr(fabric_module, "_wait_for_lakehouse_tables", ok_lakehouse_tables)
    monkeypatch.setattr(fabric_module, "_refresh_semantic_model", ok_refresh)
    monkeypatch.setattr(fabric_module, "_wait_for_inventory_model_data", ok_model_data)
    async def _ok_struct(*_a, **_k):
        return {"status": "structurally_valid", "via": "powerbi_metadata_tables", "observedTables": ["FabricItems", "FabricItemsByType"], "expectedTables": ["FabricItems", "FabricItemsByType"], "attempts": 1}
    monkeypatch.setattr(fabric_module, "_validate_semantic_model_structure", _ok_struct)
    async def _ok_refresh_status(*_a, **_k):
        return {"status": "Completed", "errorMessage": "", "raw": {"status": "Completed"}}
    monkeypatch.setattr(fabric_module, "_wait_for_powerbi_refresh_completion", _ok_refresh_status)

    async def ok_sql_endpoint(*_args, **_kwargs):
        return {
            "id": "1f5d2b3a-cccc-4dec-9d4e-0123456789ab",
            "connectionString": "tcp:test.example.fabric.microsoft.com,1433",
            "via": "fabric_lakehouse_sql_endpoint",
        }

    monkeypatch.setattr(fabric_module, "_fetch_lakehouse_sql_endpoint", ok_sql_endpoint)

    async def ok_clone(*_args, **_kwargs):
        # Inventory tool now refuses to ship a hand-rolled fallback PBIR;
        # supply a stub clone result so the report is still attempted.
        return {"format": "PBIR", "parts": [{"path": "definition.pbir", "payload": "e30=", "payloadType": "InlineBase64"}]}

    monkeypatch.setattr(fabric_module, "_build_inventory_report_definition_from_clone", ok_clone)

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
    async def ok_directlake_identity(*_args, **_kwargs):
        return {"status": "ok", "ownerMismatch": False}

    async def ok_report_render(*_args, **_kwargs):
        return {
            "status": "rendered",
            "via": "powerbi_exportTo_pdf",
            "token": "POWERBI_API_TOKEN",
            "reportId": "report-1",
            "semanticModelId": "semanticmodel-1",
            "pageCount": 1,
            "firstPage": {"name": "ReportSection1", "displayName": "Inventory"},
            "exportId": "export-1",
        }

    monkeypatch.setattr(fabric_module, "_directlake_identity_diagnostics", ok_directlake_identity)
    monkeypatch.setattr(fabric_module, "_verify_report_renderable", ok_report_render)
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"id": "workspace-1", "capacityId": "capacity-1"}, text="{}"),
                make_response(200, json_body={"value": [{"id": "capacity-1", "state": "Active"}]}, text="{}"),
                make_response(200, json_body={"id": "report-1", "datasetId": "semanticmodel-1"}, text="{}"),
                # Pages list confirms the report renders; ExportTo POST below
                # then succeeds inline (status=Succeeded) so the validation
                # produces via=powerbi_exportTo_pdf with the export id.
                make_response(200, json_body={
                    "value": [
                        {"name": "ReportSection1", "displayName": "Inventory", "order": 0},
                    ],
                }, text="{}"),
            ],
            "POST": [
                make_response(201, json_body={"id": "folder-1", "displayName": "tmp_run"}, text="{}"),
                # SQL endpoint metadata refresh — happens before model creation so
                # the DirectLake binding sees the freshly-written Delta tables.
                make_response(200, json_body={"status": "refreshed"}, text="{}"),
                make_response(200, json_body={"id": "export-1", "status": "Succeeded"}, text="{}"),
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
        return {"status": "queryable", "via": "powerbi_executeQueries", "rowCount": 3, "itemTypes": ["Report"]}

    async def no_clone(*_args, **_kwargs):
        return None

    monkeypatch.setattr(fabric_module, "_collect_accessible_inventory_items", collect_items)
    monkeypatch.setattr(fabric_module, "_create_or_reuse_inventory_item", create_item)
    monkeypatch.setattr(fabric_module, "_update_notebook_definition", ok_definition)
    monkeypatch.setattr(fabric_module, "_run_inventory_notebook", ok_notebook_run)
    monkeypatch.setattr(fabric_module, "_wait_for_lakehouse_tables", ok_lakehouse_tables)
    monkeypatch.setattr(fabric_module, "_refresh_semantic_model", ok_refresh)
    monkeypatch.setattr(fabric_module, "_wait_for_inventory_model_data", ok_model_data)
    async def _ok_struct(*_a, **_k):
        return {"status": "structurally_valid", "via": "powerbi_metadata_tables", "observedTables": ["FabricItems", "FabricItemsByType"], "expectedTables": ["FabricItems", "FabricItemsByType"], "attempts": 1}
    monkeypatch.setattr(fabric_module, "_validate_semantic_model_structure", _ok_struct)
    async def _ok_refresh_status(*_a, **_k):
        return {"status": "Completed", "errorMessage": "", "raw": {"status": "Completed"}}
    monkeypatch.setattr(fabric_module, "_wait_for_powerbi_refresh_completion", _ok_refresh_status)

    monkeypatch.setattr(fabric_module, "_build_inventory_report_definition_from_clone", no_clone)

    # Inventory tool now prefers Direct Lake; the test scenario writes
    # data via the notebook, so simulate a successful Lakehouse SQL
    # endpoint lookup. The actual model-build code falls back to the
    # DATATABLE flavor when this is missing, but the assertion below
    # explicitly checks the success path with a Direct-Lake-bound model
    # would still produce reportRenderValidation via powerbi_exportTo_pdf.
    async def ok_sql_endpoint(*_args, **_kwargs):
        return {
            "id": "1f5d2b3a-cccc-4dec-9d4e-0123456789ab",
            "connectionString": "tcp:test.example.fabric.microsoft.com,1433",
            "via": "fabric_lakehouse_sql_endpoint",
        }

    monkeypatch.setattr(fabric_module, "_fetch_lakehouse_sql_endpoint", ok_sql_endpoint)

    result = await fabric_create_workspace_inventory_solution("workspace-1", "tmp_run")

    body = json.loads(result)
    assert body["status"] == "created"
    assert body["sourceItemCount"] == 3
    assert body["preCreationSourceItemCount"] == 1
    assert body["capacityValidation"]["status"] == "active"
    assert body["semanticModelDataValidation"]["status"] == "queryable"
    assert body["reportRenderValidation"] == {
        "status": "rendered",
        "via": "powerbi_exportTo_pdf",
        "token": "POWERBI_API_TOKEN",
        "reportId": "report-1",
        "semanticModelId": "semanticmodel-1",
        "pageCount": 1,
        "firstPage": {"name": "ReportSection1", "displayName": "Inventory"},
        "exportId": "export-1",
    }
    assert any(item.get("type") == "Report" for item in body["createdItems"])
    assert any(item.get("type") == "ReportRenderValidation" for item in body["createdItems"])
    assert body["cleanup"] == []


@pytest.mark.asyncio
async def test_inventory_solution_passes_when_export_capacity_limited_but_pages_render(monkeypatch, fabric_token_env) -> None:
    """Capacity-limited workspaces (Pro / non-PDF-export SKUs) must still
    succeed when the report metadata + pages list confirm the PBIR is
    bound and renderable. Previously the tool deleted the working report
    because Power BI returned 202 with no export id from ExportTo, which
    is the documented behaviour on capacities that do not allow PDF
    export. With the layered validator the verification falls back to
    ``via=powerbi_pages_metadata`` and the report is preserved.
    """
    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)
    async def ok_directlake_identity(*_args, **_kwargs):
        return {"status": "ok", "ownerMismatch": False}

    async def ok_report_render(*_args, **_kwargs):
        return {
            "status": "rendered",
            "via": "powerbi_pages_metadata",
            "reportId": "report-1",
            "semanticModelId": "semanticmodel-1",
            "pageCount": 1,
            "firstPage": {"name": "ReportSection1", "displayName": "Inventory"},
            "exportStatus": "capacity_limited",
            "exportWarning": "Power BI ExportTo accepted the request but did not return an export id",
        }

    monkeypatch.setattr(fabric_module, "_directlake_identity_diagnostics", ok_directlake_identity)
    monkeypatch.setattr(fabric_module, "_verify_report_renderable", ok_report_render)
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(200, json_body={"id": "workspace-1", "capacityId": "capacity-1"}, text="{}"),
                make_response(200, json_body={"value": [{"id": "capacity-1", "state": "Active"}]}, text="{}"),
                make_response(200, json_body={"value": []}, text="{}"),
                make_response(200, json_body={"id": "report-1", "datasetId": "semanticmodel-1"}, text="{}"),
                # Real Pro/free capacity behaviour: pages list resolves but
                # ExportTo returns 202 + Location with no body id.
                make_response(200, json_body={
                    "value": [
                        {"name": "ReportSection1", "displayName": "Inventory", "order": 0},
                    ],
                }, text="{}"),
            ],
            "POST": [
                make_response(201, json_body={"id": "folder-1", "displayName": "tmp_run"}, text="{}"),
                # SQL endpoint metadata refresh consumes a POST slot before
                # ExportTo is even attempted.
                make_response(200, json_body={"status": "refreshed"}, text="{}"),
                # 202 with empty body — the historical failure trigger.
                make_response(202, json_body={}, text="{}"),
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

    async def ok_clone(*_args, **_kwargs):
        return {"format": "PBIR", "parts": [{"path": "definition.pbir", "payload": "e30=", "payloadType": "InlineBase64"}]}

    async def ok_sql_endpoint(*_args, **_kwargs):
        return {"id": "1f5d2b3a-cccc-4dec-9d4e-0123456789ab", "connectionString": "tcp:test.example.fabric.microsoft.com,1433", "via": "fabric_lakehouse_sql_endpoint"}

    monkeypatch.setattr(fabric_module, "_collect_accessible_inventory_items", collect_items)
    monkeypatch.setattr(fabric_module, "_create_or_reuse_inventory_item", create_item)
    monkeypatch.setattr(fabric_module, "_update_notebook_definition", ok_definition)
    monkeypatch.setattr(fabric_module, "_run_inventory_notebook", ok_notebook_run)
    monkeypatch.setattr(fabric_module, "_wait_for_lakehouse_tables", ok_lakehouse_tables)
    monkeypatch.setattr(fabric_module, "_refresh_semantic_model", ok_refresh)
    monkeypatch.setattr(fabric_module, "_wait_for_inventory_model_data", ok_model_data)
    async def _ok_struct(*_a, **_k):
        return {"status": "structurally_valid", "via": "powerbi_metadata_tables", "observedTables": ["FabricItems", "FabricItemsByType"], "expectedTables": ["FabricItems", "FabricItemsByType"], "attempts": 1}
    monkeypatch.setattr(fabric_module, "_validate_semantic_model_structure", _ok_struct)
    async def _ok_refresh_status(*_a, **_k):
        return {"status": "Completed", "errorMessage": "", "raw": {"status": "Completed"}}
    monkeypatch.setattr(fabric_module, "_wait_for_powerbi_refresh_completion", _ok_refresh_status)
    monkeypatch.setattr(fabric_module, "_build_inventory_report_definition_from_clone", ok_clone)
    monkeypatch.setattr(fabric_module, "_fetch_lakehouse_sql_endpoint", ok_sql_endpoint)

    result = await fabric_create_workspace_inventory_solution("workspace-1", "tmp_run")

    body = json.loads(result)
    assert body["status"] == "created"
    validation = body["reportRenderValidation"]
    assert validation["status"] == "rendered"
    assert validation["via"] == "powerbi_pages_metadata"
    assert validation["pageCount"] == 1
    assert validation["firstPage"] == {"name": "ReportSection1", "displayName": "Inventory"}
    assert validation["exportStatus"] == "capacity_limited"
    assert "did not return an export id" in validation["exportWarning"]
    assert any(item.get("type") == "Report" for item in body["createdItems"])
    assert body["cleanup"] == []
    assert not any(call[0] == "DELETE" for call in fake.calls)


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
    async def _ok_struct(*_a, **_k):
        return {"status": "structurally_valid", "via": "powerbi_metadata_tables", "observedTables": ["FabricItems", "FabricItemsByType"], "expectedTables": ["FabricItems", "FabricItemsByType"], "attempts": 1}
    monkeypatch.setattr(fabric_module, "_validate_semantic_model_structure", _ok_struct)
    async def _ok_refresh_status(*_a, **_k):
        return {"status": "Completed", "errorMessage": "", "raw": {"status": "Completed"}}
    monkeypatch.setattr(fabric_module, "_wait_for_powerbi_refresh_completion", _ok_refresh_status)
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

@pytest.mark.asyncio
async def test_validate_semantic_model_structure_passes_when_expected_tables_present(
    monkeypatch, fabric_token_env
) -> None:
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
                            {
                                "tables": [
                                    {
                                        "rows": [
                                            {"[Name]": "FabricItems"},
                                            {"[Name]": "FabricItemsByType"},
                                            {"[Name]": "DateTable"},
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    text="{}",
                ),
            ]
        }
    )

    result = await _validate_semantic_model_structure(
        fake, "workspace-1", "semanticmodel-1", {"FabricItems", "FabricItemsByType"}
    )

    assert isinstance(result, dict)
    assert result["status"] == "structurally_valid"
    assert result["via"] == "powerbi_executeQueries_INFO_TABLES"
    assert "FabricItems" in result["observedTables"]
    assert fake.calls[0][1].endswith("/datasets/semanticmodel-1/executeQueries")


@pytest.mark.asyncio
async def test_validate_semantic_model_structure_fails_when_table_missing(
    monkeypatch, fabric_token_env
) -> None:
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
                            {"tables": [{"rows": [{"[Name]": "DateTable"}]}]}
                        ]
                    },
                    text="{}",
                ),
            ]
        }
    )

    result = await _validate_semantic_model_structure(
        fake, "workspace-1", "semanticmodel-1", {"FabricItems"}
    )

    assert isinstance(result, str)
    assert "FabricItems" in result
    assert "missing" in result.lower()


@pytest.mark.asyncio
async def test_wait_for_powerbi_refresh_completion_returns_completed(
    monkeypatch, fabric_token_env
) -> None:
    from mcp_servers.fabric import _wait_for_powerbi_refresh_completion

    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)

    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(
                    200,
                    json_body={"value": [{"status": "Completed", "endTime": "2026-04-28T20:00:00Z"}]},
                    text="{}",
                ),
            ]
        }
    )

    result = await _wait_for_powerbi_refresh_completion(
        fake, "workspace-1", "semanticmodel-1", max_attempts=3, poll_interval_seconds=0
    )

    assert result["status"] == "Completed"
    assert result["errorMessage"] == ""
    assert fake.calls[0][1].endswith("/datasets/semanticmodel-1/refreshes?$top=1")


@pytest.mark.asyncio
async def test_wait_for_powerbi_refresh_completion_extracts_failure_details(
    monkeypatch, fabric_token_env
) -> None:
    from mcp_servers.fabric import _wait_for_powerbi_refresh_completion

    async def no_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr("mcp_servers.fabric.asyncio.sleep", no_sleep)

    service_exception = (
        '{"errorCode":"ItemRefreshFailedRetryAttemptsExceeded",'
        '"errorDescription":"We cannot access the source Delta table'
        " 'Inv281823218194ad_FabricItems' referenced by table 'FabricItems'."
        ' Either the source Delta table does not exist, or you don\\u0027t have access permissions."}'
    )
    fake = FakeAsyncClient(
        responses_by_method={
            "GET": [
                make_response(
                    200,
                    json_body={
                        "value": [
                            {
                                "status": "Failed",
                                "serviceExceptionJson": service_exception,
                            }
                        ]
                    },
                    text="{}",
                ),
            ]
        }
    )

    result = await _wait_for_powerbi_refresh_completion(
        fake, "workspace-1", "semanticmodel-1", max_attempts=3, poll_interval_seconds=0
    )

    assert result["status"] == "Failed"
    assert "Inv281823218194ad_FabricItems" in result["errorMessage"]
    assert "FabricItems" in result["errorMessage"]


def test_validate_semantic_model_definition_passes_for_correct_directlake_tmsl() -> None:
    from mcp_servers.fabric import (
        _semantic_model_definition_directlake,
        _validate_semantic_model_definition,
    )
    definition = _semantic_model_definition_directlake(
        table_name="inv_FabricItems",
        summary_table_name="inv_FabricItemsByType",
        sql_endpoint_connection_string="abc.datawarehouse.fabric.microsoft.com",
        sql_endpoint_id="12345678-1234-1234-1234-1234567890ab",
    )
    assert _validate_semantic_model_definition(definition) == []


def test_validate_semantic_model_definition_rejects_non_guid_endpoint() -> None:
    from mcp_servers.fabric import (
        _semantic_model_definition_directlake,
        _validate_semantic_model_definition,
    )
    definition = _semantic_model_definition_directlake(
        table_name="inv_FabricItems",
        summary_table_name=None,
        sql_endpoint_connection_string="cs",
        sql_endpoint_id="not-a-guid",
    )
    errs = _validate_semantic_model_definition(definition)
    assert errs
    assert any("GUID" in err for err in errs)


def test_validate_semantic_model_definition_rejects_missing_partition_expression() -> None:
    """A Direct Lake partition that references a missing shared
    expression must be rejected — this is the exact mistake that
    produces 'expressionSource was not found' refresh errors."""
    from mcp_servers.fabric import _inline_json_part, _validate_semantic_model_definition
    bim = {
        "compatibilityLevel": 1604,
        "model": {
            "expressions": [
                {"name": "DatabaseQuery", "kind": "m", "expression": [
                    'let database = Sql.Database("cs", "12345678-1234-1234-1234-1234567890ab") in database'
                ]}
            ],
            "tables": [
                {
                    "name": "T",
                    "columns": [{"name": "C", "dataType": "string", "sourceColumn": "C"}],
                    "partitions": [{
                        "name": "p",
                        "mode": "directLake",
                        "source": {
                            "type": "entity",
                            "entityName": "T",
                            "schemaName": "dbo",
                            "expressionSource": "WrongName",
                        },
                    }],
                }
            ],
        },
    }
    definition = {"format": "TMSL", "parts": [_inline_json_part("model.bim", bim)]}
    errs = _validate_semantic_model_definition(definition)
    assert any("WrongName" in err for err in errs)
