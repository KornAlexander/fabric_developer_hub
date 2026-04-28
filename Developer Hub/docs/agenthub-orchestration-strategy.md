# AgentHub Orchestration Strategy Review

Status: strategic review · applied 2026-04-25
Date: 2026-04-25
Scope: AgentHub composition, Microsoft Agent Framework orchestration, agent isolation, MCP/tool governance, model clients, and the value proposition versus GitHub Copilot CLI.

## Application Status (2026-04-25)

The P0 fixes that make the runtime match the Step 2 plan have been
applied. P1 / P2 work is sequenced in the *Remaining work* section
below.

### Applied (P0)

- **Topology dispatch is truthful.** `MAFWorkflowBuilder.build` now
  routes `reflection` → `build_reflection` (actor↔critic loop),
  `supervisor`/`hierarchical` → `build_supervisor` (lead↔workers
  graph; `HandoffBuilder` rejects our `BaseAgent` subclasses so a
  graph-edge supervisor was added), and `mixed` / `network` →
  `build_freeform`. Unknown ids degrade to freeform with a logged
  warning. `build_coordinated_sequence` is no longer reachable from
  the dispatcher. New unit tests cover each dispatch path and the
  malformed-composition fallbacks. See
  [workflow_builder.py](../Backend/src/services/agenthub/drivers/maf/workflow_builder.py)
  and [test_maf_sequential.py](../Backend/tests/unit/services/agenthub/drivers/test_maf_sequential.py).
- **Container/in-process runner signatures are unified.**
  `ContainerSlotRunner.run_slot` now accepts the same
  `step_label` kwarg as the in-process `SlotRunner`, so the MAF
  `ContainerAgent` can call either runner without `TypeError`. A
  signature-compatibility regression test was added in
  [test_container_isolation.py](../Backend/tests/unit/services/drivers/test_container_isolation.py).
- **Orchestration concept doc was de-drifted.** The "100% custom
  code" header in `ORCHESTRATION_CONCEPT.md` was replaced with the
  current MAF reality, and the architecture-to-builder table now
  matches the dispatcher.
- **Benchmark suite scaffold landed** under
  [Backend/benchmarks/](../Backend/benchmarks/). Stubbed baselines
  (`single_prompt`, `cli_like`, `agenthub_compose_only`,
  `agenthub_full`), task fixtures, and a `metrics.py` aggregator are
  in place so the harness can be reviewed before any LLM budget is
  spent.

### Remaining (P0)

- **Unify the in-process model client.** The in-process `_run_agent`
  in `orchestrator_engine.py` still calls Copilot directly
  (`COPILOT_API_BASE = "https://api.githubcopilot.com"` at line 47;
  hard-coded `chat/completions` POST around line 1075). The container
  agent already uses the pluggable `ChatClient` abstraction
  (`copilot` / `azure_openai` / `foundry`). A follow-up should move
  the in-process loop onto the same abstraction so production can
  run on Azure OpenAI / Foundry uniformly.
- **Make container isolation the production default.** `AGENT_ISOLATION`
  defaults to `inprocess`. The default should flip for production
  deployments and the container path needs an end-to-end smoke test
  beyond the existing unit-mock coverage.
- **Mark experimental architectures in the composer prompt.** Until
  every architecture has dedicated coverage and benchmarks, Step 2
  should label routes that fall back to freeform as experimental so
  the user knows.
- **Test ordering pollution.** A pre-existing leak causes
  `tests/unit/services/test_orchestrator_engine.py::test_run_agent_completes_folder_then_item_role`
  to fail only when run after `tests/unit/services/agenthub` and
  `tests/unit/services/drivers`. The test passes in isolation. This
  is not introduced by the strategy work but should be cleaned up
  before CI gates on the full suite.

### Remaining (P1)

- Structured `NEEDS_CAPABILITY` signal + graph-aware dynamic agent
  attachment.
- Durable workflow state via MAF checkpointing, surfaced into Mission
  Control session history.
- Typed agent-to-agent handoff payloads (the JSON marker block in
  `container_agent.py` is the starting point).

### Remaining (P2)

- Curated end-to-end Fabric workflows (medallion build, governance
  audit, semantic-model fixer, capacity investigation, migration
  assistant) with quality gates.
- Real benchmark wiring + recorded baselines in `docs/`.

---

## Executive Summary

AgentHub is directionally right. The product should not hard-pivot to GitHub Copilot CLI as its core architecture. The strongest product idea is a Fabric-native orchestration control plane: curated Fabric agents, workspace-aware composition, guarded MCP tools, user-scoped Fabric access, approvals, logs, artifacts, and auditable session history.

The current implementation, however, does not yet fully deliver the orchestration story implied by the UX and documentation. Microsoft Agent Framework is installed and used, but several advertised topologies are currently flattened into sequential execution. Dynamic agent attachment exists, but mostly for recovery/manual attach rather than as a general mid-flight replanning loop. Container isolation exists, but the default runtime path is still in-process.

The recommendation is to keep the strategy, but tighten the implementation and messaging: make topology execution truthful, make isolation explicit, unify model/runtime paths, and add evaluation evidence proving AgentHub performs better than a plain prompt into a CLI.

## Strategic Verdict

Do not switch strategies hard.

Do switch from "precomputed team plus mostly sequential execution" to "real, evaluated, Fabric-native orchestration."

AgentHub should become Fabric Mission Control for governed AI work. A user should be able to delegate because they can see the team, constrain tools, approve risky steps, watch execution, recover from failures, and inspect resulting artifacts.

## Current Context

AgentHub's user-facing flow is:

1. New Session composer gathers the task, attachments, workspace context, and optional branch-out settings.
2. The backend creates a `Composition` from one LLM call.
3. Step 2 presents the chosen architecture, agents, skills, handoffs, and budget for review.
4. Mission Control runs the session, streams logs/events, and tracks actions/artifacts.
5. Sessions history stores completed, failed, and cancelled runs for review.

The main backend components are:

- `ComposeService`: turns prompt/context into a `Composition`.
- `MAFUniversalDriver`: runs a Microsoft Agent Framework workflow for the composition.
- `MAFWorkflowBuilder`: maps architecture IDs to MAF workflow builders.
- `ContainerAgent`: wraps a composition slot as a MAF agent.
- `SlotRunner`: runs an in-process agent loop for a slot.
- `ContainerSlotRunner`: runs a slot in a Docker container when container isolation is enabled.
- `ToolRuntime`: the central policy and security gate for LLM tool calls.
- `ChatClient`: pluggable agent chat client for Copilot, Azure OpenAI, or Foundry in the container path.

## Verified Findings

### 1. MAF Is Really Present

Microsoft Agent Framework is a required backend dependency and the driver package registers `MAFUniversalDriver` for all architecture IDs.

Evidence:

- [`../Backend/pyproject.toml`](../Backend/pyproject.toml)
- [`../Backend/src/services/agenthub/drivers/__init__.py`](../Backend/src/services/agenthub/drivers/__init__.py)
- [`../Backend/src/services/agenthub/drivers/maf/universal_driver.py`](../Backend/src/services/agenthub/drivers/maf/universal_driver.py)

Assessment: good strategic direction. Microsoft guidance for AI apps recommends Microsoft Agent Framework for multi-agent workflows, MCP integration, workflow primitives, checkpointing, human-in-the-loop patterns, and provider flexibility.

### 2. The Starting Team Is Chosen Up Front

The initial team shape is produced by one LLM composition call. That composition contains architecture, slots, selected skills, handoffs, and budget.

Evidence:

- [`../Backend/src/services/agenthub/compose_service.py`](../Backend/src/services/agenthub/compose_service.py)
- [`../Backend/src/domain/models/composition.py`](../Backend/src/domain/models/composition.py)
- [`../Backend/src/services/agenthub/orchestrator_engine.py`](../Backend/src/services/agenthub/orchestrator_engine.py)

Assessment: this is reasonable. Precomputing a reviewable team is a product advantage because the user can inspect and approve the proposed run. The risk is assuming the up-front composition is always correct.

### 3. Dynamic Agent Spawning Exists, But It Is Narrow

The backend can add an agent to a running job via `add_agent_to_job`. Recovery logic can spawn a recovery agent after certain failures, and there is also an API endpoint for adding an agent to a session.

Evidence:

- [`../Backend/src/services/agenthub/orchestrator_engine.py`](../Backend/src/services/agenthub/orchestrator_engine.py)
- [`../Backend/src/api/agenthub_controller.py`](../Backend/src/api/agenthub_controller.py)
- [`../Backend/src/services/agenthub/catalog.yaml`](../Backend/src/services/agenthub/catalog.yaml)

Assessment: this is useful, but it is not yet a general dynamic replanning mechanism. The system should not overclaim that the orchestrator can freely reshape the team mid-mission unless there is a structured missing-capability signal, graph-aware insertion, and downstream handoff integration.

### 4. Container Isolation Exists, But Is Not The Default

The engine chooses runner mode from `AGENT_ISOLATION`. The default is `inprocess`; container isolation is enabled only when `AGENT_ISOLATION=container`.

Evidence:

- [`../Backend/src/services/agenthub/orchestrator_engine.py`](../Backend/src/services/agenthub/orchestrator_engine.py)
- [`../Backend/src/services/agenthub/drivers/container_runner.py`](../Backend/src/services/agenthub/drivers/container_runner.py)
- [`../Backend/src/services/agenthub/agent/__main__.py`](../Backend/src/services/agenthub/agent/__main__.py)

Assessment: the concept is strong, but the product language should distinguish "container-capable" from "containerized by default." If production security depends on sandboxing, container mode should become the supported production path and get dedicated tests.

Potential implementation issue: `ContainerAgent` passes `step_label` to `run_slot`, but `ContainerSlotRunner.run_slot()` does not currently accept that parameter. The container path should be validated before being presented as production-ready.

### 5. Tool Governance Is A Real Differentiator

Every LLM-driven tool call goes through `tool_runtime.execute()`. The runtime scrubs caller identity, pins the workspace from verified context, checks registered policy, applies kill switches, uses a circuit breaker, and wraps tool output as untrusted.

Evidence:

- [`../Backend/src/services/agenthub/tool_runtime.py`](../Backend/src/services/agenthub/tool_runtime.py)
- [`../Backend/src/services/agenthub/tool_policies.py`](../Backend/src/services/agenthub/tool_policies.py)

Assessment: this is one of the strongest reasons AgentHub should exist. A generic CLI does not provide this level of Fabric-specific policy, OBO/user context, auditability, and tool governance.

### 6. Architecture Semantics Are Currently Too Weak

The architecture catalog describes meaningful patterns: supervisor, sequential, hierarchical, reflection, mixed, and network. But the MAF workflow builder currently sends several of these architecture IDs into a coordinated sequential workflow.

Evidence:

- [`../Backend/src/domain/catalogs/architectures.py`](../Backend/src/domain/catalogs/architectures.py)
- [`../Backend/src/services/agenthub/drivers/maf/workflow_builder.py`](../Backend/src/services/agenthub/drivers/maf/workflow_builder.py)

Assessment: this is the largest product risk. If Step 2 says "Reflection," users should get an actor/critic loop. If it says "Supervisor," users should get real lead/worker handoff behavior. Otherwise the system looks more sophisticated than it is.

### 7. Model Pluggability Is Partial

The container agent path has a `ChatClient` abstraction selected with `AGENT_CHAT_CLIENT=copilot|azure_openai|foundry`. The in-process engine still calls the Copilot API directly.

Evidence:

- [`../Backend/src/services/agenthub/agent/chat_client.py`](../Backend/src/services/agenthub/agent/chat_client.py)
- [`../Backend/src/services/agenthub/agent/__main__.py`](../Backend/src/services/agenthub/agent/__main__.py)
- [`../Backend/src/services/agenthub/orchestrator_engine.py`](../Backend/src/services/agenthub/orchestrator_engine.py)

Assessment: Copilot is fine as a default/dev path, but production should be able to run on Azure OpenAI or Foundry consistently. Both in-process and containerized paths should use the same client abstraction.

### 8. The Existing Concept Doc Has Drifted

The current orchestration concept doc contains older text saying the flow is fully custom and later text saying MAF is the sole backend. This creates decision friction.

Evidence:

- [`../Backend/src/services/agenthub/ORCHESTRATION_CONCEPT.md`](../Backend/src/services/agenthub/ORCHESTRATION_CONCEPT.md)

Assessment: update the concept doc after implementation corrections, or replace it with a shorter canonical architecture note.

## Why Not Just Use GitHub Copilot CLI?

GitHub Copilot CLI is useful for individual developer tasks, especially code-oriented terminal workflows. It is not a Fabric workload control plane.

AgentHub can provide product value that a CLI does not:

- Fabric workspace context before execution.
- Curated agents and skills for Fabric-specific domains.
- MCP tools governed by verified user/workspace context.
- Central policy for writes/destructive operations.
- Approvals and human-in-the-loop UX.
- Mission Control logs, events, artifacts, and recovery state.
- Session history and audit trail.
- Team review before running.
- Domain-specific workflows that can be evaluated and improved over time.

Recommended positioning: Copilot CLI can be a useful sub-tool or fallback for code tasks, but it should not become the AgentHub core. The core should be Fabric-native delegation with governance and observability.

## Recommendations

### P0: Make The Current Strategy Truthful

1. Fix topology fidelity.
   - Route `reflection` to `build_reflection()`.
   - Route `supervisor`, `hierarchical`, and possibly `network` to a real handoff workflow.
   - Route `mixed` and `router` to graph/freeform execution, or hide them until they are real.
   - If an architecture is not production-ready, mark it experimental in the UI and composer prompt.

2. Decide and document the isolation mode.
   - If production security depends on containers, make `AGENT_ISOLATION=container` the production default.
   - Add tests that run or at least contract-test the container path.
   - Fix the `step_label` signature mismatch before relying on container mode.

3. Unify the model client.
   - Move in-process `_run_agent` onto the same chat-client abstraction used by the container agent.
   - Keep Copilot as a local/default option, but make Azure OpenAI/Foundry first-class production options.

4. Clean up orchestration docs and prompts.
   - Remove stale "100% custom code" language.
   - Avoid promising general mid-flight team spawning until it is implemented as a real planning/replanning loop.
   - Align Step 2 labels with actual runtime behavior.

5. Add an orchestration benchmark suite.
   - Compare AgentHub against a single-agent prompt and, where relevant, a Copilot CLI-style baseline.
   - Track task success, artifact correctness, validation pass/fail, tool denials, recovery behavior, elapsed time, and user interventions.

### P1: Build Real Adaptive Orchestration

1. Add a structured missing-capability signal.
   - Agents should be able to emit something like `NEEDS_CAPABILITY` with reason, target skill, and required handoff payload.
   - The orchestrator decides whether to attach a specialist, ask the user, or stop.

2. Make dynamic agents graph-aware.
   - A newly attached agent should have a clear role in the workflow, input context, downstream consumer, and completion criteria.
   - Recovery agents should feed results back into the run, not merely run beside it.

3. Add durable workflow state.
   - Use MAF checkpointing or equivalent storage for resume-after-restart.
   - Tie checkpoint state to session history and Mission Control events.

4. Make handoffs structured first.
   - Keep prose summaries for humans, but make agent-to-agent payloads typed JSON with clear fields for artifacts, decisions, blockers, and validation results.

### P2: Turn AgentHub Into Fabric Workflow Product

1. Focus on a small number of excellent Fabric workflows.
   - Medallion lakehouse build.
   - Workspace/governance audit.
   - Report and semantic model fixer.
   - Capacity/performance investigation.
   - Migration assistant.

2. Build quality gates into workflows.
   - Actor/critic/tester for artifact-producing tasks.
   - Execution checks for notebooks, SQL, KQL, DAX, and PBIR changes where possible.
   - Explicit final evidence: what was created, modified, validated, blocked, or skipped.

3. Make run outcomes measurable.
   - Define success criteria per workflow.
   - Persist structured evaluation data.
   - Use failures to improve composer rules, tool policies, and agent instructions.

## Product Positioning

The winning claim is not "we have many agents." The winning claim is:

> AgentHub lets Fabric users safely delegate complex workspace work to governed specialist agents, while keeping control, visibility, approvals, and evidence.

This is meaningfully different from a generic AI CLI or chat interface. The moat is not the LLM call. The moat is Fabric context, safe tool execution, orchestration UX, recoverability, and auditability.

## Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Topologies remain mostly sequential | Users lose trust when the runtime does not match the plan | Fix dispatch or hide unsupported patterns |
| Container mode is assumed but not default/tested | Security story may be overstated | Make production mode explicit and test it |
| Dynamic spawning is overclaimed | Product feels smarter than implementation | Implement structured replanning or reduce claims |
| Copilot-only paths remain in production runtime | Harder to deploy/govern in Azure environments | Use unified chat-client abstraction everywhere |
| No benchmark baseline | Cannot prove value over a single prompt/CLI | Add task-level eval suite |
| Docs drift | Engineering decisions become inconsistent | Maintain one canonical orchestration concept doc |

## Suggested Next Implementation Sequence

1. Add focused backend tests for `MAFWorkflowBuilder` dispatch behavior.
2. Change dispatch so architecture IDs map to their intended MAF builders.
3. Fix and test `ContainerSlotRunner` compatibility with `ContainerAgent`.
4. Unify in-process and container chat clients.
5. Update orchestration docs and composer prompt claims.
6. Add a deterministic eval suite with baseline comparison.

## Bottom Line

The approach is fundamentally sound, but it needs stricter honesty and stronger execution. Keep Microsoft Agent Framework, keep MCP/tool governance, keep the Fabric-native UI, and keep Copilot/Azure/Foundry model flexibility. Do not reduce AgentHub to Copilot CLI. Instead, make the orchestration layer real enough that the UI promise, runtime behavior, and evaluation results all agree.