"""Architecture driver protocol and shared utilities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from domain.models.composition import Composition
    from services.agenthub.drivers.budget import BudgetTracker
    from services.agenthub.drivers.slot_runner import SlotRunner
    from services.agenthub.orchestrator_engine import _JobExecution

logger = logging.getLogger(__name__)


@runtime_checkable
class ArchitectureDriver(Protocol):
    """Contract every architecture driver satisfies."""

    async def run(
        self,
        composition: Composition,
        execution: _JobExecution,
        slot_runner: SlotRunner,
        budget: BudgetTracker,
    ) -> None: ...
