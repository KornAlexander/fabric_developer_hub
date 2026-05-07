from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from domain.models.agent_models import AgentCategory, AgentTemplate, Job
from domain.models.composition import AgentSlot, Budget, Composition, Handoff
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
    ResourceLock,
    ResourceMode,
    SubagentRun,
    SubagentStatus,
    TaskNode,
    TaskStatus,
)
from domain.models.skill import Skill
from services.agenthub import _db, dynamic_mission_store
from services.agenthub.dynamic_orchestrator import (
    DynamicMissionController,
    GENERALIST_DIRECT_TOOL_SCOPE,
    read_claim,
    write_claim,
)


class CollectingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, **kwargs: Any) -> None:
        self.events.append({"type": event_type, **kwargs})


class ScriptedExecutor:
    def __init__(
        self,
        result_factories: dict[str, Callable[[SubagentRun, TaskNode], AgentResult]] | None = None,
        held_task_ids: set[str] | None = None,
    ) -> None:
        self.result_factories = result_factories or {}
        self.held_task_ids = held_task_ids or set()
        self.release_events = {task_id: asyncio.Event() for task_id in self.held_task_ids}
        self.started: list[tuple[str, str]] = []
        self.context_packs: dict[str, dict[str, Any]] = {}
        self.active_run_ids: set[str] = set()
        self.max_active = 0
        self.steers: list[tuple[str, str, str]] = []
        self.cancellations: list[tuple[str, str]] = []
        self._changed = asyncio.Event()

    async def run(
        self,
        *,
        mission: MissionState,
        run: SubagentRun,
        task: TaskNode,
        context_pack: dict[str, Any],
    ) -> AgentResult:
        self.started.append((run.id, task.id))
        self.context_packs[task.id] = context_pack
        self.active_run_ids.add(run.id)
        self.max_active = max(self.max_active, len(self.active_run_ids))
        self._changed.set()
        try:
            release_event = self.release_events.get(task.id)
            if release_event is not None:
                await release_event.wait()
            factory = self.result_factories.get(task.id) or self.result_factories.get(run.agent_id)
            if factory is not None:
                return factory(run, task)
            return AgentResult(
                run_id=run.id,
                task_id=task.id,
                status=AgentResultStatus.SUCCESS,
                summary=f"{task.title} complete",
                evidence=[{"taskId": task.id}],
            )
        finally:
            self.active_run_ids.discard(run.id)
            self._changed.set()

    async def steer(self, *, run: SubagentRun, message: str, reason: str) -> None:
        self.steers.append((run.id, message, reason))

    async def cancel(self, *, run: SubagentRun, reason: str) -> None:
        self.cancellations.append((run.id, reason))

    async def wait_for_started(self, count: int) -> None:
        while len(self.started) < count:
            self._changed.clear()
            await asyncio.wait_for(self._changed.wait(), timeout=1)

    def release(self, task_id: str) -> None:
        self.release_events[task_id].set()


def _skill(skill_id: str, *tools: str) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id.replace("-", " ").title(),
        description=f"{skill_id} skill",
        tools=list(tools),
    )


def _template(agent_id: str, *skills: Skill) -> AgentTemplate:
    return AgentTemplate(
        id=agent_id,
        name=agent_id,
        display_name=agent_id.title(),
        category=AgentCategory.ENGINEERING,
        description="test template",
        skills=list(skills),
        system_prompt="test system prompt",
        available_tools=[tool for skill in skills for tool in skill.tools],
        tags=[skill.id for skill in skills],
    )


def _controller(
    executor: ScriptedExecutor | None = None,
    sink: CollectingSink | None = None,
    *,
    now: Callable[[], datetime] | None = None,
) -> tuple[DynamicMissionController, ScriptedExecutor, CollectingSink]:
    effective_executor = executor or ScriptedExecutor()
    effective_sink = sink or CollectingSink()
    templates = {
        "generalist": _template("generalist", _skill("generalist", "fabric_list_items")),
        "admin": _template("admin", _skill("admin", "fabric_list_workspaces", "fabric_update_workspace")),
        "builder": _template("builder", _skill("build", "fabric_create_item", "fabric_write_file")),
        "modeler": _template("modeler", _skill("model", "fabric_update_model")),
    }
    templates["generalist"].is_internal = True
    controller = DynamicMissionController(
        executor=effective_executor,
        event_sink=effective_sink,
        agent_templates=templates,
        now=now or (lambda: datetime.now(UTC)),
    )
    return controller, effective_executor, effective_sink


def _mission_state(*, budget: MissionBudget | None = None) -> MissionState:
    return MissionState(
        brief=MissionBrief(
            session_id="mission-1",
            goal="Build a Fabric solution",
            workspace_id="workspace-1",
            budget=budget or MissionBudget(max_active_subagents=4, max_total_subagents=20),
        )
    )


def _composition() -> Composition:
    return Composition(
        session_id="session-1",
        task="Audit and build",
        architecture="sequential",
        rationale="test",
        headline="test",
        subtitle="test",
        slots=[
            AgentSlot(id="discover", agent_id="admin", role="Discover workspace"),
            AgentSlot(id="build", agent_id="builder", role="Build artifacts"),
        ],
        handoffs=[Handoff.model_validate({"from": "discover", "to": "build", "kind": "report"})],
        entrypoint_slot_id="discover",
        budget=Budget(max_turns=10, max_tool_calls=20, max_wallclock_s=60),
    )


SAMPLE_TRACEABILITY_PROMPT = (
    "Create a Fabric-ready analytics outcome from the sample workspace context: inspect the available context, "
    "plan the work as the generalist, delegate independent implementation and validation subtasks to specialists "
    "where useful, create or update the required artifacts, verify the produced outputs independently, and show a "
    "complete trace of planning decisions, delegation, monitoring, interventions if needed, verifier findings, and "
    "final outcome in Mission Control."
)


def test_seed_from_job_starts_with_generalist_mission_controller() -> None:
    controller, _, sink = _controller()
    job = Job(
        id="session-1",
        user_id="user-1",
        workspace_id="workspace-1",
        task_description="Audit and build",
        composition=_composition(),
    )

    state = controller.seed_from_job(job)

    assert state.brief.goal == "Audit and build"
    assert state.brief.preferred_strategy == "dynamic-generalist"
    assert list(state.tasks) == ["generalist"]
    assert state.tasks["generalist"].candidate_agent_ids == ["generalist"]
    assert state.tasks["generalist"].tool_scope == list(GENERALIST_DIRECT_TOOL_SCOPE)
    assert state.tasks["generalist"].created_by == "orchestrator"
    assert {entry["id"] for entry in state.blackboard["specialistCatalog"]} == {"admin", "builder", "modeler"}
    assert any(event["type"] == "mission_seeded" for event in sink.events)
    assert [decision.type for decision in state.decisions].count(OrchestratorActionType.CREATE_TASK) == 1


@pytest.mark.asyncio
async def test_independent_tasks_run_in_parallel_and_merge_structured_results() -> None:
    executor = ScriptedExecutor(held_task_ids={"inventory", "quality"})
    controller, _, sink = _controller(executor)
    state = _mission_state(budget=MissionBudget(max_active_subagents=2, max_total_subagents=10))
    controller.create_task(
        state,
        TaskNode(id="inventory", title="Inventory", objective="Inspect workspace", candidate_agent_ids=["admin"]),
        rationale="seed test task",
    )
    controller.create_task(
        state,
        TaskNode(id="quality", title="Quality", objective="Inspect model", candidate_agent_ids=["modeler"]),
        rationale="seed test task",
    )

    mission_task = asyncio.create_task(controller.run_until_idle(state))
    await executor.wait_for_started(2)

    assert executor.max_active == 2
    assert {task_id for _, task_id in executor.started} == {"inventory", "quality"}

    executor.release("inventory")
    executor.release("quality")
    await mission_task

    assert state.status == MissionStatus.COMPLETED
    assert state.tasks["inventory"].status == TaskStatus.COMPLETED
    assert state.tasks["quality"].status == TaskStatus.COMPLETED
    assert set(state.blackboard["taskResults"]) == {"inventory", "quality"}
    assert any(event["type"] == "parallel_group_spawned" for event in sink.events)
    assert any(event["type"] == "mission_completed" for event in sink.events)


@pytest.mark.asyncio
async def test_generalist_observability_events_explain_context_and_state_decisions() -> None:
    executor = ScriptedExecutor()
    controller, _, sink = _controller(executor)
    state = _mission_state()
    controller.create_task(
        state,
        TaskNode(
            id="inventory",
            title="Inventory workspace",
            objective="Inspect workspace items and report the source tables.",
            candidate_agent_ids=["admin"],
            acceptance_criteria=["Workspace inventory is complete."],
            context_summary="User needs a support-grade inventory pass.",
        ),
        rationale="seed test task",
    )

    await controller.run_until_idle(state)

    check_in = next(event for event in sink.events if event["type"] == "generalist_check_in")
    context_pack = next(event for event in sink.events if event["type"] == "generalist_context_pack")
    spawn = next(event for event in sink.events if event["type"] == "subagent_spawned")
    decision = next(event for event in sink.events if event["type"] == "generalist_state_decision")

    assert check_in["readyTaskCount"] == 1
    assert check_in["readyTaskIds"] == ["inventory"]
    assert context_pack["taskId"] == "inventory"
    assert context_pack["agentId"] == "admin"
    assert context_pack["contextDigest"]
    assert context_pack["contextPackSchemaVersion"] == 2
    assert context_pack["contextPhase"] == "research"
    assert context_pack["subagentWorkModel"] == "context-window-fork"
    assert context_pack["contextGoal"].startswith("Learn the smallest reliable facts")
    assert context_pack["contextBudgetMaxTokens"] == 12000
    assert context_pack["contextModePackage"] == "npm:context-mode@1.0.103"
    assert context_pack["contextModeFacade"] == "agenthub-governed-context-mode"
    assert context_pack["contextModeSavedTokenEstimate"] >= 0
    assert "pi.context.mode.indexed" in context_pack["contextModeEventTypes"]
    assert "Do not include raw parent transcript." in context_pack["contextOmissionPolicy"]
    assert "source refs" in context_pack["contextReturnContract"]
    assert context_pack["toolScopeCount"] == 2
    assert context_pack["acceptanceCriteriaCount"] == 1
    assert "support-grade inventory" in context_pack["steeringPreview"]
    assert spawn["contextDigest"] == context_pack["contextDigest"]
    assert spawn["contextPackSchemaVersion"] == 2
    assert spawn["contextPhase"] == "research"
    assert spawn["contextModePackage"] == "npm:context-mode@1.0.103"
    assert decision["taskId"] == "inventory"
    assert decision["agentId"] == "admin"
    assert decision["resultStatus"] == "success"
    assert decision["artifactCount"] == 0

    stored_pack = state.blackboard["contextPacks"][context_pack["contextPackRef"]]
    context_pack_v2 = stored_pack["contextPackV2"]
    assert context_pack_v2["schemaVersion"] == 2
    assert context_pack_v2["executionTemplate"]["agentId"] == "admin"
    assert context_pack_v2["executionTemplate"]["agentIdRole"] == "execution-template"
    assert context_pack_v2["executionTemplate"]["subagentWorkModel"] == "context-window-fork"
    assert context_pack_v2["contextMode"]["facade"] == "agenthub-governed-context-mode"
    assert context_pack_v2["contextMode"]["purgeHandle"].endswith(":inventory")
    assert context_pack_v2["toolPolicy"]["agenthubPolicyProxyRequired"] is True


@pytest.mark.asyncio
async def test_generalist_direct_work_event_explains_takeover_choice() -> None:
    executor = ScriptedExecutor()
    controller, _, sink = _controller(executor)
    state = _mission_state()
    controller.create_task(
        state,
        TaskNode(
            id="triage",
            title="Generalist triage",
            objective="Inspect verifier feedback and decide whether to repair, abort, or continue.",
            candidate_agent_ids=["generalist"],
            acceptance_criteria=["Decision names repair or abort rationale."],
        ),
        rationale="seed direct generalist task",
    )

    await controller.run_until_idle(state)

    direct = next(event for event in sink.events if event["type"] == "generalist_direct_work")
    assert direct["taskId"] == "triage"
    assert direct["taskTitle"] == "Generalist triage"
    assert direct["agentId"] == "generalist"
    assert "handle this task directly" in direct["reason"]
    assert direct["contextDigest"]


@pytest.mark.asyncio
async def test_sample_prompt_runtime_emits_reconstructable_orchestration_log_sequence() -> None:
    verifier_attempts = 0

    def generalist_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        if task.id == "generalist":
            return AgentResult(
                run_id=run.id,
                task_id=task.id,
                status=AgentResultStatus.SUCCESS,
                summary="Generalist planned the sample mission and delegated independent discovery and build work.",
                followup_tasks=[
                    FollowupTask(
                        title="Inspect sample workspace context",
                        objective="Inspect the available sample workspace context and identify source artifacts.",
                        candidate_agent_ids=["admin"],
                        delegation_reason="Workspace discovery is a specialist read-only task.",
                        context_summary="Sample prompt requires context inspection before implementation.",
                        acceptance_criteria=["Workspace sources and constraints are summarized."],
                        parallelism_safe=True,
                    ),
                    FollowupTask(
                        title="Create Fabric-ready analytics artifacts",
                        objective="Create the lakehouse/report artifacts requested by the sample prompt.",
                        candidate_agent_ids=["builder"],
                        delegation_reason="Artifact creation is better handled by the Fabric builder specialist.",
                        context_summary="Build a Fabric-ready analytics outcome from the sample workspace context.",
                        touch_targets=["lakehouse:Customer360", "report:Customer360"],
                        acceptance_criteria=["Lakehouse artifact exists.", "Report artifact exists."],
                        parallelism_safe=True,
                    ),
                ],
            )
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Generalist reviewed verifier findings and routed a concrete repair plus re-verification.",
            followup_tasks=[
                FollowupTask(
                    title="Repair report visual evidence",
                    objective="Repair the report binding and produce fresh screenshot evidence.",
                    candidate_agent_ids=["builder"],
                    delegation_reason="Verifier found missing screenshot evidence for the builder-owned report.",
                    context_summary="Initial verifier reported that the report screenshot evidence was missing.",
                    touch_targets=["report:Customer360"],
                    acceptance_criteria=["Screenshot evidence exists and report renders."],
                ),
                FollowupTask(
                    title="Re-verify Fabric analytics outcome",
                    objective="Independently verify the repaired Fabric artifacts and screenshot evidence.",
                    candidate_agent_ids=["fabric-verifier"],
                    delegation_reason="Generalist requires independent verification after repair.",
                    context_summary="Repair must pass the verifier before mission completion.",
                    touch_targets=["report:Customer360"],
                    acceptance_criteria=["Verifier accepts the repaired report evidence."],
                ),
            ],
        )

    def builder_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        if task.title.startswith("Repair"):
            return AgentResult(
                run_id=run.id,
                task_id=task.id,
                status=AgentResultStatus.SUCCESS,
                summary="Report binding repaired and screenshot evidence attached.",
                artifacts=[
                    {
                        "type": "Report",
                        "name": "Customer360_Readiness_Report",
                        "id": "report-1",
                        "webUrl": "https://app.fabric.microsoft.com/groups/workspace-1/reports/report-1",
                    }
                ],
                evidence=[{"kind": "screenshot", "artifactId": "report-1", "status": "captured"}],
            )
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Created initial Fabric analytics artifacts and requested independent verification.",
            artifacts=[
                {"type": "Lakehouse", "name": "Lakehouse_Customer360_Readiness", "id": "lakehouse-1"},
                    {
                        "type": "Report",
                        "name": "Customer360_Readiness_Report",
                        "id": "report-1",
                        "webUrl": "https://app.fabric.microsoft.com/groups/workspace-1/reports/report-1",
                    },
            ],
            followup_tasks=[
                FollowupTask(
                    title="Verify Fabric analytics outcome",
                    objective="Independently verify the created lakehouse/report artifacts and screenshot evidence.",
                    candidate_agent_ids=["fabric-verifier"],
                    delegation_reason="Generalist always requires independent Fabric verification for produced outputs.",
                    context_summary="Initial artifacts exist and need acceptance verification.",
                    touch_targets=["lakehouse:lakehouse-1", "report:report-1"],
                    acceptance_criteria=["Artifacts exist.", "Report screenshot evidence matches expectations."],
                )
            ],
        )

    def verifier_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        nonlocal verifier_attempts
        verifier_attempts += 1
        if verifier_attempts == 1:
            return AgentResult(
                run_id=run.id,
                task_id=task.id,
                status=AgentResultStatus.FAILED,
                summary="Verifier found missing report screenshot evidence and requested repair.",
                errors=["Report screenshot evidence missing for report-1."],
                followup_tasks=[
                    FollowupTask(
                        title="Repair report visual evidence",
                        objective="Attach screenshot evidence and prove report-1 renders.",
                        candidate_agent_ids=["builder"],
                        acceptance_criteria=["Screenshot evidence is attached."],
                    )
                ],
            )
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Verifier accepted the repaired report with screenshot evidence.",
            evidence=[
                {
                    "stepResults": [
                        {"step": "Professional quality review", "status": "passed", "evidence": "report design, information hierarchy, usability interactions/tooltips, naming convention fit, modern style/theme, championship 3-30-300 storytelling, methodology/source transparency, accessibility contrast/keyboard checks, semantic model clarity, performance, maintainability, extensibility, and clean code software engineering reviewed"},
                    ]
                },
                {
                    "toolName": "browser_verify_visual_render",
                    "url": "https://app.fabric.microsoft.com/groups/workspace-1/reports/report-1",
                    "finalUrl": "https://app.fabric.microsoft.com/groups/workspace-1/reports/report-1",
                    "screenshotPath": "/tmp/report-1.png",
                    "visualLikeElementCount": 1,
                    "bodyTextSample": "Customer360 readiness report",
                    "artifactId": "report-1",
                    "status": "accepted",
                }
            ],
        )

    executor = ScriptedExecutor(
        result_factories={
            "generalist": generalist_result,
            "builder": builder_result,
            "fabric-verifier": verifier_result,
        }
    )
    controller, _, sink = _controller(executor)
    controller.agent_templates["fabric-verifier"] = _template(
        "fabric-verifier",
        _skill("fabric-verification", "fabric_list_items", "fabric_verify_report_renderable", "fabric_capture_screenshot"),
    )
    state = controller.seed_from_job(
        Job(
            id="sample-prompt-session",
            user_id="user-1",
            workspace_id="workspace-1",
            task_description=SAMPLE_TRACEABILITY_PROMPT,
            composition=Composition(
                session_id="sample-prompt-session",
                task=SAMPLE_TRACEABILITY_PROMPT,
                architecture="dynamic",
                rationale="sample e2e traceability prompt",
                headline="Sample traceability mission",
                subtitle="Runtime sample prompt traceability proof",
                slots=[AgentSlot(id="generalist", agent_id="generalist", role="Mission controller")],
                entrypoint_slot_id="generalist",
                budget=Budget(max_turns=10, max_tool_calls=20, max_wallclock_s=60),
            ),
        )
    )

    await controller.run_until_idle(state)

    event_types = [event["type"] for event in sink.events]
    assert state.brief.goal == SAMPLE_TRACEABILITY_PROMPT
    assert state.status == MissionStatus.COMPLETED
    assert "mission_seeded" in event_types
    assert "generalist_direct_work" in event_types
    assert "generalist_check_in" in event_types
    assert "generalist_context_pack" in event_types
    assert "parallel_group_spawned" in event_types
    assert "generalist_state_decision" in event_types
    assert "subagent_result" in event_types
    assert "mission_replanned" in event_types
    assert "mission_completed" in event_types
    assert verifier_attempts == 2

    direct = next(event for event in sink.events if event["type"] == "generalist_direct_work")
    assert direct["taskId"] == "generalist"
    assert direct["contextDigest"]

    parallel_event = next(event for event in sink.events if event["type"] == "parallel_group_spawned")
    parallel_task_ids = {
        state.subagent_runs[run_id].task_id
        for run_id in parallel_event["runIds"]
    }
    assert {state.tasks[task_id].title for task_id in parallel_task_ids} == {
        "Inspect sample workspace context",
        "Create Fabric-ready analytics artifacts",
    }

    verifier_feedback = next(
        event
        for event in sink.events
        if event["type"] == "subagent_result"
        and event["agentId"] == "fabric-verifier"
        and event["resultStatus"] == "failed"
    )
    assert "missing report screenshot evidence" in verifier_feedback["summary"]

    review_direct = next(
        event
        for event in sink.events
        if event["type"] == "generalist_direct_work"
        and event["taskTitle"].startswith("Review verification feedback")
    )
    assert review_direct["contextDigest"]

    final_verification = next(
        event
        for event in reversed(sink.events)
        if event["type"] == "subagent_result" and event["agentId"] == "fabric-verifier"
    )
    assert final_verification["resultStatus"] == "success"
    assert "screenshot evidence" in final_verification["summary"]

    assert all(event.get("sessionId") == "sample-prompt-session" for event in sink.events)
    assert all(action.created_at.tzinfo is not None for action in state.decisions)
    assert any(action.type == OrchestratorActionType.SPAWN_PARALLEL_GROUP for action in state.decisions)
    assert any(action.type == OrchestratorActionType.MERGE_RESULT for action in state.decisions)
    assert any(
        action.type == OrchestratorActionType.FINISH_MISSION
        and action.rationale == "All tasks completed."
        for action in state.decisions
    )


@pytest.mark.asyncio
async def test_produced_report_without_followup_gets_mandatory_verifier_gate() -> None:
    report_url = "https://app.fabric.microsoft.com/groups/workspace-1/reports/report-1"

    def generalist_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.PARTIAL,
            summary="Created the inventory solution artifacts but did not request verification.",
            artifacts=[
                {
                    "entity_type": "WorkspaceInventorySolution",
                    "entity_name": "Inventory solution",
                    "details": json.dumps(
                        {
                            "createdItems": [
                                {
                                    "type": "Report",
                                    "id": "report-1",
                                    "displayName": "Inventory Report",
                                    "webUrl": report_url,
                                },
                                {
                                    "type": "SemanticModel",
                                    "id": "model-1",
                                    "displayName": "Inventory Model",
                                },
                            ]
                        }
                    ),
                }
            ],
        )

    def verifier_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Report renders in browser and matches the requested inventory solution.",
            evidence=[
                {
                    "stepResults": [
                        {"step": "Professional quality review", "status": "passed", "evidence": "multi-visual inventory report, information hierarchy, usability interactions/tooltips, naming convention fit, modern style/theme, championship 3-30-300 storytelling, methodology/source transparency, accessibility contrast/keyboard checks, semantic model quality, performance, maintainability, extensibility, and clean code software engineering accepted"},
                    ]
                },
                {
                    "toolName": "browser_verify_visual_render",
                    "url": report_url,
                    "finalUrl": report_url,
                    "screenshotPath": "/tmp/report-1.png",
                    "visualLikeElementCount": 1,
                    "bodyTextSample": "Inventory Report Total Items",
                }
            ],
        )

    executor = ScriptedExecutor(
        result_factories={
            "generalist": generalist_result,
            "fabric-verifier": verifier_result,
        }
    )
    controller, _, sink = _controller(executor)
    controller.agent_templates["fabric-verifier"] = _template(
        "fabric-verifier",
        _skill("fabric-verification", "browser_verify_visual_render"),
    )
    state = controller.seed_from_job(
        Job(
            id="mandatory-verifier-session",
            user_id="user-1",
            workspace_id="workspace-1",
            task_description="Create a workspace inventory report and prove it renders.",
            composition=Composition(
                session_id="mandatory-verifier-session",
                task="Create a workspace inventory report and prove it renders.",
                architecture="dynamic",
                rationale="test mandatory verifier guardrail",
                headline="Inventory mission",
                subtitle="Verifier guardrail proof",
                slots=[AgentSlot(id="generalist", agent_id="generalist", role="Mission controller")],
                entrypoint_slot_id="generalist",
                budget=Budget(max_turns=10, max_tool_calls=20, max_wallclock_s=60),
            ),
        )
    )

    await controller.run_until_idle(state)

    verifier_tasks = [
        task for task in state.tasks.values()
        if "fabric-verifier" in task.candidate_agent_ids
    ]
    assert len(verifier_tasks) == 1
    assert verifier_tasks[0].parent_task_id == "generalist"
    assert "Inventory Report" in verifier_tasks[0].objective
    assert "browser_verify_visual_render" in verifier_tasks[0].objective
    assert ("generalist", "generalist") in [(task_id, task_id) for _, task_id in executor.started]
    assert any(state.subagent_runs[run_id].agent_id == "fabric-verifier" for run_id in state.subagent_runs)

    mandatory_decision = next(
        event for event in sink.events
        if event["type"] == "generalist_state_decision"
        and "mandatory FabricVerifier gate" in event.get("summary", "")
    )
    assert mandatory_decision["followupTaskCount"] == 1

    verdict = next(event for event in sink.events if event["type"] == "verifier_verdict")
    assert verdict["passed"] is True
    assert verdict["requiresUserBrowserRender"] is True
    assert verdict["evidence"]["visualsRendered"] is True
    assert verdict["deliverables"][0]["id"] == "report-1"

    result_snapshot = state.blackboard["taskResults"]["generalist"]
    assert result_snapshot["followupTasks"][0]["candidateAgentIds"] == ["fabric-verifier"]


@pytest.mark.asyncio
async def test_verifier_text_followup_drives_repair_when_browser_shows_stuck_loading() -> None:
    report_url = "https://app.powerbi.com/groups/workspace-1/reports/report-1"

    def verifier_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Report shell painted but visual never rendered.",
            artifacts=[
                {
                    "entityType": "browser_visual_render",
                    "entityName": report_url,
                    "webUrl": report_url,
                    "details": json.dumps(
                        {
                            "ok": True,
                            "status": "passed",
                            "finalUrl": report_url,
                            "screenshotPath": "/tmp/report.png",
                            "bodyTextSample": "Power BI Report Loading your report... Report Zoomed To 100%",
                            "expectedTextMatched": False,
                            "visualSummary": {"visibleVisualLikeElementCount": 4},
                        }
                    ),
                }
            ],
            followup_tasks=[
                FollowupTask(
                    title="Repair stuck report rendering",
                    objective="Report is stuck on 'Loading your report...' and never paints visuals.",
                    candidate_agent_ids=["fabric-data-engineer"],
                )
            ],
        )

    executor = ScriptedExecutor(result_factories={"fabric-verifier": verifier_result})
    controller, _, sink = _controller(executor)
    controller.agent_templates["fabric-verifier"] = _template(
        "fabric-verifier",
        _skill("fabric-verification", "browser_verify_visual_render"),
    )
    state = _mission_state(budget=MissionBudget(max_active_subagents=1, max_total_subagents=5))
    controller.create_task(
        state,
        TaskNode(
            id="verify-report",
            title="Verify report",
            objective="Verify the report renders in the browser.",
            candidate_agent_ids=["fabric-verifier"],
        ),
        rationale="seed verifier task",
    )

    await controller.run_until_idle(state)

    verdict = next(event for event in sink.events if event["type"] == "verifier_verdict")
    assert verdict["passed"] is False
    assert "REPORT_STUCK_LOADING" in verdict["structuralFailures"]
    assert verdict["evidence"]["loadingStuckObserved"] is True
    persisted_followups = state.blackboard["taskResults"]["verify-report"]["followupTasks"]
    assert persisted_followups, "verifier follow-ups must be preserved when render is stuck"


@pytest.mark.asyncio
async def test_transient_browser_text_miss_rechecks_without_repair_loop() -> None:
    report_url = "https://app.powerbi.com/groups/workspace-1/reports/report-1"
    verifier_attempts = 0

    def verifier_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        nonlocal verifier_attempts
        verifier_attempts += 1
        if verifier_attempts == 1:
            return AgentResult(
                run_id=run.id,
                task_id=task.id,
                status=AgentResultStatus.SUCCESS,
                summary="Report server validation passed, but browser text was not visible during propagation.",
                artifacts=[
                    {"type": "Report", "name": "Inventory Report", "id": "report-1", "webUrl": report_url},
                    {
                        "stepResults": [
                            {"step": "Professional quality review", "status": "passed", "evidence": "information hierarchy, usability interactions/tooltips, naming convention fit, modern style/theme, championship 3-30-300 storytelling, methodology/source transparency, accessibility contrast/keyboard checks, semantic model quality, performance, maintainability, extensibility, and clean code software engineering accepted; only browser text propagation is unresolved"},
                        ]
                    },
                    {
                        "toolName": "browser_verify_visual_render",
                        "url": report_url,
                        "finalUrl": report_url,
                        "screenshotPath": "/tmp/report-propagating.png",
                        "bodyTextSample": "Power BI Report",
                        "status": "failed",
                        "ok": False,
                        "errorCode": "EXPECTED_TEXT_NOT_VISIBLE",
                        "reason": "expected text was not visible in the rendered page: 'Fabric Items'",
                        "expectedTextMatched": False,
                        "visualSummary": {"visibleVisualLikeElementCount": 0},
                    },
                ],
                followup_tasks=[
                    FollowupTask(
                        title="Repair report rendering issue",
                        objective="Rebuild the report because the browser text was not visible.",
                        candidate_agent_ids=["fabric-data-engineer"],
                    )
                ],
            )
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Report renders in browser after propagation.",
            evidence=[
                {"type": "Report", "name": "Inventory Report", "id": "report-1", "webUrl": report_url},
                {
                    "stepResults": [
                        {"step": "Professional quality review", "status": "passed", "evidence": "report design, information hierarchy, usability interactions/tooltips, naming convention fit, modern style/theme, championship 3-30-300 storytelling, methodology/source transparency, accessibility contrast/keyboard checks, semantic model quality, performance, maintainability, extensibility, and clean code software engineering accepted after propagation"},
                    ]
                },
                {
                    "toolName": "browser_verify_visual_render",
                    "url": report_url,
                    "finalUrl": report_url,
                    "screenshotPath": "/tmp/report-ready.png",
                    "bodyTextSample": "Power BI Report 493 Item Count",
                    "status": "passed",
                    "ok": True,
                    "expectedTextMatched": False,
                    "visualSummary": {"visibleVisualLikeElementCount": 83},
                },
            ],
        )

    executor = ScriptedExecutor(result_factories={"fabric-verifier": verifier_result})
    controller, _, sink = _controller(executor)
    controller.agent_templates["fabric-verifier"] = _template(
        "fabric-verifier",
        _skill("fabric-verification", "browser_verify_visual_render"),
    )
    controller.agent_templates["fabric-data-engineer"] = _template(
        "fabric-data-engineer",
        _skill("fabric-build", "fabric_create_workspace_inventory_solution"),
    )
    state = _mission_state(budget=MissionBudget(max_active_subagents=1, max_total_subagents=5))
    controller.create_task(
        state,
        TaskNode(
            id="verify-report",
            title="Verify report",
            objective="Verify the report renders in the browser.",
            candidate_agent_ids=["fabric-verifier"],
        ),
        rationale="seed verifier task",
    )

    await controller.run_until_idle(state)

    assert verifier_attempts == 2
    assert state.status == MissionStatus.COMPLETED
    assert not any(
        task.title.startswith("Review verification feedback")
        or task.title.startswith("Repair report rendering")
        for task in state.tasks.values()
    )
    first_snapshot = state.blackboard["taskResults"]["verify-report"]
    assert first_snapshot["followupTasks"][0]["candidateAgentIds"] == ["fabric-verifier"]
    verdicts = [event for event in sink.events if event["type"] == "verifier_verdict"]
    assert verdicts[-1]["passed"] is True


@pytest.mark.asyncio
async def test_repeated_stuck_loading_signature_bails_with_error_severity() -> None:
    report_url = "https://app.powerbi.com/groups/workspace-1/reports/report-1"

    def stuck_verifier(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Report shell painted but visual never rendered.",
            artifacts=[
                {
                    "entityType": "browser_visual_render",
                    "entityName": report_url,
                    "webUrl": report_url,
                    "details": json.dumps(
                        {
                            "ok": True,
                            "status": "passed",
                            "finalUrl": report_url,
                            "screenshotPath": "/tmp/report.png",
                            "bodyTextSample": "Power BI Report Loading your report... Report Zoomed To 100%",
                            "visualSummary": {"visibleVisualLikeElementCount": 4},
                        }
                    ),
                }
            ],
            followup_tasks=[
                FollowupTask(
                    title="Repair stuck report rendering",
                    objective="Report is stuck on 'Loading your report...' and never paints visuals.",
                    candidate_agent_ids=["fabric-data-engineer"],
                )
            ],
        )

    def generalist_re_verify(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Generalist queues another verifier pass.",
            followup_tasks=[
                FollowupTask(
                    title="Re-verify inventory report",
                    objective="Verify report renders after attempted repair.",
                    candidate_agent_ids=["fabric-verifier"],
                )
            ],
        )

    executor = ScriptedExecutor(
        result_factories={
            "fabric-verifier": stuck_verifier,
            "generalist": generalist_re_verify,
        }
    )
    controller, _, sink = _controller(executor)
    controller.agent_templates["fabric-verifier"] = _template(
        "fabric-verifier",
        _skill("fabric-verification", "browser_verify_visual_render"),
    )
    state = _mission_state(budget=MissionBudget(max_active_subagents=1, max_total_subagents=20))
    controller.create_task(
        state,
        TaskNode(
            id="verify-report",
            title="Verify report",
            objective="Verify the report renders in the browser.",
            candidate_agent_ids=["fabric-verifier"],
        ),
        rationale="seed verifier task",
    )

    await controller.run_until_idle(state)

    no_progress_events = [event for event in sink.events if event["type"] == "mission_no_progress"]
    assert no_progress_events, "expected mission_no_progress event when stuck signature repeats"
    bail = no_progress_events[0]
    assert bail["severity"] == "error"
    assert bail["repeatedFailure"] is True
    assert bail["feedbackRound"] >= 2
    assert state.status in (MissionStatus.FAILED, MissionStatus.BLOCKED)


@pytest.mark.asyncio
async def test_write_locks_prevent_conflicting_parallel_subagents() -> None:
    executor = ScriptedExecutor(held_task_ids={"first-write"})
    controller, _, _ = _controller(executor)
    state = _mission_state(budget=MissionBudget(max_active_subagents=2, max_total_subagents=10))
    shared_model = write_claim("semantic-model", "model-1")
    controller.create_task(
        state,
        TaskNode(
            id="first-write",
            title="First write",
            objective="Update model",
            candidate_agent_ids=["modeler"],
            resource_claims=[shared_model],
        ),
        rationale="seed test task",
    )
    controller.create_task(
        state,
        TaskNode(
            id="second-write",
            title="Second write",
            objective="Update same model",
            candidate_agent_ids=["modeler"],
            resource_claims=[shared_model],
        ),
        rationale="seed test task",
    )

    mission_task = asyncio.create_task(controller.run_until_idle(state))
    await executor.wait_for_started(1)

    assert len(executor.active_run_ids) == 1
    assert len(state.resource_locks) == 1
    assert [task_id for _, task_id in executor.started] == ["first-write"]

    executor.release("first-write")
    await executor.wait_for_started(2)
    await mission_task

    assert [task_id for _, task_id in executor.started] == ["first-write", "second-write"]
    assert executor.max_active == 1
    assert state.resource_locks == {}
    assert state.status == MissionStatus.COMPLETED


@pytest.mark.asyncio
async def test_read_locks_can_run_in_parallel() -> None:
    executor = ScriptedExecutor(held_task_ids={"first-read", "second-read"})
    controller, _, _ = _controller(executor)
    state = _mission_state(budget=MissionBudget(max_active_subagents=2, max_total_subagents=10))
    shared_claim = read_claim("lakehouse", "lakehouse-1")
    for task_id in ("first-read", "second-read"):
        controller.create_task(
            state,
            TaskNode(
                id=task_id,
                title=task_id,
                objective="Read shared lakehouse",
                candidate_agent_ids=["admin"],
                resource_claims=[shared_claim],
            ),
            rationale="seed test task",
        )

    mission_task = asyncio.create_task(controller.run_until_idle(state))
    await executor.wait_for_started(2)

    assert executor.max_active == 2

    executor.release("first-read")
    executor.release("second-read")
    await mission_task

    assert state.status == MissionStatus.COMPLETED


@pytest.mark.asyncio
async def test_followup_tasks_are_created_and_executed_after_parent_result() -> None:
    def root_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Discovery found a model that needs repair.",
            followup_tasks=[
                FollowupTask(
                    title="Repair semantic model",
                    objective="Fix the model relationships discovered by inventory.",
                    candidate_agent_ids=["modeler"],
                )
            ],
        )

    executor = ScriptedExecutor(result_factories={"root": root_result})
    controller, _, sink = _controller(executor)
    state = _mission_state()
    controller.create_task(
        state,
        TaskNode(id="root", title="Discovery", objective="Find follow-up work", candidate_agent_ids=["admin"]),
        rationale="seed test task",
    )

    await controller.run_until_idle(state)

    followups = [task for task in state.tasks.values() if task.parent_task_id == "root"]
    assert len(followups) == 1
    assert followups[0].status == TaskStatus.COMPLETED
    assert followups[0].dependencies == ["root"]
    assert state.replans_used == 1
    assert state.status == MissionStatus.COMPLETED
    assert [task_id for _, task_id in executor.started] == ["root", followups[0].id]
    assert any(event["type"] == "mission_replanned" for event in sink.events)


@pytest.mark.asyncio
async def test_followup_tasks_preserve_structured_delegation_brief() -> None:
    def root_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Discovery found a report to polish.",
            followup_tasks=[
                FollowupTask(
                    title="Polish inventory report",
                    objective="Improve only the Inventory report visuals using the discovered model fields.",
                    candidate_agent_ids=["modeler"],
                    delegation_reason="The modeler owns report visual-quality review.",
                    context_summary="Inventory report exists and uses model-1.",
                    touch_targets=["report:Inventory"],
                    do_not_touch=["semantic-model:model-1"],
                    acceptance_criteria=["Report layout passes visual-quality review."],
                    resource_claims=[read_claim("semantic-model", "model-1")],
                    parallelism_safe=True,
                    parallelism_notes="Read-only model inspection can run beside unrelated work.",
                )
            ],
        )

    executor = ScriptedExecutor(result_factories={"root": root_result})
    controller, _, _ = _controller(executor)
    state = _mission_state()
    controller.create_task(
        state,
        TaskNode(id="root", title="Discovery", objective="Find follow-up work", candidate_agent_ids=["admin"]),
        rationale="seed test task",
    )

    await controller.run_until_idle(state)

    followup = next(task for task in state.tasks.values() if task.parent_task_id == "root")
    context_pack = executor.context_packs[followup.id]
    assert followup.delegation_reason == "The modeler owns report visual-quality review."
    assert followup.context_summary == "Inventory report exists and uses model-1."
    assert followup.touch_targets == ["report:Inventory"]
    assert followup.do_not_touch == ["semantic-model:model-1"]
    assert followup.acceptance_criteria == ["Report layout passes visual-quality review."]
    assert followup.parallelism_safe is True
    assert context_pack["task"]["touchTargets"] == ["report:Inventory"]
    assert context_pack["task"]["doNotTouch"] == ["semantic-model:model-1"]
    assert context_pack["task"]["parallelismSafe"] is True


@pytest.mark.asyncio
async def test_failed_verifier_result_with_repair_followup_replans_instead_of_dead_ending() -> None:
    def verifier_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.FAILED,
            summary="Report render proof failed; route repair to the builder.",
            errors=["Report export failed for report-1."],
            followup_tasks=[
                FollowupTask(
                    title="Repair inventory report render failure",
                    objective=(
                        "Fix report-1 so it binds to semantic-model-1, renders successfully, "
                        "and then hand it back for verification."
                    ),
                    candidate_agent_ids=["builder"],
                    required_capabilities=["build"],
                    delegation_reason="FabricVerifier found a broken report artifact.",
                    context_summary="report-1 export failed while semantic-model-1 was queryable.",
                    touch_targets=["report:report-1", "semantic-model:semantic-model-1"],
                    acceptance_criteria=["fabric_verify_report_renderable returns status rendered."],
                )
            ],
        )

    def generalist_review_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Generalist reviewed verifier evidence and routed repair plus re-verification.",
            followup_tasks=[
                FollowupTask(
                    title="Repair inventory report render failure",
                    objective="Fix report-1 so it binds to semantic-model-1 and renders successfully.",
                    candidate_agent_ids=["builder"],
                    required_capabilities=["build"],
                    delegation_reason="Generalist accepted FabricVerifier evidence and routed repair to the builder.",
                    context_summary="report-1 export failed while semantic-model-1 was queryable.",
                    touch_targets=["report:report-1", "semantic-model:semantic-model-1"],
                    acceptance_criteria=["fabric_verify_report_renderable returns status rendered."],
                ),
                FollowupTask(
                    title="Re-verify inventory report after repair",
                    objective="Verify report-1 again against the original task after the builder repair completes.",
                    candidate_agent_ids=["fabric-verifier"],
                    required_capabilities=["fabric-verification"],
                    delegation_reason="Generalist requires a verifier pass after repair before mission completion.",
                    context_summary="Repair must be followed by acceptance verification.",
                    touch_targets=["report:report-1", "semantic-model:semantic-model-1"],
                    acceptance_criteria=["Report renders and data matches the original inventory task."],
                ),
            ],
        )

    executor = ScriptedExecutor(result_factories={"verify": verifier_result, "generalist": generalist_review_result})
    controller, _, _ = _controller(executor)
    controller.agent_templates["fabric-verifier"] = _template(
        "fabric-verifier",
        _skill("fabric-verification", "fabric_verify_report_renderable"),
    )
    state = _mission_state()
    controller.create_task(
        state,
        TaskNode(
            id="verify",
            title="Verify inventory report",
            objective="Validate created report against task expectations.",
            candidate_agent_ids=["fabric-verifier"],
        ),
        rationale="seed verifier task",
    )

    await controller.run_until_idle(state)

    review = next(task for task in state.tasks.values() if task.parent_task_id == "verify")
    repair, reverify = [task for task in state.tasks.values() if task.parent_task_id == review.id]

    assert state.tasks["verify"].status == TaskStatus.COMPLETED
    assert review.status == TaskStatus.COMPLETED
    assert review.candidate_agent_ids == ["generalist"]
    assert review.dependencies == ["verify"]
    assert "accomplished" in review.objective
    assert "what is good" in review.objective

    review_context = executor.context_packs[review.id]
    upstream_result = review_context["upstreamResults"][0]
    assert upstream_result["taskId"] == "verify"
    assert upstream_result["status"] == "failed"
    assert upstream_result["followupTasks"][0]["title"] == "Repair inventory report render failure"

    assert repair.status == TaskStatus.COMPLETED
    assert repair.candidate_agent_ids == ["builder"]
    assert repair.dependencies == [review.id]
    assert reverify.status == TaskStatus.COMPLETED
    assert reverify.candidate_agent_ids == ["fabric-verifier"]
    assert reverify.dependencies == [review.id, repair.id]
    assert state.status == MissionStatus.COMPLETED
    assert [task_id for _, task_id in executor.started] == ["verify", review.id, repair.id, reverify.id]


@pytest.mark.asyncio
async def test_non_converging_verifier_feedback_loop_fails_with_named_reason() -> None:
    def verifier_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.FAILED,
            summary="Report still does not render after repair.",
            errors=["fabric_verify_report_renderable still fails for report-1."],
            followup_tasks=[
                FollowupTask(
                    title="Repair report render failure again",
                    objective="Try another repair for report-1.",
                    candidate_agent_ids=["builder"],
                    acceptance_criteria=["report-1 renders successfully"],
                )
            ],
        )

    def generalist_review_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Generalist sees a possible alternate repair and schedules one more verification pass.",
            followup_tasks=[
                FollowupTask(
                    title="Repair inventory report",
                    objective="Apply a different repair to report-1.",
                    candidate_agent_ids=["builder"],
                    acceptance_criteria=["report-1 renders successfully"],
                ),
                FollowupTask(
                    title="Re-verify inventory report",
                    objective="Verify report-1 after the alternate repair.",
                    candidate_agent_ids=["fabric-verifier"],
                    acceptance_criteria=["FabricVerifier accepts report-1."],
                ),
            ],
        )

    executor = ScriptedExecutor(result_factories={"fabric-verifier": verifier_result, "generalist": generalist_review_result})
    controller, _, sink = _controller(executor)
    controller.agent_templates["fabric-verifier"] = _template(
        "fabric-verifier",
        _skill("fabric-verification", "fabric_verify_report_renderable"),
    )
    state = _mission_state()
    controller.create_task(
        state,
        TaskNode(
            id="verify",
            title="Verify inventory report",
            objective="Validate created report against task expectations.",
            candidate_agent_ids=["fabric-verifier"],
        ),
        rationale="seed verifier task",
    )

    await controller.run_until_idle(state)

    assert state.status == MissionStatus.FAILED
    loop = state.blackboard["verificationFeedbackLoops"]["verify"]
    # The repeated identical signature triggers an early bail at round 2,
    # rather than running a third repair attempt that would re-confirm the
    # same failure.
    assert loop["rounds"] == 2
    assert any(task.status == TaskStatus.FAILED and task.parent_task_id for task in state.tasks.values())
    assert any(
        decision.type == OrchestratorActionType.FAIL_MISSION
        and (
            "verification feedback loop exceeded no-progress limit" in decision.rationale
            or "same structural failure twice" in decision.rationale
        )
        for decision in state.decisions
    )
    assert any(event["type"] == "task_failed" and "not converging" in event["message"] for event in sink.events)


@pytest.mark.asyncio
async def test_replan_budget_exhaustion_blocks_with_named_warning() -> None:
    def discovery_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Discovery found one more task, but no replan budget remains.",
            followup_tasks=[
                FollowupTask(
                    title="Follow up with builder",
                    objective="Create an additional artifact.",
                    candidate_agent_ids=["builder"],
                )
            ],
        )

    executor = ScriptedExecutor(result_factories={"root": discovery_result})
    controller, _, sink = _controller(executor)
    state = _mission_state(budget=MissionBudget(max_active_subagents=1, max_total_subagents=5, max_replans=0))
    controller.create_task(
        state,
        TaskNode(
            id="root",
            title="Discover work",
            objective="Discover and route follow-up work.",
            candidate_agent_ids=["admin"],
        ),
        rationale="seed root task",
    )

    await controller.run_until_idle(state)

    assert state.tasks["root"].status == TaskStatus.BLOCKED
    assert state.status == MissionStatus.BLOCKED
    assert len(state.tasks) == 1
    assert any(
        decision.type == OrchestratorActionType.MARK_TASK_BLOCKED
        and "max_replans reached" in decision.rationale
        for decision in state.decisions
    )
    assert any(
        event["type"] == "task_blocked"
        and "Mission replan budget is exhausted" in event["message"]
        for event in sink.events
    )


@pytest.mark.asyncio
async def test_ambiguous_followup_siblings_are_serialized_by_default() -> None:
    def root_result(run: SubagentRun, task: TaskNode) -> AgentResult:
        return AgentResult(
            run_id=run.id,
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="Discovery produced two write tasks.",
            followup_tasks=[
                FollowupTask(
                    title="Create lakehouse table",
                    objective="Create the inventory table.",
                    candidate_agent_ids=["builder"],
                ),
                FollowupTask(
                    title="Create semantic model",
                    objective="Create the semantic model after table shape is stable.",
                    candidate_agent_ids=["modeler"],
                ),
            ],
        )

    executor = ScriptedExecutor(result_factories={"root": root_result})
    controller, _, _ = _controller(executor)
    state = _mission_state()
    controller.create_task(
        state,
        TaskNode(id="root", title="Discovery", objective="Find follow-up work", candidate_agent_ids=["admin"]),
        rationale="seed test task",
    )

    await controller.run_until_idle(state)

    followups = [task for task in state.tasks.values() if task.parent_task_id == "root"]
    assert len(followups) == 2
    assert followups[0].dependencies == ["root"]
    assert followups[1].dependencies == ["root", followups[0].id]


@pytest.mark.asyncio
async def test_steer_running_subagent_records_directive_and_emits_event() -> None:
    executor = ScriptedExecutor(held_task_ids={"inspect"})
    controller, _, sink = _controller(executor)
    state = _mission_state()
    controller.create_task(
        state,
        TaskNode(id="inspect", title="Inspect", objective="Inspect source", candidate_agent_ids=["admin"]),
        rationale="seed test task",
    )

    mission_task = asyncio.create_task(controller.run_until_idle(state))
    await executor.wait_for_started(1)
    run_id = next(iter(state.subagent_runs))

    steered = await controller.steer_subagent(
        state,
        run_id,
        message="Use Sales_Raw_2026 instead of Sales_Raw.",
        reason="A peer branch found the renamed source table.",
    )

    assert steered is True
    assert state.subagent_runs[run_id].directives == ["Use Sales_Raw_2026 instead of Sales_Raw."]
    assert executor.steers == [(run_id, "Use Sales_Raw_2026 instead of Sales_Raw.", "A peer branch found the renamed source table.")]
    assert any(event["type"] == "subagent_steered" and event["runId"] == run_id for event in sink.events)
    assert any(
        event["type"] == "generalist_steering"
        and event["runId"] == run_id
        and event["reason"] == "A peer branch found the renamed source table."
        for event in sink.events
    )

    executor.release("inspect")
    await mission_task


@pytest.mark.asyncio
async def test_tool_loop_signal_first_steers_running_subagent() -> None:
    executor = ScriptedExecutor()
    controller, _, sink = _controller(executor)
    state = _mission_state()
    state.tasks["build"] = TaskNode(
        id="build",
        title="Build item",
        objective="Create the Fabric item.",
        candidate_agent_ids=["builder"],
        status=TaskStatus.RUNNING,
    )
    run = SubagentRun(
        id="run-build",
        task_id="build",
        agent_id="builder",
        agent_session_id="agent-session-1",
        status=SubagentStatus.RUNNING,
    )
    state.subagent_runs[run.id] = run

    decision = await controller.supervise_tool_loop(
        state,
        agent_session_id="agent-session-1",
        tool_name="fabric_create_item",
        arg_hash="abc123",
        output_preview="POLICY_DENIED: circuit breaker",
        replacement_candidate_agent_ids=["modeler"],
    )

    assert decision["action"] == "steer"
    assert state.decisions[-2].type == OrchestratorActionType.INSPECT_SUBAGENT_LOGS
    assert state.decisions[-1].type == OrchestratorActionType.STEER_SUBAGENT
    assert executor.steers[0][0] == "run-build"
    assert "Stop retrying" in executor.steers[0][1]
    assert any(event["type"] == "subagent_inspected" and event["runId"] == "run-build" for event in sink.events)


@pytest.mark.asyncio
async def test_repeated_tool_loop_abandons_run_and_replans_replacement() -> None:
    controller, _, sink = _controller()
    state = _mission_state()
    state.tasks["build"] = TaskNode(
        id="build",
        title="Build item",
        objective="Create the Fabric item.",
        candidate_agent_ids=["builder"],
        status=TaskStatus.RUNNING,
    )
    state.tasks["validate"] = TaskNode(
        id="validate",
        title="Validate item",
        objective="Validate the created item.",
        candidate_agent_ids=["modeler"],
        dependencies=["build"],
    )
    run = SubagentRun(
        id="run-build",
        task_id="build",
        agent_id="builder",
        agent_session_id="agent-session-1",
        status=SubagentStatus.RUNNING,
    )
    state.subagent_runs[run.id] = run
    state.resource_locks["workspace-item:report"] = ResourceLock(
        key="workspace-item:report",
        mode=ResourceMode.WRITE,
        owner_run_ids=[run.id],
    )

    await controller.supervise_tool_loop(
        state,
        agent_session_id="agent-session-1",
        tool_name="fabric_create_item",
        arg_hash="abc123",
        output_preview="POLICY_DENIED: circuit breaker",
        replacement_candidate_agent_ids=["modeler"],
    )
    decision = await controller.supervise_tool_loop(
        state,
        agent_session_id="agent-session-1",
        tool_name="fabric_create_item",
        arg_hash="abc123",
        output_preview="POLICY_DENIED: circuit breaker again",
        replacement_candidate_agent_ids=["modeler"],
    )

    replacement_id = decision["replacementTaskId"]
    replacement = state.tasks[replacement_id]
    assert decision["action"] == "abandon"
    assert run.status == SubagentStatus.CANCELLED
    assert state.tasks["build"].status == TaskStatus.COMPLETED
    assert replacement.candidate_agent_ids == ["modeler"]
    assert replacement.dependencies == []
    assert "Avoid repeating `fabric_create_item`" in replacement.objective
    assert state.tasks["validate"].dependencies == [replacement_id]
    assert state.resource_locks == {}
    assert state.blackboard["supersededTasks"]["build"]["replacementTaskId"] == replacement_id
    assert any(event["type"] == "subagent_abandoned" and event["replacementTaskId"] == replacement_id for event in sink.events)


@pytest.mark.asyncio
async def test_cancel_running_subagent_releases_lock_and_allows_next_task() -> None:
    executor = ScriptedExecutor(held_task_ids={"stuck"})
    controller, _, sink = _controller(executor)
    state = _mission_state(budget=MissionBudget(max_active_subagents=2, max_total_subagents=10))
    shared_model = write_claim("semantic-model", "model-1")
    controller.create_task(
        state,
        TaskNode(
            id="stuck",
            title="Stuck writer",
            objective="Update model slowly",
            priority=0,
            candidate_agent_ids=["modeler"],
            resource_claims=[shared_model],
        ),
        rationale="seed test task",
    )
    controller.create_task(
        state,
        TaskNode(
            id="replacement",
            title="Replacement writer",
            objective="Update model after cancellation",
            priority=100,
            candidate_agent_ids=["modeler"],
            resource_claims=[shared_model],
        ),
        rationale="seed test task",
    )

    mission_task = asyncio.create_task(controller.run_until_idle(state))
    await executor.wait_for_started(1)
    stuck_run_id = next(iter(state.subagent_runs))

    cancelled = await controller.cancel_subagent(state, stuck_run_id, reason="No progress after inspection.")
    await executor.wait_for_started(2)
    await mission_task

    assert cancelled is True
    assert executor.cancellations == [(stuck_run_id, "No progress after inspection.")]
    assert state.tasks["stuck"].status == TaskStatus.CANCELLED
    assert state.tasks["replacement"].status == TaskStatus.COMPLETED
    assert state.resource_locks == {}
    assert any(event["type"] == "subagent_cancelled" and event["runId"] == stuck_run_id for event in sink.events)


@pytest.mark.asyncio
async def test_stale_subagent_inspection_records_decision() -> None:
    fixed_now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    controller, _, sink = _controller(now=lambda: fixed_now)
    state = _mission_state()
    run = SubagentRun(
        id="run-stale",
        task_id="task-stale",
        agent_id="admin",
        status=SubagentStatus.RUNNING,
        heartbeat_at=fixed_now - timedelta(seconds=90),
    )
    state.subagent_runs[run.id] = run

    stale = await controller.inspect_stale_subagents(state, stale_after_seconds=30)

    assert stale == [run]
    assert state.decisions[-1].type == OrchestratorActionType.INSPECT_SUBAGENT_LOGS
    assert any(event["type"] == "subagent_stale" and event["runId"] == "run-stale" for event in sink.events)


@pytest.mark.asyncio
async def test_total_subagent_budget_blocks_remaining_ready_tasks() -> None:
    controller, executor, sink = _controller()
    state = _mission_state(budget=MissionBudget(max_active_subagents=1, max_total_subagents=1))
    controller.create_task(
        state,
        TaskNode(id="first", title="First", objective="First task", candidate_agent_ids=["admin"]),
        rationale="seed test task",
    )
    controller.create_task(
        state,
        TaskNode(id="second", title="Second", objective="Second task", candidate_agent_ids=["admin"]),
        rationale="seed test task",
    )

    await controller.run_until_idle(state)

    assert [task_id for _, task_id in executor.started] == ["first"]
    assert state.tasks["first"].status == TaskStatus.COMPLETED
    assert state.tasks["second"].status == TaskStatus.BLOCKED
    assert state.status == MissionStatus.BLOCKED
    assert any(event["type"] == "task_blocked" and event["taskId"] == "second" for event in sink.events)


def test_dynamic_mission_store_roundtrips_full_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTHUB_DB_PATH", str(tmp_path / "agenthub.db"))
    _db.reset_path_cache()
    try:
        state = _mission_state()
        state.status = MissionStatus.BLOCKED
        state.replans_used = 1
        state.blackboard["taskResults"] = {"root": {"summary": "done"}}
        state.tasks["root"] = TaskNode(
            id="root",
            title="Root",
            objective="Root task",
            status=TaskStatus.COMPLETED,
            candidate_agent_ids=["admin"],
            result_ref="result-root",
        )
        state.subagent_runs["run-root"] = SubagentRun(
            id="run-root",
            task_id="root",
            agent_id="admin",
            status=SubagentStatus.COMPLETED,
            result_ref="result-root",
        )
        state.results["result-root"] = AgentResult(
            id="result-root",
            run_id="run-root",
            task_id="root",
            status=AgentResultStatus.SUCCESS,
            summary="Root complete",
            artifacts=[{"kind": "report", "id": "report-1"}],
        )
        state.resource_locks["semantic-model:model-1"] = ResourceLock(
            key="semantic-model:model-1",
            mode=ResourceMode.WRITE,
            owner_run_ids=["run-root"],
        )
        state.decisions.append(
            OrchestratorAction(
                type=OrchestratorActionType.MERGE_RESULT,
                rationale="Persist test decision.",
                task_id="root",
                target_run_id="run-root",
                payload={"resultId": "result-root"},
            )
        )

        dynamic_mission_store.save_mission_state(state)
        loaded = dynamic_mission_store.load_mission_state("mission-1")

        assert loaded is not None
        assert loaded.model_dump(mode="json", by_alias=True) == state.model_dump(mode="json", by_alias=True)
        assert dynamic_mission_store.delete_mission_state("mission-1") is True
        assert dynamic_mission_store.load_mission_state("mission-1") is None
    finally:
        _db.reset_path_cache()


@pytest.mark.asyncio
async def test_no_improvement_in_failure_count_triggers_bail() -> None:
    """When the verifier reports a different signature but the same
    *count* of failures across rounds, the orchestrator must treat the
    loop as stagnant and bail rather than waste another iteration."""
    controller, _, _ = _controller(ScriptedExecutor())
    controller.agent_templates["fabric-verifier"] = _template(
        "fabric-verifier", _skill("fabric-verification", "browser_verify_visual_render")
    )
    state = _mission_state()
    task = TaskNode(
        id="verify-1",
        title="Verify",
        objective="Verify report.",
        candidate_agent_ids=["fabric-verifier"],
    )
    controller.create_task(state, task, rationale="seed")

    def _result(errs: list[str]) -> AgentResult:
        return AgentResult(
            run_id="run",
            task_id=task.id,
            status=AgentResultStatus.SUCCESS,
            summary="verifier round",
            errors=errs,
            followup_tasks=[
                FollowupTask(title=f"fix {errs[0]}", objective="fix it",
                             candidate_agent_ids=["fabric-data-engineer"])
            ],
        )

    # Round 1: 2 errors of one shape.
    r1, sig1, repeated1, _ = controller._record_verifier_feedback_round(
        state, task, _result(["err-A-detail-1", "err-B-detail-1"]),
    )
    assert r1 == 1
    assert repeated1 is False, "first round can never count as a repeat"

    # Round 2: a *different* pair of errors (different signature) but
    # still 2 of them — count did not decrease, so we bail.
    r2, sig2, repeated2, _ = controller._record_verifier_feedback_round(
        state, task, _result(["err-A-detail-2", "err-C-detail-1"]),
    )
    assert r2 == 2
    assert sig2 != sig1, "test guarantees different signatures"
    assert repeated2 is True, "count-stagnation should mark round 2 as no-progress"
