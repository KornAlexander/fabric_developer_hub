"""ContainerSlotRunner — runs each slot in its own Docker container.

Drop-in replacement for ``SlotRunner``. Architecture drivers call
``run_slot()`` identically; the only difference is that the agent loop
executes in an isolated container instead of in-process.

Phase 1 design: tool calls are proxied back to the orchestrator via
a simple HTTP callback. The orchestrator runs them through
``tool_runtime.execute()`` (preserving the security chokepoint).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from domain.models.agent_models import AgentAssignment, AgentStatus
from services.agenthub.agent_registry import get_template
from services.agenthub.drivers.budget import BudgetTracker
from services.agenthub.drivers.container_backend import (
    ContainerBackend,
    ContainerInfo,
    SlotContainerConfig,
)
from services.agenthub.drivers.container_pool import ContainerPool
from services.agenthub.drivers.handoff import HandoffExtractor, HandoffPayload
from services.agenthub.drivers.slot_runner import SlotResult

if TYPE_CHECKING:
    from services.agenthub.orchestrator_engine import _JobExecution

logger = logging.getLogger(__name__)

# Defaults — overridable via env vars
_DEFAULT_IMAGE = "agenthub-agent:latest"
_DEFAULT_CPU = 1.0
_DEFAULT_MEMORY = "2g"
_DEFAULT_TIMEOUT_S = 900  # 15 min hard timeout per container
_KILL_GRACE_S = 5


class ContainerSlotRunner:
    """Runs each slot in a Docker container.

    Same ``run_slot()`` / ``extract_handoff()`` interface as
    ``SlotRunner`` so architecture drivers are unchanged.
    """

    def __init__(
        self,
        execution: _JobExecution,
        engine: Any,
        budget: BudgetTracker,
        backend: ContainerBackend,
        pool: ContainerPool,
    ):
        self._execution = execution
        self._engine = engine
        self._budget = budget
        self._backend = backend
        self._pool = pool
        self._extractor = HandoffExtractor()
        self._slot_assignments: dict[str, AgentAssignment] = {}
        self._image = os.environ.get("AGENT_IMAGE", _DEFAULT_IMAGE)
        self._cpu = float(os.environ.get("AGENT_CONTAINER_CPUS", str(_DEFAULT_CPU)))
        self._memory = os.environ.get("AGENT_CONTAINER_MEMORY", _DEFAULT_MEMORY)
        self._network = os.environ.get("AGENT_NETWORK") or None
        self._timeout = float(os.environ.get("AGENT_CONTAINER_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S)))
        self._orchestrator_endpoint = os.environ.get(
            "AGENT_ORCHESTRATOR_ENDPOINT", "http://host.docker.internal:5000"
        )

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
        """Run a single slot in an isolated container.

        ``step_label`` mirrors the in-process ``SlotRunner.run_slot``
        signature so the MAF ``ContainerAgent`` can call either runner
        interchangeably. The container path doesn't currently surface
        the label through the StepTracker (the container produces its
        own progress events), but it is logged for observability.
        """
        if step_label:
            logger.debug(
                "[CONTAINER_RUNNER] step_label=%s slot=%s",
                step_label, slot_id,
            )
        assignment = self._slot_assignments.get(slot_id)
        if assignment is None:
            logger.error("[CONTAINER_RUNNER] No assignment for slot %s", slot_id)
            return SlotResult(slot_id=slot_id, status="error")

        if self._execution.cancel_event.is_set():
            return SlotResult(slot_id=slot_id, status="cancelled")

        budget_reason = self._budget.check_budget()
        if budget_reason:
            logger.info("[CONTAINER_RUNNER] Budget exhausted before slot %s: %s", slot_id, budget_reason)
            self._execution.emit("budget_exhausted", reason=budget_reason, slotId=slot_id)
            return SlotResult(slot_id=slot_id, status="budget_exhausted")

        tpl = get_template(assignment.agent_id)
        if tpl is None:
            logger.error("[CONTAINER_RUNNER] Unknown template '%s'", assignment.agent_id)
            return SlotResult(slot_id=slot_id, status="error")

        # Build goal with handoff injection
        goal = assignment.goal
        if upstream_handoffs:
            prefix_parts = []
            for hp in upstream_handoffs:
                role = self._slot_assignments.get(hp.from_slot_id)
                role_name = role.role if role else hp.from_slot_id
                prefix_parts.append(hp.render_for_injection(role_name))
            goal = "\n\n".join(prefix_parts) + "\n\n" + goal

        effective_max = max_turns if max_turns is not None else self._budget.allocate(15)
        effective_max = max(1, effective_max)

        # Determine allowed tools
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
        allowed = list(
            skill_tools & set(tpl.available_tools)
            if skill_tools
            else set(tpl.available_tools)
        )

        config = SlotContainerConfig(
            slot_id=slot_id,
            agent_id=assignment.agent_id,
            session_id=job.id,
            assignment_session_id=assignment.session_id,
            role=assignment.role,
            goal=goal,
            system_prompt=tpl.system_prompt,
            model=os.environ.get("AGENT_MODEL", "gpt-4o"),
            max_rounds=effective_max,
            allowed_tools=allowed,
            orchestrator_endpoint=self._orchestrator_endpoint,
            copilot_token=self._execution.copilot_token,
            workspace_id=job.workspace_id,
            budget_remaining_turns=self._budget.remaining_turns(),
            budget_remaining_tool_calls=max(0, self._budget.budget.max_tool_calls - self._budget.tool_calls_used),
            architecture=job.composition.architecture if job.composition else "",
            job_id=job.id,
        )

        container: ContainerInfo | None = None
        started_at = time.monotonic()

        # Emit running status
        self._execution.emit(
            "slot_progress", slotId=assignment.session_id,
            agentId=assignment.session_id, status="running",
            activeAgentId=assignment.session_id,
            agentName=tpl.display_name, role=assignment.role,
            isolation="container",
        )
        assignment.status = AgentStatus.RUNNING

        try:
            async with self._pool:
                container = await self._backend.create(
                    config,
                    image=self._image,
                    cpu_limit=self._cpu,
                    memory_limit=self._memory,
                    network=self._network,
                )

                await self._backend.start(container.container_id)

                # Wait for the container to finish, racing against
                # cancellation and timeout.
                cancel_task = asyncio.create_task(self._execution.cancel_event.wait())
                wait_task = asyncio.create_task(
                    self._backend.wait(container.container_id, timeout=self._timeout)
                )

                done, pending = await asyncio.wait(
                    {wait_task, cancel_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()

                if cancel_task in done and wait_task not in done:
                    # User cancelled — kill the container
                    logger.info("[CONTAINER_RUNNER] Slot %s cancelled — killing container", slot_id)
                    await self._backend.kill(container.container_id)
                    await asyncio.sleep(0.1)
                    assignment.status = AgentStatus.ERROR
                    assignment.current_step = "Cancelled by user"
                    self._execution.emit(
                        "slot_progress", slotId=assignment.session_id,
                        agentId=assignment.session_id, status="failed",
                        agentName=tpl.display_name, reason="cancelled",
                    )
                    return SlotResult(slot_id=slot_id, status="cancelled")

                exit_code = wait_task.result()
                duration = time.monotonic() - started_at

                # Capture logs for forensics
                container_logs = await self._backend.logs(container.container_id, tail=200)

                if exit_code == 0:
                    logger.info(
                        "[CONTAINER_RUNNER] Slot %s completed (exit=0, %.1fs)",
                        slot_id, duration,
                    )
                    assignment.status = AgentStatus.COMPLETED
                    assignment.current_step = "Completed"
                    self._execution.emit(
                        "slot_progress", slotId=assignment.session_id,
                        agentId=assignment.session_id, status="done",
                        agentName=tpl.display_name,
                    )
                    return SlotResult(
                        slot_id=slot_id,
                        status="success",
                        turns_used=effective_max,  # approximate — container doesn't report back yet
                    )
                elif exit_code == 137:
                    logger.warning(
                        "[CONTAINER_RUNNER] Slot %s OOM-killed (exit=137, %.1fs)",
                        slot_id, duration,
                    )
                    assignment.status = AgentStatus.ERROR
                    assignment.current_step = "Out of memory"
                    self._execution.emit(
                        "slot_progress", slotId=assignment.session_id,
                        agentId=assignment.session_id, status="failed",
                        agentName=tpl.display_name, reason="oom_killed",
                    )
                    return SlotResult(slot_id=slot_id, status="error")
                else:
                    logger.error(
                        "[CONTAINER_RUNNER] Slot %s exited with code %d (%.1fs)\nLogs:\n%s",
                        slot_id, exit_code, duration, container_logs[:2000],
                    )
                    assignment.status = AgentStatus.ERROR
                    assignment.current_step = f"Container exited with code {exit_code}"
                    self._execution.emit(
                        "slot_progress", slotId=assignment.session_id,
                        agentId=assignment.session_id, status="failed",
                        agentName=tpl.display_name,
                        reason=f"exit_code_{exit_code}",
                    )
                    return SlotResult(slot_id=slot_id, status="error")

        except asyncio.TimeoutError:
            logger.error("[CONTAINER_RUNNER] Slot %s timed out after %.0fs", slot_id, self._timeout)
            if container:
                await self._backend.kill(container.container_id)
            assignment.status = AgentStatus.ERROR
            assignment.current_step = "Timed out"
            self._execution.emit(
                "slot_progress", slotId=assignment.session_id,
                agentId=assignment.session_id, status="failed",
                agentName=tpl.display_name, reason="timeout",
            )
            return SlotResult(slot_id=slot_id, status="error")

        except asyncio.CancelledError:
            if container:
                await self._backend.kill(container.container_id)
            raise

        except Exception as exc:
            logger.exception("[CONTAINER_RUNNER] Slot %s failed: %s", slot_id, exc)
            if container:
                await self._backend.kill(container.container_id)
            assignment.status = AgentStatus.ERROR
            assignment.current_step = f"Container error: {str(exc)[:100]}"
            self._execution.emit(
                "slot_progress", slotId=assignment.session_id,
                agentId=assignment.session_id, status="failed",
                agentName=tpl.display_name, reason="container_error",
            )
            return SlotResult(slot_id=slot_id, status="error")

        finally:
            if container:
                try:
                    await self._backend.remove(container.container_id)
                except Exception as exc:
                    logger.warning("[CONTAINER_RUNNER] Failed to remove container: %s", exc)

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
