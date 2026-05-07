"""Unit tests for ``services.agenthub.workspace_context_service``."""

from __future__ import annotations

from services.agenthub.workspace_context_service import (
    WorkspaceContext,
    WorkspaceInventory,
    build_workspace_context,
)


# ── WorkspaceContext.render() ────────────────────────────────────────

def test_render_empty_context() -> None:
    ctx = WorkspaceContext()
    assert ctx.is_empty()
    assert ctx.render() == ""


def test_render_destination_only() -> None:
    ctx = build_workspace_context(
        workspace_id="ws-1",
        workspace_name="My Workspace",
        workspace_items=[
            {"id": "a1", "name": "Sales_Lakehouse", "type": "Lakehouse"},
            {"id": "a2", "name": "Pipeline_1", "type": "DataPipeline"},
        ],
    )
    rendered = ctx.render()
    assert "Destination:" in rendered
    assert "My Workspace" in rendered
    assert "Lakehouse: Sales_Lakehouse" in rendered
    assert "DataPipeline: Pipeline_1" in rendered
    assert not ctx.is_empty()


def test_render_with_referenced_workspace() -> None:
    ctx = build_workspace_context(
        workspace_id="ws-dest",
        workspace_name="Production",
        workspace_items=[
            {"id": "a1", "name": "Main_Lakehouse", "type": "Lakehouse"},
        ],
        context_items=[
            {"type": "workspace", "name": "Shared Data", "id": "ws-ref"},
        ],
        referenced_workspace_items={
            "ws-ref": [
                {"id": "b1", "name": "Raw_Data", "type": "Lakehouse"},
                {"id": "b2", "name": "DW_Analytics", "type": "Warehouse"},
            ],
        },
    )
    rendered = ctx.render()
    assert 'Destination: "Production"' in rendered
    assert 'Referenced: "Shared Data"' in rendered
    assert "Lakehouse: Raw_Data" in rendered
    assert "Warehouse: DW_Analytics" in rendered


def test_render_with_referenced_items() -> None:
    ctx = build_workspace_context(
        workspace_id="ws-1",
        workspace_name="WS",
        context_items=[
            {"type": "DataPipeline", "name": "Pipeline_1", "id": "p1"},
            {"type": "Lakehouse", "name": "Sales_LH", "id": "lh1", "workspaceId": "ws-1"},
        ],
    )
    rendered = ctx.render()
    assert "REFERENCED ITEMS:" in rendered
    assert "Pipeline_1 (DataPipeline" in rendered
    assert "Sales_LH (Lakehouse" in rendered


def test_render_skips_duplicate_destination_in_referenced() -> None:
    """A workspace referenced via pill that matches the destination
    should not appear twice."""
    ctx = build_workspace_context(
        workspace_id="ws-1",
        workspace_name="WS",
        workspace_items=[{"id": "a1", "name": "LH", "type": "Lakehouse"}],
        context_items=[
            {"type": "workspace", "name": "WS", "id": "ws-1"},
        ],
        referenced_workspace_items={
            "ws-1": [{"id": "a1", "name": "LH", "type": "Lakehouse"}],
        },
    )
    # Should only have 1 inventory (the destination), not a duplicate.
    assert len(ctx.inventories) == 1
    assert ctx.inventories[0].is_destination is True


def test_render_empty_workspace() -> None:
    ctx = build_workspace_context(
        workspace_id="ws-1",
        workspace_name="Empty WS",
        workspace_items=[],
    )
    rendered = ctx.render()
    assert "(empty workspace)" in rendered


def test_render_includes_large_inventories_without_truncation() -> None:
    items = [
        {"id": f"item-{i}", "name": f"Item_{i}", "type": "Notebook"}
        for i in range(120)
    ]
    ctx = build_workspace_context(
        workspace_id="ws-1",
        workspace_name="Big WS",
        workspace_items=items,
    )
    rendered = ctx.render()
    assert "Notebook: Item_0" in rendered
    assert "Notebook: Item_119" in rendered
    assert "more items" not in rendered
    assert "TRUNCATED" not in rendered


def test_render_has_no_total_character_cap() -> None:
    items = [
        {"id": f"item-{i}", "name": f"A_Very_Long_Item_Name_{i}_{'x' * 500}", "type": "Notebook"}
        for i in range(80)
    ]
    ctx = build_workspace_context(
        workspace_id="ws-1",
        workspace_name="Huge WS",
        workspace_items=items,
    )
    rendered = ctx.render()
    assert "A_Very_Long_Item_Name_79" in rendered
    assert "TRUNCATED" not in rendered
    assert len(rendered) > 8_000


# ── build_workspace_context() ────────────────────────────────────────

def test_build_no_context_items() -> None:
    ctx = build_workspace_context(
        workspace_id="ws-1",
    )
    assert len(ctx.inventories) == 1
    assert ctx.inventories[0].workspace_id == "ws-1"
    assert ctx.inventories[0].items == []
    assert ctx.referenced_items == []


def test_build_mixed_context_items() -> None:
    """Workspace pills become inventories; non-workspace pills become
    referenced items."""
    ctx = build_workspace_context(
        workspace_id="dest",
        workspace_name="Dest",
        context_items=[
            {"type": "workspace", "name": "Other", "id": "ws-other"},
            {"type": "Lakehouse", "name": "LH", "id": "lh-1"},
            {"type": "SemanticModel", "name": "SM", "id": "sm-1", "workspaceId": "dest"},
        ],
    )
    # 2 inventories: destination + referenced workspace
    assert len(ctx.inventories) == 2
    assert ctx.inventories[0].is_destination
    assert ctx.inventories[1].workspace_name == "Other"
    # 2 referenced items (LH + SM)
    assert len(ctx.referenced_items) == 2
    names = {r["name"] for r in ctx.referenced_items}
    assert names == {"LH", "SM"}


def test_build_with_none_inputs() -> None:
    """All optional inputs as None should produce a valid (minimal) context."""
    ctx = build_workspace_context(
        workspace_id="ws-1",
        workspace_name=None,
        context_items=None,
        workspace_items=None,
        referenced_workspace_items=None,
    )
    assert len(ctx.inventories) == 1
    assert ctx.inventories[0].workspace_name == "ws-1"  # falls back to id
