from __future__ import annotations

from services.agenthub import tool_policies
from services.agenthub.tool_runtime import (
    ToolSensitivity,
    clear_registry_for_tests,
    get_policy,
)


def test_register_all_loads_declared_policies() -> None:
    clear_registry_for_tests()
    try:
        tool_policies.register_all()

        assert get_policy("fabric_list_workspaces") is not None
        assert get_policy("fabric_list_workspaces").auto_allowed is True
        assert get_policy("fabric_create_workspace").sensitivity is ToolSensitivity.WRITE
        assert get_policy("fabric_create_item").auto_allowed is True
        assert get_policy("fabric_create_folder").auto_allowed is True
        assert get_policy("fabric_validate_workspace_capacity").sensitivity is ToolSensitivity.READ_SAFE
        assert get_policy("fabric_verify_report_renderable").sensitivity is ToolSensitivity.READ_SENSITIVE
        assert get_policy("fabric_verify_report_renderable").auto_allowed is True
        assert get_policy("fabric_verify_workspace_inventory_solution").sensitivity is ToolSensitivity.READ_SENSITIVE
        assert get_policy("fabric_verify_workspace_inventory_solution").auto_allowed is True
        assert get_policy("browser_verify_visual_render").sensitivity is ToolSensitivity.READ_SENSITIVE
        assert get_policy("browser_verify_visual_render").auto_allowed is True
        assert get_policy("fabric_definition_checkout").sensitivity is ToolSensitivity.READ_SENSITIVE
        assert get_policy("fabric_definition_checkout").auto_allowed is True
        assert get_policy("fabric_definition_publish").sensitivity is ToolSensitivity.WRITE
        assert get_policy("fabric_definition_publish").auto_allowed is False
        assert get_policy("fabric_definition_discard_checkout").sensitivity is ToolSensitivity.WRITE
        assert get_policy("fabric_definition_discard_checkout").auto_allowed is False
        assert get_policy("docs").sensitivity is ToolSensitivity.READ_SAFE
        assert get_policy("list_workspaces").sensitivity is ToolSensitivity.READ_SAFE
        assert get_policy("list_workspaces").auto_allowed is True
        assert get_policy("get_item_definition").sensitivity is ToolSensitivity.READ_SENSITIVE
        assert get_policy("get_item_definition").auto_allowed is True
        assert get_policy("create_workspace").sensitivity is ToolSensitivity.WRITE
        assert get_policy("create_workspace").auto_allowed is False
        assert get_policy("delete_workspace").sensitivity is ToolSensitivity.DESTRUCTIVE
        assert get_policy("delete_workspace").auto_allowed is False
        assert get_policy("web_search").sensitivity is ToolSensitivity.READ_SAFE
        assert get_policy("web_fetch_url").sensitivity is ToolSensitivity.READ_SENSITIVE
    finally:
        clear_registry_for_tests()


def test_warn_about_unregistered_returns_only_unknown_tools(caplog) -> None:
    unknown = tool_policies.warn_about_unregistered([
        "fabric_list_items",
        "missing_tool",
        "missing_tool",
        "another_missing_tool",
    ])

    assert unknown == ["another_missing_tool", "missing_tool"]
    assert "missing_tool" in caplog.text
