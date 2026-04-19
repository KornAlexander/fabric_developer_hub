"""Gather the destination workspace's CURRENT STATE before calling the LLM.

The Job 2 spec forbids the planner from guessing at reality. Every fact
that the planner sees about the destination workspace comes from here.

Authentication model
--------------------
All lookups are dispatched through the MCP manager's ``call_tool`` which
forwards the user's OBO-exchanged Fabric token (``mcp_tokens['fabric']``).
That token is acquired via ``github_chat_controller._acquire_mcp_tokens``
from the *caller's* Fabric bearer, so every lookup runs as the user and
respects tenant isolation.

Timeouts
--------
Each tool call is wrapped in ``asyncio.wait_for`` with a generous
per-call timeout (``_TOOL_TIMEOUT_S``) so one slow endpoint can't stall
plan generation. Individual failures are captured in
``WorkspaceSnapshot.lookup_failures`` rather than raising — the planner
needs to know the picture is incomplete so it can return a clarification
step instead of hallucinating.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable
from typing import Any

from domain.models.plan import WorkspaceSnapshot

logger = logging.getLogger(__name__)


_TOOL_TIMEOUT_S = 15.0
_MAX_ITEMS_TO_DEEP_FETCH_PER_TYPE = 10
_MAX_TABLES_PER_ITEM = 50

# Canonical Fabric item types we know how to deep-fetch. The tuple maps
# *intent/attachment keywords* → *Fabric item type names* so callers can
# say "mentioned 'lakehouse'" and we'll reach for ``Lakehouse``.
_TYPE_KEYWORDS: dict[str, str] = {
    "lakehouse": "Lakehouse",
    "warehouse": "Warehouse",
    "notebook": "Notebook",
    "pipeline": "DataPipeline",
    "dataflow": "Dataflow",
    "report": "Report",
    "semantic model": "SemanticModel",
    "semanticmodel": "SemanticModel",
    "dataset": "SemanticModel",
    "kql": "KQLDatabase",
    "eventhouse": "KQLDatabase",
    "sql db": "SQLDatabase",
}


def _restricted_tool_set(*names: str) -> set[str]:
    """Shrink the MCP allow-list to just the tools we need for this fan-out.

    Defense in depth: the MCP manager validates every call against its
    policy allow-list, and we further restrict each individual call to the
    single tool we're invoking so a compromised prompt can't pivot.
    """
    return set(names)


def infer_mentioned_types(
    intent: str,
    attachments: Iterable[dict[str, Any]] | None,
    selected_items: Iterable[dict[str, Any]] | None,
) -> set[str]:
    """Return the set of Fabric item types we should deep-fetch.

    Driven by three signals:
      1. Any ``selected_items[*].type`` the user explicitly chose.
      2. Any ``attachments[*].name`` extension that implies an item type
         (``.pbip`` → Report; ``.pq`` → Dataflow; ``.sql`` → Warehouse;
         ``.ipynb`` → Notebook).
      3. Keyword matches in the user's natural-language intent.
    """
    types: set[str] = set()
    for item in selected_items or []:
        t = str(item.get("type") or "").strip()
        if t and t != "workspace":
            types.add(t)

    for att in attachments or []:
        name = str(att.get("name") or "").lower()
        if name.endswith((".ipynb",)):
            types.add("Notebook")
        elif name.endswith((".pbip", ".pbit")):
            types.add("Report")
        elif name.endswith((".pq",)):
            types.add("Dataflow")
        elif name.endswith((".sql",)):
            types.add("Warehouse")

    text = (intent or "").lower()
    for keyword, canonical in _TYPE_KEYWORDS.items():
        if re.search(rf"\b{re.escape(keyword)}s?\b", text):
            types.add(canonical)

    return types


async def _call_tool_safe(
    mcp_manager: Any,
    tool_name: str,
    args: dict[str, Any],
    mcp_tokens: dict,
    correlation_id: str,
) -> Any | None:
    """Invoke one MCP tool with a timeout; log + swallow failures.

    Returns the parsed JSON payload on success, ``None`` on any failure.
    The failure is recorded by the caller in ``lookup_failures`` so the
    planner knows the world view is incomplete.
    """
    try:
        async with asyncio.timeout(_TOOL_TIMEOUT_S):
            result = await mcp_manager.call_tool(
                tool_name,
                args,
                mcp_tokens,
                allowed_tools=_restricted_tool_set(tool_name),
            )
    except TimeoutError:
        logger.warning(
            "[PLAN][%s] tool timeout name=%s args=%s",
            correlation_id, tool_name, {k: v for k, v in args.items() if k != "token"},
        )
        return None
    except Exception:
        logger.warning(
            "[PLAN][%s] tool failed name=%s", correlation_id, tool_name,
            exc_info=True,
        )
        return None
    try:
        return json.loads(str(result))
    except (TypeError, ValueError):
        logger.warning(
            "[PLAN][%s] tool returned non-JSON payload name=%s", correlation_id, tool_name,
        )
        return None


async def gather_current_state(
    mcp_manager: Any,
    mcp_tokens: dict | None,
    destination_workspace_id: str,
    destination_workspace_name: str | None,
    mentioned_types: set[str],
    *,
    correlation_id: str,
) -> WorkspaceSnapshot:
    """Build a ``WorkspaceSnapshot`` for the destination workspace.

    Strategy:
      1. List all items in the workspace (one Fabric call).
      2. For each item type in ``mentioned_types`` that's also present in
         the workspace, fire item-type-specific deep fetches in parallel
         (capped at ``_MAX_ITEMS_TO_DEEP_FETCH_PER_TYPE`` per type so we
         don't melt the API on 1000-table lakehouses).

    The returned snapshot is safe to inline in a prompt — large tables
    are truncated and opaque fields are dropped.
    """
    snapshot = WorkspaceSnapshot(
        workspace_id=destination_workspace_id,
        workspace_name=destination_workspace_name,
    )

    if mcp_manager is None or mcp_tokens is None:
        snapshot.lookup_failures.append("mcp_unavailable")
        return snapshot

    items_raw = await _call_tool_safe(
        mcp_manager,
        "fabric_list_items",
        {"workspace_id": destination_workspace_id},
        mcp_tokens,
        correlation_id,
    )
    if items_raw is None:
        snapshot.lookup_failures.append("fabric_list_items")
        return snapshot

    items: list[dict[str, Any]] = []
    for it in items_raw if isinstance(items_raw, list) else []:
        if not isinstance(it, dict):
            continue
        items.append({
            "id": it.get("id"),
            "displayName": it.get("displayName") or it.get("name"),
            "type": it.get("type"),
            "description": (it.get("description") or "")[:200] or None,
        })
    snapshot.items = items

    # Deep fetch only for types the user actually mentioned.
    lakehouses = [i for i in items if i["type"] == "Lakehouse" and "Lakehouse" in mentioned_types]
    semantic_models = [
        i for i in items
        if i["type"] == "SemanticModel" and "SemanticModel" in mentioned_types
    ]

    tasks: list[tuple[str, str, asyncio.Task]] = []

    for lh in lakehouses[:_MAX_ITEMS_TO_DEEP_FETCH_PER_TYPE]:
        task = asyncio.create_task(
            _call_tool_safe(
                mcp_manager,
                "sl_get_lakehouse_tables",
                {"workspace_id": destination_workspace_id, "lakehouse_id": lh["id"]},
                mcp_tokens,
                correlation_id,
            )
        )
        tasks.append(("lakehouse_tables", lh["id"], task))

    for sm in semantic_models[:_MAX_ITEMS_TO_DEEP_FETCH_PER_TYPE]:
        task = asyncio.create_task(
            _call_tool_safe(
                mcp_manager,
                "sl_get_semantic_model_tables",
                {"workspace_id": destination_workspace_id, "model_id": sm["id"]},
                mcp_tokens,
                correlation_id,
            )
        )
        tasks.append(("sm_tables", sm["id"], task))

    if tasks:
        await asyncio.gather(*[t[2] for t in tasks], return_exceptions=False)

    for kind, item_id, task in tasks:
        data = task.result()
        if data is None:
            snapshot.lookup_failures.append(f"{kind}:{item_id}")
            continue
        # Normalize + truncate.
        rows = data if isinstance(data, list) else []
        compact = [
            {k: v for k, v in r.items() if k in ("name", "type", "format", "location", "columns")}
            for r in rows[:_MAX_TABLES_PER_ITEM]
            if isinstance(r, dict)
        ]
        if kind == "lakehouse_tables":
            snapshot.lakehouse_tables[item_id] = compact
        else:
            snapshot.semantic_model_tables[item_id] = compact

    logger.info(
        "[PLAN][%s] snapshot ws=%s items=%d deep_types=%s failures=%d",
        correlation_id, destination_workspace_id, len(snapshot.items),
        sorted(mentioned_types), len(snapshot.lookup_failures),
    )
    return snapshot


def authorize_destination(
    workspace_id: str,
    user_accessible_workspace_ids: Iterable[str],
) -> None:
    """Raise ``PermissionError`` if the destination workspace is not visible
    to the caller via their OBO-scoped workspace list.

    This is the cross-check required by the Job 2 spec: we never issue a
    Fabric lookup for a workspace the user cannot see, to prevent the
    planner from being used as a confused deputy.
    """
    normalized = {str(w).lower() for w in user_accessible_workspace_ids if w}
    if str(workspace_id).lower() not in normalized:
        raise PermissionError(
            f"User does not have access to destination workspace {workspace_id}"
        )
