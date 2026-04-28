"""Pump MAF WorkflowEvent instances into ``_JobExecution.emit``.

Responsibilities
----------------
* Subscribe to the stream returned by ``workflow.run(stream=True)``
* For each MAF event, emit a matching entry on the existing SSE
  event queue so the frontend Mission Control view is unchanged
* Swallow MAF-internal adapter events (input normalisation,
  response-to-conversation, aggregator/end nodes) which MAF docs
  explicitly tell consumers they can ignore
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.agenthub.orchestrator_engine import _JobExecution

logger = logging.getLogger(__name__)

# MAF executor ids used by SequentialBuilder for its adapter nodes.
# Emitting these as slot_progress would pollute Mission Control, so
# we suppress them.
_INTERNAL_EXECUTOR_IDS = frozenset({
    "input-conversation",
    "end",
    "complete",
})


def _is_internal_executor(executor_id: str | None) -> bool:
    if not executor_id:
        return False
    if executor_id in _INTERNAL_EXECUTOR_IDS:
        return True
    # SequentialBuilder's per-agent response converters are prefixed.
    if executor_id.startswith("to-conversation:"):
        return True
    return False


async def pump_workflow_events(
    stream: Any,
    execution: _JobExecution,
    *,
    composition_id: str | None = None,
) -> None:
    """Consume a MAF stream and emit a matching sequence of events.

    Terminal MAF event types are mapped onto a single ``run_overview``
    snapshot emit at the end so the UI gets a consistent final state.
    The actual ``job_complete`` / ``job_failed`` emits are the
    orchestrator's responsibility — this function only translates
    mid-run activity.
    """
    async for event in stream:
        try:
            _emit_one(event, execution)
        except Exception:  # pragma: no cover — logging only
            logger.exception("[MAF_EVENT] Failed to translate event %r", event)


def _emit_one(event: Any, execution: _JobExecution) -> None:
    """Translate a single MAF event into an orchestrator emit."""
    etype = getattr(event, "type", None) or type(event).__name__
    executor_id = getattr(event, "executor_id", None) or getattr(event, "source_id", None)

    # Ignore internal adapter nodes outright.
    if _is_internal_executor(executor_id):
        logger.debug("[MAF_EVENT] Skipping internal executor %s", executor_id)
        return

    # MAF's python event model uses string-ish ``type`` on many events.
    etype_str = str(etype).lower()

    if "executorinvoke" in etype_str or etype_str == "executor_invoke":
        execution.emit(
            "slot_progress",
            slotId=executor_id,
            agentId=executor_id,
            status="running",
        )
    elif "executorcompleted" in etype_str or etype_str == "executor_completed":
        execution.emit(
            "slot_progress",
            slotId=executor_id,
            agentId=executor_id,
            status="completed",
        )
    elif etype_str == "output":
        # Shared-conversation output — surface as a phase_detail so it
        # lands on the session timeline but doesn't get counted as a
        # separate slot.
        execution.emit(
            "phase_detail",
            source="maf",
            detail="workflow produced output snapshot",
        )
    elif etype_str == "status":
        state = getattr(event, "state", None)
        execution.emit(
            "phase_detail",
            source="maf",
            detail=f"workflow state: {state}",
        )
    else:
        logger.debug("[MAF_EVENT] Unmapped event type=%s executor=%s", etype_str, executor_id)
