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
    agent_container_options_from_env,
)
from services.agenthub.drivers.container_pool import ContainerPool
from services.agenthub.drivers.handoff import HandoffExtractor, HandoffPayload
from services.agenthub.drivers.slot_runner import SlotResult

if TYPE_CHECKING:
    from services.agenthub.orchestrator_engine import _JobExecution

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 900  # 15 min hard timeout per container
_KILL_GRACE_S = 5


def _pi_subagents_observability_enabled() -> bool:
    runtime = os.environ.get("AGENTHUB_ORCHESTRATION_RUNTIME", "dynamic").strip().lower()
    observability = os.environ.get("AGENTHUB_PI_OBSERVABILITY", "").strip().lower()
    enabled_values = {"pi-subagents", "pisubagents", "pi_subagents", "1", "true", "on"}
    return runtime in enabled_values or observability in enabled_values


def _pi_subagent_extension() -> dict[str, str]:
    return {
        "id": "pi-subagents",
        "label": "pi-subagents",
        "packageName": "pi-subagents",
        "version": "0.21.3",
    }


def _dynamic_pi_context(execution: Any, assignment_session_id: str) -> tuple[str, str | None, str | None]:
    mission = getattr(execution, "dynamic_mission_state", None)
    if mission is not None:
        for run in mission.subagent_runs.values():
            if run.agent_session_id != assignment_session_id:
                continue
            task = mission.tasks.get(run.task_id)
            return run.id, run.task_id, task.title if task is not None else None
    return assignment_session_id, None, None


def _clip(value: str | None, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _emit_pi_subagent_runner_terminal(
    execution: Any,
    assignment: AgentAssignment,
    *,
    agent_name: str,
    control_type: str,
    to: str,
    message: str,
    reason: str,
    state: str,
    result_status: str,
    summary: str,
    started_at: float,
    tool_count: int = 0,
) -> None:
    if not _pi_subagents_observability_enabled():
        return
    run_id, task_id, task_title = _dynamic_pi_context(execution, assignment.session_id)
    duration_ms = int((time.monotonic() - started_at) * 1000)
    extension = _pi_subagent_extension()
    task_label = task_title or assignment.role
    common = {
        "schemaVersion": 1,
        "runId": run_id,
        "agent": assignment.agent_id,
        "agentId": assignment.session_id,
        "agentName": agent_name or assignment.role,
        "taskId": task_id,
        "taskTitle": task_title,
        "extension": extension,
    }
    execution.emit(
        "pi.subagents.control",
        **common,
        controlType=control_type,
        to=to,
        message=_clip(message, 1000),
        reason=reason,
        elapsedMs=duration_ms,
        toolCount=tool_count,
    )
    execution.emit(
        "pi.subagents.result",
        **common,
        mode="single",
        status=result_status,
        summary=_clip(summary or message, 4000),
        usage={"toolCount": tool_count, "durationMs": duration_ms},
        sessionFile=f"agenthub://sessions/{execution.job.id}/subagents/{run_id}.jsonl",
        artifactPaths={
            "jsonlPath": f"agenthub://sessions/{execution.job.id}/subagents/{run_id}.jsonl",
            "metadataPath": f"agenthub://sessions/{execution.job.id}/subagents/{run_id}_meta.json",
        },
    )
    execution.emit(
        "pi.subagents.status",
        **common,
        mode="single",
        state=state,
        activityState="active_long_running" if result_status == "completed" else "needs_attention",
        task=task_label,
        summary=_clip(summary or message, 1000),
        toolCount=tool_count,
        durationMs=duration_ms,
        sessionFile=f"agenthub://sessions/{execution.job.id}/subagents/{run_id}.jsonl",
    )


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
        container_options = agent_container_options_from_env()
        self._image = container_options["image"]
        self._cpu = container_options["cpu_limit"]
        self._memory = container_options["memory_limit"]
        self._network = container_options["network"]
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
        allowed_tools_override: set[str] | None = None,
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

        job = self._execution.job
        if allowed_tools_override is not None:
            allowed = sorted(set(allowed_tools_override) & set(tpl.available_tools))
        else:
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
                warm_run = False
                acquire_warm = getattr(self._backend, "acquire_warm_container", None)
                if callable(acquire_warm):
                    container = await acquire_warm(
                        config,
                        image=self._image,
                        cpu_limit=self._cpu,
                        memory_limit=self._memory,
                        network=self._network,
                    )

                if container is not None:
                    run_warm = getattr(self._backend, "run_agent_in_warm_container", None)
                    warm_run = callable(run_warm)
                    if not warm_run:
                        logger.warning(
                            "[CONTAINER_RUNNER] Backend returned warm container but cannot execute it; cold-starting slot %s",
                            slot_id,
                        )
                        await self._backend.remove(container.container_id)
                        container = None

                if container is None:
                    container = await self._backend.create(
                        config,
                        image=self._image,
                        cpu_limit=self._cpu,
                        memory_limit=self._memory,
                        network=self._network,
                    )

                    await self._backend.start(container.container_id)
                else:
                    self._execution.emit(
                        "log_line",
                        agentId=assignment.session_id,
                        agentName=tpl.display_name,
                        level="info",
                        message="Warm isolated agent container acquired. Starting the slot immediately.",
                        tags=["startup", "container_warm_pool"],
                    )

                # Wait for the container to finish, racing against
                # cancellation and timeout.
                cancel_task = asyncio.create_task(self._execution.cancel_event.wait())
                if warm_run:
                    wait_task = asyncio.create_task(
                        run_warm(container.container_id, config, timeout=self._timeout)
                    )
                else:
                    wait_task = asyncio.create_task(
                        self._backend.wait(container.container_id, timeout=self._timeout)
                    )
                terminal_action_task = asyncio.create_task(
                    self._wait_for_terminal_success(assignment)
                )

                done, pending = await asyncio.wait(
                    {wait_task, cancel_task, terminal_action_task},
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
                    _emit_pi_subagent_runner_terminal(
                        self._execution,
                        assignment,
                        agent_name=tpl.display_name,
                        control_type="needs_attention",
                        to="cancelled",
                        message="User cancelled the isolated Pi subagent container.",
                        reason="cancelled",
                        state="cancelled",
                        result_status="cancelled",
                        summary="Cancelled by user",
                        started_at=started_at,
                    )
                    return SlotResult(slot_id=slot_id, status="cancelled")

                if terminal_action_task in done:
                    terminal_reason = terminal_action_task.result()
                    duration = time.monotonic() - started_at
                    logger.info(
                        "[CONTAINER_RUNNER] Slot %s completed from verified terminal action (%s, %.1fs); stopping container",
                        slot_id, terminal_reason, duration,
                    )
                    await self._backend.kill(container.container_id)
                    assignment.status = AgentStatus.COMPLETED
                    assignment.current_step = "Completed"
                    self._execution.emit(
                        "slot_progress", slotId=assignment.session_id,
                        agentId=assignment.session_id, status="done",
                        agentName=tpl.display_name,
                        reason=terminal_reason,
                    )
                    _emit_pi_subagent_runner_terminal(
                        self._execution,
                        assignment,
                        agent_name=tpl.display_name,
                        control_type="active_long_running",
                        to="complete",
                        message="Verified terminal Fabric artifact signal received; stopping the isolated Pi subagent container.",
                        reason=terminal_reason,
                        state="complete",
                        result_status="completed",
                        summary="Verified terminal Fabric artifact signal received.",
                        started_at=started_at,
                    )
                    return SlotResult(
                        slot_id=slot_id,
                        status="success",
                        turns_used=effective_max,
                    )

                wait_result = wait_task.result()
                if warm_run:
                    exit_code = wait_result.exit_code
                    container_logs = wait_result.logs
                else:
                    exit_code = wait_result
                    container_logs = await self._backend.logs(container.container_id, tail=200)
                duration = time.monotonic() - started_at

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
                    _emit_pi_subagent_runner_terminal(
                        self._execution,
                        assignment,
                        agent_name=tpl.display_name,
                        control_type="needs_attention",
                        to="failed",
                        message="The isolated Pi subagent container was OOM-killed.",
                        reason="oom_killed",
                        state="failed",
                        result_status="failed",
                        summary="Out of memory while running the isolated Pi subagent container.",
                        started_at=started_at,
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
                    _emit_pi_subagent_runner_terminal(
                        self._execution,
                        assignment,
                        agent_name=tpl.display_name,
                        control_type="needs_attention",
                        to="failed",
                        message=f"The isolated Pi subagent container exited with code {exit_code}.",
                        reason=f"exit_code_{exit_code}",
                        state="failed",
                        result_status="failed",
                        summary=f"Container exited with code {exit_code}.",
                        started_at=started_at,
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
            _emit_pi_subagent_runner_terminal(
                self._execution,
                assignment,
                agent_name=tpl.display_name,
                control_type="needs_attention",
                to="failed",
                message=f"The isolated Pi subagent container timed out after {self._timeout:.0f}s.",
                reason="timeout",
                state="failed",
                result_status="failed",
                summary="Timed out while running the isolated Pi subagent container.",
                started_at=started_at,
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
            _emit_pi_subagent_runner_terminal(
                self._execution,
                assignment,
                agent_name=tpl.display_name,
                control_type="needs_attention",
                to="failed",
                message=f"The isolated Pi subagent container failed: {str(exc)[:200]}",
                reason="container_error",
                state="failed",
                result_status="failed",
                summary=f"Container error: {str(exc)[:500]}",
                started_at=started_at,
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

    async def _wait_for_terminal_success(self, assignment: AgentAssignment) -> str:
        while True:
            reason = _verified_terminal_action_reason(assignment)
            if reason:
                return reason
            await asyncio.sleep(1)


def _verified_terminal_action_reason(assignment: AgentAssignment) -> str | None:
    for action in assignment.actions:
        if action.action_type != "Created" or action.entity_type != "WorkspaceInventorySolution":
            continue
        details = _action_details(action.details)
        if str(details.get("status") or "").lower() != "created":
            continue
        if details.get("dataSource") != "lakehouse_delta_tables":
            continue
        if details.get("semanticModelStorageMode") != "DirectLake":
            continue
        if details.get("persistentDataWritten") is not True:
            continue
        quality = details.get("qualityValidation")
        render = details.get("reportRenderValidation")
        if isinstance(quality, dict) and quality.get("status") != "passed":
            continue
        if isinstance(render, dict) and render.get("status") != "rendered":
            continue
        return "verified_inventory_solution_created"
    return None


def _action_details(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
