"""SlotRunner — wraps ``_run_agent`` with budget, handoff, and status logic.

Drivers call ``run_slot()`` for each slot. The runner handles:
- Pre-flight budget check
- Handoff payload injection into the agent's goal
- Post-slot handoff extraction
- Cancellation propagation
- Step-level observability logging via StepTracker
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from domain.models.agent_models import AgentAssignment, AgentStatus
from services.agenthub.agent_registry import get_template
from services.agenthub.drivers.budget import BudgetTracker
from services.agenthub.drivers.handoff import HandoffExtractor, HandoffPayload

if TYPE_CHECKING:
    from services.agenthub.drivers.step_tracker import StepTracker
    from services.agenthub.orchestrator_engine import _JobExecution

logger = logging.getLogger(__name__)


@dataclass
class SlotResult:
    """Outcome of a single slot execution."""

    slot_id: str
    status: Literal["success", "partial", "error", "cancelled", "budget_exhausted"]
    handoff: HandoffPayload | None = None
    turns_used: int = 0
    tool_calls_used: int = 0


class SlotRunner:
    """Wraps ``_run_agent`` with pre-/post-processing each driver needs."""

    def __init__(
        self,
        execution: _JobExecution,
        engine,  # OrchestratorEngine — kept untyped to avoid circular import
        budget: BudgetTracker,
        tracker: StepTracker | None = None,
    ):
        self._execution = execution
        self._engine = engine
        self._budget = budget
        self._tracker = tracker
        self._extractor = HandoffExtractor()
        # slot_id → AgentAssignment mapping built during start_job
        self._slot_assignments: dict[str, AgentAssignment] = {}

    def register_slot(self, slot_id: str, assignment: AgentAssignment) -> None:
        """Register a slot_id → assignment mapping so run_slot can find it."""
        self._slot_assignments[slot_id] = assignment

    async def run_slot(
        self,
        slot_id: str,
        *,
        upstream_handoffs: list[HandoffPayload] | None = None,
        max_turns: int | None = None,
        step_label: str | None = None,
        allowed_tools_override: set[str] | None = None,
    ) -> SlotResult:
        """Run a single slot and return the result.

        ``step_label``: structured step label from the StepTracker
        (e.g. 'STEP 3 · FabricAdmin'). Used for observability logging.
        """
        _label = step_label or slot_id
        _t0 = time.monotonic()
        assignment = self._slot_assignments.get(slot_id)
        if assignment is None:
            logger.error("[SLOT_RUNNER] No assignment registered for slot %s", slot_id)
            return SlotResult(slot_id=slot_id, status="error")

        # Check cancellation
        if self._execution.cancel_event.is_set():
            return SlotResult(slot_id=slot_id, status="cancelled")

        # Pre-flight budget check
        budget_reason = self._budget.check_budget()
        if budget_reason:
            logger.info("[SLOT_RUNNER] Budget exhausted before slot %s: %s", slot_id, budget_reason)
            self._execution.emit(
                "budget_exhausted", reason=budget_reason, slotId=slot_id,
            )
            return SlotResult(slot_id=slot_id, status="budget_exhausted")

        tpl = get_template(assignment.agent_id)
        if tpl is None:
            logger.error("[SLOT_RUNNER] Unknown agent template '%s' for slot %s", assignment.agent_id, slot_id)
            return SlotResult(slot_id=slot_id, status="error")

        # Inject upstream handoffs into the goal
        if upstream_handoffs:
            prefix_parts = []
            for hp in upstream_handoffs:
                role = self._slot_assignments.get(hp.from_slot_id)
                role_name = role.role if role else hp.from_slot_id
                prefix_parts.append(hp.render_for_injection(role_name))
            handoff_context = "\n\n".join(prefix_parts)
            assignment.goal = f"{handoff_context}\n\n{assignment.goal}"

        # Determine effective max turns
        effective_max = max_turns if max_turns is not None else self._budget.allocate(15)
        effective_max = max(1, effective_max)

        # Set up user queue
        user_q: asyncio.Queue = asyncio.Queue()
        self._execution.user_message_queues[assignment.session_id] = user_q

        if allowed_tools_override is not None:
            allowed = set(allowed_tools_override)
        else:
            # Narrow tools — same logic as start_job
            job = self._execution.job
            slot = None
            if job.composition:
                for s in job.composition.slots:
                    if s.id == slot_id:
                        slot = s
                        break
            selected_skill_ids = {s.id for s in (slot.skills if slot else [])}
            skill_tools: set[str] = set()
            for sk in tpl.skills:
                if not selected_skill_ids or sk.id in selected_skill_ids:
                    skill_tools.update(sk.tools)
            allowed = (
                skill_tools & set(tpl.available_tools)
                if skill_tools
                else set(tpl.available_tools)
            )

        # Patch _run_agent's round limit via monkeypatch on MAX_AGENT_ROUNDS
        # Instead, we'll pass the max_turns through a modified approach:
        # We store it on the assignment for _run_agent to pick up.
        from services.agenthub import orchestrator_engine as oe
        original_max = oe.MAX_AGENT_ROUNDS
        oe.MAX_AGENT_ROUNDS = effective_max
        try:
            await self._engine._run_agent(
                self._execution, assignment, tpl, user_q, allowed_tools=allowed,
            )
        finally:
            oe.MAX_AGENT_ROUNDS = original_max

        # Record budget consumption — use the actual round count stored
        # by _run_agent on the assignment, NOT the phase count (which is
        # inflated by PHASE_START markers in the LLM output).
        actual_rounds = getattr(assignment, '_actual_rounds', 1)
        for _ in range(actual_rounds):
            self._budget.record_turn()
        for _ in range(len(assignment.actions)):
            self._budget.record_tool_call()

        # Determine status
        if assignment.status == AgentStatus.COMPLETED:
            has_decision = any(d for p in assignment.phases for d in p.decisions)
            has_actions = bool(assignment.actions)
            slot_status: Literal["success", "partial", "error", "cancelled", "budget_exhausted"] = (
                "success" if has_decision or has_actions else "partial"
            )
        elif assignment.status == AgentStatus.ERROR:
            if self._execution.cancel_event.is_set():
                slot_status = "cancelled"
            else:
                slot_status = "error"
        else:
            slot_status = "partial"

        _duration = time.monotonic() - _t0
        # Log step completion via tracker if available
        if self._tracker and step_label:
            self._tracker.log_slot_done(
                step_label, slot_id,
                status=slot_status, rounds=actual_rounds,
                tools=len(assignment.actions), duration_s=_duration,
            )

        return SlotResult(
            slot_id=slot_id,
            status=slot_status,
            turns_used=actual_rounds,
            tool_calls_used=len(assignment.actions),
        )

    def extract_handoff(
        self,
        from_slot_id: str,
        to_slot_id: str,
        kind: str,
    ) -> HandoffPayload:
        """Extract a handoff payload from a completed slot."""
        assignment = self._slot_assignments.get(from_slot_id)
        if assignment is None:
            return HandoffPayload(
                from_slot_id=from_slot_id,
                to_slot_id=to_slot_id,
                kind=kind,  # type: ignore[arg-type]
                status="error",
                summary="No assignment found for this slot",
            )
        return self._extractor.extract(from_slot_id, to_slot_id, kind, assignment)
