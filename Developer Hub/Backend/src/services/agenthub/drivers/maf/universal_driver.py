"""MAFUniversalDriver — one driver for every architecture.

Replaces all legacy topology drivers (solo, sequential, parallel,
supervisor, hierarchical, reflection, mixed, router, network) with a
single class that delegates topology translation to
[MAFWorkflowBuilder.build](./workflow_builder.py).

Event pumping, budget short-circuits and error handling remain
identical to ``MAFSequentialDriver`` — we keep the observable
contract with ``_JobExecution`` unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from services.agenthub.drivers.maf.event_adapter import pump_workflow_events
from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

if TYPE_CHECKING:
    from domain.models.composition import Composition
    from services.agenthub.drivers.budget import BudgetTracker
    from services.agenthub.drivers.slot_runner import SlotRunner
    from services.agenthub.drivers.step_tracker import StepTracker
    from services.agenthub.orchestrator_engine import _JobExecution

logger = logging.getLogger(__name__)


class MAFUniversalDriver:
    """Architecture-agnostic driver. Topology is read from
    ``composition.architecture`` at ``run()`` time and dispatched to
    the matching MAF builder."""

    async def run(
        self,
        composition: Composition,
        execution: _JobExecution,
        slot_runner: SlotRunner,
        budget: BudgetTracker,
        tracker: StepTracker | None = None,
    ) -> None:
        if execution.cancel_event.is_set():
            return

        budget_reason = budget.check_budget()
        if budget_reason:
            logger.info(
                "[MAF] Budget exhausted before workflow start: %s", budget_reason,
            )
            execution.emit("budget_exhausted", reason=budget_reason)
            return

        from services.agenthub.agent_registry import get_template

        builder = MAFWorkflowBuilder(slot_runner=slot_runner)
        try:
            workflow = builder.build(composition, get_template)
        except ValueError as exc:
            logger.error("[MAF] Cannot build workflow: %s", exc)
            execution.emit(
                "phase_detail",
                source="maf",
                detail=f"maf build error: {exc}",
            )
            raise

        if tracker:
            tracker.log_phase(
                f"MAF {composition.architecture} workflow "
                f"({len(composition.slots)} slots)",
            )

        initial_input = _composition_prompt(composition)
        logger.info(
            "[MAF] Starting workflow arch=%s slots=%d",
            composition.architecture, len(composition.slots),
        )
        try:
            stream = workflow.run(initial_input, stream=True)
            await pump_workflow_events(stream, execution)
        except Exception as exc:
            # MAF raises WorkflowConvergenceException when a cyclic graph
            # exceeds its max_iterations cap. For us that's an expected
            # end state (typically: budget exhausted but a handoff cycle
            # kept producing messages), so we degrade gracefully instead
            # of marking the whole driver run as failed.
            exc_name = type(exc).__name__
            if exc_name == "WorkflowConvergenceException":
                logger.warning(
                    "[MAF] Workflow stopped at max iterations (%s) — treating as graceful end",
                    exc,
                )
                execution.emit(
                    "phase_detail",
                    source="maf",
                    detail=f"workflow halted: {exc}",
                )
                return
            logger.exception("[MAF] Workflow execution failed")
            execution.emit(
                "phase_detail",
                source="maf",
                detail=f"maf workflow failed: {exc}",
            )
            raise
        finally:
            if tracker:
                tracker.log_session_summary()


def _composition_prompt(composition: Composition) -> str:
    """Derive the initial user prompt — mirrors ``MAFSequentialDriver``."""
    prompt = getattr(composition, "prompt", None)
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return ""
