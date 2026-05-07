"""Workspace context assembly for the Compose LLM.

Builds a structured text block describing the destination workspace
inventory and any explicitly referenced items so the Composer can make
workspace-aware team-shape decisions (greenfield vs brownfield, which
items to reuse, etc.).

The service operates on data the frontend already has (workspace item
lists) supplemented by lightweight Fabric API lookups for explicitly
referenced items.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

@dataclass
class WorkspaceInventory:
    """Items that exist in a single workspace."""

    workspace_id: str
    workspace_name: str
    items: list[dict[str, Any]] = field(default_factory=list)
    is_destination: bool = False


@dataclass
class WorkspaceContext:
    """Assembled context about workspaces and items, ready for prompt
    injection."""

    inventories: list[WorkspaceInventory] = field(default_factory=list)
    referenced_items: list[dict[str, Any]] = field(default_factory=list)

    def render(self) -> str:
        """Format as a text block suitable for the compose user message."""
        parts: list[str] = []

        if self.inventories:
            parts.append("WORKSPACE INVENTORY:")
            for inv in self.inventories:
                tag = "Destination" if inv.is_destination else "Referenced"
                parts.append(
                    f'── {tag}: "{inv.workspace_name}" '
                    f"(workspace_id={inv.workspace_id}) ──"
                )
                items = inv.items
                if not items:
                    parts.append("  (empty workspace)")
                else:
                    for it in items:
                        name = it.get("name") or it.get("displayName") or "?"
                        itype = it.get("type") or "Item"
                        item_id = it.get("id") or ""
                        parts.append(f"  {itype}: {name} (id={item_id})")
                parts.append("")  # blank line between workspaces

        if self.referenced_items:
            parts.append("REFERENCED ITEMS:")
            for ref in self.referenced_items:
                name = ref.get("name") or "?"
                itype = ref.get("type") or "Item"
                item_id = ref.get("id") or ""
                parts.append(f"── {name} ({itype}, id={item_id}) ──")
                details = ref.get("details")
                if details:
                    parts.append(f"  {details}")
                parts.append("")

        return "\n".join(parts).rstrip()

    def is_empty(self) -> bool:
        return not self.inventories and not self.referenced_items


def build_workspace_context(
    *,
    workspace_id: str,
    workspace_name: str | None = None,
    context_items: list[dict[str, Any]] | None = None,
    workspace_items: list[dict[str, Any]] | None = None,
    referenced_workspace_items: dict[str, list[dict[str, Any]]] | None = None,
) -> WorkspaceContext:
    """Build a ``WorkspaceContext`` from data the frontend already has.

    Parameters
    ----------
    workspace_id:
        The destination workspace UUID.
    workspace_name:
        Display name of the destination workspace.
    context_items:
        The ``context_items`` array from the frontend (pills below the
        composer). Each entry has ``{name, type, id?, workspaceId?}``.
    workspace_items:
        Full item list for the destination workspace, as returned by
        ``GET /api/workspaces/{id}/items``. If provided, avoids an
        extra API call.
    referenced_workspace_items:
        Map of ``workspace_id -> items[]`` for non-destination workspaces
        referenced via pills. Frontend sends these from its mention-picker
        cache.

    Returns a ``WorkspaceContext`` ready for ``render()``.
    """
    context_items = context_items or []
    workspace_items = workspace_items or []
    referenced_workspace_items = referenced_workspace_items or {}

    ctx = WorkspaceContext()

    # ── Destination workspace inventory ──────────────────────────
    ctx.inventories.append(WorkspaceInventory(
        workspace_id=workspace_id,
        workspace_name=workspace_name or workspace_id,
        items=workspace_items,
        is_destination=True,
    ))

    # ── Referenced workspaces (from context_items pills) ─────────
    seen_ws: set[str] = {workspace_id}
    for ci in context_items:
        if ci.get("type") == "workspace":
            ws_id = ci.get("id") or ci.get("workspaceId") or ""
            if ws_id and ws_id not in seen_ws:
                seen_ws.add(ws_id)
                items = referenced_workspace_items.get(ws_id, [])
                ctx.inventories.append(WorkspaceInventory(
                    workspace_id=ws_id,
                    workspace_name=ci.get("name") or ws_id,
                    items=items,
                ))

    # ── Explicitly referenced items (non-workspace pills) ────────
    for ci in context_items:
        if ci.get("type") and ci.get("type") != "workspace":
            ctx.referenced_items.append({
                "name": ci.get("name") or "?",
                "type": ci.get("type") or "Item",
                "id": ci.get("id") or "",
                "workspaceId": ci.get("workspaceId") or workspace_id,
            })

    if ctx.is_empty():
        logger.debug("[WORKSPACE_CTX] No workspace context to inject")
    else:
        inv_count = sum(len(i.items) for i in ctx.inventories)
        logger.info(
            "[WORKSPACE_CTX] Built context: %d workspace(s), %d inventory items, %d referenced items",
            len(ctx.inventories), inv_count, len(ctx.referenced_items),
        )

    return ctx
