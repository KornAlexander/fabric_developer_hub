"""Plan pipeline tests — contract, grounding, no-action, conflict, determinism.

These tests cover the Job 2 plan-generation path end-to-end with the LLM
call mocked, so they exercise the diff/snapshot/validate/retry logic
without any network dependency.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from domain.models.plan import (
    Plan,
    PlanValidationError,
    WorkspaceSnapshot,
)
from services.agenthub.orchestrator_engine import OrchestratorEngine
from services.agenthub.plan_diff import compute_diff
from services.agenthub.workspace_state import infer_mentioned_types

# ── helpers ──────────────────────────────────────────────────────────


def _valid_plan_payload(steps: list[dict] | None = None, **overrides: Any) -> dict:
    base = {
        "summary": "Create a bronze lakehouse in the destination workspace.",
        "assumptions": [],
        "prerequisites": [],
        "steps": steps if steps is not None else [
            {
                "id": "s1",
                "order": 1,
                "title": "Create lh_bronze",
                "action": "create",
                "target": {
                    "itemType": "Lakehouse",
                    "displayName": "lh_bronze",
                    "workspaceId": "ws-dest",
                },
                "inputs": [],
                "dependsOn": [],
                "rationale": "Destination has no bronze lakehouse yet.",
                "risk": "low",
                "reversible": True,
            }
        ],
        "noAction": [],
        "conflicts": [],
        "clarificationsNeeded": [],
    }
    base.update(overrides)
    return base


class _FakeEngine(OrchestratorEngine):
    """Engine with every external dependency stubbed."""

    def __init__(
        self,
        *,
        llm_response: str,
        llm_retry_response: str | None = None,
        accessible_workspaces: list[str] | None = None,
        snapshot_items: list[dict] | None = None,
    ) -> None:
        super().__init__()
        self._llm_responses = [llm_response]
        if llm_retry_response is not None:
            self._llm_responses.append(llm_retry_response)
        self._post_calls: list[dict] = []
        self._snapshot_items = snapshot_items or []
        self._accessible = accessible_workspaces

    async def _post_copilot(self, body, headers, correlation_id):  # type: ignore[override]
        self._post_calls.append(body)
        if not self._llm_responses:
            return "{}"
        return self._llm_responses.pop(0)


class _StubMcp:
    """Minimal MCP manager — returns canned payloads for specific tools."""

    def __init__(
        self,
        *,
        workspaces: list[dict] | None = None,
        items: list[dict] | None = None,
    ) -> None:
        self._workspaces = workspaces or [{"id": "ws-dest", "displayName": "Dest"}]
        self._items = items or []
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, args, tokens, allowed_tools=None):  # noqa: D401
        self.calls.append((name, args))
        if name == "fabric_list_workspaces":
            return json.dumps(self._workspaces)
        if name == "fabric_list_items":
            return json.dumps(self._items)
        if name in {"sl_get_lakehouse_tables", "sl_get_semantic_model_tables"}:
            return json.dumps([])
        return "[]"


# ── 1. CONTRACT ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_contract_valid_llm_response() -> None:
    """A well-shaped JSON response validates into a Plan and is returned."""
    engine = _FakeEngine(llm_response=json.dumps(_valid_plan_payload()))
    engine.mcp_manager = _StubMcp()

    plan = await engine.generate_plan(
        task_description="Create a bronze lakehouse called lh_bronze",
        workspace_id="ws-dest",
        copilot_token="tok",
        context={"workspace_name": "Dest"},
        attachments=[],
        mcp_tokens={"fabric": "x"},
    )

    assert isinstance(plan, Plan)
    assert len(plan.steps) == 1
    assert plan.steps[0].target.display_name == "lh_bronze"
    assert plan.job_id  # injected by _parse_plan
    # Camel-case round-trip on dump — new contract exposes workspaceItems
    # in place of noAction and a footer with execution_blocked.
    dumped = plan.model_dump(by_alias=True)
    assert "workspaceItems" in dumped and "clarificationsNeeded" in dumped
    assert "footer" in dumped and "executionBlocked" in dumped["footer"]


# ── 2. GROUNDING ─────────────────────────────────────────────────────


def test_grounding_infer_types_from_selected_items_and_intent() -> None:
    types = infer_mentioned_types(
        intent="Add a semantic model over the Sales warehouse",
        attachments=[{"name": "clean.ipynb"}],
        selected_items=[{"type": "Lakehouse", "name": "lh_raw"}],
    )
    assert "Lakehouse" in types         # from selected_items
    assert "Notebook" in types          # from attachment extension
    assert "SemanticModel" in types     # from intent keyword
    assert "Warehouse" in types         # from intent keyword


@pytest.mark.asyncio
async def test_grounding_prompt_contains_diff_and_snapshot(monkeypatch) -> None:
    """The LLM body must include the computed diff and snapshot so the model
    cannot invent reality."""
    engine = _FakeEngine(llm_response=json.dumps(_valid_plan_payload()))
    engine.mcp_manager = _StubMcp(
        items=[{"id": "it-1", "displayName": "lh_bronze", "type": "Lakehouse"}],
    )

    await engine.generate_plan(
        task_description='Recreate "lh_bronze" as a Notebook',
        workspace_id="ws-dest",
        copilot_token="tok",
        context={"workspace_name": "Dest"},
        attachments=[],
        mcp_tokens={"fabric": "x"},
    )

    assert engine._post_calls, "LLM was never called"
    user_msg = engine._post_calls[0]["messages"][1]["content"]
    body_text = user_msg if isinstance(user_msg, str) else json.dumps(user_msg)
    assert "diff" in body_text
    assert "currentState" in body_text or "current_state" in body_text.lower() or "items" in body_text


# ── 3. NO-ACTION ─────────────────────────────────────────────────────


def test_no_action_when_selected_item_already_matches_destination() -> None:
    snapshot = WorkspaceSnapshot(
        workspace_id="ws-dest",
        items=[{"id": "it-1", "displayName": "lh_bronze", "type": "Lakehouse"}],
    )
    diff = compute_diff(
        intent="Make sure lh_bronze exists",
        selected_items=[{
            "name": "lh_bronze",
            "type": "Lakehouse",
            "workspaceId": "ws-dest",
        }],
        snapshot=snapshot,
    )
    kinds = [d.kind for d in diff]
    assert "NO_ACTION" in kinds
    assert "CONFLICT" not in kinds
    assert "CREATE" not in kinds


# ── 4. CONFLICT ──────────────────────────────────────────────────────


def test_conflict_when_destination_has_same_name_different_type() -> None:
    snapshot = WorkspaceSnapshot(
        workspace_id="ws-dest",
        items=[{"id": "it-9", "displayName": "payload", "type": "Notebook"}],
    )
    diff = compute_diff(
        intent="Create a Lakehouse called payload",
        selected_items=[{
            "name": "payload",
            "type": "Lakehouse",
            "workspaceId": "ws-src",
        }],
        snapshot=snapshot,
    )
    conflict_entries = [d for d in diff if d.kind == "CONFLICT"]
    assert len(conflict_entries) == 1
    assert conflict_entries[0].display_name == "payload"
    assert "Notebook" in conflict_entries[0].details


def test_missing_prereq_entries_surface_from_lookup_failures() -> None:
    snapshot = WorkspaceSnapshot(
        workspace_id="ws-dest",
        lookup_failures=["fabric_list_items timed out"],
    )
    diff = compute_diff(intent="", selected_items=[], snapshot=snapshot)
    kinds = [d.kind for d in diff]
    assert "MISSING_PREREQ" in kinds


# ── 5. DETERMINISM ───────────────────────────────────────────────────


def test_determinism_same_inputs_produce_same_diff() -> None:
    """compute_diff must be a pure function of its inputs."""
    snapshot = WorkspaceSnapshot(
        workspace_id="ws-dest",
        items=[
            {"id": "a", "displayName": "lh_bronze", "type": "Lakehouse"},
            {"id": "b", "displayName": "nb_etl", "type": "Notebook"},
        ],
    )
    selected = [
        {"name": "lh_bronze", "type": "Lakehouse", "workspaceId": "ws-dest"},
        {"name": "lh_silver", "type": "Lakehouse", "workspaceId": "ws-src"},
    ]
    intent = "Promote bronze to silver"
    d1 = compute_diff(intent=intent, selected_items=selected, snapshot=snapshot)
    d2 = compute_diff(intent=intent, selected_items=selected, snapshot=snapshot)
    assert [e.model_dump() for e in d1] == [e.model_dump() for e in d2]


# ── Retry / fallback paths ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_invalid_then_valid_retry_succeeds() -> None:
    engine = _FakeEngine(
        llm_response="not even JSON",
        llm_retry_response=json.dumps(_valid_plan_payload()),
    )
    engine.mcp_manager = _StubMcp()

    plan = await engine.generate_plan(
        task_description="Create lh_bronze",
        workspace_id="ws-dest",
        copilot_token="tok",
        mcp_tokens={"fabric": "x"},
    )
    assert isinstance(plan, Plan)
    assert len(plan.steps) == 1


@pytest.mark.asyncio
async def test_llm_two_failures_raises_plan_validation_error() -> None:
    """Spec §3.2: a second schema failure raises ``PlanValidationError``
    instead of degrading to a clarify-only plan. The controller maps
    this to a 422 so the UI shows the empty state.
    """
    engine = _FakeEngine(
        llm_response="garbage",
        llm_retry_response="also garbage",
    )
    engine.mcp_manager = _StubMcp()

    with pytest.raises(PlanValidationError):
        await engine.generate_plan(
            task_description="Create lh_bronze",
            workspace_id="ws-dest",
            copilot_token="tok",
            mcp_tokens={"fabric": "x"},
        )


@pytest.mark.asyncio
async def test_destination_authorization_rejects_unknown_workspace() -> None:
    engine = _FakeEngine(llm_response=json.dumps(_valid_plan_payload()))
    engine.mcp_manager = _StubMcp(workspaces=[{"id": "ws-other"}])

    with pytest.raises(PermissionError):
        await engine.generate_plan(
            task_description="Create lh_bronze",
            workspace_id="ws-dest",
            copilot_token="tok",
            mcp_tokens={"fabric": "x"},
        )
