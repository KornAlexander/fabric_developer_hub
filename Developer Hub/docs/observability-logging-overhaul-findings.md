# AgentHub Observability and Logging Overhaul Findings

Date: 2026-04-27

This report captures the current observability gaps found while reviewing the New Session flow and the live mission execution logs. The current system now logs much more than before, but the logs still do not answer the support questions we actually care about:

- What did the user do?
- What did the frontend request?
- What did the backend receive?
- What did Fabric, Copilot, MCP, or the tool runtime return?
- What did the backend normalize, cache, filter, persist, and return?
- What did the frontend receive?
- What did the UI display, hide, truncate, or synthesize?
- What exact execution step was running when a mission stalled, failed, or produced a bad result?

The current logging is high volume but still low accountability. We need a substantial logging redesign, not just more log lines.

## Previous Findings: New Session Flow

### What is useful today

The backend now has useful request and domain-level signals for the New Session page:

- Request start/end logs in `Developer Hub/Backend/src/main.py` show method, path, status, latency, request ID, user ID, and session ID.
- Workspace cache and refresh logs in `Developer Hub/Backend/src/api/agenthub_controller.py` show cache freshness, Fabric fetch start, raw workspace count, normalized count, and reconcile counts.
- Workspace item logs show request start, cache hit, Fabric fetch start, folder count, item count, type breakdown, and elapsed time.
- Session summary and list logs show aggregate session counts and status totals.
- Compose model logs show whether the model catalog was fetched and which model became the default.

These are good foundations. They prove backend activity happened and give timing/count-level evidence.

### What is noisy or low value

Several logs generate volume without answering support questions:

- `_serialize_job()` logs one trace line per session serialization in `Developer Hub/Backend/src/api/agenthub_controller.py`. On session lists or repeated session polling, this floods the log stream with rows that only say a model was marshalled.
- OpenTelemetry console JSON is enabled in `Developer Hub/docker-compose.yaml`. This mixes raw span JSON into the same stream as human-readable semantic logs. The JSONL trace file is useful; stdout span dumping should not be default.
- Workspace item responses currently log `cached=maybe`. That is not actionable. We need exact cache provenance: `source=cache|fabric|stale-cache`, `cache_age_ms`, `captured_at`, and `refresh_requested`.
- Request header summaries repeat large browser user-agent strings on every request. This is occasionally useful but too verbose for hot polling paths.

### What is missing

The largest New Session gap is frontend accountability. The backend can tell us it returned data, but the frontend does not prove what it displayed.

Missing evidence:

- Page lifecycle: New Session mounted, route/query params, tab ID, logical flow ID.
- Frontend request/response summaries for workspaces, workspace items, recent sessions, compose models, and create-session.
- Frontend display decisions: selected workspace, fallback/default selection, hidden/disabled workspace counts, filtered item counts, empty/error/loading states.
- Backend response digests: stable hashes or compact ID summaries of returned workspaces/items/models so we can compare backend output with frontend input without logging full sensitive payloads.
- Fabric raw-to-normalized comparison: raw count, invalid rows dropped, unsupported types, folder/item split, type breakdown, and continuation/page information.
- End-to-end request correlation: `Developer Hub/Frontend/src/utils/correlation.ts` exists and `Developer Hub/Frontend/src/controller/AgentHubApi.ts` can stamp `X-Request-ID`, but the main New Session flows are not consistently wrapped in a frontend-minted logical request/action ID.
- OTel trace IDs are often `tr:- sp:-` in semantic logs, which means app logs and OTel spans are not reliably joined.

## Current Findings: Execution Logs

The live mission execution logs are even more mismatched than the New Session logs.

### Backend sample problem

The pasted backend logs are dominated by repeated polling:

- `REQUEST start GET /api/sessions/{session_id}`
- `[SESSION-STORE] get hit`
- `[SESSION] load`
- `[SESSION] serialize`
- `GET /api/sessions/{session_id} -> 200`

This repeats every 2 seconds, and in the sample it repeats for two different running sessions. That creates a wall of logs while telling us almost nothing about actual execution.

Root cause: `Developer Hub/Frontend/src/components/AgentHub/mission/useMissionStream.ts` contains a fallback poll that calls `api.getSession(sessionId)` every 2 seconds. The comment says it runs even while SSE is connected so the UI can recover missed terminal events. The code still performs the full `GET /api/sessions/{id}` call before deciding `if (isConnected && !isTerminal) return`. This means the frontend hammers the backend for full session payloads even while the real execution stream is connected.

This is the single biggest source of useless backend execution-log volume in the sample.

### Backend execution event problem

Actual runtime events are emitted through `_JobExecution.emit()` in `Developer Hub/Backend/src/services/agenthub/orchestrator_engine.py`. Internally, this method has good data:

- event type
- sequence ID
- session ID
- timestamp
- log category
- event payload
- ring buffer for replay
- trace-only ring buffer
- slot progress
- artifacts
- change records

But the backend log line emitted for the event is only:

```text
[EMIT:<session> seq=<n> rid=<request>] <event_type>
```

For events like `orchestrator_decision`, `subagent_stale`, `subagent_steered`, and `log_line`, this is too thin. The backend log proves an event was emitted, but not what decision was made, what run was stale, how stale it was, what directive was sent, or what user-visible log text was emitted.

The backend has the payload at emit time but throws most of it away in the human log.

### Frontend screenshot problem

The screenshot shows the opposite problem: the frontend displays only a tiny, heavily curated subset of runtime events:

- 1 high-level log
- 4 detailed logs
- 5 diagnostic logs
- A few rows like `status: running`, `Tool call: create_workspace_inventory_solution(...)`, `Running create workspace inventory solution`, and `Building Fabric inventory solution in tmp_*`.

This is not enough to understand execution. A user or operator cannot tell:

- which exact session request or SSE event produced each row
- whether the tool was authorized, dispatched, still running, succeeded, partially succeeded, or failed
- how long the tool has been running
- what Fabric calls the tool is making internally
- how many source items were found
- which artifacts were created
- whether model/report validation is running
- whether the UI is showing streamed SSE events, fallback poll events, or synthesized state
- whether a row came from backend event payload, frontend reducer synthesis, or a display formatter

### Frontend reducer compression

`Developer Hub/Frontend/src/components/AgentHub/mission/missionReducer.ts` converts rich events into short `LogEntry` rows. That is fine for a compact UI, but it is not enough for observability.

Examples:

- `orchestrator_decision` becomes `Orchestrator: <rationale>` and loses decision type, task ID, run ID, and payload.
- `tool_call_started` becomes a formatted text row with `argsPreview`, but the UI truncates compact rows aggressively.
- `tool_call_ended` becomes `Completed: <tool> - <duration>` and hides output size, policy decision, result digest, created artifacts, validation status, and error preview unless it failed.
- `log_line` becomes plain formatted text and loses tags in the UI.
- `run_overview` updates state but does not create an explicit log row explaining that this was a snapshot or recovery path.
- Trace events are dropped entirely from the public reducer, which is correct for public logs, but there is no operator-only way to inspect them from the UI.

The frontend log window in `Developer Hub/Frontend/src/components/AgentHub/mission/MissionControlPage.tsx` then truncates compact messages to about 150 to 170 chars and full modal rows to about 420 chars. This makes the visual log readable, but it cannot be the only support evidence.

### Tool runtime visibility mismatch

Tool dispatch through `Developer Hub/Backend/src/services/agenthub/tool_runtime.py` is much richer than the frontend display:

- dispatch start
- tool name
- session
- tenant/workspace
- argument keys
- policy decision
- sensitivity
- auto-allowed flag
- argument hash
- dropped identity keys
- latency
- output char count
- preview

But the mission UI usually shows only `Running <tool>` and maybe `Completed: <tool>`. The UI is missing the fields that explain whether a tool was actually dispatched, authorized, successful, slow, partial, or blocked by policy.

### Long-running Fabric tool opacity

The inventory tool `fabric_create_workspace_inventory_solution` in `Developer Hub/Backend/src/mcp_servers/fabric.py` is a long-running multi-step operation. It validates capacity, collects inventory, creates/reuses a folder, creates data artifacts, updates a notebook definition, runs the notebook, validates tables, creates/refreshes/validates a semantic model, creates a report, validates rendering, and may clean up failed artifacts.

Today the mission frontend collapses that into a few rows. Backend logs only show tool dispatch start/end and eventual output preview. During the long middle section, support cannot see which sub-step is active unless the MCP server itself happens to log enough detail.

This is a critical gap because long-running tools are where users will report "it is stuck" or "it did not create the right thing."

## Main Diagnosis

We currently have three different realities:

1. Backend application logs: very noisy around HTTP polling and session serialization, thin around event payloads.
2. SSE event stream: richer event objects exist, but are mostly transient and not logged with payload summaries.
3. Frontend mission logs: compact display rows, useful for a pleasant UI, but not useful as the operational audit trail.

These three realities are not reconciled. There is no single evidence chain that proves what the user saw and how it maps to backend execution.

## Required Logging Changes

### 1. Introduce a first-class execution event ledger

Every emitted mission event should be recorded as a structured execution event, separate from the human log line.

Recommended fields:

- `event_id`
- `session_id`
- `seq`
- `trace_seq` for trace-only events
- `request_id`
- `trace_id`
- `span_id`
- `user_id`
- `event_type`
- `log_category`
- `agent_id`
- `agent_name`
- `task_id`
- `run_id`
- `tool_name`
- `call_id`
- `status`
- `duration_ms`
- `payload_digest`
- `payload_summary`
- `payload_preview_redacted`
- `created_artifact_count`
- `change_record_count`
- `error_code`
- `error_preview_redacted`

Store this in SQLite or JSONL locally first. The human backend log should reference it with `event_id` and include a compact summary.

### 2. Stop full-session polling while SSE is healthy

Change `useMissionStream` so it does not call `GET /api/sessions/{id}` every 2 seconds while SSE is connected and the session is running.

Better options:

- Poll only when SSE is disconnected.
- Use a cheap `HEAD` or `GET /api/sessions/{id}/status` endpoint that returns only `status`, `version`, `last_event_seq`, and `updated_at`.
- Emit reliable terminal events and close SSE only after terminal delivery is acknowledged/replayable.
- If polling must remain, suppress normal access logs for healthy status polls or aggregate them once per minute.

### 3. Remove per-serialization log spam

Stop logging `_serialize_job()` for every session serialization by default.

Replace with endpoint-level summaries:

- `/api/sessions` response: `count`, `status_counts`, `limit`, `offset`, `payload_bytes`, `ids_digest`.
- `/api/sessions/{id}` response: `status`, `agent_count`, `active_agent_count`, `event_seq`, `payload_bytes`, `updated_at`, `context_keys`.

Only log per-session serialization when there is a serialization error or an explicit debug flag.

### 4. Log event payload summaries at emit time

Change `_JobExecution.emit()` human logs from event type only to compact event summaries.

Examples:

```text
[EMIT session=442b776e seq=16 type=orchestrator_decision category=detailed task=inventory run=generalist decision=steer rationale="no progress for 120s" payload_digest=abc123]
[EMIT session=442b776e seq=17 type=subagent_stale category=detailed run=generalist task=inventory stale_seconds=120]
[EMIT session=442b776e seq=20 type=log_line category=high_level level=warn tags=dynamic_supervision,stale_subagent message="Mission control sent a progress-check directive" digest=def456]
```

The full payload should go to the structured event ledger, not the regular log line.

### 5. Split display logs from support logs

Mission Control needs two concepts:

- Display logs: curated, short, user-friendly.
- Support/event logs: structured, complete enough to diagnose, redacted, downloadable/copyable.

The current tabs should not pretend that five display rows are diagnostic observability. Add a support view or event inspector that shows selected event details, including sequence, source, category, event payload summary, request ID, and backend correlation IDs.

### 6. Add frontend observability events

The frontend should emit structured local events for:

- Mission page mounted
- Initial session fetched
- SSE connecting/open/error/close/reconnect
- Replay requested with `lastEventId`
- Event received: `type`, `seq`, `category`, `payload_digest`
- Reducer applied/dropped duplicate/dropped trace event
- Fallback poll started/stopped/skipped due to healthy SSE
- UI displayed log count per category
- Active agent selected
- Terminal state displayed
- User clicked cancel
- Cancel request sent/succeeded/failed

These can initially go to console and a bounded in-memory ring buffer. Later they can be uploaded or attached to support diagnostics.

### 7. Add logical flow IDs

Request IDs alone are too small. A mission has many HTTP requests, SSE frames, background tasks, and tool calls.

Add:

- `flow_id`: one frontend-minted ID for the user action or page lifecycle.
- `session_id`: mission/session ID.
- `event_id`: one ID per emitted event.
- `tool_call_id`: one ID per tool call.
- `operation_id`: one ID per long-running tool sub-operation.

Propagate these through frontend requests, backend logs, OTel spans, SSE events, tool runtime calls, MCP manager calls, and long-running Fabric tool sub-steps.

### 8. Instrument long-running tools internally

For `fabric_create_workspace_inventory_solution`, emit sub-step events such as:

- capacity validation started/completed
- source inventory collected: workspace count, item count, type breakdown
- folder created/reused
- lakehouse/warehouse created/reused
- notebook definition updated
- notebook run started/completed/failed
- table validation started/completed
- semantic model created/refreshed/query validated
- report created/render validated/cleanup completed
- final result summary: status, errors, warnings, created item count, created item digest

These sub-step events should be visible in support logs and summarized in the frontend. This is required for long-running operations.

### 9. Normalize categories and names

The current categories are still useful, but their meaning must be enforced:

- `high_level`: user-relevant state transitions, terminal outcomes, blockers, major created artifacts.
- `detailed`: task progress, decisions, tool starts/ends, important intermediate states.
- `diagnostic`: policy decisions, hashes, payload summaries, retry details, raw provider status codes.
- `trace`: internal-only raw payloads, token-free request/response shape, replay/debug internals.

Frontend labels should make clear that `Diagnostic logs` are still display logs unless the support event inspector is open.

### 10. Add response and payload digests

For support without leaking sensitive data, every important payload should have a stable digest and bounded summary:

- workspace list digest
- workspace item list digest
- session payload digest
- SSE event payload digest
- tool argument digest
- tool output digest
- created artifact digest

Then support can prove that backend output and frontend input matched without printing entire payloads.

## Immediate Priority Plan

### P0: Kill the worst noise

1. Disable OTel console exporter by default; keep JSONL file output.
2. Stop or gate `_serialize_job()` per-row logging.
3. Stop polling full sessions every 2 seconds while SSE is connected.
4. Add endpoint-level summaries for `GET /api/sessions/{id}` and `GET /api/sessions`.

### P1: Make execution events accountable

1. Add a structured event ledger for `_JobExecution.emit()`.
2. Add compact event payload summaries to backend `[EMIT]` logs.
3. Add event IDs and payload digests to SSE events.
4. Include request/trace/span IDs on event payloads where available.

### P2: Make the frontend prove what it displayed

1. Add a frontend observability ring buffer.
2. Log mission stream lifecycle and reducer outcomes.
3. Log category counts and rendered row counts.
4. Add a support inspector/modal for raw event summaries.
5. Add a support export button for frontend ring buffer plus backend correlation IDs.

### P3: Make long-running tools inspectable

1. Add sub-step telemetry to `fabric_create_workspace_inventory_solution`.
2. Send progress events during long-running tool execution, not only at start/end.
3. Surface sub-step progress in Mission Control.
4. Persist final tool result summaries and created artifact digests.

### P4: Align with OpenTelemetry

1. Fix missing `tr:- sp:-` on semantic request logs.
2. Create spans for mission execution, event emission, tool runtime dispatch, MCP process execution, and long-running Fabric sub-steps.
3. Record event IDs and session IDs as span attributes.
4. Keep external exporters optional; local traces remain the default for now.

## Example Target Log Shape

Backend human log:

```text
[high_level req=<flow/request> u=<user> s=<session> tr=<trace> sp=<span>] [MISSION] started architecture=dynamic agents=1 workspace=<id> task_digest=<hash>
[detailed req=<flow/request> u=<user> s=<session> tr=<trace> sp=<span>] [EVENT] seq=12 type=tool_call_started agent=generalist tool=fabric_create_workspace_inventory_solution call=<id> args_digest=<hash> args_summary={workspace_id:<id>, folder_name:tmp_*}
[diagnostic req=<flow/request> u=<user> s=<session> tr=<trace> sp=<span>] [TOOL] call=<id> policy=allowed sensitivity=write arg_hash=<hash> timeout=900s
[detailed req=<flow/request> u=<user> s=<session> tr=<trace> sp=<span>] [TOOL-STEP] call=<id> step=source_inventory_collected source_items=87 source_workspaces=4 types={Report:20,SemanticModel:10,...}
[high_level req=<flow/request> u=<user> s=<session> tr=<trace> sp=<span>] [TOOL] call=<id> completed status=partial duration_ms=126000 created=5 warnings=2 errors=1 result_digest=<hash>
```

Frontend support event:

```json
{
  "source": "mission-ui",
  "flowId": "fe-...",
  "sessionId": "442b776e-...",
  "eventSeq": 12,
  "eventType": "tool_call_started",
  "reducerAction": "displayed",
  "category": "diagnostic",
  "nodeId": "generalist",
  "displayText": "Running create workspace inventory solution",
  "payloadDigest": "abc123",
  "visibleRows": { "high_level": 1, "detailed": 4, "diagnostic": 5 }
}
```

## Bottom Line

We do need huge changes. The current logs are a mix of useful backend foundations, excessive polling noise, thin event emission logs, and overly compressed frontend display rows.

The goal should not be "more logs everywhere." The goal should be a joined evidence chain:

```text
user action -> frontend request -> backend input -> provider/tool result -> backend normalized output -> SSE event -> frontend reducer -> displayed UI row -> terminal outcome
```

Until that chain exists, support will keep seeing large log streams that still cannot answer basic questions like "what did the user see?", "what did the tool actually create?", and "where did execution stall?"