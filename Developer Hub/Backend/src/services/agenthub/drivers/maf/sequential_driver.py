"""MAFSequentialDriver — drop-in replacement for ``SequentialDriver``.

Implements the ``ArchitectureDriver`` protocol so
``DriverRegistry.get('sequential')`` can return this driver when the
``FEATURE_DRIVER_MAF_SEQUENTIAL_ENABLED`` feature flag is set.

The flow is identical in observable behaviour to
``SequentialDriver``:

1. Resolve slot execution order from handoffs (or falls back to
   composition order).
2. Run each slot via MAF's ``SequentialBuilder`` — each participant
   is a ``ContainerAgent`` wrapping the existing ``SlotRunner``,
   so slots still execute in isolated containers.
3. MAF events are pumped back through ``_JobExecution.emit`` so
   the frontend SSE stream stays unchanged.

If ``agent_framework`` is unavailable at runtime, the driver logs a
warning and falls through to the legacy driver via a hard-registered
fallback in ``drivers/__init__.py``. We don't re-check availability
here — the registry decides whether to instantiate us.
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


class MAFSequentialDriver:
    """MAF-backed sequential pipeline driver."""

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
                "[MAF_SEQ] Budget exhausted before workflow start: %s", budget_reason,
            )
            execution.emit("budget_exhausted", reason=budget_reason)
            return

        builder = MAFWorkflowBuilder(slot_runner=slot_runner)

        # Import locally — keeps the dependency on ``agent_registry``
        # explicit at the boundary where we need it.
        from services.agenthub.agent_registry import get_template

        try:
            workflow = builder.build_sequential(composition, get_template)
        except ImportError as exc:
            logger.warning(
                "[MAF_SEQ] agent_framework missing at runtime — aborting MAF "
                "driver, falling back is the registry's job: %s", exc,
            )
            execution.emit(
                "phase_detail",
                source="maf",
                detail="agent_framework not installed; MAF driver aborted",
            )
            return
        except ValueError as exc:
            logger.error("[MAF_SEQ] Cannot build workflow: %s", exc)
            execution.emit(
                "phase_detail",
                source="maf",
                detail=f"maf build error: {exc}",
            )
            return

        if tracker:
            tracker.log_phase(
                f"MAF sequential pipeline ({len(composition.slots)} slots)",
            )

        # Extract the mission goal from the composition. Empty string
        # is acceptable — the first agent will still run and rely on
        # its configured goal.
        initial_input = _composition_prompt(composition)

        logger.info(
            "[MAF_SEQ] Starting workflow (slots=%d)", len(composition.slots),
        )
        try:
            stream = workflow.run(initial_input, stream=True)
            await pump_workflow_events(stream, execution)
        except Exception as exc:
            logger.exception("[MAF_SEQ] Workflow execution failed")
            execution.emit(
                "phase_detail",
                source="maf",
                detail=f"maf workflow failed: {exc}",
            )
            return
        finally:
            if tracker:
                tracker.log_session_summary()


def _composition_prompt(composition: Composition) -> str:
    """Derive the initial user prompt for the MAF workflow.

    The composition's first slot carries the mission goal in its
    ``role`` / ``goal`` fields once ``start_job`` sets up assignments
    — but at this driver-level entry point we only have the
    composition itself. Fall back to the composition's own
    ``prompt`` attribute when present, otherwise empty string (the
    ContainerAgent's upstream injection handles the null case).
    """
    prompt = getattr(composition, "prompt", None)
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return ""
