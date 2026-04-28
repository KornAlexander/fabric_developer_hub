---
description: "Analyze and implement AgentHub generalist/subagent orchestration, verification loops, and traceable logging"
name: "AgentHub Orchestration Traceability"
argument-hint: "Optional focus area, such as backend orchestration, frontend mission logs, or e2e verification"
agent: "agent"
---

We are working in the VS Code workspace:

`/home/lukaszobst/Fabric ClawHub`

The main application lives under:

`/home/lukaszobst/Fabric ClawHub/Developer Hub`

Important areas:

- Backend: `Developer Hub/Backend`
- Backend source: `Developer Hub/Backend/src`
- Backend tests: `Developer Hub/Backend/tests`
- Backend virtualenv Python: `Developer Hub/Backend/.venv/bin/python`
- AgentHub backend services: `Developer Hub/Backend/src/services/agenthub`
- Dynamic orchestration runtime: `Developer Hub/Backend/src/services/agenthub/dynamic_orchestrator.py`
- Orchestrator/session streaming engine: `Developer Hub/Backend/src/services/agenthub/orchestrator_engine.py`
- Orchestration domain models: `Developer Hub/Backend/src/domain/models/dynamic_orchestration.py` and `Developer Hub/Backend/src/domain/models/agent_models.py`
- Agent registry/catalog: `Developer Hub/Backend/src/services/agenthub/agent_registry.py` and `Developer Hub/Backend/src/services/agenthub/catalog.yaml`
- Session/audit storage: `Developer Hub/Backend/src/services/agenthub/session_store.py`
- Backend log/correlation utilities: `Developer Hub/Backend/src/services/correlation.py` and `Developer Hub/Backend/src/main.py`
- Tool policy/runtime: `Developer Hub/Backend/src/services/agenthub/tool_policies.py` and `Developer Hub/Backend/src/services/agenthub/tool_runtime.py`
- Frontend: `Developer Hub/Frontend`
- Frontend source: `Developer Hub/Frontend/src`
- Frontend package/scripts: `Developer Hub/Frontend/package.json`
- Frontend AgentHub UI: `Developer Hub/Frontend/src/components/AgentHub`
- Frontend mission control: `Developer Hub/Frontend/src/components/AgentHub/mission`
- Mission stream/event types/reducer: `Developer Hub/Frontend/src/components/AgentHub/mission/events.ts`, `useMissionStream.ts`, and `missionReducer.ts`
- Mission log presentation/visibility: `Developer Hub/Frontend/src/components/AgentHub/mission/logPresentation.ts` and `logVisibility.ts`
- Mission control page: `Developer Hub/Frontend/src/components/AgentHub/mission/MissionControlPage.tsx`
- Team/internal agent visibility: `Developer Hub/Frontend/src/components/AgentHub/team`
- Frontend e2e tests: `Developer Hub/Frontend/e2e`
- Existing behavior note: `Developer Hub/docs/prompts/agents behaviour.md`

Your assignment:

Analyze the current backend and frontend implementation against the orchestration concept below, then implement the missing changes required to make the behavior real, observable, and consistently verified by automated tests.

Do not stop at analysis. Inspect the code, identify gaps, implement focused backend/frontend/test changes, and iterate until the relevant unit and e2e tests pass or until a genuine external-service blocker is documented with the closest available validation.

Target orchestration concept:

1. The generalist is the entry point for a mission and owns the whole plan.
2. The generalist decides whether to perform a task directly or delegate it to specialized subagents.
3. Delegation to specialized subagents is preferred when a subtask maps clearly to an agent capability.
4. The generalist can spawn multiple subagents in parallel when subtasks are independent and parallel execution improves throughput.
5. Each subagent receives structured, task-specific context rather than the entire raw mission context.
6. The generalist regularly checks running agents by consuming their progress/log signals, without needing to ingest every low-level log line.
7. The generalist detects whether a subagent is on track, blocked, off track, producing inconsistent results, or failing expected outcomes.
8. When a subagent is off track or blocked, the generalist can intervene by steering it with additional guidance, cancelling/reassigning the subtask, spawning a replacement agent, or taking the task over directly.
9. When a subagent finishes, it sends structured feedback to the generalist with results, issues, observations, and useful context gathered during the subtask.
10. The generalist incorporates subagent feedback into the overall plan and can adjust later assignments or steer still-running agents when new information changes the plan.
11. The generalist can intentionally wait for a subagent result before continuing if downstream work depends on that feedback.
12. The generalist always plans independent verification for produced outputs.
13. Verification is delegated to a verifier agent when outputs need acceptance checks, especially for Fabric/Power BI deliverables, created items, data artifacts, reports, visuals, screenshots, or UI-visible results.
14. The verifier checks outputs against explicit expected outcomes and reports pass/fail findings with evidence.
15. If verification fails, verifier findings are routed back to the generalist for review, repair planning, and possible reassignment rather than being treated as blind repair instructions.
16. The generalist loops through repair and reverification until outputs pass, retry/replan budget is exhausted, or the plan is no longer feasible.
17. The generalist can abort a mission when repeated verification failures, impossible prerequisites, unrecoverable agent failures, or exhausted budgets make continued execution wasteful.

Traceability and logging requirements:

All orchestration behavior above must be logged in a way that allows someone to reconstruct the sequence of decisions, actions, interventions, verification results, and outcomes.

The trace must capture, at minimum:

- timestamp
- mission/session id
- actor id and actor role, such as generalist, subagent, verifier, or system
- event type
- plan state or relevant task state at the time
- decision/action/intervention/verification outcome
- concise rationale
- parent task and child task relationships
- delegated agent id/session id when applicable
- structured subtask context summary
- feedback received from subagents
- verification criteria and findings
- retry/replan counters and abort reasons
- backend log category using the existing explicit categories: `high_level`, `detailed`, `diagnostic`, and `trace`

Trace/log visibility must satisfy both audiences:

- Backend developers can inspect persisted and streamed audit/log records after execution.
- Frontend users can inspect the important decision/action/intervention/verification sequence in Mission Control during execution and after execution.

Trace-level internal details must not leak into public frontend logs. Follow the existing rule that `trace` is internal-only and should not enter public SSE replay/ring/frontend logs.

Required analysis workflow:

1. Check current git status and do not revert unrelated user changes.
2. Inspect the existing orchestration flow before editing:
   - `Developer Hub/Backend/src/services/agenthub/dynamic_orchestrator.py`
   - `Developer Hub/Backend/src/services/agenthub/orchestrator_engine.py`
   - `Developer Hub/Backend/src/domain/models/dynamic_orchestration.py`
   - `Developer Hub/Backend/src/domain/models/agent_models.py`
   - `Developer Hub/Backend/src/services/agenthub/agent_registry.py`
   - `Developer Hub/Backend/src/services/agenthub/catalog.yaml`
3. Inspect the existing logging/audit path before editing:
   - `Developer Hub/Backend/src/services/agenthub/session_store.py`
   - `Developer Hub/Backend/src/services/correlation.py`
   - `Developer Hub/Backend/src/main.py`
   - event emission and SSE replay code in `orchestrator_engine.py`
4. Inspect the frontend mission log path before editing:
   - `Developer Hub/Frontend/src/components/AgentHub/mission/events.ts`
   - `Developer Hub/Frontend/src/components/AgentHub/mission/useMissionStream.ts`
   - `Developer Hub/Frontend/src/components/AgentHub/mission/missionReducer.ts`
   - `Developer Hub/Frontend/src/components/AgentHub/mission/logPresentation.ts`
   - `Developer Hub/Frontend/src/components/AgentHub/mission/logVisibility.ts`
   - `Developer Hub/Frontend/src/components/AgentHub/mission/MissionControlPage.tsx`
5. Build a short gap matrix with three columns:
   - Requirement from this prompt
   - Current implementation evidence
   - Change needed
6. Implement the smallest coherent set of changes that makes the requirements true and testable.

Implementation expectations:

- Preserve existing session/orchestration behavior unless a change is required to satisfy the orchestration concept.
- Keep the generalist internal; do not make it appear as a normal public frontend agent.
- Do not weaken tool policy, confirmation, or destructive-operation guardrails.
- Prefer structured event payloads over parsing prose where possible.
- Keep public log messages concise and readable.
- Keep diagnostic/trace payloads rich enough for developers to reconstruct decisions without exposing secrets or raw private context.
- Redact secrets, tokens, auth headers, connection strings, and private env values from logs, test snapshots, prompts, and final summaries.
- Make parent/child task relationships explicit in event payloads and stored logs where needed.
- Make intervention, reassignment, cancellation, takeover, verifier assignment, verifier result, repair plan, reverification, and abort events explicit enough for tests and UI filtering.
- If an event already exists, extend it compatibly instead of creating duplicate concepts.
- If an event shape changes, update backend serializers, frontend event types, mission reducer handling, log presentation, and tests together.
- If trace data is added, enforce that trace-only records stay out of public frontend log streams.

Backend test expectations:

From `Developer Hub/Backend`, use the project venv:

```bash
.venv/bin/python -m ruff check <changed backend files>
.venv/bin/python -m pytest --no-cov <focused affected backend tests>
```

Relevant focused backend tests include:

```bash
.venv/bin/python -m pytest --no-cov \
  tests/unit/services/agenthub/test_dynamic_orchestrator.py \
  tests/unit/services/test_orchestrator_engine.py \
  tests/unit/api/test_agenthub_controller_routes.py \
  tests/unit/services/agenthub/test_session_store.py \
  tests/unit/services/test_correlation.py \
  tests/unit/test_setup_logging.py
```

Add or update backend tests to prove:

- generalist decisions emit structured traceable events
- parallel delegation is represented in plan/task state
- subagent context summaries and feedback are captured
- monitoring/intervention/reassignment/takeover decisions are traceable
- verifier tasks are planned and their findings feed back through the generalist
- failed verification triggers repair/reverification or abort according to retry/replan limits
- trace category records do not leak to public frontend/SSE replay
- stored audit/log records retain enough fields to reconstruct execution

Frontend test expectations:

From `Developer Hub/Frontend`, inspect `package.json` first and use existing scripts. Useful commands include:

```bash
npx tsc -p tsconfig.json --noEmit
npm run build:test
npm test -- --run
```

Relevant focused frontend tests include:

```bash
npm test -- --run \
  tests/mission/missionReducer.test.ts \
  tests/mission/logPresentation.test.ts \
  tests/mission/logVisibility.test.ts \
  tests/teamVisibility.test.ts
```

Add or update frontend tests to prove:

- decision/action/intervention/verification events render in Mission Control logs
- log visibility tiers expose high-level user-relevant decisions while preserving diagnostic detail filters
- trace-only records are hidden from public UI
- internal generalist/orchestrator nodes remain hidden from public team/agent views
- stored/replayed mission events still reconstruct the important sequence after reload

E2E verification expectations:

Use the existing new-session and mission-control Playwright infrastructure. If local services are not already running, start them from `Developer Hub` with:

```bash
./start.sh dev
```

Then run the smallest useful e2e slice first, broadening only as needed:

```bash
cd "Developer Hub/Frontend"
npm run test:e2e:new-session
npm run test:e2e -- e2e/orchestrator-internal.spec.ts --project=chromium
```

If there is already a mission-control/logging e2e spec, extend it. If not, add a focused Playwright spec that drives a sample mission prompt through the mocked or local backend path and asserts that Mission Control shows a reconstructable sequence containing:

- generalist planning decision
- specialist delegation
- parallel subagent execution when independent tasks exist
- progress monitoring/checkpoint
- subagent feedback
- intervention or recovery path when the test fixture simulates a blocked/off-track agent
- verifier assignment
- verification result
- repair/reverification or successful finalization
- backend-visible event/log evidence for the same sequence

Sample mission prompt for e2e coverage:

```text
Create a Fabric-ready analytics outcome from the sample workspace context: inspect the available context, plan the work as the generalist, delegate independent implementation and validation subtasks to specialists where useful, create or update the required artifacts, verify the produced outputs independently, and show a complete trace of planning decisions, delegation, monitoring, interventions if needed, verifier findings, and final outcome in Mission Control.
```

Testing loop requirement:

Run validation after each meaningful implementation phase. If validation fails, fix the root cause and rerun. Continue this loop until:

- focused backend tests pass
- focused frontend tests pass
- frontend typecheck/build passes for touched areas
- relevant Playwright e2e coverage passes consistently
- logging/audit records are inspectable in backend storage or stream output
- important public events are visible in frontend Mission Control
- trace/internal-only details are not visible in public frontend logs
- any external auth/Fabric blocker is documented with the closest passing local validation

Definition of done:

Provide a concise final report with:

1. Gap matrix summary: which orchestration/logging requirements were already present, partially present, or missing.
2. Backend changes made, including affected event shapes, storage fields, and log categories.
3. Frontend changes made, including Mission Control visibility and replay behavior.
4. Tests added or updated.
5. Validation commands run and their results.
6. Evidence that the e2e sample prompt verifies planning, delegation, monitoring, intervention/recovery, verifier feedback, repair/reverification, finalization/abort behavior, and reconstructable logging.
7. Any remaining risks, skipped checks, or external-service blockers.

Start now by inspecting the repository and current git state, then proceed through implementation and validation autonomously until the orchestration, logging, frontend visibility, and e2e verification requirements are satisfied or a genuine blocker is reached.