"""Compute the server-side diff between the user's desired end state and the
destination workspace's current state.

This is the ground truth the planner is required to honour. Without a diff
the LLM will happily propose creating items that already exist, or miss
that an item is the wrong type. With it, the planner's job reduces to
*sequencing* and *explaining* — it is not allowed to invent reality.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from domain.models.plan import DiffEntryAction, WorkspaceSnapshot


def _norm(name: str | None) -> str:
    return (name or "").strip().casefold()


def _extract_name_hints(intent: str) -> list[str]:
    """Pick out quoted / Proper-Case tokens from the intent that look like
    target object names. We use them to spot naming collisions without
    requiring the user to explicitly name every target in ``selected_items``.
    """
    hints: list[str] = []
    # Quoted strings ("foo" or 'foo').
    hints.extend(re.findall(r"['\"]([^'\"]{2,64})['\"]", intent or ""))
    # CamelCase or snake_case-ish proper nouns.
    hints.extend(re.findall(r"\b([A-Z][A-Za-z0-9]{2,}(?:_[A-Za-z0-9]+)*)\b", intent or ""))
    return hints


def compute_diff(
    *,
    intent: str,
    selected_items: Iterable[dict[str, Any]] | None,
    snapshot: WorkspaceSnapshot,
) -> list[DiffEntryAction]:
    """Return a list of diff entries the planner must respect.

    Heuristics (deliberately conservative — the planner can always ask for
    clarification if the diff is thin):

    * For every ``selected_item`` with a type + displayName, look for an
      existing workspace item with the same displayName (case-insensitive).
        - Same name, same type → ``NO_ACTION`` (already present).
        - Same name, different type → ``CONFLICT``.
        - Same name, same type, item is in a *different* workspace →
          ``CREATE`` (we'll need to recreate at the destination).
    * For every name hint from the intent that doesn't match any item, we
      emit a ``CREATE`` placeholder so the LLM knows what's desired.
    * Lookup failures surfaced by the snapshot become ``MISSING_PREREQ``
      entries so the planner produces a ``clarify`` step.
    """
    entries: list[DiffEntryAction] = []

    items_by_name: dict[str, dict[str, Any]] = {
        _norm(it.get("displayName")): it for it in snapshot.items if it.get("displayName")
    }

    selected_list = list(selected_items or [])
    handled_names: set[str] = set()

    for sel in selected_list:
        sel_name = str(sel.get("name") or sel.get("displayName") or "").strip()
        sel_type = str(sel.get("type") or "").strip()
        if not sel_name or sel_type == "workspace":
            continue
        key = _norm(sel_name)
        handled_names.add(key)
        existing = items_by_name.get(key)
        if existing is None:
            entries.append(DiffEntryAction(
                kind="CREATE",
                item_type=sel_type or "unknown",
                display_name=sel_name,
                details="selected_item not present in destination workspace",
            ))
            continue
        if _norm(existing.get("type")) == _norm(sel_type):
            # Source WS differs from destination → planner may still need to
            # create a *new* instance at the destination. Same-workspace
            # match is a true NO_ACTION.
            if sel.get("workspaceId") and str(sel["workspaceId"]).lower() != str(
                snapshot.workspace_id
            ).lower():
                entries.append(DiffEntryAction(
                    kind="CREATE",
                    item_type=sel_type,
                    display_name=sel_name,
                    existing_item_id=None,
                    details=(
                        f"An item named '{sel_name}' of type {sel_type} already exists in "
                        "the destination — but the selected one lives in a different "
                        "workspace. Planner must decide whether to reuse the destination "
                        "copy, rename, or overwrite."
                    ),
                ))
            else:
                entries.append(DiffEntryAction(
                    kind="NO_ACTION",
                    item_type=sel_type,
                    display_name=sel_name,
                    existing_item_id=existing.get("id"),
                    details="Matching item already present in destination",
                ))
        else:
            entries.append(DiffEntryAction(
                kind="CONFLICT",
                item_type=sel_type,
                display_name=sel_name,
                existing_item_id=existing.get("id"),
                details=(
                    f"Destination has an item named '{sel_name}' but its type is "
                    f"{existing.get('type')!r}, not {sel_type!r}."
                ),
            ))

    # Name hints from intent that weren't covered by selected_items.
    for hint in _extract_name_hints(intent):
        key = _norm(hint)
        if key in handled_names:
            continue
        if key in items_by_name:
            entries.append(DiffEntryAction(
                kind="NO_ACTION",
                item_type=items_by_name[key].get("type") or "unknown",
                display_name=hint,
                existing_item_id=items_by_name[key].get("id"),
                details="Name in user intent matches an existing item by displayName",
            ))

    # Snapshot gaps → MISSING_PREREQ so the planner can clarify.
    for failure in snapshot.lookup_failures:
        entries.append(DiffEntryAction(
            kind="MISSING_PREREQ",
            item_type="workspace",
            display_name=snapshot.workspace_name or snapshot.workspace_id,
            details=f"Current-state lookup failed: {failure}",
        ))

    return entries
