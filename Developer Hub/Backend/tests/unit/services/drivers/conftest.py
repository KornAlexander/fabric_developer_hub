"""Shared fixtures for architecture driver tests.

Provides mock compositions, executions, and a fake SlotRunner that
records calls without touching any real LLM or MCP infrastructure.
"""
from __future__ import annotations

import asyncio
from typing import Literal
from unittest.mock import MagicMock

import pytest

from domain.models.agent_models import (
    AgentAssignment,
    AgentDecision,
    AgentStatus,
    Job,
    JobStatus,
    PhaseStatus,
    ReasoningPhase,
)
from domain.models.composition import (
    AgentSlot,
    Budget,
    Composition,
    Handoff,
)
from services.agenthub.drivers.budget import BudgetTracker
from services.agenthub.drivers.handoff import HandoffPayload
from services.agenthub.drivers.slot_runner import SlotResult, SlotRunner
from services.agenthub.orchestrator_engine import _JobExecution


# ── Fake SlotRunner ──────────────────────────────────────────────────

class FakeSlotRunner:
    """A SlotRunner substitute that records calls and returns scripted
    results. Avoids any real LLM/MCP interaction.
    """

    def __init__(
        self,
        *,
        execution: _JobExecution | None = None,
        budget: BudgetTracker | None = None,
        slot_results: dict[str, str] | None = None,
        slot_decisions: dict[str, str] | None = None,
    ):
        self._execution = execution or MagicMock(cancel_event=asyncio.Event())
        self._budget = budget or BudgetTracker(budget=Budget())
        # slot_id -> status to return (default: "success")
        self._results = slot_results or {}
        # slot_id -> decision text (used by router/reflection for parsing)
        self._decisions = slot_decisions or {}
        self.calls: list[dict] = []
        self._slot_assignments: dict[str, AgentAssignment] = {}
        self._call_counts: dict[str, int] = {}  # per-slot call counter

    def register_slot(self, slot_id: str, assignment: AgentAssignment) -> None:
        self._slot_assignments[slot_id] = assignment

    async def run_slot(
        self,
        slot_id: str,
        *,
        upstream_handoffs: list[HandoffPayload] | None = None,
        max_turns: int | None = None,
        step_label: str | None = None,
    ) -> SlotResult:
        self.calls.append({
            "slot_id": slot_id,
            "upstream_handoffs": upstream_handoffs,
            "max_turns": max_turns,
            "step_label": step_label,
        })

        status: str = self._results.get(slot_id, "success")
        self._call_counts[slot_id] = self._call_counts.get(slot_id, 0) + 1
        call_num = self._call_counts[slot_id]

        # Simulate assignment state changes
        assignment = self._slot_assignments.get(slot_id)
        if assignment:
            if status == "error":
                assignment.status = AgentStatus.ERROR
                assignment.current_step = "Simulated error"
            else:
                assignment.status = AgentStatus.COMPLETED
                assignment.current_step = "Completed"
                decision_text = self._decisions.get(slot_id, f"Slot {slot_id} completed successfully")
                # Append call number to make each iteration unique (avoids stall detection)
                decision_text = f"{decision_text} (call #{call_num})"
                assignment.phases.append(ReasoningPhase(
                    phase_number=call_num,
                    title="Work",
                    description="",
                    status=PhaseStatus.COMPLETED,
                    decisions=[AgentDecision(summary=decision_text)],
                ))

        return SlotResult(slot_id=slot_id, status=status, turns_used=1)

    def extract_handoff(
        self,
        from_slot_id: str,
        to_slot_id: str,
        kind: str,
    ) -> HandoffPayload:
        assignment = self._slot_assignments.get(from_slot_id)
        summary = "No data"
        status: Literal["success", "partial", "error"] = "success"
        if assignment:
            if assignment.status == AgentStatus.ERROR:
                summary = assignment.current_step or "Error"
                status = "error"
            elif assignment.phases and assignment.phases[-1].decisions:
                summary = assignment.phases[-1].decisions[-1].summary
            else:
                summary = f"Slot {from_slot_id} output"
        return HandoffPayload(
            from_slot_id=from_slot_id,
            to_slot_id=to_slot_id,
            kind=kind,
            status=status,
            summary=summary,
        )


# ── Composition builders ─────────────────────────────────────────────

def make_composition(
    *,
    architecture: str = "solo",
    slots: list[dict] | None = None,
    handoffs: list[dict] | None = None,
    entrypoint: str | None = None,
    budget: Budget | None = None,
) -> Composition:
    """Build a Composition from compact dicts."""
    if slots is None:
        slots = [{"id": "s1", "agent_id": "fabric-admin", "role": "Agent"}]

    slot_objs = [
        AgentSlot(
            id=s["id"],
            agent_id=s.get("agent_id", "fabric-admin"),
            role=s.get("role", s["id"]),
            parent_id=s.get("parent_id"),
            subteam=s.get("subteam"),
        )
        for s in slots
    ]
    handoff_objs = []
    for h in (handoffs or []):
        handoff_objs.append(Handoff.model_validate({
            "from": h["from"], "to": h["to"],
            "kind": h.get("kind", "delegate"),
            "condition": h.get("condition"),
        }))

    return Composition(
        session_id="test-session",
        task="Test task",
        architecture=architecture,
        rationale="Test rationale",
        headline="Test headline",
        subtitle="Test subtitle",
        slots=slot_objs,
        handoffs=handoff_objs,
        entrypoint_slot_id=entrypoint or slot_objs[0].id,
        budget=budget or Budget(),
    )


def make_execution(composition: Composition | None = None) -> _JobExecution:
    """Build a _JobExecution with a minimal Job."""
    comp = composition or make_composition()
    job = Job(
        id="job-test-1",
        user_id="u-1",
        workspace_id="ws-1",
        task_description="test",
        composition=comp,
        status=JobStatus.RUNNING,
    )
    exe = _JobExecution(job, copilot_token="fake", mcp_tokens=None)
    # Collect emitted events for assertions
    exe._test_events: list[dict] = []
    original_emit = exe.emit

    def recording_emit(event_type: str, **kwargs):
        exe._test_events.append({"type": event_type, **kwargs})
        original_emit(event_type, **kwargs)

    exe.emit = recording_emit
    return exe


def make_runner(
    execution: _JobExecution,
    composition: Composition,
    *,
    slot_results: dict[str, str] | None = None,
    slot_decisions: dict[str, str] | None = None,
) -> FakeSlotRunner:
    """Build a FakeSlotRunner pre-registered with assignments."""
    runner = FakeSlotRunner(
        execution=execution,
        budget=BudgetTracker(budget=composition.budget),
        slot_results=slot_results,
        slot_decisions=slot_decisions,
    )
    for slot in composition.slots:
        assignment = AgentAssignment(
            agent_id=slot.agent_id,
            session_id=f"sess-{slot.id}",
            role=slot.role,
            goal=f"Do {slot.role}",
            status=AgentStatus.QUEUED,
        )
        runner.register_slot(slot.id, assignment)
        execution.job.agents.append(assignment)
    return runner
