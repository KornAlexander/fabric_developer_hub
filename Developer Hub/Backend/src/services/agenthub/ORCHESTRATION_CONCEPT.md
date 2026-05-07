# AgentHub Multi-Agent Orchestration — Current State, Target Concept, and Recommendations

> Status: living architecture note · Last updated: 2026-04-25
> Scope: the `services/agenthub/` package — compose, drivers, container runner, agent loop.
> Related: [CAPABILITY_ARCHITECTURE.md](./CAPABILITY_ARCHITECTURE.md) (skills + MCP fleet),
> [agenthub-orchestration-strategy.md](../../../../docs/agenthub-orchestration-strategy.md) (P0/P1/P2 plan).

---

## 1. Current state — how the multi-agent flow actually works today

The orchestration backend is **Microsoft Agent Framework** (`agent_framework`,
required dependency in `pyproject.toml`). `MAFUniversalDriver` is the only
driver and is registered for every architecture id. Each architecture id
maps to a dedicated MAF builder:

| Architecture | Builder | MAF primitive |
|---|---|---|
| `solo`, `sequential` | `build_sequential` | `SequentialBuilder` |
| `parallel` | `build_concurrent` | `ConcurrentBuilder` |
| `reflection` | `build_reflection` | `WorkflowBuilder` (actor↔critic loop) |
| `supervisor`, `hierarchical` | `build_supervisor` | `WorkflowBuilder` (lead↔worker graph; MAF `HandoffBuilder` reserved for future `Agent` subclass migration) |
| `mixed`, `network` | `build_freeform` | `WorkflowBuilder` (declared edges) |
| anything else | `build_freeform` | `WorkflowBuilder` (degraded) |

Slot execution is delegated to a `SlotRunner` (in-process) or
`ContainerSlotRunner` (Docker). The runner is selected by
`AGENT_ISOLATION` (`inprocess` is the default; `container` is the
isolated production path). Both runners expose an identical
`run_slot(slot_id, *, upstream_handoffs, max_turns, step_label)`
signature so the MAF `ContainerAgent` can swap them transparently.

### 1.1 Lifecycle

```
POST /api/sessions (user prompt + attachments)
        │
        ▼
┌───────────────────────────────┐
│ ComposeService                │    single LLM call
│ compose_service.py            │    → picks architecture
│                               │    → picks agents (slots)
│                               │    → attaches skills
│                               │    → wires handoff graph
│                               │    → sets Budget
└───────────────┬───────────────┘
                │ Composition (frozen artifact)
                ▼
┌───────────────────────────────┐
│ OrchestratorEngine.start_job  │    orchestrator_engine.py
│                               │    creates _JobExecution
│                               │    event queue + ring buffer
└───────────────┬───────────────┘
                │
                ▼
┌───────────────────────────────┐
│ DriverRegistry.get(arch)      │    drivers/registry.py
│ sequential | supervisor |     │
│ hierarchical | reflection |   │
│ mixed | solo | ...            │
└───────────────┬───────────────┘
                │ driver.run(composition, …)
                ▼
┌───────────────────────────────┐    one per slot
│ ContainerSlotRunner.run_slot  │    drivers/container_runner.py
│   ├─ ContainerPool.acquire()  │    semaphore, default max 8
│   ├─ DockerBackend.acquire    │    instant warm sandbox checkout
│   │  _warm_container()        │    falls back to cold create/start
│   └─ DockerBackend.wait/exec  │    15-min hard timeout
└───────────────┬───────────────┘
                │ HTTP callbacks (tool schemas + tool calls)
                ▼
┌───────────────────────────────┐
│ Agent container               │    agent/__main__.py
│   reads SLOT_CONFIG env var   │    Dockerfile.agent
│   LLM loop (Copilot API)      │    no tool runtime inside
│   proxies tool calls to       │    no secrets inside
│   orchestrator:5000           │
└───────────────┬───────────────┘
                │ SSE events
                ▼
┌───────────────────────────────┐
│ Frontend Mission Control      │    useMissionStream.ts
│ subscribes via SSE with       │    Last-Event-ID resume
│ Last-Event-ID header          │
└───────────────────────────────┘
```

### 1.2 Key facts

| Fact | Evidence |
|---|---|
| The agent lineup is decided **once, upfront**, in a single LLM call — not spawned dynamically mid-mission. | [compose_service.py](./compose_service.py) produces an immutable `Composition`; `orchestrator_engine.py` docstring: *"no plan artifact, no pre-materialised step list, no prerequisite verification"*. |
| Each slot runs in **its own ephemeral Docker container**. | [drivers/container_runner.py](./drivers/container_runner.py) + [drivers/container_backend.py](./drivers/container_backend.py). Image built from [Dockerfile.agent](../../../Dockerfile.agent). |
| Agent startup is warmed ahead of time. | [drivers/container_backend.py](./drivers/container_backend.py) keeps `AGENT_CONTAINER_WARM_POOL_SIZE` single-use idle sandboxes ready; slots run the existing agent entrypoint through `docker exec`, then the sandbox is removed and replenished. |
| Agents have **no tool runtime inside** the container — they proxy every tool call back to the orchestrator over HTTP. | [agent/__main__.py](./agent/__main__.py) `_proxy_tool_call` → `orchestrator_endpoint`/`api/internal/tools/...`. |
| Topologies are **fixed shapes** chosen by the composer, not emergent. | [drivers/__init__.py](./drivers/__init__.py) registers `solo`, `sequential`, `supervisor`, `hierarchical`, `reflection`, `mixed`. |
| Concurrency is bounded by a semaphore (default 8). | [drivers/container_pool.py](./drivers/container_pool.py). |
| Events stream to the UI with monotonic seq + 500-event ring buffer for SSE resume. | [orchestrator_engine.py](./orchestrator_engine.py) `_JobExecution.emit` / `replay_since`. |
| Backend is designed single-replica; multi-replica scale needs shared rate-limiter + MSAL cache. | [CAPABILITY_ARCHITECTURE.md](./CAPABILITY_ARCHITECTURE.md#horizontal-scale-posture). |

### 1.3 Strengths of the current design

- **Strong isolation.** Tool execution, secrets, and the MCP fleet live
  in the orchestrator, never in the agent container. A compromised
  agent LLM cannot reach credentials or bypass `tool_runtime.execute()`.
- **Single security chokepoint.** Every tool call — regardless of which
  agent — goes through the same policy gate on the orchestrator.
- **Pluggable container backend.** The `ContainerBackend` protocol lets
  us swap Docker for Kubernetes without rewriting drivers.
- **Deterministic topologies.** Drivers are small, explicit, auditable.

### 1.4 Gaps / smells

| Gap | Impact |
|---|---|
| No durable workflow state — a backend restart mid-mission loses in-flight runs. | Missions >15 min or during deploys fail silently. |
| No mid-flight replan / dynamic agent spawning. If the composer gets it wrong, the mission can't recover without a new session. | Brittle on ambiguous prompts. |
| Six custom drivers (~2000 LOC combined) reinvent patterns that open-source frameworks ship built-in. | Maintenance cost; slow to add new topologies (e.g. group-chat, debate-with-judge, magentic). |
| Handoff extraction is regex-based on free-text LLM output. | Fragile; silent data loss when the LLM phrases its handoff differently. |
| Warm pool exhaustion still falls back to a cold Docker start. | Bursts above `AGENT_CONTAINER_WARM_POOL_SIZE` can pay the cold-start tax; tune warm size to expected parallel slot count. |
| Agent loop talks only to GitHub Copilot (`api.githubcopilot.com`). No model abstraction. | Hard to A/B models, hard to plug Azure OpenAI or Foundry endpoints. |
| Event protocol + replay buffer + SSE is hand-rolled. | Works, but re-implements what workflow engines give for free. |

---

## 2. Target concept

Keep the parts that are load-bearing for security and observability
(**container isolation + tool runtime chokepoint + SSE event stream**)
and replace the hand-rolled orchestration glue with a supported
framework. The design goal is:

> *The composer still produces a `Composition`. A framework translates
> that `Composition` into a workflow whose nodes are agents running in
> isolated containers. The framework owns durability, checkpointing,
> retries, and topology primitives. We own isolation, tools, and the UI
> event stream.*

### 2.1 Target architecture

```
Composition (unchanged)
        │
        ▼
┌───────────────────────────────┐
│ WorkflowBuilder               │    NEW — translates Composition
│ drivers/workflow_builder.py   │    into an MAF Workflow object.
│                               │    Replaces sequential.py,
│                               │    supervisor.py, mixed.py, …
└───────────────┬───────────────┘
                │ Workflow
                ▼
┌───────────────────────────────┐
│ Microsoft Agent Framework     │    owns:
│ runtime                       │      • topology execution
│                               │      • checkpointing
│                               │      • handoff primitives
│                               │      • group-chat / magentic
│                               │      • durable resume
└───────────────┬───────────────┘
                │ per-node invocation
                ▼
┌───────────────────────────────┐
│ ContainerAgentAdapter         │    NEW — an MAF AIAgent whose
│ drivers/maf_container_agent.py│    .run() delegates to
│                               │    ContainerSlotRunner.
└───────────────┬───────────────┘
                │
                ▼
┌───────────────────────────────┐
│ ContainerSlotRunner           │    UNCHANGED
│ agent/__main__.py             │    (the isolation layer stays)
└───────────────────────────────┘
```

### 2.2 What changes, what stays

| Layer | Today | Target |
|---|---|---|
| Compose (prompt → `Composition`) | [compose_service.py](./compose_service.py) | **unchanged** |
| Architecture drivers | 6 custom drivers in [drivers/](./drivers/) | **replaced** by a single `WorkflowBuilder` that maps `Composition.architecture` → MAF builder (`SequentialBuilder`, `MagenticBuilder`, `HandoffBuilder`, `ConcurrentBuilder`, `WorkflowBuilder` for `mixed`). |
| Handoff extraction | Regex on free text in [drivers/handoff.py](./drivers/handoff.py) | **replaced** by MAF handoff edges + structured outputs. |
| Budget / step tracking | [drivers/budget.py](./drivers/budget.py) + [drivers/step_tracker.py](./drivers/step_tracker.py) | Port budget into MAF middleware; drop step_tracker in favour of MAF events. |
| Event stream (SSE + ring) | [orchestrator_engine.py](./orchestrator_engine.py) `_JobExecution` | Keep the public SSE contract; adapter subscribes to MAF `WorkflowEvent`s and re-emits in the existing shape. |
| Container isolation | [drivers/container_runner.py](./drivers/container_runner.py) + [agent/__main__.py](./agent/__main__.py) | **unchanged** — wrapped in an MAF `AIAgent` subclass. |
| Tool runtime + MCP | [services/mcp/](../mcp/) + `tool_runtime.py` | **unchanged** — still the single chokepoint. |
| Model client | Hard-coded Copilot API in [agent/__main__.py](./agent/__main__.py) | Pluggable — MAF `ChatClient` abstraction allows Azure OpenAI / Foundry / Copilot per deploy. |
| Durability | None | MAF `CheckpointManager` (in-memory → Redis → Durable Task Scheduler as we scale). |

### 2.3 Why Microsoft Agent Framework

- **Official Microsoft product**, GA-track as of late 2025, the unified
  successor to Semantic Kernel Agents + AutoGen. Matches the stack
  preference.
- **Built-in topology primitives** map 1:1 onto our drivers:
  - `SequentialBuilder` ↔ `sequential` driver
  - `ConcurrentBuilder` ↔ `parallel` / fan-out
  - `HandoffBuilder` ↔ `hierarchical`, `mixed`
  - `MagenticBuilder` ↔ `supervisor` (the current alias target)
  - `WorkflowBuilder` (graph) ↔ any DAG we can't express above
- **First-class MCP support** — reuses our existing MCP fleet without
  rewriting `MCPClientManager`.
- **Checkpointing + durable workflows** — free resume-after-restart.
- **Streaming events** — we can adapt MAF `WorkflowEvent`s into the
  existing SSE shape with no frontend changes.
- **Pluggable model clients** — removes the Copilot-API hard dependency
  in the agent loop, enables Azure OpenAI / Foundry deployments.

### 2.4 What MAF does *not* address (and why we keep custom code there)

- **Per-agent Docker sandboxing.** MAF assumes in-process agents. Our
  security model *requires* isolation. We keep `ContainerSlotRunner`
  and wrap it in an MAF `AIAgent` subclass so MAF sees a normal agent
  while each `.run()` actually spawns a container.
- **Tool policy gate / MCP transport.** These live in the orchestrator
  process and stay there. The containerised agent continues to proxy
  tool calls back.
- **OBO token exchange + Fabric auth.** Handled by `AuthenticationService`
  — MAF is model-agnostic and doesn't care.

---

## 3. Recommendations

### 3.1 ✅ Complete — MAF is the sole orchestration backend

**Status 2026-04-25:** all short-, medium- and long-term items from
the original §3.1–§3.2 plan are implemented. Microsoft Agent Framework
is a **required** dependency (no feature flag, no optional extra,
no fallback) and every architecture in the driver registry is served
by [MAFUniversalDriver](./drivers/maf/universal_driver.py).

| # | Deliverable | Status |
|---|---|---|
| 1 | `MAFWorkflowBuilder` with one build method per topology (`sequential`, `concurrent`, `handoff`, `reflection`, `freeform`) + dispatcher | ✅ [workflow_builder.py](./drivers/maf/workflow_builder.py) |
| 2 | `ContainerAgent` MAF `BaseAgent` wrapping `SlotRunner` — container isolation preserved | ✅ [container_agent.py](./drivers/maf/container_agent.py) |
| 3 | Event stream adapter (MAF `WorkflowEvent` → `_JobExecution.emit`) | ✅ [event_adapter.py](./drivers/maf/event_adapter.py) |
| 4 | `MAFUniversalDriver` registered for every architecture (`solo`, `sequential`, `parallel`, `supervisor`, `hierarchical`, `reflection`, `mixed`, `router`, `network`) | ✅ [drivers/__init__.py](./drivers/__init__.py) |
| 5 | Legacy architecture driver modules deleted from the tree (`solo.py`, `sequential.py`, `supervisor.py`, `parallel.py`, `router.py`, `hierarchical.py`, `reflection.py`, `mixed.py`, `legacy.py`) | ✅ |
| 6 | Structured handoffs — JSON-fenced payload in each assistant message (replaces regex extraction on the MAF path) | ✅ [container_agent.py `_result_to_text`](./drivers/maf/container_agent.py) |
| 7 | Opt-in MAF checkpointing (`AGENTHUB_CHECKPOINTING_ENABLED=1`; file store via `AGENTHUB_CHECKPOINT_DIR`) | ✅ [checkpointing.py](./drivers/maf/checkpointing.py) |
| 8 | Pluggable `ChatClient` in the agent container — env var `AGENT_CHAT_CLIENT=copilot\|azure_openai\|foundry`, Copilot default | ✅ [agent/chat_client.py](./agent/chat_client.py) |
| 9 | `agent-framework` promoted from optional extra to required dependency | ✅ [pyproject.toml](../../../pyproject.toml) |
| 10 | Driver-layer test suite green (118 tests, zero regressions in the full 769-test unit suite) | ✅ |

**Topology → MAF builder map:**

| Architecture | MAF builder |
|---|---|
| `solo`, `sequential` | `SequentialBuilder` |
| `parallel` | `ConcurrentBuilder` |
| `reflection` | freeform `WorkflowBuilder` with actor↔critic cycle, `max_iterations = max(4, budget.max_turns)` |
| `supervisor`, `hierarchical`, `network` | `HandoffBuilder` (lead as start, workers reachable from lead, workers hand back to lead) |
| `mixed`, `router`, any unknown id | freeform `WorkflowBuilder` consuming `composition.handoffs` verbatim |

**Escape hatch (debug-only):** none. The hand-rolled drivers have been
deleted from the tree; MAF is the only code path.

### 3.2 ✅ Complete (merged into §3.1)

Medium-term items 5–8 from the original plan were implemented in the
same pass as §3.1 rather than a second milestone:

- Port of `supervisor`/`hierarchical`/`mixed`/`parallel` to MAF builders — see §3.1 table.
- Structured handoffs replacing regex — see §3.1 row 6.
- Checkpointing — see §3.1 row 7.
- Pluggable `ChatClient` — see §3.1 row 8.

### 3.3 Longer term

9. **Durable Task Scheduler / Azure Container Apps Jobs backend.**
   Once MAF is in place, the `ContainerBackend` protocol can target
   Container Apps Jobs instead of Docker. Gives us per-tenant
   autoscale and cleaner prod operations.
10. **Multi-replica backend.** With MAF checkpointing handling
    durability and a Redis-backed rate limiter + MSAL cache (already
    called out in [CAPABILITY_ARCHITECTURE.md](./CAPABILITY_ARCHITECTURE.md#what-breaks-at-1-backend-replica)),
    the backend becomes horizontally scalable.
11. **Mid-flight replan.** MAF's workflow-level hooks make it feasible
    for an agent to emit a "replan" request that a supervisor handles
    without tearing down the session.

### 3.4 Explicit non-goals

- **No LangGraph / AutoGen / CrewAI.** The stack preference is
  Microsoft-first and MAF is the successor to AutoGen anyway.
- **No removal of container isolation.** The current security posture
  is the most defensible feature of the system.
- **No rewrite of `ComposeService`.** It's doing its job — one LLM
  call, deterministic artifact, easy to test.

---

## 4. Migration risk register

| Risk | Mitigation |
|---|---|
| MAF's per-node execution assumes cheap in-process calls; our container-per-slot can be slower during bursts. | ✅ Warm pool implemented: pre-started single-use Docker sandboxes make normal slot checkout near-instant; `ContainerPool` still caps concurrency. |
| MAF event vocabulary doesn't match our existing SSE event types 1:1. | Adapter layer in [event_adapter.py](./drivers/maf/event_adapter.py) maps MAF events → our types. Frontend stays unchanged. |
| MAF is still evolving; API surface may change. | Pin a version floor (`agent-framework>=0.1`); MAF imports are isolated behind [drivers/maf/workflow_builder.py](./drivers/maf/workflow_builder.py) so a future swap is a single-file change. |
| Regression in topology behaviour vs. the hand-rolled drivers. | The legacy drivers have been deleted; any regression is fixed forward in the MAF builders. MAF event tests + the 769-test unit suite gate every change. |
| Copilot API hard-coded in the agent image blocked other models. | ✅ Resolved — [agent/chat_client.py](./agent/chat_client.py) provides a `ChatClient` abstraction selected via `AGENT_CHAT_CLIENT` env. |
| MAF becoming a required dependency increases the supply-chain surface. | Pinned minimum version; `agent-framework` is a Microsoft-owned package, same trust tier as the rest of the Azure SDKs we depend on. |

---

## 5. Appendix — file inventory touched by this migration

**Stays unchanged:**
- [compose_service.py](./compose_service.py), [compose_models.py](./compose_models.py), [compose/](./compose/)
- [agent_registry.py](./agent_registry.py), [catalog.yaml](./catalog.yaml), [catalog_loader.py](./catalog_loader.py)
- [capability_registry.py](./capability_registry.py)
- [session_store.py](./session_store.py), [rate_limit.py](./rate_limit.py), [tool_runtime.py](./tool_runtime.py), [tool_policies.py](./tool_policies.py)
- [agent/__main__.py](./agent/__main__.py) (container agent loop) — minor: add pluggable ChatClient
- [drivers/container_backend.py](./drivers/container_backend.py), [drivers/container_pool.py](./drivers/container_pool.py), [drivers/container_runner.py](./drivers/container_runner.py), [drivers/container_reaper.py](./drivers/container_reaper.py)

**Deleted:**
- `drivers/solo.py`, `drivers/sequential.py`, `drivers/supervisor.py`, `drivers/hierarchical.py`, `drivers/reflection.py`, `drivers/mixed.py`, `drivers/parallel.py`, `drivers/router.py`, `drivers/legacy.py` — replaced by the MAF universal driver.

**Still present (used by MAF path):**
- [drivers/handoff.py](./drivers/handoff.py) — `HandoffPayload` is reused by `ContainerAgent` as the synthetic upstream wrapper.
- [drivers/step_tracker.py](./drivers/step_tracker.py) — used by `MAFUniversalDriver` for phase logging.

**New:**
- [drivers/maf/workflow_builder.py](./drivers/maf/workflow_builder.py) — `Composition` → MAF `Workflow`
- [drivers/maf/container_agent.py](./drivers/maf/container_agent.py) — `ContainerSlotRunner` as MAF `BaseAgent`
- [drivers/maf/event_adapter.py](./drivers/maf/event_adapter.py) — MAF events → `_JobExecution.emit`
- [drivers/maf/universal_driver.py](./drivers/maf/universal_driver.py) — the one driver that serves every architecture
- [drivers/maf/checkpointing.py](./drivers/maf/checkpointing.py) — env-gated MAF checkpoint storage
- [agent/chat_client.py](./agent/chat_client.py) — pluggable chat client (Copilot / Azure OpenAI / Foundry)

**Thinned:**
- [orchestrator_engine.py](./orchestrator_engine.py) — most per-slot loop logic moves to MAF; `_JobExecution` keeps only the SSE ring buffer + event contract.
