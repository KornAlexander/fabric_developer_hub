# Dynamic Generalist Orchestrator Concept

Status: concept proposal  
Date: 2026-04-26  
Scope: AgentHub orchestration runtime, Mission Control, agent lifecycle, task graph, subagent execution, steering, recovery, and migration from fixed upfront compositions.

## Executive Summary

AgentHub should move toward a dynamic generalist-orchestrator model.

The current system is still organized around a single upfront `Composition`: one LLM call picks the architecture, slots, agents, skills, handoffs, and budget before the run starts. Microsoft Agent Framework then executes that composed topology. This is reviewable, auditable, and relatively easy to reason about, but it is too rigid for real Fabric work where the right workstreams often become clear only after discovery.

The target strategy should be closer to a mission controller: a generalist orchestrator owns the live mission state, decomposes work as it learns, dynamically starts specialized subagents, runs independent tasks in parallel, monitors their logs and progress, steers or cancels them when needed, merges their results, and replans continuously until the mission is complete or blocked.

Recommendation: make the dynamic generalist orchestrator the long-term default, but do it as a staged migration. Do not throw away the current agent catalog, tool runtime, approvals, container isolation, SSE event stream, or Mission Control. Those are the strongest parts of the product. Change the orchestration core from "execute the fixed composition" to "maintain and evolve a live task graph." The existing `Composition` can become an initial hypothesis or optional seed plan, not the authoritative execution graph.

## Current Status

### What Exists Today

The current AgentHub flow is:

1. The user creates a session with a prompt, attachments, workspace context, and optional architecture preference.
2. `ComposeService.compose()` performs one LLM analysis call and returns a `Composition`.
3. The `Composition` contains the selected architecture, slots, agents, skills, handoffs, entrypoint, rationale, and budget.
4. `OrchestratorEngine.start_job()` turns every composition slot into an `AgentAssignment`.
5. `MAFUniversalDriver` dispatches the composition into a Microsoft Agent Framework workflow.
6. Each slot is executed through `SlotRunner` or `ContainerSlotRunner`, depending on `AGENT_ISOLATION`.
7. Mission Control subscribes to the session event stream and renders logs, agent status, approvals, artifacts, and terminal job status.

Relevant implementation anchors:

- [Backend/src/services/agenthub/compose_service.py](../Backend/src/services/agenthub/compose_service.py)
- [Backend/src/domain/models/composition.py](../Backend/src/domain/models/composition.py)
- [Backend/src/services/agenthub/orchestrator_engine.py](../Backend/src/services/agenthub/orchestrator_engine.py)
- [Backend/src/services/agenthub/drivers/maf/workflow_builder.py](../Backend/src/services/agenthub/drivers/maf/workflow_builder.py)
- [Backend/src/services/agenthub/drivers/container_runner.py](../Backend/src/services/agenthub/drivers/container_runner.py)
- [Backend/src/services/agenthub/tool_runtime.py](../Backend/src/services/agenthub/tool_runtime.py)

### Current Runtime Capabilities

The current runtime already has several capabilities that are useful for the dynamic model:

| Capability | Current State | Relevance To Dynamic Orchestration |
|---|---|---|
| Agent catalog and skills | Agents declare skills, tools, boundaries, and prompts. | Reuse as the dynamic orchestrator's specialist registry. |
| Tool governance | All LLM tool calls pass through `tool_runtime.execute()`. | Keep as the security boundary for every subagent. |
| Container path | `ContainerSlotRunner` can run agents in isolated containers. | Reuse for ephemeral dynamic subagents. |
| Bounded concurrency | `ContainerPool` uses a semaphore, default max 8. | Becomes the first concurrency control for parallel subagents. |
| Event stream | `_JobExecution.emit()` provides monotonic event sequence and replay. | Extend with task graph and subagent lifecycle events. |
| Message injection | `inject_message()` can push text into running agents' queues. | Starting point for orchestrator steering. |
| Dynamic add-agent hook | `add_agent_to_job()` can attach a new agent to a running job. | Starting point for dynamic subagent spawning. |
| Failure recovery | `_handle_agent_failures_once()` can spawn recovery agents for some failures. | Starting point for policy-based replan and replacement. |
| Human approvals | Runtime emits `approval_required` and the UI supports inline approval. | Keep for risky orchestrator decisions and write actions. |

### Current Strategic Limitation

The current orchestrator explicitly does not own planning. Its role is to execute what `ComposeService` already produced. This is visible in `OrchestratorEngine`: composition happens before execution, then `start_job()` consumes the frozen composition.

That means the system is mostly fixed at the beginning of the run:

- The initial agents are known upfront.
- The handoff graph is known upfront.
- Parallelism only occurs if the upfront architecture chose a parallel or graph shape.
- New agents can be added, but this is narrow: manual attach or recovery, not a central planning loop.
- Dynamically-added agents are not first-class task-graph nodes with dependencies, result contracts, resource locks, or composed handoffs.
- The orchestrator can inject messages, but there is no structured steering protocol, no log-inspection policy, and no formal stuck-agent handling loop.

This is the right foundation for a reviewable v1, but it is not yet the Copilot-style generalist orchestration model the product should move toward.

## Current Orchestration Strategy

The current strategy can be summarized as fixed-composition orchestration.

```text
User prompt + context
        |
        v
ComposeService: one upfront LLM call
        |
        v
Composition: architecture + slots + handoffs + budget
        |
        v
MAFWorkflowBuilder builds a workflow from the composition
        |
        v
SlotRunner / ContainerSlotRunner executes each slot
        |
        v
Mission Control renders events, logs, approvals, artifacts
```

This strategy has real strengths:

- It produces something reviewable before execution.
- It constrains the run with a budget and known agent list.
- It gives the UI a stable graph to display.
- It makes expected handoffs explicit.
- It is easier to test than a fully dynamic planner.
- It is easier to reason about from a governance perspective.

It also has structural weaknesses:

- The most important planning decision happens before discovery.
- The system may pick the wrong specialists for ambiguous work.
- It can overfit to the user's initial prompt wording.
- It cannot naturally react when an agent discovers new work that needs a different specialist.
- Parallelism is topology-driven, not opportunity-driven.
- Failure recovery is separate from normal planning.
- A fixed graph encourages the UI to present certainty the system may not actually have.

## Proposed Strategy

The proposed strategy is dynamic generalist orchestration with parallel specialized subagents.

The generalist orchestrator becomes a runtime agent, not just a static composer. It owns the mission loop, keeps a live task graph, and decides what to do next based on current state.

```text
User prompt + workspace context
        |
        v
Mission brief: goals, constraints, budgets, approval policy
        |
        v
Generalist orchestrator loop
        |
        +--> create task graph nodes
        +--> spawn specialized subagents
        +--> run independent tasks in parallel
        +--> monitor logs, heartbeats, tool calls, and progress
        +--> steer, cancel, retry, or replace subagents
        +--> merge results into mission state
        +--> replan until done, blocked, cancelled, or budget-exhausted
```

In this model, a subagent receives:

- A specific task objective.
- Relevant context selected by the generalist.
- Tool permissions scoped to that task.
- Constraints, success criteria, and output contract.
- Upstream artifacts or summaries it needs.
- Deadline, budget, and approval policy.

The subagent returns a structured result:

- `status`: success, partial, blocked, failed, cancelled.
- `summary`: what it did and what matters.
- `artifacts`: files, Fabric item references, reports, notebooks, schema changes, or generated outputs.
- `evidence`: important logs, checks, query results, validation outputs.
- `errors`: failures encountered and their causes.
- `caveats`: limitations, assumptions, risks, or incomplete work.
- `followupTasks`: optional suggested tasks the generalist may accept, merge, reject, or delegate.
- `handoffContext`: compact information another specialist needs.

## Comparison

| Dimension | Current Fixed Composition | Dynamic Generalist Orchestrator |
|---|---|---|
| Planning | One upfront compose call. | Continuous observe, decide, dispatch, monitor, merge, replan loop. |
| Agent selection | Known before run starts. | Chosen on demand as work is discovered. |
| Parallelism | Determined by selected architecture. | Determined dynamically from task independence and resource conflicts. |
| User review | Strong upfront review of team and topology. | Review shifts to mission constraints, budgets, and approval gates. |
| Predictability | Higher. The graph is mostly known. | Lower. The graph evolves during execution. |
| Adaptability | Limited; recovery/manual attach only. | Core behavior; replanning is normal. |
| Failure handling | Recovery logic is separate from normal execution. | Failures become inputs to the orchestrator loop. |
| UI model | Static graph with progress updates. | Live task graph that grows, branches, collapses, and resolves. |
| Audit model | Audit the composed graph and slot events. | Audit every orchestrator decision, spawn, steering directive, cancellation, and result merge. |
| Testing | Easier deterministic tests. | Requires scenario/evaluation harness and policy invariants. |
| Risk | Wrong initial team or rigid graph. | Runaway loops, duplicate work, conflicting writes, harder explainability. |

## Benefits Of Moving

### Better Fit For Real Fabric Work

Fabric tasks are often discovery-heavy. The orchestrator may not know upfront whether the mission needs a data engineer, admin, modeler, report builder, capacity investigator, or security specialist. A dynamic generalist can inspect workspace state first, then decide.

### Higher Throughput

The orchestrator can identify independent work and launch it concurrently:

- Inventory workspace items while another agent inspects semantic model metadata.
- Run data quality checks while another agent reviews capacity settings.
- Generate report layout ideas while a modeler prepares measure definitions.
- Ask an admin agent to validate permissions while a data engineer prepares transformations.

### Less Overplanning

The system no longer needs to pretend it knows the whole plan before execution. It can start with a mission brief, run discovery, and specialize only when the evidence supports it.

### Better Recovery

Recovery becomes a normal orchestration action:

- Retry a transient failure.
- Spawn a different specialist.
- Ask the user for missing configuration.
- Stop only the failed branch while other branches continue.
- Replace a stuck subagent before the entire session fails.

### Stronger Product Differentiation

Mission Control becomes more meaningful. Instead of rendering a static plan with status chips, it can show a live, intelligent control plane: task creation, parallel waves, agent spawning, steering, cancellations, approvals, and result synthesis.

## Drawbacks And Risks

### Less Upfront User Certainty

Users currently see the proposed team before execution. A dynamic model cannot honestly show the final agent graph upfront. The UI must shift from "approve this exact team" to "approve this mission policy and initial strategy."

### Harder Governance

Dynamic spawning must not mean unconstrained spawning. Every subagent needs scoped tools, budgets, resource locks, and audit records. Otherwise the system becomes harder to trust than the fixed graph.

### Concurrency Hazards

Parallel agents can conflict:

- Two agents may write the same Fabric item.
- One agent may refresh a semantic model while another changes its source schema.
- One branch may delete or rename an artifact another branch still expects.

The runtime needs resource locks and dependency management before parallel writes are safe.

### Context Explosion

If the generalist forwards all discovered state to every subagent, cost and quality degrade. A dynamic model requires context-pack building: each subagent receives only the relevant facts, artifacts, constraints, and prior results.

### More Complex Testing

Fixed plans can be tested as deterministic topology cases. Dynamic orchestration needs scenario-based evaluation:

- Did it spawn the right specialists?
- Did it avoid duplicate work?
- Did it parallelize safe branches?
- Did it stop unsafe branches?
- Did it preserve approvals?
- Did it converge within budget?

### Risk Of Orchestrator Loops

A generalist that can spawn and replan can also thrash. The system needs hard limits:

- Max active subagents.
- Max total subagents.
- Max task graph depth.
- Max replans.
- Max time without progress.
- Max repeated tool calls.
- Strong terminal-state rules.

## Recommended Target Architecture

### Core Concept

Replace the fixed composition as the runtime authority with a persisted live mission state.

```text
MissionState
    MissionBrief
    TaskGraph
    SubagentRuns
    Blackboard
    ResourceLocks
    Budgets
    ApprovalState
    EventJournal
```

The generalist orchestrator loop reads `MissionState`, chooses one or more `OrchestratorAction`s, applies them, observes results, and repeats.

### New Runtime Objects

#### MissionBrief

The mission brief replaces the current frozen composition as the top-level contract.

Fields:

- `sessionId`
- `goal`
- `workspaceId`
- `constraints`
- `successCriteria`
- `approvalPolicy`
- `budget`
- `preferredStrategy`
- `initialContextRefs`

#### TaskNode

Represents a unit of work in the live graph.

Fields:

- `id`
- `title`
- `objective`
- `status`: queued, running, blocked, completed, failed, cancelled.
- `priority`
- `dependencies`
- `assignedAgentRunId`
- `candidateAgentIds`
- `requiredCapabilities`
- `resourceClaims`
- `contextRefs`
- `resultRef`
- `createdBy`: orchestrator, user, subagent.
- `parentTaskId`

#### SubagentRun

Represents one spawned specialist execution.

Fields:

- `id`
- `taskId`
- `agentId`
- `agentSessionId`
- `status`
- `startedAt`
- `completedAt`
- `heartbeatAt`
- `toolScope`
- `budget`
- `contextPackRef`
- `logCursor`
- `resultRef`
- `cancellationReason`

#### AgentResult

Structured return payload from subagent to generalist.

Fields:

- `status`
- `summary`
- `artifacts`
- `evidence`
- `errors`
- `caveats`
- `followupTasks`
- `handoffContext`

#### OrchestratorAction

Every decision the generalist makes should be represented as an explicit action.

Action types:

- `create_task`
- `spawn_subagent`
- `spawn_parallel_group`
- `steer_subagent`
- `inspect_subagent_logs`
- `cancel_subagent`
- `retry_task`
- `merge_result`
- `request_user_approval`
- `ask_user_clarification`
- `mark_task_blocked`
- `finish_mission`
- `fail_mission`

Each action should include a short rationale so Mission Control and audit logs can explain why it happened.

### Generalist Loop

The orchestrator loop should be explicit and bounded.

```text
while mission is active:
    observe current mission state
    identify completed, blocked, stuck, and ready tasks
    merge new subagent results
    update task graph
    choose next actions
    enforce budgets and resource locks
    dispatch ready subagents in parallel
    steer or cancel unhealthy subagents when needed
    request approvals for risky actions
    persist state and emit events
```

This loop should be model-driven, but not model-freeform. The LLM should choose from a schema of allowed `OrchestratorAction`s, and the runtime should validate every action before applying it.

### Parallel Scheduling

Parallelism should be safe and bounded.

The scheduler can launch tasks concurrently when:

- All dependencies are completed.
- Required context is available.
- The task does not require a locked resource already claimed by another running task.
- The mission and tenant budgets allow another active subagent.
- The task's tool scope is approved.

Required concurrency controls:

- `maxActiveSubagents`
- `maxTotalSubagents`
- `maxParallelWritesPerWorkspace`
- `maxReadOnlySubagents`
- per-tool concurrency limits
- per-workspace resource locks
- per-item write locks

Resource claims should include Fabric-level targets such as:

- workspace
- lakehouse
- warehouse
- semantic model
- report
- notebook
- pipeline
- capacity
- tenant settings

### Steering And Cancellation

The generalist should be able to steer subagents, but steering must be structured.

Examples:

```json
{
  "type": "steer_subagent",
  "targetRunId": "run-123",
  "reason": "A peer agent discovered that the source table name is Sales_Raw_2026, not Sales_Raw.",
  "message": "Update your inspection to use Sales_Raw_2026. Preserve your current assumptions except for the table name."
}
```

```json
{
  "type": "cancel_subagent",
  "targetRunId": "run-456",
  "reason": "The task is no longer needed because another branch found that the workspace has no semantic model."
}
```

Implementation implications:

- In-process agents can reuse `inject_message()` queues.
- Container agents need a durable steering channel, not only startup `SLOT_CONFIG`.
- Subagents must periodically check for directives and cancellation.
- Steering messages should be logged as first-class mission events.
- Cancellation should be per subagent, not only whole-job cancellation.

### Log Inspection And Stuck Detection

The generalist should not blindly wait forever. It should monitor subagent health.

Signals:

- no heartbeat for N seconds
- repeated identical tool calls
- repeated similar log lines
- high turn count with no artifact or task-state change
- repeated policy denial
- repeated transient tool failure
- task deadline exceeded
- subagent explicitly reports blocked

Actions:

- inspect recent logs
- steer with new context
- reduce scope
- spawn a replacement specialist
- ask user for clarification
- cancel the subagent
- mark task blocked and continue independent branches

The existing circuit breaker in `tool_runtime.py` is a good start, but dynamic orchestration needs health at the subagent and task level too.

## What To Keep

Do not replace these parts:

- Agent catalog, skills, and boundaries.
- Tool runtime as the single authorization and policy chokepoint.
- Human approval events and approval UI.
- Mission Control event streaming and replay model.
- Container isolation path.
- Session history and audit trail.
- Existing fixed topologies for simple, reviewable recipes.
- Microsoft Agent Framework as an execution substrate where it helps.

The strategy change is not "delete everything and build a chatbot." The product advantage is still governed Fabric-native orchestration. The change is that the central orchestrator becomes an active runtime planner instead of a static pre-run composer.

## What Must Change

### Backend

1. Add a `DynamicMissionController` or `GeneralistOrchestrator` service.
   - Owns mission loop.
   - Produces validated `OrchestratorAction`s.
   - Updates persisted mission state.
   - Dispatches subagents through a scheduler.

2. Add persisted mission state tables.
   - `missions`
   - `task_nodes`
   - `subagent_runs`
   - `agent_results`
   - `resource_locks`
   - `orchestrator_decisions`

3. Change `Composition` from runtime authority to seed strategy.
   - Keep it for initial decomposition and UI preview.
   - Do not require every future subagent to exist as a composition slot.
   - Add a migration path where `Composition` is converted into initial `TaskNode`s.

4. Replace `add_agent_to_job()` with a first-class `spawn_subagent()` path.
   - Dynamic agents need scoped tools, context packs, task IDs, resource claims, budgets, result contracts, and lifecycle events.
   - Avoid the current dynamic path's default of giving a newly-added agent the template's full tool surface.

5. Add task-scoped context pack building.
   - Build compact context for each subagent from mission brief, blackboard, artifacts, workspace inventory, and parent task results.
   - Store context packs for auditability.

6. Add subagent result schema and parser.
   - A subagent should finish by returning `AgentResult`, not arbitrary prose.
   - Runtime should validate and normalize results before merging.

7. Add per-subagent steering and cancellation.
   - In-process path can use queues.
   - Container path needs a message channel the agent polls or receives over HTTP/SSE/websocket.

8. Add dynamic orchestration events.
   - `orchestrator_decision`
   - `task_created`
   - `task_started`
   - `task_updated`
   - `task_blocked`
   - `subagent_spawned`
   - `subagent_steered`
   - `subagent_cancelled`
   - `subagent_result`
   - `resource_lock_acquired`
   - `resource_lock_released`
   - `mission_replanned`

9. Add restart durability.
   - The dynamic controller must persist every decision and task state transition.
   - Restart should resume active missions or mark running subagents as unknown and reconcile.

### Frontend

1. Change Step 2 from fixed team review to mission strategy review.
   - Show mission goal, constraints, approval policy, budget, initial decomposition, and likely specialists.
   - Be honest that the graph can evolve.

2. Upgrade Mission Control from fixed graph to live task graph.
   - Show tasks, dependencies, parallel waves, subagent runs, dynamic spawning, steering, cancellations, blockers, and merged results.

3. Add controls for user steering.
   - Send a note to the generalist.
   - Target a running subagent.
   - Pause, cancel, retry, or continue a branch.

4. Add explainability views.
   - Why was this subagent spawned?
   - Why did the orchestrator parallelize these tasks?
   - Why was a branch cancelled?
   - Which result caused this replan?

### Agent Runtime

1. Make subagents task-scoped.
   - The prompt should include task objective, context pack, constraints, success criteria, and result schema.

2. Add heartbeat and progress reports.
   - Subagents should emit lightweight progress periodically, not only final logs.

3. Add directive handling.
   - Subagents should check for steering/cancellation instructions between turns and tool calls.

4. Add structured final result.
   - The generalist should not need to regex-parse arbitrary text.

### Security And Governance

1. Scope tools per task.
   - Dynamic subagents must not automatically get the whole template tool surface.

2. Require approvals for risky dynamic decisions.
   - Write actions.
   - Destructive actions.
   - Cross-workspace effects.
   - Large blast-radius changes.
   - Spawning privileged agents.

3. Add resource locks.
   - Especially for Fabric item writes and refresh operations.

4. Audit every orchestrator decision.
   - Store action, rationale, model input summary, model output, validation result, and applied state change.

## Migration Plan

### Phase 0: Design And Evaluation Harness

Goal: prove the new strategy before changing production behavior.

Deliverables:

- This concept reviewed and accepted.
- Define `MissionBrief`, `TaskNode`, `SubagentRun`, `AgentResult`, and `OrchestratorAction` schemas.
- Build deterministic fake-agent harness for dynamic missions.
- Add benchmark scenarios: workspace audit, end-to-end analytics build, semantic model repair, capacity investigation, failed-data-pipeline recovery.
- Define success metrics: task completion, unnecessary subagents, safe parallelism, approvals preserved, total latency, tool calls, cost, and user intervention count.

### Phase 1: Hybrid Mode With Fixed Composition As Seed

Goal: keep current UX working while introducing live task graph internals.

Deliverables:

- Convert existing `Composition.slots` into initial `TaskNode`s.
- Persist task graph and subagent runs.
- Emit task graph events to Mission Control.
- Execute initial graph similarly to today, but through the new scheduler.
- Keep current fixed composition behavior as a fallback flag.

### Phase 2: Dynamic Subagent Spawning

Goal: let the generalist create new tasks and specialists mid-run.

Deliverables:

- Add validated `spawn_subagent` action.
- Replace ad hoc `add_agent_to_job()` usage with task-scoped dynamic spawning.
- Add tool-scope selection per task.
- Add structured subagent result contract.
- Add result merge into blackboard.
- Add Mission Control UI for dynamically-created agents and tasks.

### Phase 3: Parallel Scheduler And Resource Locks

Goal: make parallelism safe and useful.

Deliverables:

- Add dependency-aware ready queue.
- Add bounded concurrency by mission, workspace, tenant, and tool class.
- Add Fabric resource locks.
- Add parallel group events and UI rendering.
- Add tests that verify independent tasks run concurrently and conflicting writes do not.

### Phase 4: Steering, Log Inspection, And Stuck Handling

Goal: make the generalist an active supervisor, not just a dispatcher.

Deliverables:

- Add subagent heartbeat events.
- Add targeted steering directives.
- Add per-subagent cancellation.
- Add stuck detection policies.
- Add log inspection summaries.
- Add user controls for steering and branch management.

### Phase 5: Dynamic Default

Goal: make dynamic orchestration the normal path.

Deliverables:

- Step 2 becomes mission strategy review.
- Fixed architectures remain available for simple deterministic recipes.
- Dynamic controller becomes default for complex and open-ended tasks.
- Retire MAF fixed graph as the only execution path.
- Preserve fixed workflows for tested, narrow use cases where determinism matters.

## Recommended Decision

Move to the dynamic generalist-orchestrator model, but not as an overnight replacement.

The current fixed-composition strategy is useful as a v1 scaffold and still valuable for simple tasks. But the product vision is stronger if AgentHub behaves like a live mission controller: it can discover, delegate, parallelize, recover, steer, and synthesize as the work unfolds.

The key is to make the dynamic model governed, observable, and bounded. A generalist orchestrator that can spawn subagents is powerful only if every action is validated, every subagent is scoped, every risky operation is approved, and every decision is visible in Mission Control.

Recommended end state:

- Dynamic generalist orchestration is the default for complex Fabric missions.
- Fixed compositions remain as seed plans, recipes, or deterministic execution modes.
- Mission Control becomes a live task graph and decision journal.
- Subagents are ephemeral, task-scoped, tool-scoped, monitored, steerable, cancellable, and expected to return structured results.
- Parallelism is opportunistic but bounded by dependencies, budgets, and resource locks.

## Open Questions

1. Should the generalist orchestrator itself be an LLM agent, a rule-based controller with LLM decisions, or a hybrid?
2. Should Step 2 remain a required review step, or should trusted users be able to run dynamic missions immediately with policy guardrails?
3. What is the maximum acceptable number of active subagents per user, workspace, and tenant?
4. Which Fabric resources require exclusive write locks?
5. Should dynamically-spawned privileged agents require user approval before starting, or only before writes?
6. How much of the generalist's reasoning should be visible to users versus summarized as decisions?
7. Should MAF remain the execution substrate for subagents, or should dynamic subagent execution use a custom scheduler directly over `SlotRunner` and `ContainerSlotRunner`?

## Near-Term Recommendation

Start with a hybrid implementation: keep `ComposeService` for an initial mission brief and optional seed tasks, then add a dynamic mission controller that can create and run additional task nodes. This gives us the new strategy without losing the current review, governance, and observability foundations.

The first production milestone should not be "fully autonomous dynamic everything." It should be:

> The orchestrator can start from an initial plan, discover missing work, spawn a bounded specialist subagent with scoped tools, run it in parallel when safe, merge its structured result, and show the whole decision trail in Mission Control.

That milestone is small enough to test, but it changes the product direction decisively.