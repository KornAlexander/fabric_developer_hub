"""Dynamic generalist-orchestrator runtime primitives."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from domain.models.agent_models import AgentTemplate, Job
from domain.models.composition import Composition
from domain.models.context_pack import (
    ContextPackBacktrackPolicyV2,
    ContextModeTelemetryV2,
    ContextPackBudgetV2,
    ContextPackCompactionV2,
    ContextPackInstructionBudgetV2,
    ContextPackSourceBudgetV2,
    ContextPackV2,
)
from domain.models.dynamic_orchestration import (
    AgentResult,
    AgentResultStatus,
    FollowupTask,
    MissionBrief,
    MissionBudget,
    MissionState,
    MissionStatus,
    OrchestratorAction,
    OrchestratorActionType,
    ResourceClaim,
    ResourceLock,
    ResourceMode,
    SubagentRun,
    SubagentStatus,
    TaskNode,
    TaskStatus,
)
from services.agenthub.agent_registry import AGENT_TEMPLATES, GENERALIST_AGENT_ID
from services.agenthub.verifier_verdict import (
    compute_structural_rubric,
    emit_verifier_verdict,
    synthesize_browser_evidence_followup,
    synthesize_mandatory_verification_followup,
    user_renderable_deliverables_from_result,
)
from services.observability import bounded_text, stable_digest

logger = logging.getLogger(__name__)

FABRIC_VERIFIER_AGENT_ID = "fabric-verifier"
MAX_VERIFIER_FEEDBACK_REVIEWS_PER_ROOT = 2
GENERALIST_DIRECT_TOOL_SCOPE: tuple[str, ...] = (
    "fabric_list_workspaces",
    "fabric_list_items",
    "fabric_list_folders",
    "fabric_validate_workspace_capacity",
    "fabric_diagnose_workspace_artifacts",
    "fabric_get_semantic_model_refresh_history",
    "fabric_verify_report_renderable",
    "azure_list_subscriptions",
    "azure_list_resource_groups",
    "azure_list_resources",
    "azure_get_resource",
    "azure_check_permissions",
    "azure_list_role_assignments",
    "azure_list_role_definitions",
    "azure_get_activity_log",
    "azure_get_resource_health",
    "azure_list_diagnostic_settings",
    "azure_list_metric_definitions",
    "azure_query_metrics",
    "azure_network_inventory",
    "azure_diagnose_resource",
    "azure_list_fabric_capacities",
    "azure_get_fabric_capacity",
    "entra_token_diagnostics",
    "entra_get_signed_in_user",
    "entra_get_user",
    "entra_get_service_principal",
    "entra_diagnose_principal_access",
    "entra_list_group_memberships",
    "entra_list_app_role_assignments",
    "sequentialthinking",
    "get_current_time",
    "convert_time",
    "web_search",
    "web_fetch_url",
)
PI_CONTEXT_MODE_PACKAGE = "npm:context-mode@1.0.103"
PI_CONTEXT_MODE_MCP_SERVER = {
    "name": "context-mode",
    "command": "npx",
    "args": ["-y", "context-mode@1.0.103"],
    "config_path": ".pi/mcp.json",
}
CONTEXT_MODE_TELEMETRY_EVENTS = [
    "pi.context.mode.indexed",
    "pi.context.mode.retrieved",
    "pi.context.mode.compacted",
    "pi.context.mode.rehydrated",
    "pi.context.mode.savings",
]
QRSPI_PROTOCOL = "question-research-design-structure-plan-implement-verify-review"
QRSPI_PHASE_MODEL = [
    "question",
    "research",
    "design",
    "structure",
    "plan",
    "worktree",
    "implement",
    "verify",
    "review",
]


class MissionEventSink(Protocol):
    def emit(self, event_type: str, **kwargs: Any) -> None: ...


class SubagentExecutor(Protocol):
    async def run(
        self,
        *,
        mission: MissionState,
        run: SubagentRun,
        task: TaskNode,
        context_pack: dict[str, Any],
    ) -> AgentResult: ...

    async def steer(self, *, run: SubagentRun, message: str, reason: str) -> None: ...

    async def cancel(self, *, run: SubagentRun, reason: str) -> None: ...


class NoopEventSink:
    def emit(self, event_type: str, **kwargs: Any) -> None:
        return None


@dataclass(slots=True)
class DynamicMissionController:
    """Owns the live task graph for dynamic AgentHub missions.

    This controller is intentionally independent of the fixed MAF driver.
    It can seed from an existing Composition, then schedule dynamic,
    task-scoped subagents with bounded parallelism and resource locks.
    """

    executor: SubagentExecutor
    event_sink: MissionEventSink = field(default_factory=NoopEventSink)
    agent_templates: Mapping[str, AgentTemplate] = field(default_factory=lambda: AGENT_TEMPLATES)
    now: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(UTC))
    on_state_change: Callable[[MissionState], None] | None = None
    _active_tasks: dict[asyncio.Task[AgentResult], str] = field(default_factory=dict, init=False)

    def seed_from_job(self, job: Job) -> MissionState:
        require_approvals = bool(job.composition.budget.require_approvals) if job.composition else True
        brief = MissionBrief(
            session_id=job.id,
            goal=job.composition.task if job.composition else job.task_description,
            workspace_id=job.workspace_id,
            approval_policy={"requireApprovals": require_approvals},
            budget=self._budget_from_composition(job.composition),
            preferred_strategy="dynamic-generalist",
        )
        state = MissionState(brief=brief)
        state.blackboard.setdefault("taskResults", {})
        state.blackboard.setdefault("contextPacks", {})
        state.blackboard["specialistCatalog"] = self._specialist_catalog()
        self._seed_generalist_task(state)
        self._emit("mission_seeded", state, taskCount=len(state.tasks))
        return state

    def create_task(
        self,
        state: MissionState,
        task: TaskNode,
        *,
        rationale: str,
    ) -> TaskNode:
        if task.depth > state.brief.budget.max_task_graph_depth:
            task.status = TaskStatus.BLOCKED
        state.tasks[task.id] = task
        self._record_action(
            state,
            OrchestratorActionType.CREATE_TASK,
            rationale,
            task_id=task.id,
            payload={"task": task.model_dump(mode="json", by_alias=True)},
        )
        self._emit("task_created", state, task=task.model_dump(mode="json", by_alias=True))
        return task

    async def dispatch_ready(self, state: MissionState) -> list[SubagentRun]:
        started: list[SubagentRun] = []
        ready_tasks = self._ready_tasks(state)
        self._emit(
            "generalist_check_in",
            state,
            queuedTaskCount=sum(1 for task in state.tasks.values() if task.status == TaskStatus.QUEUED),
            readyTaskCount=len(ready_tasks),
            runningSubagentCount=self._running_count(state),
            completedTaskCount=sum(1 for task in state.tasks.values() if task.status == TaskStatus.COMPLETED),
            blockedTaskCount=sum(1 for task in state.tasks.values() if task.status == TaskStatus.BLOCKED),
            failedTaskCount=sum(1 for task in state.tasks.values() if task.status == TaskStatus.FAILED),
            readyTaskIds=[task.id for task in ready_tasks[:10]],
        )
        for task in ready_tasks:
            if self._running_count(state) >= state.brief.budget.max_active_subagents:
                break
            if len(state.subagent_runs) >= state.brief.budget.max_total_subagents:
                self._mark_task_blocked(
                    state,
                    task,
                    "Mission subagent budget is exhausted.",
                    reason="max_total_subagents reached",
                )
                continue
            if not self._can_acquire_locks(state, task):
                continue
            agent_id = self._select_agent(task)
            if agent_id is None:
                self._mark_task_blocked(
                    state,
                    task,
                    "No available agent matches the task requirements.",
                    reason="no matching specialist",
                )
                continue
            run = self._spawn_subagent(state, task, agent_id)
            started.append(run)
        if len(started) > 1:
            self._record_action(
                state,
                OrchestratorActionType.SPAWN_PARALLEL_GROUP,
                "Started independent ready tasks in parallel.",
                payload={"runIds": [run.id for run in started]},
            )
            self._emit("parallel_group_spawned", state, runIds=[run.id for run in started])
        return started

    async def run_until_idle(self, state: MissionState) -> MissionState:
        while state.status == MissionStatus.ACTIVE:
            await self.dispatch_ready(state)
            pending = [task for task in self._active_tasks if not task.done()]
            if not pending:
                break
            completed_tasks, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for completed_task in completed_tasks:
                await self._handle_execution_completion(state, completed_task)
        self._refresh_mission_terminal_state(state)
        return state

    async def steer_subagent(
        self,
        state: MissionState,
        run_id: str,
        *,
        message: str,
        reason: str,
    ) -> bool:
        run = state.subagent_runs.get(run_id)
        if run is None or run.status != SubagentStatus.RUNNING:
            return False
        run.directives.append(message)
        self._touch(state)
        await self.executor.steer(run=run, message=message, reason=reason)
        self._record_action(
            state,
            OrchestratorActionType.STEER_SUBAGENT,
            reason,
            task_id=run.task_id,
            target_run_id=run.id,
            payload={"message": message},
        )
        self._emit("subagent_steered", state, runId=run.id, taskId=run.task_id, reason=reason, message=message)
        self._emit(
            "generalist_steering",
            state,
            runId=run.id,
            taskId=run.task_id,
            agentId=run.agent_id,
            reason=reason,
            message=message,
            directiveCount=len(run.directives),
        )
        return True

    async def cancel_subagent(self, state: MissionState, run_id: str, *, reason: str) -> bool:
        run = state.subagent_runs.get(run_id)
        if run is None or run.status not in (SubagentStatus.QUEUED, SubagentStatus.RUNNING):
            return False
        task = state.tasks.get(run.task_id)
        for active_task, active_run_id in list(self._active_tasks.items()):
            if active_run_id == run_id and not active_task.done():
                active_task.cancel()
        await self.executor.cancel(run=run, reason=reason)
        self._finish_run_cancelled(state, run, reason)
        if task is not None:
            task.status = TaskStatus.CANCELLED
        self._record_action(
            state,
            OrchestratorActionType.CANCEL_SUBAGENT,
            reason,
            task_id=run.task_id,
            target_run_id=run.id,
        )
        self._emit("subagent_cancelled", state, runId=run.id, taskId=run.task_id, reason=reason)
        return True

    async def inspect_stale_subagents(
        self,
        state: MissionState,
        *,
        stale_after_seconds: int,
    ) -> list[SubagentRun]:
        stale_runs: list[SubagentRun] = []
        current_time = self.now()
        for run in state.subagent_runs.values():
            if run.status != SubagentStatus.RUNNING or run.heartbeat_at is None:
                continue
            age = (current_time - run.heartbeat_at).total_seconds()
            if age < stale_after_seconds:
                continue
            stale_runs.append(run)
            self._record_action(
                state,
                OrchestratorActionType.INSPECT_SUBAGENT_LOGS,
                f"Subagent heartbeat is stale by {age:.0f}s.",
                task_id=run.task_id,
                target_run_id=run.id,
            )
            self._emit("subagent_stale", state, runId=run.id, taskId=run.task_id, staleSeconds=age)
        return stale_runs

    async def supervise_tool_loop(
        self,
        state: MissionState,
        *,
        agent_session_id: str,
        tool_name: str,
        arg_hash: str,
        output_preview: str,
        replacement_candidate_agent_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """React to a repeated tool-call loop observed in a running subagent.

        The tool runtime should stay a low-level safety rail. Dynamic missions
        need a higher-level response: mission control inspects the run, steers
        once with a concrete directive, and if the same loop continues it
        abandons that run and replaces it with a tighter task brief.
        """
        run = self._run_for_agent_session(state, agent_session_id)
        if run is None:
            return {"action": "ignored", "reason": "run_not_found"}
        task = state.tasks.get(run.task_id)
        if task is None:
            return {"action": "ignored", "reason": "task_not_found", "runId": run.id}

        supervision = state.blackboard.setdefault("supervision", {})
        run_supervision = supervision.setdefault(run.id, {"signals": [], "interventions": 0})
        signal = {
            "kind": "tool_loop",
            "toolName": tool_name,
            "argHash": arg_hash,
            "outputPreview": output_preview[:500],
            "observedAt": self.now().isoformat(),
        }
        run_supervision.setdefault("signals", []).append(signal)
        matching_signal_count = sum(
            1
            for observed in run_supervision.get("signals", [])
            if observed.get("kind") == "tool_loop"
            and observed.get("toolName") == tool_name
            and observed.get("argHash") == arg_hash
        )

        rationale = (
            f"Inspected {run.agent_id} logs after repeated `{tool_name}` calls "
            f"with identical arguments ({arg_hash})."
        )
        self._record_action(
            state,
            OrchestratorActionType.INSPECT_SUBAGENT_LOGS,
            rationale,
            task_id=run.task_id,
            target_run_id=run.id,
            payload=signal,
        )
        self._emit(
            "subagent_inspected",
            state,
            runId=run.id,
            taskId=run.task_id,
            signal=signal,
            matchingSignalCount=matching_signal_count,
        )

        if run.status != SubagentStatus.RUNNING:
            return {"action": "observed", "runId": run.id, "matchingSignalCount": matching_signal_count}

        if matching_signal_count == 1:
            message = (
                f"Mission control inspected your recent tool log and found a repeated `{tool_name}` "
                "call with identical arguments. Stop retrying that exact call. Re-read the relevant "
                "workspace state, change the approach or parameters, and if the operation is blocked, "
                "finish with a DECISION that names the blocker and proposes a follow-up task instead."
            )
            await self.steer_subagent(
                state,
                run.id,
                message=message,
                reason="Repeated identical tool call loop detected.",
            )
            run_supervision["interventions"] = int(run_supervision.get("interventions", 0)) + 1
            return {
                "action": "steer",
                "runId": run.id,
                "taskId": run.task_id,
                "message": message,
                "matchingSignalCount": matching_signal_count,
            }

        replacement = self._abandon_run_and_create_replacement(
            state,
            task,
            run,
            reason=(
                f"Repeated `{tool_name}` loop continued after mission-control steering. "
                "Abandoning this run and trying a different approach."
            ),
            replacement_candidate_agent_ids=replacement_candidate_agent_ids,
            signal=signal,
        )
        run_supervision["abandoned"] = True
        run_supervision["replacementTaskId"] = replacement.id
        return {
            "action": "abandon",
            "runId": run.id,
            "taskId": run.task_id,
            "replacementTaskId": replacement.id,
            "matchingSignalCount": matching_signal_count,
        }

    def heartbeat(self, state: MissionState, run_id: str) -> bool:
        run = state.subagent_runs.get(run_id)
        if run is None or run.status != SubagentStatus.RUNNING:
            return False
        run.heartbeat_at = self.now()
        self._touch(state)
        self._emit("subagent_heartbeat", state, runId=run.id, taskId=run.task_id)
        return True

    def _run_for_agent_session(self, state: MissionState, agent_session_id: str) -> SubagentRun | None:
        for run in state.subagent_runs.values():
            if run.agent_session_id == agent_session_id:
                return run
        return None

    def _seed_generalist_task(self, state: MissionState) -> None:
        task = TaskNode(
            id="generalist",
            title="Generalist mission controller",
            objective=(
                "You are the mission planner and router, NOT a builder. Inspect the mission "
                "and the SPECIALIST CATALOG, then delegate every implementation, mutation, "
                "or build step to the best matching specialist by emitting a "
                "DYNAMIC_RESULT_START followupTasks block with their candidateAgentIds. "
                "Spawn independent follow-ups in parallel when their resourceClaims and "
                "dependencies allow. You may only execute a step yourself when (a) NO "
                "specialist in the catalog can do it, OR (b) it is a trivial routing, "
                "discovery, or read-only check that takes a single tool call. Never call "
                "creation, write, or mutation tools yourself \u2014 always delegate those. "
                "For Fabric item inventory, semantic model, report, or visualization solution "
                "missions, delegate the implementation to fabric-data-engineer (it owns "
                "fabric_create_workspace_inventory_solution) and queue a final fabric-verifier "
                "follow-up after it. When delegating, preserve the user's explicit requirements and "
                "add implicit professional-quality requirements: excellent report design and usability, "
                "clear maintainable semantic modeling, clean performant data/notebook code, and an "
                "extensible structure that a real Fabric team could keep evolving."
            ),
            candidate_agent_ids=[GENERALIST_AGENT_ID],
            tool_scope=list(GENERALIST_DIRECT_TOOL_SCOPE),
            context_refs=list(state.brief.initial_context_refs),
            parallelism_safe=False,
            created_by="orchestrator",
        )
        self.create_task(
            state,
            task,
            rationale="Seeded the dynamic mission with the internal generalist controller.",
        )

    def _seed_tasks_from_composition(self, state: MissionState, composition: Composition) -> None:
        dependencies_by_slot: dict[str, list[str]] = {slot.id: [] for slot in composition.slots}
        for handoff in composition.handoffs:
            dependencies_by_slot.setdefault(handoff.to, []).append(handoff.from_)
        for slot in composition.slots:
            task = TaskNode(
                id=slot.id,
                title=slot.role,
                objective=(
                    f"Execute composition slot {slot.id} for mission: {composition.task}"
                ),
                dependencies=dependencies_by_slot.get(slot.id, []),
                candidate_agent_ids=[slot.agent_id],
                required_capabilities=[skill.name for skill in slot.skills],
                context_refs=list(state.brief.initial_context_refs),
                created_by="composition",
            )
            self.create_task(
                state,
                task,
                rationale="Seeded from the reviewed fixed composition.",
            )

    def _specialist_catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for agent_id, template in self.agent_templates.items():
            if getattr(template, "is_internal", False):
                continue
            catalog.append({
                "id": agent_id,
                "name": template.display_name,
                "description": template.description,
                "tags": list(template.tags),
                "skills": [
                    {
                        "id": skill.id,
                        "name": skill.name,
                        "description": skill.description,
                        "tools": list(skill.tools),
                    }
                    for skill in template.skills
                ],
            })
        catalog.sort(key=lambda entry: entry["id"])
        return catalog

    def _spawn_subagent(self, state: MissionState, task: TaskNode, agent_id: str) -> SubagentRun:
        tool_scope = self._tool_scope(task, agent_id)
        context_pack = self._build_context_pack(state, task, agent_id=agent_id, tool_scope=tool_scope)
        context_summary = _summarize_context_pack(context_pack)
        context_pack_ref = f"context-{task.id}"
        state.blackboard.setdefault("contextPacks", {})[context_pack_ref] = context_pack
        run = SubagentRun(
            task_id=task.id,
            agent_id=agent_id,
            status=SubagentStatus.RUNNING,
            started_at=self.now(),
            heartbeat_at=self.now(),
            tool_scope=tool_scope,
            context_pack_ref=context_pack_ref,
        )
        state.subagent_runs[run.id] = run
        task.status = TaskStatus.RUNNING
        task.assigned_agent_run_id = run.id
        self._acquire_locks(state, task, run.id)
        self._record_action(
            state,
            OrchestratorActionType.SPAWN_SUBAGENT,
            "Spawned the best available specialist for a ready task.",
            task_id=task.id,
            target_run_id=run.id,
            payload={
                "agentId": agent_id,
                "agentName": self.agent_templates.get(agent_id).display_name if self.agent_templates.get(agent_id) else agent_id,
                "toolScope": list(run.tool_scope),
                "toolScopeCount": len(run.tool_scope),
                "contextDigest": context_summary["contextDigest"],
                "contextPackSchemaVersion": context_summary["contextPackSchemaVersion"],
                "contextPhase": context_summary["contextPhase"],
                "contextGoal": context_summary["contextGoal"],
                "contextBudgetMaxTokens": context_summary["contextBudgetMaxTokens"],
                "qrspiProtocol": context_summary["qrspiProtocol"],
                "instructionBudgetPhaseLimit": context_summary["instructionBudgetPhaseLimit"],
                "verticalSlicePolicy": context_summary["verticalSlicePolicy"],
                "backtrackAllowed": context_summary["backtrackAllowed"],
                "contextModePackage": context_summary["contextModePackage"],
                "taskTitle": task.title,
                "objectivePreview": bounded_text(task.objective, max_chars=500),
                "steeringPreview": _task_steering_preview(task),
            },
        )
        execution_task = asyncio.create_task(
            self.executor.run(mission=state, run=run, task=task, context_pack=context_pack)
        )
        self._active_tasks[execution_task] = run.id
        self._touch(state)
        self._emit(
            "generalist_context_pack",
            state,
            runId=run.id,
            agentId=agent_id,
            agentName=self.agent_templates.get(agent_id).display_name if self.agent_templates.get(agent_id) else agent_id,
            contextPackRef=context_pack_ref,
            toolScope=list(run.tool_scope),
            toolScopeCount=len(run.tool_scope),
            objectivePreview=bounded_text(task.objective, max_chars=500),
            steeringPreview=_task_steering_preview(task),
            **context_summary,
        )
        if agent_id == GENERALIST_AGENT_ID:
            self._emit(
                "generalist_direct_work",
                state,
                runId=run.id,
                taskId=task.id,
                agentId=agent_id,
                taskTitle=task.title,
                reason=(
                    "Generalist chose to handle this task directly because it is internal planning, "
                    "safe discovery, routing, verification triage, or lightweight checking work."
                ),
                toolScopeCount=len(run.tool_scope),
                objectivePreview=bounded_text(task.objective, max_chars=500),
                contextDigest=context_summary["contextDigest"],
            )
        self._emit(
            "subagent_spawned",
            state,
            run=run.model_dump(mode="json", by_alias=True),
            task=task.model_dump(mode="json", by_alias=True),
            runId=run.id,
            taskId=task.id,
            agentId=agent_id,
            agentName=self.agent_templates.get(agent_id).display_name if self.agent_templates.get(agent_id) else agent_id,
            taskTitle=task.title,
            toolScopeCount=len(run.tool_scope),
            contextDigest=context_summary["contextDigest"],
            contextPackSchemaVersion=context_summary["contextPackSchemaVersion"],
            contextPhase=context_summary["contextPhase"],
            contextGoal=context_summary["contextGoal"],
            contextBudgetMaxTokens=context_summary["contextBudgetMaxTokens"],
            subagentWorkModel=context_summary["subagentWorkModel"],
            qrspiProtocol=context_summary["qrspiProtocol"],
            qrspiPhaseModel=context_summary["qrspiPhaseModel"],
            instructionBudgetPhaseLimit=context_summary["instructionBudgetPhaseLimit"],
            instructionBudgetBasis=context_summary["instructionBudgetBasis"],
            verticalSlicePolicy=context_summary["verticalSlicePolicy"],
            backtrackAllowed=context_summary["backtrackAllowed"],
            backtrackTargetPhases=context_summary["backtrackTargetPhases"],
            reviewPolicy=context_summary["reviewPolicy"],
            contextModePackage=context_summary["contextModePackage"],
            contextModeFacade=context_summary["contextModeFacade"],
            upstreamResultCount=context_summary["upstreamResultCount"],
            specialistCatalogCount=context_summary["specialistCatalogCount"],
            steeringPreview=_task_steering_preview(task),
        )
        return run

    async def _handle_execution_completion(
        self,
        state: MissionState,
        execution_task: asyncio.Task[AgentResult],
    ) -> None:
        run_id = self._active_tasks.pop(execution_task, None)
        if run_id is None:
            return
        run = state.subagent_runs.get(run_id)
        if run is None:
            return
        task = state.tasks.get(run.task_id)
        if task is None:
            return
        if run.status == SubagentStatus.CANCELLED:
            return
        try:
            result = execution_task.result()
        except asyncio.CancelledError:
            self._finish_run_cancelled(state, run, run.cancellation_reason or "cancelled")
            task.status = TaskStatus.CANCELLED
            return
        except Exception as exc:
            logger.exception("Dynamic subagent %s failed", run.id)
            result = AgentResult(
                run_id=run.id,
                task_id=task.id,
                status=AgentResultStatus.FAILED,
                summary=f"Subagent failed: {exc}",
                errors=[str(exc)],
            )
        self._merge_result(state, task, run, result)

    def _merge_result(
        self,
        state: MissionState,
        task: TaskNode,
        run: SubagentRun,
        result: AgentResult,
    ) -> None:
        if run.agent_id == FABRIC_VERIFIER_AGENT_ID:
            self._apply_verifier_structural_backstop(state, task, result)
        state.results[result.id] = result
        state.blackboard.setdefault("taskResults", {})[task.id] = {
            "summary": result.summary,
            "status": result.status.value,
            "handoffContext": result.handoff_context,
            "artifacts": result.artifacts,
            "evidence": result.evidence,
            "errors": result.errors,
            "caveats": result.caveats,
            "followupTasks": [followup.model_dump(mode="json", by_alias=True) for followup in result.followup_tasks],
        }
        run.result_ref = result.id
        run.completed_at = self.now()
        task.result_ref = result.id
        if result.status in (AgentResultStatus.SUCCESS, AgentResultStatus.PARTIAL) or result.followup_tasks:
            run.status = SubagentStatus.COMPLETED
            task.status = TaskStatus.COMPLETED
        elif result.status == AgentResultStatus.BLOCKED:
            run.status = SubagentStatus.BLOCKED
            task.status = TaskStatus.BLOCKED
        elif result.status == AgentResultStatus.CANCELLED:
            run.status = SubagentStatus.CANCELLED
            task.status = TaskStatus.CANCELLED
        else:
            run.status = SubagentStatus.FAILED
            task.status = TaskStatus.FAILED
        self._release_locks(state, run.id)
        self._record_action(
            state,
            OrchestratorActionType.MERGE_RESULT,
            "Merged structured subagent result into mission state.",
            task_id=task.id,
            target_run_id=run.id,
            payload={
                "resultId": result.id,
                "status": result.status.value,
                "resultStatus": result.status.value,
                "taskStatus": task.status.value,
                "agentId": run.agent_id,
                "summary": bounded_text(result.summary, max_chars=700),
                "artifactCount": len(result.artifacts),
                "evidenceCount": len(result.evidence),
                "errorCount": len(result.errors),
                "followupTaskCount": len(result.followup_tasks),
            },
        )
        self._emit(
            "generalist_state_decision",
            state,
            runId=run.id,
            taskId=task.id,
            agentId=run.agent_id,
            resultStatus=result.status.value,
            taskStatus=task.status.value,
            summary=bounded_text(result.summary, max_chars=700),
            rationale="Merged subagent feedback into mission state and selected the next routing path.",
            artifactCount=len(result.artifacts),
            evidenceCount=len(result.evidence),
            errorCount=len(result.errors),
            followupTaskCount=len(result.followup_tasks),
        )
        self._emit(
            "subagent_result",
            state,
            runId=run.id,
            taskId=task.id,
            agentId=run.agent_id,
            resultStatus=result.status.value,
            taskStatus=task.status.value,
            summary=bounded_text(result.summary, max_chars=700),
            artifactCount=len(result.artifacts),
            evidenceCount=len(result.evidence),
            errorCount=len(result.errors),
            followupTaskCount=len(result.followup_tasks),
            result=result.model_dump(mode="json", by_alias=True),
        )
        if run.agent_id != FABRIC_VERIFIER_AGENT_ID:
            self._ensure_generalist_actually_delegated(state, task, run, result)
            self._ensure_mandatory_verifier_followup(state, task, run, result)
            state.blackboard.setdefault("taskResults", {}).setdefault(task.id, {})["followupTasks"] = [
                followup.model_dump(mode="json", by_alias=True) for followup in result.followup_tasks
            ]
        if self._should_route_verifier_feedback_to_generalist(run, result):
            feedback_round, signature, repeated, severity = self._record_verifier_feedback_round(
                state, task, result
            )
            if repeated or feedback_round > MAX_VERIFIER_FEEDBACK_REVIEWS_PER_ROOT:
                bail_reason = (
                    "verification feedback loop exceeded no-progress limit"
                    if not repeated
                    else (
                        "verifier reported the same structural failure twice in a row "
                        "with no progress between attempts"
                    )
                )
                summary_text = (
                    f"Fabric verification is not converging on task {task.id}. "
                    f"Round {feedback_round} produced failure signature {signature or '(none)'}. "
                    + (
                        "The previous repair attempt produced the SAME failure signature, "
                        "so the generalist is bailing instead of looping."
                        if repeated
                        else "The retry budget is exhausted."
                    )
                )
                self._emit(
                    "mission_no_progress",
                    state,
                    runId=run.id,
                    taskId=task.id,
                    agentId=run.agent_id,
                    severity=severity,
                    feedbackRound=feedback_round,
                    failureSignature=signature,
                    repeatedFailure=repeated,
                    maxFeedbackReviewRounds=MAX_VERIFIER_FEEDBACK_REVIEWS_PER_ROOT,
                    summary=bounded_text(summary_text, max_chars=700),
                    rationale=bail_reason,
                )
                self._mark_task_failed(
                    state,
                    task,
                    summary_text,
                    reason=bail_reason,
                    payload={
                        "severity": severity,
                        "feedbackRound": feedback_round,
                        "failureSignature": signature,
                        "repeatedFailure": repeated,
                        "maxFeedbackReviewRounds": MAX_VERIFIER_FEEDBACK_REVIEWS_PER_ROOT,
                        "errors": list(result.errors),
                        "summary": result.summary,
                    },
                )
            else:
                self._create_generalist_verification_review_task(state, task, result, feedback_round=feedback_round)
        else:
            self._create_followup_tasks(state, task, result.followup_tasks)
        # Always emit the structural verifier_verdict last so SSE replay
        # carries the explicit verdict after the merged subagent_result.
        if run.agent_id == FABRIC_VERIFIER_AGENT_ID:
            emit_verifier_verdict(
                state=state,
                task=task,
                run=run,
                result=result,
                emit=self._emit,
                now=self.now,
                root_task_id_fn=self._root_task_id,
            )
        self._touch(state)

    def _apply_verifier_structural_backstop(
        self,
        state: MissionState,
        task: TaskNode,
        result: AgentResult,
    ) -> None:
        # If FabricVerifier claimed success but the user-renderable deliverable
        # lacks browser evidence, synthesize a repair follow-up. If browser
        # evidence already passes the deterministic rubric, suppress LLM
        # follow-ups that only chase scrapeable text in Power BI's canvas UI.
        rubric_passed, structural_failures, evidence, deliverables = compute_structural_rubric(
            state, task, result
        )
        if (
            result.followup_tasks
            and rubric_passed
            and result.status == AgentResultStatus.SUCCESS
            and not result.errors
        ):
            logger.info(
                "[verifier-rubric] Ignoring %s verifier follow-up(s) for task %s because browser evidence passed.",
                len(result.followup_tasks),
                task.id,
            )
            result.followup_tasks.clear()
            return
        if not rubric_passed and _is_transient_browser_propagation_failure(structural_failures, evidence):
            result.followup_tasks.clear()
            result.followup_tasks.append(synthesize_browser_evidence_followup(deliverables, structural_failures))
            logger.info(
                "[verifier-rubric] Replaced verifier follow-ups for task %s with browser-only recheck due to transient browser evidence: %s",
                task.id,
                ",".join(structural_failures),
            )
            return
        if not result.followup_tasks and not rubric_passed:
            synthetic = synthesize_browser_evidence_followup(deliverables, structural_failures)
            result.followup_tasks.append(synthetic)
            logger.info(
                "[verifier-rubric] Forced repair followup for task %s due to structural failures: %s",
                task.id,
                ",".join(structural_failures),
            )

    def _should_route_verifier_feedback_to_generalist(
        self,
        run: SubagentRun,
        result: AgentResult,
    ) -> bool:
        if run.agent_id != FABRIC_VERIFIER_AGENT_ID or not result.followup_tasks:
            return False
        return not all(_is_browser_reverify_followup(followup) for followup in result.followup_tasks)

    def _ensure_generalist_actually_delegated(
        self,
        state: MissionState,
        task: TaskNode,
        run: SubagentRun,
        result: AgentResult,
    ) -> None:
        """Block false-success from the generalist.

        The generalist must NEVER claim a creation/visualization mission is
        complete on its own. If it returns ``success`` / ``partial`` with no
        follow-up tasks, but its narrative reveals it intended to delegate
        (or the user goal demands user-facing Fabric deliverables it did
        not produce), synthesize a delegation follow-up to the named
        specialist so the orchestrator does not finish the mission early.
        """
        if run.agent_id != GENERALIST_AGENT_ID:
            return
        if result.status not in (AgentResultStatus.SUCCESS, AgentResultStatus.PARTIAL):
            return
        if result.followup_tasks:
            return

        deliverables = user_renderable_deliverables_from_result(state, task, result)
        produced_kinds = {
            str(art.get("type") or art.get("entity_type") or "").strip().lower()
            for art in (result.artifacts or [])
        }
        produced_real_artifact = any(
            kind not in {"", "folder", "directory"} for kind in produced_kinds
        )
        narrative = " ".join(
            [
                bounded_text(result.summary or "", max_chars=4000),
                " ".join(
                    bounded_text(str(d), max_chars=400)
                    for ev in (result.evidence or [])
                    for d in (
                        ev.get("decisions", []) if isinstance(ev, dict) else []
                    )
                ),
            ]
        ).lower()
        delegation_intent_markers = (
            "i will delegate",
            "delegate to ",
            "delegate the task",
            "delegating to",
            "hand off",
            "handoff to",
            "specialist to create",
            "specialist to build",
            "fabricdataengineer",
            "fabric-data-engineer",
            "tool is unavailable",
            "tool unavailable",
            "preventing direct implementation",
        )
        narrative_intends_delegation = any(m in narrative for m in delegation_intent_markers)

        goal = (state.brief.goal or task.objective or "").lower()
        goal_demands_deliverables = bool(
            re.search(r"\b(report|semantic\s*model|dashboard|visuali[sz]ation)\b", goal)
            and re.search(r"\b(create|build|generate|produce|end[\s-]*to[\s-]*end|solution)\b", goal)
        )
        deliverables_missing = (
            goal_demands_deliverables and not deliverables and not produced_real_artifact
        )

        if not (narrative_intends_delegation or deliverables_missing):
            return

        scheduled = state.blackboard.setdefault("generalistDelegationGuardForTasks", {})
        if scheduled.get(task.id):
            return

        specialist_id = "fabric-data-engineer"
        specialist_template = self.agent_templates.get(specialist_id)
        if specialist_template is None:
            logger.warning(
                "[generalist-guard] Cannot synthesize delegation follow-up for task %s — "
                "specialist %s not registered.",
                task.id,
                specialist_id,
            )
            return

        original_goal = state.brief.goal or task.objective or ""
        followup = FollowupTask(
            title="Implement the requested Fabric deliverables",
            objective=bounded_text(
                "The generalist must NOT do this work itself. Execute the original user mission "
                "by creating the requested Fabric items end-to-end (ingestion, transformation, "
                "semantic model, and report) so the user has visible deliverables. "
                "Treat the explicit prompt as the minimum scope and produce the best professional "
                "implementation that still satisfies it: durable Lakehouse-backed data, clean "
                "schema-aware notebook code, a maintainable semantic model with named measures, "
                "and a visually excellent multi-visual report with useful grouping, sorting, and "
                "executive-level readability. "
                "Use the appropriate Fabric tools (e.g. fabric_create_workspace_inventory_solution "
                "or, if it is unavailable, the underlying fabric_create_folder + fabric_create_item + "
                "fabric_write_file primitives). Return the produced Report and SemanticModel item ids "
                "and webUrls so FabricVerifier can independently confirm browser rendering.\n\n"
                f"Original user mission: {original_goal}",
                max_chars=4000,
            ),
            candidate_agent_ids=[specialist_id],
            delegation_reason=(
                "Generalist guard: the generalist returned success without producing user-facing "
                "Fabric deliverables and either narrated a delegation intent or left the goal's "
                "required artifacts missing. Forcing delegation to the named specialist."
            ),
            context_summary=bounded_text(result.summary or "", max_chars=600),
            acceptance_criteria=[
                "Folder, Lakehouse (or Warehouse), Notebook, SemanticModel, and Report exist in Fabric.",
                "Report is bound to the produced SemanticModel and renders in browser.",
                "Report/model/code quality is professional: multiple useful visuals, clean measures, persisted data, maintainable notebook logic, and no one-card proof-of-life deliverable.",
                "Result returns each deliverable's id, displayName, and webUrl for verifier.",
            ],
            parallelism_safe=False,
        )
        result.followup_tasks.append(followup)
        result.status = AgentResultStatus.PARTIAL
        scheduled[task.id] = {
            "runId": run.id,
            "scheduledAt": self.now().isoformat(),
            "reason": "narrative-delegation" if narrative_intends_delegation else "missing-deliverables",
        }
        self._emit(
            "generalist_steering",
            state,
            runId=run.id,
            taskId=task.id,
            agentId=run.agent_id,
            agentName=self.agent_templates.get(GENERALIST_AGENT_ID).display_name if self.agent_templates.get(GENERALIST_AGENT_ID) else GENERALIST_AGENT_ID,
            reason=(
                "Generalist returned success without producing the requested user-facing Fabric "
                "deliverables. Forcing delegation to FabricDataEngineer instead of finishing the "
                "mission with empty artifacts."
            ),
            message=bounded_text(followup.objective, max_chars=600),
            directiveCount=1,
            severity="warning",
        )
        logger.info(
            "[generalist-guard] Forced delegation follow-up for task %s (run=%s reason=%s).",
            task.id,
            run.id,
            scheduled[task.id]["reason"],
        )

    def _ensure_mandatory_verifier_followup(
        self,
        state: MissionState,
        task: TaskNode,
        run: SubagentRun,
        result: AgentResult,
    ) -> None:
        if result.status not in (AgentResultStatus.SUCCESS, AgentResultStatus.PARTIAL):
            return
        if any(FABRIC_VERIFIER_AGENT_ID in followup.candidate_agent_ids for followup in result.followup_tasks):
            return
        root_task_id = self._root_task_id(state, task)
        for existing_task in state.tasks.values():
            if existing_task.id == task.id:
                continue
            if FABRIC_VERIFIER_AGENT_ID not in existing_task.candidate_agent_ids:
                continue
            if existing_task.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
                continue
            if task.id in existing_task.dependencies or self._root_task_id(state, existing_task) == root_task_id:
                return
        scheduled = state.blackboard.setdefault("mandatoryVerifierScheduledForTasks", {})
        if scheduled.get(task.id):
            return
        deliverables = user_renderable_deliverables_from_result(state, task, result)
        if not deliverables:
            return
        followup = synthesize_mandatory_verification_followup(
            deliverables,
            original_goal=state.brief.goal,
        )
        result.followup_tasks.append(followup)
        scheduled[task.id] = {
            "runId": run.id,
            "deliverables": deliverables,
            "scheduledAt": self.now().isoformat(),
        }
        self._emit(
            "generalist_state_decision",
            state,
            runId=run.id,
            taskId=task.id,
            agentId=run.agent_id,
            resultStatus=result.status.value,
            taskStatus=task.status.value,
            summary=(
                "Scheduled mandatory FabricVerifier gate for produced user-facing deliverables: "
                + ", ".join(str(d.get("name") or d.get("id") or d.get("type")) for d in deliverables[:6])
            ),
            rationale=(
                "User-facing Fabric/Power BI artifacts cannot be accepted from producer output alone; "
                "the generalist must hand off verification to FabricVerifier with browser evidence."
            ),
            artifactCount=len(result.artifacts),
            evidenceCount=len(result.evidence),
            errorCount=len(result.errors),
            followupTaskCount=len(result.followup_tasks),
        )

    def _record_verifier_feedback_round(
        self,
        state: MissionState,
        task: TaskNode,
        result: AgentResult,
    ) -> tuple[int, str, bool, str]:
        """Track per-root verifier feedback rounds and detect no-progress.

        Returns ``(round_number, failure_signature, repeated, severity)``
        where ``repeated`` is True when **either**:

        * the current structural failure signature is identical to the
          previous round's (the repair attempt produced no observable
          change), **or**
        * the number of structural failures did not strictly decrease
          between rounds (the loop is treading water — same number of
          broken things, possibly different ones).

        The second case catches retry loops where the generalist
        "fixes" one issue but introduces a different one, which would
        otherwise let the loop run until the hard round budget.
        """
        root_task_id = self._root_task_id(state, task)
        loops = state.blackboard.setdefault("verificationFeedbackLoops", {})
        loop = loops.setdefault(
            root_task_id,
            {
                "rounds": 0,
                "taskIds": [],
                "summaries": [],
                "signatures": [],
                "failureCounts": [],
            },
        )
        signature, severity = self._verifier_failure_signature(state, task, result)
        try:
            _ok, structural_failures, _ev, _dl = compute_structural_rubric(state, task, result)
        except Exception:
            structural_failures = []
        failure_count = len(structural_failures) + len(result.errors or [])
        prior_signatures = list(loop.get("signatures") or [])
        prior_counts = list(loop.get("failureCounts") or [])
        loop["rounds"] = int(loop.get("rounds") or 0) + 1
        loop.setdefault("taskIds", []).append(task.id)
        loop.setdefault("summaries", []).append(result.summary[:500])
        loop.setdefault("signatures", []).append(signature)
        loop.setdefault("failureCounts", []).append(failure_count)
        identical_signature = bool(
            signature
            and prior_signatures
            and prior_signatures[-1] == signature
        )
        # "No improvement" — failure count did not decrease vs prior round.
        # Require at least one prior round so the very first verifier
        # response can't trip this short-circuit. Also require
        # failure_count > 0 because zero failures means the verifier
        # actually passed and the caller wouldn't be on the feedback
        # path anyway.
        no_improvement_in_count = bool(
            prior_counts
            and failure_count > 0
            and failure_count >= prior_counts[-1]
        )
        repeated = identical_signature or no_improvement_in_count
        return int(loop["rounds"]), signature, repeated, severity

    def _verifier_failure_signature(
        self,
        state: MissionState,
        task: TaskNode,
        result: AgentResult,
    ) -> tuple[str, str]:
        """Build a short, comparable signature of the current verifier failure
        and classify its severity.

        Severity:
        * ``error``   — the rendered artifact is structurally broken
          (stuck loading, render error, visuals not rendered, browser error).
        * ``warning`` — only soft signals (missing browser evidence, missing
          expected text without other failure).
        """
        try:
            _passed, structural_failures, _evidence, _deliverables = compute_structural_rubric(
                state, task, result
            )
        except Exception:
            structural_failures = []
        followup_titles = sorted(
            {(followup.title or "").strip().lower() for followup in result.followup_tasks if followup.title}
        )
        error_codes = sorted({str(err)[:80] for err in (result.errors or [])})
        signature_parts = [
            "|".join(sorted(structural_failures)),
            "|".join(followup_titles),
            "|".join(error_codes),
        ]
        signature = stable_digest({"sig": signature_parts})
        hard_failures = {
            "REPORT_STUCK_LOADING",
            "BROWSER_ERROR_OBSERVED",
            "VISUALS_NOT_RENDERED",
        }
        severity = "error" if any(f in hard_failures for f in structural_failures) or error_codes else "warning"
        return signature, severity

    def _root_task_id(self, state: MissionState, task: TaskNode) -> str:
        current = task
        seen: set[str] = set()
        while current.parent_task_id and current.parent_task_id in state.tasks and current.parent_task_id not in seen:
            seen.add(current.id)
            current = state.tasks[current.parent_task_id]
        return current.id

    def _create_generalist_verification_review_task(
        self,
        state: MissionState,
        parent_task: TaskNode,
        result: AgentResult,
        *,
        feedback_round: int,
    ) -> None:
        if not self._consume_replan_budget(
            state,
            parent_task,
            reason="verification feedback needs generalist review",
        ):
            return
        proposed_count = len(result.followup_tasks)
        loop_guidance = (
            "This is the first failed verifier feedback round for this task root."
            if feedback_round == 1
            else (
                f"This is failed verifier feedback round {feedback_round} for the same task root. "
                "Explicitly compare current evidence with earlier attempts. If there is low or no improvement, "
                "stop routing another repair and return blocked/failed with the specific reason."
            )
        )
        task = TaskNode(
            title=f"Review verification feedback: {parent_task.title}",
            objective=(
                "Review FabricVerifier feedback for the completed Fabric deliverable and decide what happens next. "
                "Judge whether the original task is accomplished, what is good, what is bad, and what is lacking. "
                "Treat the verifier's proposed follow-up tasks as recommendations, not automatic instructions. "
                f"{loop_guidance} "
                "If repair is needed, emit a DYNAMIC_RESULT_START followupTasks block with precise repair tasks "
                "for the owning specialist and a final FabricVerifier verification task listed after the repairs "
                "with parallelismSafe=false so the result is checked again. If no safe repair exists, report the "
                "mission as blocked, failed, or partial instead of silently accepting broken artifacts. Name the missing "
                "tool, permission, broken artifact, repeated loop, or other concrete reason."
            ),
            dependencies=[parent_task.id],
            candidate_agent_ids=[GENERALIST_AGENT_ID],
            delegation_reason="FabricVerifier returned failed acceptance evidence that needs generalist triage.",
            context_summary=(
                f"Verifier result status={result.status.value}; feedbackRound={feedback_round}; "
                f"proposedRepairTasks={proposed_count}; "
                f"summary={result.summary[:600]}"
            ),
            touch_targets=list(parent_task.touch_targets),
            do_not_touch=list(parent_task.do_not_touch),
            acceptance_criteria=[
                "Decision states accomplished/not accomplished, good, bad, and lacking points.",
                "Repair work, if needed, is routed to the owning specialist with concrete acceptance criteria.",
                "A final FabricVerifier verification task is scheduled after any repair tasks.",
            ],
            context_refs=[parent_task.result_ref] if parent_task.result_ref else [],
            parallelism_safe=False,
            created_by="subagent",
            parent_task_id=parent_task.id,
            depth=parent_task.depth + 1,
        )
        self.create_task(
            state,
            task,
            rationale="Routed verifier feedback to the internal generalist for triage and repair-loop decision.",
        )
        self._emit("mission_replanned", state, parentTaskId=parent_task.id, taskId=task.id)

    def _create_followup_tasks(
        self,
        state: MissionState,
        parent_task: TaskNode,
        followup_tasks: list[FollowupTask],
    ) -> None:
        previous_followup_id: str | None = None
        for followup in followup_tasks:
            if not self._consume_replan_budget(
                state,
                parent_task,
                reason=f"follow-up task requested: {followup.title}",
            ):
                return
            dependencies = [parent_task.id]
            if not followup.parallelism_safe and previous_followup_id:
                dependencies.append(previous_followup_id)
            task = TaskNode(
                title=followup.title,
                objective=followup.objective,
                priority=followup.priority,
                dependencies=dependencies,
                candidate_agent_ids=list(followup.candidate_agent_ids),
                required_capabilities=list(followup.required_capabilities),
                delegation_reason=followup.delegation_reason,
                context_summary=followup.context_summary,
                touch_targets=list(followup.touch_targets),
                do_not_touch=list(followup.do_not_touch),
                acceptance_criteria=list(followup.acceptance_criteria),
                resource_claims=list(followup.resource_claims),
                context_refs=list(followup.context_refs) + [parent_task.result_ref] if parent_task.result_ref else list(followup.context_refs),
                parallelism_safe=followup.parallelism_safe,
                parallelism_notes=followup.parallelism_notes,
                created_by="subagent",
                parent_task_id=parent_task.id,
                depth=parent_task.depth + 1,
            )
            self.create_task(
                state,
                task,
                rationale=f"Accepted follow-up task suggested by {parent_task.id}.",
            )
            self._emit("mission_replanned", state, parentTaskId=parent_task.id, taskId=task.id)
            previous_followup_id = task.id

    def _abandon_run_and_create_replacement(
        self,
        state: MissionState,
        task: TaskNode,
        run: SubagentRun,
        *,
        reason: str,
        replacement_candidate_agent_ids: list[str] | None,
        signal: dict[str, Any],
    ) -> TaskNode:
        run.status = SubagentStatus.CANCELLED
        run.completed_at = self.now()
        run.cancellation_reason = reason
        self._release_locks(state, run.id)

        replacement_id = f"{task.id}-retry-{uuid.uuid4().hex[:6]}"
        candidates = list(replacement_candidate_agent_ids or task.candidate_agent_ids)
        replacement = TaskNode(
            id=replacement_id,
            title=f"Retry: {task.title}",
            objective=(
                f"{task.objective}\n\nMission-control correction: {reason}\n"
                f"Avoid repeating `{signal.get('toolName')}` with the same arguments hash "
                f"{signal.get('argHash')}. Inspect current state first, then use a different "
                "tool sequence or parameters."
            )[:8_000],
            priority=max(0, task.priority - 1),
            dependencies=list(task.dependencies),
            candidate_agent_ids=candidates,
            required_capabilities=list(task.required_capabilities),
            delegation_reason=f"Replacement for abandoned run {run.id}: {reason}",
            context_summary=task.context_summary,
            touch_targets=list(task.touch_targets),
            do_not_touch=list(task.do_not_touch),
            acceptance_criteria=list(task.acceptance_criteria),
            resource_claims=list(task.resource_claims),
            tool_scope=list(task.tool_scope),
            context_refs=list(task.context_refs),
            parallelism_safe=task.parallelism_safe,
            parallelism_notes=task.parallelism_notes,
            created_by="orchestrator",
            parent_task_id=task.id,
            depth=task.depth + 1,
        )

        superseded = state.blackboard.setdefault("supersededTasks", {})
        superseded[task.id] = {
            "replacementTaskId": replacement.id,
            "abandonedRunId": run.id,
            "reason": reason,
            "signal": signal,
        }
        task.status = TaskStatus.COMPLETED
        task.assigned_agent_run_id = run.id

        self.create_task(
            state,
            replacement,
            rationale="Mission control abandoned an off-course subagent and created a replacement task.",
        )
        self._rewrite_downstream_dependencies(state, old_task_id=task.id, replacement_task_id=replacement.id)
        self._record_action(
            state,
            OrchestratorActionType.CANCEL_SUBAGENT,
            reason,
            task_id=task.id,
            target_run_id=run.id,
            payload={"replacementTaskId": replacement.id, "signal": signal},
        )
        self._emit("subagent_abandoned", state, runId=run.id, taskId=task.id, replacementTaskId=replacement.id, reason=reason)
        self._emit("mission_replanned", state, parentTaskId=task.id, taskId=replacement.id)
        self._touch(state)
        return replacement

    def _rewrite_downstream_dependencies(self, state: MissionState, *, old_task_id: str, replacement_task_id: str) -> None:
        for downstream in state.tasks.values():
            if downstream.id == old_task_id or downstream.id == replacement_task_id:
                continue
            if old_task_id not in downstream.dependencies:
                continue
            downstream.dependencies = [
                replacement_task_id if dep_id == old_task_id else dep_id
                for dep_id in downstream.dependencies
            ]

    def _finish_run_cancelled(self, state: MissionState, run: SubagentRun, reason: str) -> None:
        run.status = SubagentStatus.CANCELLED
        run.completed_at = self.now()
        run.cancellation_reason = reason
        self._release_locks(state, run.id)
        self._touch(state)

    def _refresh_mission_terminal_state(self, state: MissionState) -> None:
        if any(task.status == TaskStatus.QUEUED for task in state.tasks.values()):
            return
        if any(task.status == TaskStatus.RUNNING for task in state.tasks.values()):
            return
        failed_tasks = [task for task in state.tasks.values() if task.status == TaskStatus.FAILED]
        blocked_tasks = [task for task in state.tasks.values() if task.status == TaskStatus.BLOCKED]
        if failed_tasks:
            state.status = MissionStatus.FAILED
            reason = self._terminal_task_reason(state, failed_tasks[0], severity="error")
            self._record_action(
                state,
                OrchestratorActionType.FAIL_MISSION,
                reason,
                task_id=failed_tasks[0].id,
                payload={"severity": "error", "failedTaskIds": [task.id for task in failed_tasks]},
            )
            self._emit("mission_failed", state, reason=reason)
        elif blocked_tasks:
            state.status = MissionStatus.BLOCKED
            reason = self._terminal_task_reason(state, blocked_tasks[0], severity="warning")
            self._record_action(
                state,
                OrchestratorActionType.MARK_TASK_BLOCKED,
                reason,
                task_id=blocked_tasks[0].id,
                payload={"severity": "warning", "blockedTaskIds": [task.id for task in blocked_tasks]},
            )
            self._emit("mission_blocked", state, reason=reason)
        elif any(task.status == TaskStatus.CANCELLED for task in state.tasks.values()):
            state.status = MissionStatus.CANCELLED
            self._emit("mission_cancelled", state)
        else:
            state.status = MissionStatus.COMPLETED
            self._record_action(state, OrchestratorActionType.FINISH_MISSION, "All tasks completed.")
            self._emit("mission_completed", state)
        self._touch(state)

    def _terminal_task_reason(self, state: MissionState, task: TaskNode, *, severity: str) -> str:
        result_summary = ""
        result_errors: list[str] = []
        if task.result_ref and task.result_ref in state.results:
            result = state.results[task.result_ref]
            result_summary = result.summary
            result_errors = list(result.errors)
        detail = "; ".join(result_errors) or task.context_summary or result_summary or task.delegation_reason or "No detailed reason was reported."
        return f"Mission {severity}: task {task.id} ({task.title}) is {task.status.value}: {detail[:700]}"

    def _consume_replan_budget(self, state: MissionState, parent_task: TaskNode, *, reason: str) -> bool:
        if state.replans_used >= state.brief.budget.max_replans:
            self._mark_task_blocked(
                state,
                parent_task,
                (
                    "Mission replan budget is exhausted. Generalist cannot safely create more repair or verification "
                    f"tasks for {parent_task.id}; last requested replan was: {reason}."
                ),
                reason="max_replans reached",
            )
            return False
        state.replans_used += 1
        return True

    def _ready_tasks(self, state: MissionState) -> list[TaskNode]:
        ready: list[TaskNode] = []
        for task in state.tasks.values():
            if task.status != TaskStatus.QUEUED:
                continue
            if all(state.tasks.get(dep_id) and state.tasks[dep_id].status == TaskStatus.COMPLETED for dep_id in task.dependencies):
                ready.append(task)
        ready.sort(key=lambda task: (task.priority, task.id))
        return ready

    def _running_count(self, state: MissionState) -> int:
        return sum(1 for run in state.subagent_runs.values() if run.status == SubagentStatus.RUNNING)

    def _select_agent(self, task: TaskNode) -> str | None:
        for agent_id in task.candidate_agent_ids:
            if agent_id in self.agent_templates:
                return agent_id
        if not task.required_capabilities:
            return None
        required = {capability.lower() for capability in task.required_capabilities}
        for agent_id, template in self.agent_templates.items():
            skill_names = {skill.name.lower() for skill in template.skills}
            skill_ids = {skill.id.lower() for skill in template.skills}
            tags = {tag.lower() for tag in template.tags}
            if required & (skill_names | skill_ids | tags):
                return agent_id
        return None

    def _tool_scope(self, task: TaskNode, agent_id: str) -> list[str]:
        if "*" in task.tool_scope:
            return ["*"]
        template = self.agent_templates.get(agent_id)
        available = set(template.available_tools if template else [])
        if task.tool_scope:
            scoped = [tool for tool in task.tool_scope if not available or tool in available]
            return scoped
        return sorted(available)

    def _build_context_pack(self, state: MissionState, task: TaskNode, *, agent_id: str, tool_scope: list[str]) -> dict[str, Any]:
        upstream_results = []
        task_results = state.blackboard.get("taskResults", {})
        for dependency_id in task.dependencies:
            if dependency_id in task_results:
                upstream_results.append({"taskId": dependency_id, **task_results[dependency_id]})
        specialist_catalog = state.blackboard.get("specialistCatalog", [])
        context_pack_v2 = self._build_context_pack_v2(
            state,
            task,
            agent_id=agent_id,
            tool_scope=tool_scope,
            upstream_results=upstream_results,
            specialist_catalog=specialist_catalog,
        )
        context_pack_v2_payload = context_pack_v2.model_dump(mode="json", by_alias=True)
        return {
            "mission": {
                "sessionId": state.brief.session_id,
                "goal": state.brief.goal,
                "workspaceId": state.brief.workspace_id,
                "constraints": list(state.brief.constraints),
                "successCriteria": list(state.brief.success_criteria),
            },
            "task": task.model_dump(mode="json", by_alias=True),
            "upstreamResults": upstream_results,
            "blackboardRefs": list(task.context_refs),
            "specialistCatalog": specialist_catalog,
            "contextPackV2": context_pack_v2_payload,
            "contextManagement": {
                "schemaVersion": context_pack_v2_payload["schemaVersion"],
                "qrspiProtocol": context_pack_v2_payload["qrspiProtocol"],
                "qrspiPhaseModel": context_pack_v2_payload["qrspiPhaseModel"],
                "phase": context_pack_v2_payload["phase"],
                "contextGoal": context_pack_v2_payload["contextGoal"],
                "instructionBudget": context_pack_v2_payload["instructionBudget"],
                "phaseInputs": context_pack_v2_payload["phaseInputs"],
                "subagentWorkModel": "context-window-fork",
                "agentIdIsExecutionTemplate": True,
                "verticalSlicePolicy": context_pack_v2_payload["verticalSlicePolicy"],
                "backtrackPolicy": context_pack_v2_payload["backtrackPolicy"],
                "reviewPolicy": context_pack_v2_payload["reviewPolicy"],
                "contextModeFacade": context_pack_v2_payload["contextMode"]["facade"],
                "contextModeEvents": context_pack_v2_payload["contextMode"]["events"],
                "handoffDigest": context_pack_v2_payload["handoffDigest"],
            },
        }

    def _build_context_pack_v2(
        self,
        state: MissionState,
        task: TaskNode,
        *,
        agent_id: str,
        tool_scope: list[str],
        upstream_results: list[dict[str, Any]],
        specialist_catalog: list[dict[str, Any]],
    ) -> ContextPackV2:
        phase = _context_pack_phase(task, agent_id)
        source_refs = _context_source_refs(state, task, upstream_results)
        retrieval_queries = _context_retrieval_queries(state, task, source_refs)
        token_payload = {
            "goal": state.brief.goal,
            "task": task.model_dump(mode="json", by_alias=True),
            "upstreamResults": upstream_results,
            "sourceRefs": source_refs,
            "retrievalQueries": retrieval_queries,
        }
        estimated_tokens = _estimate_tokens(token_payload)
        max_tokens = _phase_max_tokens(phase)
        compaction_threshold = max(1_000, int(max_tokens * 0.8))
        handoff_digest = stable_digest({
            "sessionId": state.brief.session_id,
            "taskId": task.id,
            "phase": phase,
            "sourceRefs": source_refs,
            "retrievalQueries": retrieval_queries,
            "acceptanceCriteria": list(task.acceptance_criteria),
        })
        compact_payload_estimate = _estimate_tokens({
            "taskTitle": task.title,
            "objective": bounded_text(task.objective, max_chars=600),
            "sourceRefs": source_refs[:8],
            "retrievalQueries": retrieval_queries[:4],
            "returnContract": _context_return_contract(phase),
        })
        return ContextPackV2(
            qrspi_protocol=QRSPI_PROTOCOL,
            qrspi_phase_model=list(QRSPI_PHASE_MODEL),
            phase=phase,
            mission={
                "sessionId": state.brief.session_id,
                "workspaceId": state.brief.workspace_id,
                "goalDigest": stable_digest(state.brief.goal),
                "constraints": list(state.brief.constraints),
                "successCriteria": list(state.brief.success_criteria),
                "approvalPolicy": dict(state.brief.approval_policy),
            },
            task=task.model_dump(mode="json", by_alias=True),
            execution_template={
                "agentId": agent_id,
                "agentTemplateName": self.agent_templates.get(agent_id).display_name if self.agent_templates.get(agent_id) else agent_id,
                "agentIdRole": "execution-template",
                "subagentWorkModel": "context-window-fork",
                "toolScope": list(tool_scope),
                "toolScopeCount": len(tool_scope),
            },
            context_goal=_context_goal(task, phase),
            context_budget=ContextPackBudgetV2(
                estimated_tokens=estimated_tokens,
                max_tokens=max_tokens,
                reserved_output_tokens=1_200 if phase != "research" else 1_600,
                compaction_threshold=compaction_threshold,
            ),
            instruction_budget=ContextPackInstructionBudgetV2(
                phase_instruction_limit=_phase_instruction_limit(phase),
                inherited_instruction_count=len(state.brief.constraints) + len(task.acceptance_criteria),
                max_instruction_tokens=_phase_instruction_tokens(phase),
                no_magic_words_required=True,
                compact_handoff_required=True,
            ),
            phase_inputs=_context_phase_inputs(
                state,
                task,
                phase=phase,
                source_refs=source_refs,
                retrieval_queries=retrieval_queries,
                upstream_results=upstream_results,
            ),
            source_budget=ContextPackSourceBudgetV2(
                max_files=12 if phase != "research" else 24,
                max_docs=6 if phase != "research" else 12,
                max_fabric_items=8,
                max_prior_findings=5 if phase != "research" else 10,
            ),
            source_refs=source_refs,
            retrieval_provenance=retrieval_queries,
            tool_policy={
                "allowedTools": list(tool_scope),
                "allowedToolCount": len(tool_scope),
                "deniedTools": ["raw_secret_reads", "cross_tenant_context_search", "unreviewed_write_tools"],
                "mcpAccessMode": "pi-mcp-adapter-proxy-via-agenthub-policy",
                "agenthubPolicyProxyRequired": True,
            },
            compaction=ContextPackCompactionV2(
                prior_summary_digest=stable_digest(upstream_results) if upstream_results else None,
                omitted_detail_count=max(0, estimated_tokens - compact_payload_estimate),
                freshness="current",
                reason=f"{phase}_subagent_context_window",
                threshold_tokens=compaction_threshold,
            ),
            evidence_requirements=list(task.acceptance_criteria),
            redaction_proof={
                "rawTranscriptIncluded": False,
                "secretLikeValuesStripped": True,
                "secretBearingFilesAllowed": False,
                "crossTenantRetrievalAllowed": False,
            },
            omission_policy=_context_omission_policy(phase),
            return_contract=_context_return_contract(phase),
            vertical_slice_policy=_vertical_slice_policy(phase),
            backtrack_policy=ContextPackBacktrackPolicyV2(
                allowed=True,
                valid_previous_phases=_valid_backtrack_phases(phase),
                evidence_required=_backtrack_evidence_required(phase),
                checkpoint_required=phase in {"worktree", "implement", "repair"},
            ),
            review_policy=_review_policy(phase),
            handoff_digest=handoff_digest,
            context_mode=ContextModeTelemetryV2(
                package=PI_CONTEXT_MODE_PACKAGE,
                mcp_server=PI_CONTEXT_MODE_MCP_SERVER,
                indexed_source_refs=source_refs[:12],
                retrieval_queries=retrieval_queries,
                saved_token_estimate=max(0, estimated_tokens - compact_payload_estimate),
                compaction_digest=stable_digest({"handoffDigest": handoff_digest, "compactPayloadEstimate": compact_payload_estimate}),
                rehydration_source="ContextPackV2.selectedSnippets",
                purge_handle=f"context-mode:{state.brief.workspace_id}:{state.brief.session_id}:{task.id}",
                isolation_scope=f"workspace:{state.brief.workspace_id}:session:{state.brief.session_id}",
                events=CONTEXT_MODE_TELEMETRY_EVENTS,
            ),
        )

    def _can_acquire_locks(self, state: MissionState, task: TaskNode) -> bool:
        for claim in task.resource_claims:
            lock = state.resource_locks.get(claim.key)
            if lock is None:
                continue
            if lock.mode == ResourceMode.READ and claim.mode == ResourceMode.READ:
                continue
            return False
        return True

    def _acquire_locks(self, state: MissionState, task: TaskNode, run_id: str) -> None:
        for claim in task.resource_claims:
            lock = state.resource_locks.get(claim.key)
            if lock is None:
                state.resource_locks[claim.key] = ResourceLock(
                    key=claim.key,
                    mode=claim.mode,
                    owner_run_ids=[run_id],
                )
                self._emit("resource_lock_acquired", state, key=claim.key, mode=claim.mode.value, runId=run_id)
                continue
            if run_id not in lock.owner_run_ids:
                lock.owner_run_ids.append(run_id)

    def _release_locks(self, state: MissionState, run_id: str) -> None:
        for key, lock in list(state.resource_locks.items()):
            if run_id not in lock.owner_run_ids:
                continue
            lock.owner_run_ids = [owner for owner in lock.owner_run_ids if owner != run_id]
            if not lock.owner_run_ids:
                state.resource_locks.pop(key, None)
                self._emit("resource_lock_released", state, key=key, runId=run_id)

    def _mark_task_blocked(self, state: MissionState, task: TaskNode, message: str, *, reason: str) -> None:
        task.status = TaskStatus.BLOCKED
        self._record_action(
            state,
            OrchestratorActionType.MARK_TASK_BLOCKED,
            reason,
            task_id=task.id,
            payload={"message": message},
        )
        self._touch(state)
        self._emit("task_blocked", state, taskId=task.id, reason=reason, message=message)

    def _mark_task_failed(
        self,
        state: MissionState,
        task: TaskNode,
        message: str,
        *,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        task.status = TaskStatus.FAILED
        self._record_action(
            state,
            OrchestratorActionType.FAIL_MISSION,
            reason,
            task_id=task.id,
            payload={"message": message, **(payload or {})},
        )
        self._touch(state)
        self._emit("task_failed", state, taskId=task.id, reason=reason, message=message)

    def _record_action(
        self,
        state: MissionState,
        action_type: OrchestratorActionType,
        rationale: str,
        *,
        task_id: str | None = None,
        target_run_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> OrchestratorAction:
        action = OrchestratorAction(
            type=action_type,
            rationale=rationale,
            task_id=task_id,
            target_run_id=target_run_id,
            payload=payload or {},
        )
        state.decisions.append(action)
        self._touch(state)
        self._emit(
            "orchestrator_decision",
            state,
            decision=action.model_dump(mode="json", by_alias=True),
        )
        return action

    def _emit(self, event_type: str, state: MissionState, **kwargs: Any) -> None:
        self.event_sink.emit(event_type, sessionId=state.brief.session_id, **kwargs)

    def _touch(self, state: MissionState) -> None:
        state.updated_at = self.now()
        if self.on_state_change is not None:
            self.on_state_change(state)

    def _budget_from_composition(self, composition: Composition | None) -> MissionBudget:
        if composition is None:
            return MissionBudget()
        return MissionBudget(
            max_active_subagents=4,
            max_total_subagents=max(1, min(50, len(composition.slots) + 12)),
            max_replans=20,
            max_task_graph_depth=6,
        )


def write_claim(kind: str, resource_id: str) -> ResourceClaim:
    return ResourceClaim(kind=kind, id=resource_id, mode=ResourceMode.WRITE)


def read_claim(kind: str, resource_id: str) -> ResourceClaim:
    return ResourceClaim(kind=kind, id=resource_id, mode=ResourceMode.READ)


def _is_transient_browser_propagation_failure(
    structural_failures: list[str],
    evidence: dict[str, Any],
) -> bool:
    if not structural_failures:
        return False
    allowed_failures = {"BROWSER_ERROR_OBSERVED", "VISUALS_NOT_RENDERED"}
    if not set(structural_failures).issubset(allowed_failures):
        return False
    if not evidence.get("browserVerifiedUrls"):
        return False
    errors = [str(error).lower() for error in (evidence.get("errorsObserved") or [])]
    if not errors:
        return False
    transient_markers = (
        "expected text",
        "not visible",
        "screenshot artifact is empty",
        "too small",
        "browser capture exceeded",
    )
    terminal_markers = (
        "couldn't load",
        "could not load",
        "something went wrong",
        "unable to render",
        "can't display",
        "access",
        "permission",
        "auth",
    )
    return all(
        any(marker in error for marker in transient_markers)
        and not any(marker in error for marker in terminal_markers)
        for error in errors
    )


def _is_browser_reverify_followup(followup: FollowupTask) -> bool:
    candidate_ids = set(followup.candidate_agent_ids or [])
    if not candidate_ids or not candidate_ids.issubset({FABRIC_VERIFIER_AGENT_ID}):
        return False
    text = f"{followup.title}\n{followup.objective}".lower()
    return "browser" in text and "verify" in text


def _summarize_context_pack(context_pack: dict[str, Any]) -> dict[str, Any]:
    task = context_pack.get("task") if isinstance(context_pack.get("task"), dict) else {}
    mission = context_pack.get("mission") if isinstance(context_pack.get("mission"), dict) else {}
    upstream_results = context_pack.get("upstreamResults") or []
    specialist_catalog = context_pack.get("specialistCatalog") or []
    blackboard_refs = context_pack.get("blackboardRefs") or []
    context_pack_v2 = context_pack.get("contextPackV2") if isinstance(context_pack.get("contextPackV2"), dict) else {}
    context_budget = context_pack_v2.get("contextBudget") if isinstance(context_pack_v2.get("contextBudget"), dict) else {}
    instruction_budget = context_pack_v2.get("instructionBudget") if isinstance(context_pack_v2.get("instructionBudget"), dict) else {}
    phase_inputs = context_pack_v2.get("phaseInputs") if isinstance(context_pack_v2.get("phaseInputs"), dict) else {}
    backtrack_policy = context_pack_v2.get("backtrackPolicy") if isinstance(context_pack_v2.get("backtrackPolicy"), dict) else {}
    context_mode = context_pack_v2.get("contextMode") if isinstance(context_pack_v2.get("contextMode"), dict) else {}
    return {
        "contextDigest": stable_digest(context_pack),
        "contextPackSchemaVersion": context_pack_v2.get("schemaVersion"),
        "qrspiProtocol": context_pack_v2.get("qrspiProtocol"),
        "qrspiPhaseModel": list(context_pack_v2.get("qrspiPhaseModel") or [])[:12],
        "contextPhase": context_pack_v2.get("phase"),
        "contextGoal": bounded_text(context_pack_v2.get("contextGoal"), max_chars=280),
        "contextBudgetEstimatedTokens": context_budget.get("estimatedTokens"),
        "contextBudgetMaxTokens": context_budget.get("maxTokens"),
        "contextCompactionThreshold": context_budget.get("compactionThreshold"),
        "instructionBudgetPhaseLimit": instruction_budget.get("phaseInstructionLimit"),
        "instructionBudgetInheritedCount": instruction_budget.get("inheritedInstructionCount"),
        "instructionBudgetMaxTokens": instruction_budget.get("maxInstructionTokens"),
        "instructionBudgetBasis": instruction_budget.get("budgetBasis"),
        "phaseInputArtifactIds": list(phase_inputs.get("artifactIds") or [])[:10],
        "phaseOriginalTaskHiddenFromResearch": phase_inputs.get("originalTaskHiddenFromResearch"),
        "phaseQuestionCount": len(phase_inputs.get("neutralQuestions") or []),
        "subagentWorkModel": (context_pack_v2.get("executionTemplate") or {}).get("subagentWorkModel") if isinstance(context_pack_v2.get("executionTemplate"), dict) else None,
        "contextOmissionPolicy": list(context_pack_v2.get("omissionPolicy") or [])[:8],
        "contextReturnContract": list(context_pack_v2.get("returnContract") or [])[:8],
        "verticalSlicePolicy": context_pack_v2.get("verticalSlicePolicy"),
        "backtrackAllowed": backtrack_policy.get("allowed"),
        "backtrackTargetPhases": list(backtrack_policy.get("validPreviousPhases") or [])[:8],
        "reviewPolicy": context_pack_v2.get("reviewPolicy"),
        "contextHandoffDigest": context_pack_v2.get("handoffDigest"),
        "contextModePackage": context_mode.get("package"),
        "contextModeFacade": context_mode.get("facade"),
        "contextModeSavedTokenEstimate": context_mode.get("savedTokenEstimate"),
        "contextModePurgeHandle": context_mode.get("purgeHandle"),
        "contextModeIsolationScope": context_mode.get("isolationScope"),
        "contextModeEventTypes": list(context_mode.get("events") or [])[:10],
        "missionGoalPreview": bounded_text(mission.get("goal"), max_chars=500),
        "workspaceId": mission.get("workspaceId"),
        "taskId": task.get("id"),
        "taskTitle": bounded_text(task.get("title"), max_chars=220),
        "upstreamResultCount": len(upstream_results),
        "upstreamTaskIds": [str(result.get("taskId")) for result in upstream_results[:10] if isinstance(result, dict)],
        "blackboardRefCount": len(blackboard_refs),
        "blackboardRefs": [str(ref) for ref in blackboard_refs[:10]],
        "specialistCatalogCount": len(specialist_catalog),
        "candidateAgents": list(task.get("candidateAgentIds") or task.get("candidate_agent_ids") or [])[:10],
        "requiredCapabilities": list(task.get("requiredCapabilities") or task.get("required_capabilities") or [])[:10],
        "acceptanceCriteriaCount": len(task.get("acceptanceCriteria") or task.get("acceptance_criteria") or []),
        "touchTargets": list(task.get("touchTargets") or task.get("touch_targets") or [])[:10],
        "doNotTouch": list(task.get("doNotTouch") or task.get("do_not_touch") or [])[:10],
    }


def _context_pack_phase(task: TaskNode, agent_id: str) -> str:
    text = f"{task.id} {task.title} {task.objective} {' '.join(task.required_capabilities)}".lower()
    if agent_id == FABRIC_VERIFIER_AGENT_ID or "verify" in text or "verifier" in text:
        return "verify"
    if task.id == "generalist" and agent_id == GENERALIST_AGENT_ID:
        return "question"
    if "design" in text or "option" in text or "decision" in text:
        return "design"
    if "structure" in text or "vertical slice" in text or "checkpoint" in text:
        return "structure"
    if "worktree" in text or "workspace checkpoint" in text:
        return "worktree"
    if "review" in text or "risk" in text:
        return "review"
    if "repair" in text or "fix" in text:
        return "repair"
    if agent_id == GENERALIST_AGENT_ID:
        return "plan"
    if task.created_by == "composition" or "research" in text or "inspect" in text or "inventory" in text:
        return "research"
    return "implement"


def _context_goal(task: TaskNode, phase: str) -> str:
    prefix = {
        "question": "Ask neutral questions before researching or designing a change.",
        "research": "Learn the smallest reliable facts needed for the next plan.",
        "design": "Synthesize factual research into options, current state, desired state, and open decisions.",
        "structure": "Outline vertical slices, contracts, checkpoints, and validation order before tactical planning.",
        "plan": "Create or refine a compact executable plan from verified facts.",
        "worktree": "Create a revertable implementation checkpoint before mutation begins.",
        "implement": "Complete one approved plan segment without replaying parent history.",
        "review": "Review the plan or diff from a fresh context window.",
        "verify": "Verify evidence from a fresh context window against acceptance criteria.",
        "repair": "Repair only the failed verifier assertion with minimal context.",
    }.get(phase, "Complete the delegated context-window task.")
    return bounded_text(f"{prefix} Task: {task.objective}", max_chars=900)


def _context_source_refs(state: MissionState, task: TaskNode, upstream_results: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    refs.extend(str(ref) for ref in state.brief.initial_context_refs)
    refs.extend(str(ref) for ref in task.context_refs)
    refs.extend(f"touch:{target}" for target in task.touch_targets)
    refs.extend(f"avoid:{target}" for target in task.do_not_touch)
    refs.extend(f"upstream-task:{result.get('taskId')}" for result in upstream_results if isinstance(result, dict) and result.get("taskId"))
    refs.extend(f"resource:{claim.kind}:{claim.id}:{claim.mode.value}" for claim in task.resource_claims)
    deduped: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if not ref or ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
    return deduped[:40]


def _context_retrieval_queries(state: MissionState, task: TaskNode, source_refs: list[str]) -> list[str]:
    candidates = [
        f"mission:{bounded_text(state.brief.goal, max_chars=160)}",
        f"task:{bounded_text(task.title, max_chars=120)}",
        f"objective:{bounded_text(task.objective, max_chars=180)}",
    ]
    candidates.extend(f"source:{ref}" for ref in source_refs[:6])
    candidates.extend(f"criterion:{bounded_text(criterion, max_chars=120)}" for criterion in task.acceptance_criteria[:4])
    return [candidate for candidate in candidates if candidate.strip()][:12]


def _context_omission_policy(phase: str) -> list[str]:
    base = [
        "Do not include raw parent transcript.",
        "Do not include secret-bearing env values, tokens, or credentials.",
        "Do not include unrelated specialist catalog entries.",
        "Do not include raw tool output when a digest or source ref is enough.",
    ]
    if phase == "research":
        base.append("Do not include the desired implementation outcome unless the research question explicitly requires it.")
    if phase in {"question", "design", "structure", "plan"}:
        base.append("Do not depend on magic words from the user; use the structural phase gates.")
    if phase in {"implement", "repair"}:
        base.append("Do not include research details beyond approved snippets and source refs.")
    if phase in {"review", "verify"}:
        base.append("Do not include implementer chat history unless explicitly retrieved by digest.")
    return base


def _context_return_contract(phase: str) -> list[str]:
    if phase == "question":
        return ["neutral research questions", "unknowns", "risk probes", "source areas to inspect"]
    if phase == "research":
        return ["compressed findings", "source refs", "uncertainty notes", "recommended next context queries"]
    if phase == "design":
        return ["current state", "desired state", "options", "open questions", "decision log"]
    if phase == "structure":
        return ["vertical slices", "contracts", "checkpoints", "validation order", "rollback points"]
    if phase == "plan":
        return ["ordered plan", "target refs", "risk notes", "test strategy", "approval needs"]
    if phase == "worktree":
        return ["checkpoint ref", "changed scope", "rollback command", "slice boundary"]
    if phase == "verify":
        return ["verdict", "evidence refs", "failed criteria", "minimal repair instructions"]
    if phase == "review":
        return ["review findings", "risk ranking", "missing tests", "approval recommendation"]
    if phase == "repair":
        return ["changed refs", "fixed assertion", "remaining risk", "verification command"]
    return ["completion summary", "changed refs", "evidence", "blockers", "follow-up questions"]


def _phase_max_tokens(phase: str) -> int:
    return {
        "question": 3_500,
        "research": 12_000,
        "design": 6_500,
        "structure": 5_500,
        "plan": 7_000,
        "worktree": 4_000,
        "implement": 6_000,
        "review": 5_000,
        "verify": 5_000,
        "repair": 4_500,
    }.get(phase, 6_000)


def _phase_instruction_limit(phase: str) -> int:
    return {
        "question": 4,
        "research": 5,
        "design": 6,
        "structure": 6,
        "plan": 6,
        "worktree": 4,
        "implement": 5,
        "review": 5,
        "verify": 5,
        "repair": 4,
    }.get(phase, 5)


def _phase_instruction_tokens(phase: str) -> int:
    return {
        "question": 550,
        "research": 750,
        "design": 900,
        "structure": 900,
        "plan": 900,
        "worktree": 650,
        "implement": 850,
        "review": 800,
        "verify": 800,
        "repair": 700,
    }.get(phase, 850)


def _context_phase_inputs(
    state: MissionState,
    task: TaskNode,
    *,
    phase: str,
    source_refs: list[str],
    retrieval_queries: list[str],
    upstream_results: list[dict[str, Any]],
) -> dict[str, Any]:
    neutral_questions = [
        f"What current behavior or artifact facts are needed for {bounded_text(task.title, max_chars=120)}?",
        "Which source refs prove the current behavior without relying on prior chat?",
        "What uncertainty must be resolved before design or structure is safe?",
    ]
    return {
        "neutralQuestions": neutral_questions,
        "originalTaskHiddenFromResearch": phase == "research",
        "loadedArtifactKinds": ["mission-brief", "context-pack", "source-refs"],
        "artifactIds": [f"context-pack:{state.brief.session_id}:{task.id}"],
        "sourceRefCount": len(source_refs),
        "retrievalQueryCount": len(retrieval_queries),
        "upstreamResultCount": len(upstream_results),
    }


def _vertical_slice_policy(phase: str) -> dict[str, Any]:
    return {
        "preferred": True,
        "strategy": "thin-end-to-end-slice-before-horizontal-layers",
        "checkpointBeforeMutation": phase in {"worktree", "implement", "repair"},
        "sliceEvidenceRequired": phase in {"implement", "verify", "repair"},
    }


def _valid_backtrack_phases(phase: str) -> list[str]:
    order = list(QRSPI_PHASE_MODEL)
    if phase not in order:
        return ["question", "research", "design", "structure", "plan"]
    return order[:order.index(phase)]


def _backtrack_evidence_required(phase: str) -> list[str]:
    if phase in {"design", "structure", "plan"}:
        return ["missing fact", "contradictory source ref", "unresolved decision"]
    if phase in {"implement", "verify", "review", "repair"}:
        return ["failed criterion", "diff evidence", "test or screenshot receipt", "source ref for mismatch"]
    return ["unclear question", "insufficient source coverage"]


def _review_policy(phase: str) -> dict[str, Any]:
    return {
        "planReviewIsAlignmentGate": True,
        "codeReviewRequiredBeforeFinish": phase in {"implement", "verify", "review", "repair"},
        "reviewContext": "fresh-design-structure-plan-diff-evidence",
        "humanApprovalRequiredForWrites": True,
    }


def _estimate_tokens(value: Any) -> int:
    try:
        text = json.dumps(value, default=str, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(value)
    return max(1, (len(text) + 3) // 4)


def _task_steering_preview(task: TaskNode) -> str:
    parts = [
        f"objective={bounded_text(task.objective, max_chars=360)}",
    ]
    if task.delegation_reason:
        parts.append(f"delegation={bounded_text(task.delegation_reason, max_chars=240)}")
    if task.context_summary:
        parts.append(f"context={bounded_text(task.context_summary, max_chars=240)}")
    if task.acceptance_criteria:
        parts.append("acceptance=" + "; ".join(bounded_text(item, max_chars=140) for item in task.acceptance_criteria[:4]))
    if task.do_not_touch:
        parts.append("do_not_touch=" + ",".join(task.do_not_touch[:8]))
    return " | ".join(parts)[:1200]
