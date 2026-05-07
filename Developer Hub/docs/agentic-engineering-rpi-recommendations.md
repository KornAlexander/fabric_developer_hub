# Agentic Engineering RPI Recommendations

Status: recommendations after reviewing the current AgentHub runtime, the video `No Vibes Allowed: Solving Hard Problems in Complex Codebases - Dex Horthy, HumanLayer`, and the newer `Everything We Got Wrong About Research-Plan-Implement - Dexter Horthy` / QRSPI material.

The useful takeaway is not that we need one magic prompt or one more package. The core pattern is disciplined context engineering for hard, brownfield work: ask neutral questions, research the system without prematurely forming implementation opinions, compress that research into a reviewable artifact, align on design and structure before a tactical plan exists, implement from a small context window, review the code that ships, and compact frequently before the agent drifts into a noisy context window. The newer QRSPI framing matters because it explains where plain RPI breaks at scale: monolithic prompts silently skip steps, hidden "magic words" become product defects, and readable plans can create false confidence when they are not grounded in validated codebase facts.

## Executive Recommendation

The current settings in [docker-compose.yaml](../docker-compose.yaml) are necessary runtime plumbing, but they are not enough to claim best-practice agentic engineering:

```yaml
AGENT_ISOLATION: container
AGENTHUB_MCP_RUNTIME: container
AGENTHUB_ORCHESTRATION_RUNTIME: pi-subagents
AGENTHUB_PI_OBSERVABILITY: pi-subagents
```

They give us useful isolation, per-mission MCP runtime behavior, and Pi-shaped observability events. They do not yet enforce RPI, frequent context compaction, research artifacts, plan review, human alignment, phase gates, or context-pack budgets.

We should build a first-party Pi extension package for AgentHub's agentic engineering protocol, but the backend must remain the security and state authority. The extension should own the product-level RPI/context protocol and event semantics. The Python backend should enforce tool policy, workspace scoping, container isolation, persistence, approvals, and verifier gates.

## Video Principles To Apply

1. RPI is a context-management protocol, not ceremony.
   Research compresses truth from the codebase. Planning compresses intent. Implementation should run from the smallest useful context, not from a giant chat history.

2. Subagents are primarily for controlling context.
   The video explicitly warns against treating subagents as anthropomorphic role labels. Use subagents to fork context windows, run targeted searches, and return compressed findings. Use role-specific agents only when tool permissions, expertise, or verification independence truly differ.

3. Frequent intentional compaction beats reactive restarts.
   We should not wait until a mission is off track. Research snapshots, plan files, context pack summaries, and verifier receipts are all compaction points.

4. Human review is part of the system.
   Plans are not just model instructions. They are a mental-alignment artifact for the human or peer reviewer. A bad plan can create hundreds of bad lines of code, so plan review should be a first-class gate for high-risk missions.

5. No-vibes execution means evidence.
   The agent should show what it learned, what files or APIs it touched, what it plans to change, how it tested, and why the verifier accepted or rejected the result.

## QRSPI Refinements From The Newer Talk

The newer talk does not invalidate the current AgentHub direction. It sharpens it. Our recent `ContextPackV2`, Pi extension, context-mode facade, and subagent-as-context-work changes are aligned with the direction, but the workflow should become more explicit than `research -> plan -> implement`.

1. Add `question` before `research`.
   The first artifact should be neutral research questions, not a broad feature ticket. This makes the agent enumerate what it needs to learn before it starts collecting facts.

2. Keep research factual and partially blind.
   Research workers should answer neutral questions as documentarians. For complex changes, they should not receive the full desired implementation outcome, because that invites opinions, premature solutioning, and selective evidence gathering. The output should be source refs, current behavior, uncertainty, and gaps, not recommendations.

3. Split `plan` into `design`, `structure`, and `plan`.
   The design artifact is where the agent dumps its understanding of current state, desired state, applicable patterns, resolved decisions, and open questions. The structure artifact is the phased outline: vertical slices, checkpoints, new contracts, and validation order. The tactical plan comes after those two artifacts have been reviewed.

4. Budget instructions, not only tokens.
   The talk's failure mode was not just context size; it was instruction-budget overflow. Long prompts can look obeyed while critical interactive steps are silently skipped. AgentHub should track per-phase instruction counts and keep each phase small enough that its control flow is reliable.

5. Remove magic-word dependencies.
   If the correct path requires the user to say "work back and forth with me before writing the plan," the workflow is broken. Interaction points must be structural gates in the backend and Pi protocol, not hidden phrases in a prompt.

6. Treat plan review as alignment, not shipping approval.
   A readable plan is not proof that the code will work. Plans should reduce code-review surprises, but humans and verifier agents still need to review the code, tests, diffs, and evidence receipts that actually ship.

7. Prefer vertical slices over horizontal layers.
   Structure work as testable end-to-end slices with checkpoints. Avoid plans that complete all database work, then all API work, then all UI work, because integration risk piles up at the point where context is most degraded.

8. Make backtracking first-class.
   QRSPI is not a waterfall. Bad research should send the mission back to questions. Missing design facts should re-run research. A structure flaw should revisit design. A fundamental implementation miss should re-open plan or design instead of patching blindly.

9. Keep fresh-context thresholds visible.
   Use a soft target around 40% context utilization and force compaction or a fresh phase window around 60% for complex work. Persist artifacts to disk/storage, then load only the phase inputs needed next.

10. Use implementation isolation for risky work.
    Container isolation is already strong, but complex code changes should also support worktree-style isolation, one checkpoint per implementation slice, and revertable evidence per phase.

## Current Codebase Assessment

### What Is Already Strong

- [orchestrator_engine.py](../Backend/src/services/agenthub/orchestrator_engine.py) defaults to a dynamic mission controller instead of the old fixed-composition runtime. That is directionally right for long-running, uncertain work.
- [dynamic_orchestrator.py](../Backend/src/services/agenthub/dynamic_orchestrator.py) already has task-scoped subagent runs, resource locks, follow-up tasks, verifier repair loops, tool-loop supervision, and context-pack events.
- [container_runner.py](../Backend/src/services/agenthub/drivers/container_runner.py) keeps agents inside isolated containers and proxies tool calls back to the backend.
- [mission_runtime_manager.py](../Backend/src/services/mcp/mission_runtime_manager.py) creates per-mission MCP runtime containers with CPU, memory, PID, read-only filesystem, and tmpfs controls.
- [pi_backend_harness.py](../Backend/src/services/agenthub/pi_backend_harness.py) exposes the AgentHub tool policy surface as a Pi harness manifest instead of pretending there are no backend tools.
- [piExtensionPackages.ts](../Frontend/src/components/AgentHub/mission/pi/piExtensionPackages.ts) declares real Pi packages and local extensions, including `context-mode`, `@a5c-ai/babysitter-pi`, `pi-subagents`, and the log compactor.
- The local log compactor in [.pi/extensions/fabric-clawhub-log-compactor.ts](../.pi/extensions/fabric-clawhub-log-compactor.ts) is a good precedent: it turns a product policy into a first-party Pi extension and makes the UI prove the policy through typed attributes and E2E tests.

### What Is Still Superficial

- `AGENTHUB_ORCHESTRATION_RUNTIME=pi-subagents` does not currently make `pi-subagents` the server-side execution engine. In [orchestrator_engine.py](../Backend/src/services/agenthub/orchestrator_engine.py), any value other than `fixed`, `maf`, `legacy`, or `composition` keeps the dynamic AgentHub runtime. The `pi-subagents` value mostly enables Pi-shaped observability through `_pi_subagents_observability_enabled()`.
- [container_runner.py](../Backend/src/services/agenthub/drivers/container_runner.py) also uses the `pi-subagents` value to emit `pi.subagents.*` terminal/status/result events. That is useful, but it is event projection, not a Pi-owned delegation protocol.
- [dynamic_orchestrator.py](../Backend/src/services/agenthub/dynamic_orchestrator.py) builds context packs with mission, task, upstream results, blackboard refs, and specialist catalog. It does not yet track token budget, retrieval provenance, freshness, secret redaction proof, source citations, compaction state, or phase-specific context policies.
- `_build_dynamic_agent_goal()` in [orchestrator_engine.py](../Backend/src/services/agenthub/orchestrator_engine.py) injects upstream JSON and the specialist catalog directly into the prompt. That is workable but not the RPI style from the video. It is still prompt assembly, not a disciplined context lifecycle.
- The current subagent selector still starts from `candidate_agent_ids` and template matching. That can be useful for tool scope and domain knowledge, but the primary scheduling question should be: what context window does this task deserve, what should be omitted, and what compressed result must come back?
- The current Pi package registry and backend harness prove package presence and tool-surface awareness. They do not prove that research snapshots, plan artifacts, compaction boundaries, or human plan review are governing real mission execution.

## Recommended Architecture

### 1. Add A First-Party Agentic Engineering Pi Extension

Create a local first-party extension package, initially under `.pi/extensions`, with a name like `@fabric-clawhub/pi-agentic-engineering`. Keep it separate from the existing log compactor.

Responsibilities:

- Declare the RPI protocol used by AgentHub missions.
- Publish phase and context policies to the runtime and frontend.
- Register extension tools or commands for research snapshots, plan review, context compaction, context-pack stats, and verifier receipts.
- Emit typed Pi events that Mission Control can render without scraping generic logs.
- Bridge Pi extension UI prompts into AgentHub approval and clarification flows.

Suggested event contract:

```text
pi.qrspi.question.created
pi.rpi.research.started
pi.rpi.research.completed
pi.qrspi.design.review_requested
pi.qrspi.design.approved
pi.qrspi.structure.created
pi.qrspi.structure.approved
pi.rpi.plan.created
pi.rpi.plan.review_requested
pi.rpi.plan.approved
pi.qrspi.worktree.created
pi.rpi.implementation.started
pi.rpi.implementation.completed
pi.context.pack.created
pi.context.compaction.started
pi.context.compaction.completed
pi.verifier.receipt.created
pi.qrspi.phase.backtrack_requested
pi.phase.gate.blocked
pi.phase.gate.approved
```

This extension should not execute Fabric writes directly. Any write-capable tool must still cross Python `tool_runtime.execute(...)` and the existing AgentHub approval/audit policy.

### 2. Introduce `ContextPackV2`

Replace the current ad hoc context-pack dictionary with a versioned schema. The dynamic controller can still construct it, but the schema should be explicit and testable.

Minimum fields:

- `schemaVersion`
- `phase`: `research`, `plan`, `implement`, `review`, `verify`, or `repair`
- `mission`: tenant, session, workspace, goal digest, constraints, success criteria
- `task`: objective, allowed touch targets, do-not-touch targets, dependencies, resource claims
- `sourceRefs`: files, APIs, docs, Fabric items, and timestamps used to build the context
- `retrievalProvenance`: search queries, context-mode keys, or upstream task ids
- `budget`: estimated tokens, max tokens, reserved output tokens, compaction threshold
- `instructionBudget`: phase instruction count, inherited instruction count, and max allowed directives
- `phaseInputs`: artifact ids loaded for this phase, with an explicit note when the original task is intentionally hidden from research
- `toolPolicy`: allowed tools, denied tools, sensitivity counts, auto-allowed count
- `compaction`: prior summary digest, omitted detail count, freshness, reason
- `evidenceRequirements`: tests, screenshots, verifier checks, manual review needed
- `backtrackPolicy`: valid previous phases and the evidence needed to reopen each phase
- `redactionProof`: whether secrets/env/token-looking values were stripped

The context pack should be persisted with a digest and surfaced in public events as a compact summary. The full pack belongs in backend storage and audit paths, not in noisy UI rows.

### 3. Turn QRSPI Into A Backend State Machine

RPI should not be only prompt text, and the newer QRSPI correction should not be only a longer prompt. Add explicit phases to the dynamic mission controller:

1. `question`
   Convert the user goal into neutral research questions. Store the original ticket separately from the research prompt so research workers do not bias their findings toward a preferred implementation.

2. `research`
   Spawn context-isolated research subagents to inspect vertical slices of the codebase, Fabric workspace, docs, or prior session state. They must answer the neutral questions with compressed facts, source refs, and uncertainty notes. They must not mutate state or propose changes unless the phase explicitly asks for options.

3. `design`
   Build a short design discussion artifact from the user goal and factual research. It should capture current state, desired state, applicable local patterns, open questions, options, rejected approaches, and decisions that need human ownership.

4. `structure`
   Turn the design into an execution outline: vertical slices, contracts/types/interfaces, checkpoints, verification order, rollback points, and per-slice context budgets.

5. `plan`
   Build the tactical implementation document from the approved design and structure. The plan should include target files/items, ordered steps, test strategy, approval needs, risk, and rollback notes.

6. `plan_review`
   For high-risk missions, require human approval or a reviewer/verifier agent before implementation. This is the mental-alignment gate from the videos.

7. `worktree_or_workspace_checkpoint`
   For complex code changes, isolate implementation work in a revertable branch/worktree/checkpoint before mutation begins. Container isolation controls runtime safety; worktree-style isolation controls code-review and rollback ergonomics.

8. `implement`
   Give implementation agents the approved plan plus only the needed context pack for one vertical slice. Avoid giving them every prior tool result and every possible specialist catalog entry.

9. `verify`
   Run a fresh-context verifier. It should receive the design, structure, plan, diff/artifact receipts, and acceptance criteria, not the implementer's full chat history.

10. `repair_or_finish`
   If verification fails, compact the verifier feedback into a repair plan. Stop if the same failure signature repeats or if budget is exhausted.

This is compatible with the existing `DynamicMissionController`; the change is to make phase transitions, context compaction, and review gates first-class state instead of inferred behavior.

Backward transitions are part of the design. Research can send the mission back to `question`; design can request more research; structure can reopen design; implementation can reopen plan or design when it finds a fundamental mismatch. These transitions should be visible in Mission Control instead of being hidden inside a long chat.

### 4. Use Subagents Differently

Change the mental model from `planner agent`, `QA agent`, `data scientist agent` as permanent personalities to context-specific workers:

- Research subagents: short-lived, read-only, return facts and source refs.
- Implementation subagents: bounded to one plan segment and one tool scope.
- Reviewer subagents: fresh context, focused on plan quality or diff risk.
- Verifier subagents: fresh context, evidence-driven acceptance checks.

Domain specialist templates still matter for tool access and expertise, but subagent spawning should be justified by context isolation, not just by naming a role. A subagent request should therefore carry context intent before persona:

- `contextGoal`: what it must learn or change
- `contextBudget`: maximum input tokens, output tokens, and compaction threshold
- `sourceBudget`: max files, docs, Fabric items, or prior findings it may inspect
- `omissionPolicy`: what it must not receive from the parent transcript
- `returnContract`: facts, source refs, uncertainty, evidence, and follow-up questions
- `handoffDigest`: compact result digest saved for the next phase

This is the correction to the current role-label risk: `agent_id` becomes the execution template, while the context pack becomes the real unit of work.

### 5. Make Runtime Naming Honest

Until `pi-subagents` truly owns delegation, avoid presenting `AGENTHUB_ORCHESTRATION_RUNTIME=pi-subagents` as if it means production execution is run by the Pi package.

Two honest options:

1. Rename the runtime value to `agenthub-dynamic` and keep `AGENTHUB_PI_OBSERVABILITY=pi-subagents` for the current event projection.
2. Implement a real server-side Pi subagents adapter that calls into `pi-subagents` or a Pi runtime service while preserving AgentHub's tenant, session, tool, approval, and container controls.

The first option is safer immediately. The second is the longer-term Pi-native path.

### 6. Treat `context-mode` As A Governed Implementation Detail

Keep `context-mode` active, but do not make it the source of truth. Use it behind AgentHub controls for retrieval, FTS, sandboxed analysis, compaction recovery, and context savings telemetry. AgentHub should own retention, purge, tenant isolation, redaction, audit, and context-pack schemas.

The Pi package page for `context-mode` confirms that it is more than an MCP helper. It is published as a Pi extension and skill package, and the installed package exposes Pi extension hooks for `session_start`, `tool_call`, `tool_result`, `before_agent_start`, `session_before_compact`, `session_compact`, and `session_shutdown`. Those hooks are exactly the place to tune context windows: capture tool events, block context-flooding calls, build a compact resume snapshot before compaction, and rehydrate only high-value memory when a new agent turn starts.

Current package state:

- The project is pinned to `context-mode@1.0.103` in [.pi/settings.json](../.pi/settings.json), [.pi/mcp.json](../.pi/mcp.json), [package.json](../Frontend/package.json), [piExtensionPackages.ts](../Frontend/src/components/AgentHub/mission/pi/piExtensionPackages.ts), and [pi_backend_harness.py](../Backend/src/services/agenthub/pi_backend_harness.py).
- The Pi package page currently advertises `context-mode@1.0.111`, published May 4, 2026. Do not auto-upgrade this inside the product path until the new package diff, Elastic-2.0 constraints, native SQLite behavior, and hook behavior are reviewed.
- The current installed `1.0.103` package already has the needed core shape: a Pi extension, a `context-mode` MCP server, sandbox tools, FTS5 indexing, resume snapshots, `ctx-stats`, and `ctx-doctor`.

Immediate uses:

- Build on-demand research snapshots with `ctx_execute`, `ctx_batch_execute`, `ctx_index`, and `ctx_search` so raw grep output, Playwright snapshots, logs, and large docs do not flood the agent context.
- Store compacted context summaries during `session_before_compact` and expose the digest through `ContextPackV2.compaction`.
- Measure context savings per mission with `ctx_stats` and publish `pi.context.savings.recorded` or `pi.context.mode.stats` events to Mission Control.
- Retrieve targeted prior findings instead of replaying entire logs or the full parent transcript.
- Use `tool_call` routing to block large raw shell/web/file reads when a sandboxed context-mode call would preserve the same information with less context.
- Use `before_agent_start` as a safe injection point for the small active-memory guide that implementation, review, or verification agents actually need.

Recommended AgentHub integration:

1. Add a backend `ContextModeContextService` facade rather than letting agents call the package directly.
2. Give each mission and tenant its own context-mode storage root or container volume.
3. On research completion, index the research snapshot and save only its digest, source refs, token estimate, and retrieval queries into `ContextPackV2`.
4. On plan approval, retrieve only findings relevant to the approved plan segment; do not replay all research output into implementation.
5. On implementation spawn, create a context pack that includes the approved plan, current task, source refs, and selected context-mode snippets.
6. On verification, use a fresh context window with plan, diff/evidence receipts, and verifier criteria; retrieve prior implementation notes only when the verifier asks for them.
7. Emit context telemetry: `pi.context.mode.indexed`, `pi.context.mode.retrieved`, `pi.context.mode.compacted`, `pi.context.mode.rehydrated`, and `pi.context.mode.savings`.

Context-window policy:

- Research phase may use a larger private context window, but it must return a small compressed artifact.
- Plan phase should receive research summaries and source refs, not raw tool output.
- Implementation phase should receive the smallest viable pack for one approved plan segment.
- Review and verification phases should start with fresh context and explicit evidence, not implementation chat history.
- Repair phase should receive the failed assertion, verifier receipt, changed files/items, and a reduced repair plan.

Required controls:

- Per-tenant and per-session storage isolation.
- No access to secret-bearing files or env values.
- No shared FTS indexes across workspaces.
- License review for hosted deployments.
- Tests proving context artifacts can be purged.

### 7. Use Babysitter For Process Gates, Not Authority

`@a5c-ai/babysitter-pi` aligns with the video's process discipline: quality gates, breakpoints, journals, resume, doctor, and process-as-code workflows. Use those concepts through the first-party AgentHub protocol, but do not let Babysitter become a second orchestrator that can bypass AgentHub policy.

Good fit:

- Plan approval breakpoints.
- Implementation gate definitions.
- Verifier receipt requirements.
- Resume and doctor diagnostics.

Do not allow:

- Package-installed tools without AgentHub review.
- Unreviewed harness dispatch.
- Autonomy modes that bypass approvals.
- Cross-session `.a5c` storage.

## Implementation Plan

### Phase 0: Be Honest And Observable

- Add `@fabric-clawhub/pi-agentic-engineering` to `.pi/settings.json` and the frontend/backend Pi package registries.
- Emit `pi.rpi.*` and `pi.context.*` events from existing dynamic mission milestones without changing execution semantics yet.
- Add `ContextPackV2` as a backend model and adapt `_build_context_pack()` to produce both legacy and V2 summaries.
- Add context-mode telemetry fields to `ContextPackV2`: indexed source refs, retrieval queries, saved-token estimate, compaction digest, rehydration source, and purge handle.
- Add a governed context-mode facade so backend code can request indexing/search/stats without handing agents unrestricted direct package access.
- Rename or document the current runtime distinction: AgentHub dynamic execution with Pi subagents observability.
- Add unit tests proving `AGENTHUB_ORCHESTRATION_RUNTIME=pi-subagents` does not silently bypass AgentHub controls.

### Phase 1: Real QRSPI Gates

- Add question and blind/factual research phases for complex missions.
- Persist research snapshots and plan artifacts.
- Persist design and structure artifacts separately from tactical plans.
- Require design interaction by control flow, not by prompt phrasing.
- Track instruction budget per phase and fail closed when a phase prompt exceeds the configured directive count.
- Require plan review for write-heavy, destructive, or cross-item Fabric missions.
- Require structure review for complex missions where vertical slices, rollback, or checkpoint order are non-obvious.
- Route large research, docs, logs, and Playwright evidence through context-mode indexing/search and prove the implementation phase receives only selected snippets.
- Update Mission Control to show questions, research, design, structure, plan, approval, implementation, backtracking, and verifier receipts as first-class rows.
- Add context budget and compaction telemetry to the Pi live execution surface.
- Add backward transitions: research can request better questions, design can request more research, structure can reopen design, and implementation can reopen plan/design when evidence proves the current path is flawed.

### Phase 2: Pi-Native Adapter Spike

- Build a server-side Pi runtime service or adapter for the RPI extension.
- Let Pi own session turn semantics and extension hooks while AgentHub owns tools, credentials, containers, and audit.
- Evaluate whether `pi-subagents` can own delegation safely after source review and sandbox tests.
- Keep the existing AgentHub dynamic controller as the fallback until parity is proven.

### Phase 3: Production Hardening

- Persist resumable phase state in SQLite/event ledger, not only in memory.
- Add replay tests for backend restart during research, plan review, implementation, and verification.
- Add cross-tenant isolation tests for context-mode and Pi package storage.
- Add cost/token budgets per phase.
- Add no-progress detection at the RPI phase level, not only verifier failure signatures.

## E2E Proof Criteria

The current E2E tests prove package presence, Pi Web UI rendering, tool counts, and log compaction. Add tests that prove behavior:

- A complex prompt creates a research snapshot before implementation starts.
- A complex prompt creates neutral research questions before research starts.
- Research does not receive the full feature ticket when the mission is configured for blind factual research.
- Research outputs facts, source refs, uncertainty, and gaps without implementation recommendations.
- Design and structure artifacts are created before the tactical plan.
- Design review is required by backend phase state, not by a magic phrase in the user prompt.
- Structure breaks work into vertical slices with independent verification checkpoints.
- The plan artifact includes file/item refs, ordered steps, test strategy, and approval requirements.
- A write-heavy mission pauses at plan review unless configured for an approved autonomy profile.
- Instruction-budget telemetry shows phase prompts stay below the configured directive threshold.
- Implementation receives a small `ContextPackV2`, not the full transcript.
- Implementation receives only the approved vertical slice context, not all prior research output.
- Older context is compacted and a `pi.context.compaction.completed` event records saved tokens and digest.
- `context-mode` indexes a research snapshot or large evidence artifact, and implementation retrieves a targeted snippet with retrieval provenance instead of receiving the raw artifact.
- Context savings telemetry is visible in Mission Control and includes raw bytes/tokens avoided, returned bytes/tokens, and the active context-mode package version.
- Purging a mission removes its context-mode index entries or volume, and cross-tenant searches cannot retrieve another mission's indexed content.
- Verifier runs with fresh context and references the plan plus evidence receipts.
- A repeated verifier failure stops or routes to a repair plan instead of looping.
- A structural implementation blocker triggers a visible backtrack to plan/design instead of endless patching.
- Code/diff review remains required for production-risk missions even when design, structure, and plan are approved.
- Screenshots prove Mission Control shows QRSPI phases, context compaction, and verifier receipts without exposing raw secrets or trace-only logs.

## Final Position

Yes, create a Pi extension, but make it an AgentHub-owned agentic engineering extension rather than a cosmetic package declaration. Yes, change the agent approach: use subagents mainly for context isolation and fresh review, not just for role theater. Yes, build RPI/QRSPI and proper context management into the extension surface and backend state machine. No, the current four environment variables are not enough. They are the substrate. The best-practice layer is the explicit question-research-design-structure-plan-implement protocol, context-pack lifecycle, compaction policy, review gates, backtracking rules, code-review expectations, and evidence-backed verification.