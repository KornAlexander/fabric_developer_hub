"""Dynamic generalist-orchestrator runtime primitives."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from domain.models.agent_models import AgentTemplate, Job
from domain.models.composition import Composition
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
    emit_verifier_verdict,
    synthesize_mandatory_verification_followup,
    user_renderable_deliverables_from_result,
)
from services.observability import bounded_text, stable_digest

logger = logging.getLogger(__name__)

FABRIC_VERIFIER_AGENT_ID = "fabric-verifier"
MAX_VERIFIER_FEEDBACK_REVIEWS_PER_ROOT = 2


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
                "Inspect the mission, use the full MCP tool fleet when useful, "
                "complete only safe routing or lightweight discovery directly, "
                "and delegate implementation, modeling, governance, report creation, "
                "verification, and app work to specialized agents with structured task briefs. "
                "Do not create or mutate Fabric artifacts directly; spawn the specialist instead."
            ),
            candidate_agent_ids=[GENERALIST_AGENT_ID],
            tool_scope=[
                "fabric_list_workspaces",
                "fabric_list_items",
                "fabric_list_folders",
                "fabric_validate_workspace_capacity",
            ],
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
        context_pack = self._build_context_pack(state, task)
        context_summary = _summarize_context_pack(context_pack)
        context_pack_ref = f"context-{task.id}"
        state.blackboard.setdefault("contextPacks", {})[context_pack_ref] = context_pack
        tool_scope = self._tool_scope(task, agent_id)
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
            self._ensure_mandatory_verifier_followup(state, task, run, result)
        # Structural backstop (Phase A/C): if FabricVerifier claimed success but
        # the user-renderable deliverable lacks browser evidence (or shows a
        # stuck loading state / error modal), synthesise a repair follow-up so
        # the existing review/repair loop runs instead of silently accepting
        # the broken artifact. This preserves the verifier-driven gate even
        # when the verifier LLM only consults metadata.
        if run.agent_id == FABRIC_VERIFIER_AGENT_ID and not result.followup_tasks:
            from services.agenthub.verifier_verdict import (
                compute_structural_rubric,
                synthesize_browser_evidence_followup,
            )
            rubric_passed, structural_failures, _evidence, deliverables = compute_structural_rubric(
                state, task, result
            )
            if not rubric_passed:
                synthetic = synthesize_browser_evidence_followup(deliverables, structural_failures)
                # Mutating result.followup_tasks here (instead of routing
                # directly) keeps the existing route/feedback-round/replan
                # bookkeeping intact and visible in the event ledger.
                result.followup_tasks.append(synthetic)
                logger.info(
                    "[verifier-rubric] Forced repair followup for task %s due to structural failures: %s",
                    task.id,
                    ",".join(structural_failures),
                )
        if self._should_route_verifier_feedback_to_generalist(run, result):
            feedback_round = self._record_verifier_feedback_round(state, task, result)
            if feedback_round > MAX_VERIFIER_FEEDBACK_REVIEWS_PER_ROOT:
                self._mark_task_failed(
                    state,
                    task,
                    (
                        "Fabric verification is not converging after repeated repair attempts. "
                        f"Round {feedback_round} still produced failed verification feedback for task {task.id}."
                    ),
                    reason="verification feedback loop exceeded no-progress limit",
                    payload={
                        "severity": "error",
                        "feedbackRound": feedback_round,
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

    def _should_route_verifier_feedback_to_generalist(
        self,
        run: SubagentRun,
        result: AgentResult,
    ) -> bool:
        return run.agent_id == FABRIC_VERIFIER_AGENT_ID and bool(result.followup_tasks)

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
    ) -> int:
        root_task_id = self._root_task_id(state, task)
        loops = state.blackboard.setdefault("verificationFeedbackLoops", {})
        loop = loops.setdefault(root_task_id, {"rounds": 0, "taskIds": [], "summaries": []})
        loop["rounds"] = int(loop.get("rounds") or 0) + 1
        loop.setdefault("taskIds", []).append(task.id)
        loop.setdefault("summaries", []).append(result.summary[:500])
        return int(loop["rounds"])

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

    def _build_context_pack(self, state: MissionState, task: TaskNode) -> dict[str, Any]:
        upstream_results = []
        task_results = state.blackboard.get("taskResults", {})
        for dependency_id in task.dependencies:
            if dependency_id in task_results:
                upstream_results.append({"taskId": dependency_id, **task_results[dependency_id]})
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
            "specialistCatalog": state.blackboard.get("specialistCatalog", []),
        }

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


def _summarize_context_pack(context_pack: dict[str, Any]) -> dict[str, Any]:
    task = context_pack.get("task") if isinstance(context_pack.get("task"), dict) else {}
    mission = context_pack.get("mission") if isinstance(context_pack.get("mission"), dict) else {}
    upstream_results = context_pack.get("upstreamResults") or []
    specialist_catalog = context_pack.get("specialistCatalog") or []
    blackboard_refs = context_pack.get("blackboardRefs") or []
    return {
        "contextDigest": stable_digest(context_pack),
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
