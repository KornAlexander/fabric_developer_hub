# Claude Code Interaction Streaming And Fabric Mission Control Parity

Date: 2026-04-30

## Purpose

This document analyzes the `./claude-code/` runtime from the perspective of user-visible agent interaction: how activity is streamed, how logs and tool actions are shown, how the chat remains steerable while work is running, how input is queued or interrupts the current turn, and how detailed output is collapsed into a readable high-level overview as the workflow moves forward.

It then maps those mechanisms to the live Fabric workload implementation in `./Developer Hub/`, identifies what already exists, and describes what is still missing to reach the same interaction quality in Mission Control.

## Executive Summary

Claude Code feels interactive because it treats the agent run as a live stream, not as a background job that occasionally reports status. The same runtime loop handles model stream events, tool-use events, progress events, user input, interruption, queued follow-up messages, and transcript updates. The UI does not just print everything forever. It shows the current granular activity while it is useful, then collapses older detail into short summary rows, grouped tool activity, hidden counts, compact transcript views, and context summaries.

Fabric Developer Hub already has a serious event backbone: monotonic event sequence numbers, an in-memory SSE queue, replay rings, persisted public mission events, trace-only internal events, event categories, payload summaries, tool progress events, dynamic generalist/subagent events, verifier verdict events, and frontend reducer logic that turns those events into a live Mission Control view.

The main parity gap is not the absence of events. The gap is the interaction contract around them:

- Fabric Mission Control is mostly read-only today, even though backend steering primitives exist.
- Fabric emits useful milestone events, but model output is not streamed token-by-token and tool calls only appear after a non-streaming model response returns.
- Fabric has high-level/detailed/diagnostic filters, but it does not yet have Claude Code's stronger automatic collapse pattern where older verbose activity becomes a compact high-level row while the current activity remains detailed.
- Fabric's visual log stream is improving, but it still reads more like an event table than a crafted live transcript. Claude Code uses indentation, connectors, status dots, dimmed metadata, compact active-state rows, and stable completed summaries to make fast output feel calm and readable.
- Fabric has per-agent message queues, but the user-facing semantics are not explicit: there are no public `user_message_queued`, `user_message_delivered`, `turn_interrupted`, or `steering_pending` events.
- Fabric has payload summaries for support/debug use, but it lacks a first-class user-facing roll-up stream that tells slower readers what just happened as the mission advances.

The recommended path is to keep Fabric's event-sourced architecture, add a live steering composer, make queued/interrupt semantics explicit, and add a collapse layer that turns detailed event bursts into durable high-level mission summaries.

## Claude Code: Runtime Shape

### Core Turn Loop

The center of Claude Code is `claude-code/src/query.ts`.

The exported `query()` function wraps `queryLoop()`, an async generator. That matters: every meaningful event from the model/tool loop can be yielded as soon as it happens. The REPL does not wait for a whole final answer before updating. The loop yields:

- `stream_request_start` when a model request begins.
- Streaming model deltas and assistant messages.
- Tool-use blocks as they appear.
- Progress messages from tools.
- Tool-result messages.
- Synthetic missing-tool-result errors when interruption or fallback requires them.
- Compact/microcompact boundary messages.
- Optional tool-use summary messages for SDK/mobile clients.

The key architectural point is that the model turn, tool execution, transcript updates, and UI stream all share one pipeline.

Relevant files:

- `claude-code/src/query.ts`
- `claude-code/src/screens/REPL.tsx`
- `claude-code/src/utils/messages.ts`
- `claude-code/src/services/tools/StreamingToolExecutor.ts`

### REPL Consumption

`claude-code/src/screens/REPL.tsx` consumes the generator. The `onQueryEvent()` callback passes each event into `handleMessageFromStream()` from `claude-code/src/utils/messages.ts`.

That handler translates raw runtime messages into UI state:

- It appends committed messages to the transcript.
- It keeps transient streaming text separate from committed messages.
- It tracks streaming tool uses.
- It tracks streaming thinking blocks.
- It handles tombstones by removing targeted messages.
- It clears transient streaming text when a final message lands, so the UI switches atomically from live streaming text to committed transcript text.
- It ignores `tool_use_summary` messages in the REPL path because those are SDK/mobile summary artifacts, not normal terminal transcript messages.

This separation is one reason the UI can feel live without becoming incoherent. Current activity can be animated and partial. Past activity becomes committed and stable.

### Streaming Tool Execution

`StreamingToolExecutor` is the mechanism that makes tool activity feel immediate.

In `query.ts`, as assistant messages stream in, the loop detects `tool_use` blocks and calls `streamingToolExecutor.addTool(...)`. The executor does not wait for all assistant text to finish. It can start executing tools as soon as tool-use blocks exist and the abort signal is still valid.

The executor tracks each tool with statuses like:

- `queued`
- `executing`
- `completed`
- `yielded`

It also separates progress messages from final results. Progress messages can be yielded immediately while final tool results are yielded in a controlled order. That is the key difference between a live workflow and a batch workflow. The user sees that a tool has started, what it is doing, and when it completes, even before the next assistant response.

Relevant file:

- `claude-code/src/services/tools/StreamingToolExecutor.ts`

## Claude Code: Visual Log And Output Stream Quality

The streaming behavior is only half of why Claude Code feels good. The other half is visual presentation. It does not show live output as a flat chronological table. It turns the model/tool stream into a shaped transcript with indentation, markers, status, grouping, and calm motion.

This matters for Fabric because Developer Hub already emits a lot of structured events. The remaining UX gap is that users still have to mentally reconstruct the shape of the work from event rows. Claude Code does more of that reconstruction visually.

### 1. Output Has A Strong Visual Grammar

Claude Code uses a small set of repeatable visual primitives:

- assistant text is primary prose,
- tool responses are nested under the initiating action,
- secondary output is dimmed,
- active work has a visible unresolved state,
- completed work changes to a resolved state,
- failures use a distinct error state,
- metadata such as token counts, tool counts, duration, and shortcuts is present but visually quieter than the main action.

`MessageResponse.tsx` is a good example. Tool results and subordinate output are rendered with a leading response marker and indentation. Nested `MessageResponse` instances deliberately avoid adding repeated markers, so the hierarchy stays clean instead of becoming noisy. The `Ratchet` wrapper also helps keep already-rendered output visually stable instead of constantly reflowing as new lines arrive.

The effect is that a user can glance at the transcript and understand nesting:

```text
Assistant action
  response/output for that action
  more subordinate detail
Next assistant action
```

Fabric Mission Control currently has a stronger dashboard frame, but its log rows often compete at the same visual level. Agent labels, timestamps, event prose, tool details, and support metadata can all feel like peer information. Claude Code's transcript makes the current sentence/action primary and pushes supporting details into a visually subordinate lane.

### 2. Active, Completed, And Failed States Are Instantly Scannable

`ToolUseLoader.tsx` renders an active unresolved tool with a blinking marker, then switches the marker color/state once the tool resolves or fails. `AgentProgressLine.tsx` uses a tree-like connector, a dimmed status line, bold agent labels, and compact counts such as tool uses and tokens.

The important point is not the exact terminal glyphs. It is the state language:

- unresolved work looks alive,
- completed work becomes calm and compact,
- failed work stands out,
- the latest useful sub-action is visible without expanding everything.

Fabric has active/running/done styling in places, but it should make this state grammar more consistent across the entire Mission Control log. A running tool, running agent, running verifier, queued steering message, approval wait, retry, and completed artifact should all share a recognizable visual state language.

### 3. The Stream Preserves Rhythm Instead Of Becoming A Wall

Claude Code keeps live output readable by controlling vertical rhythm:

- only the last few progress messages are shown in the active progress view,
- older progress is acknowledged with `+N more tool uses`,
- repeated read/search operations collapse into a single dim summary,
- the current operation gets room, while old operations become one-line summaries,
- transcript expansion is always available through the visible shortcut hint.

`AgentTool/UI.tsx` caps the normal progress display to the most recent processed messages and switches to a condensed one-line mode when the terminal is too small. That gives the user a graceful reading path under pressure. Instead of trying to keep up with every line, they can track the current phase, the amount of hidden activity, and the route to full detail.

Fabric has category filters and a condensed/expanded log toggle, but the visual rhythm is still mostly row-based. The UI shows a filtered slice of events; it does not yet shape old bursts into a live transcript with explicit local expansion points.

### 4. Tool Output Is Presented As Evidence, Not Noise

Claude Code does not treat tool output as ordinary chat text. Tool output is usually:

- indented under the action that produced it,
- visually quieter than assistant prose,
- clipped or summarized when too large,
- rendered with stable status markers,
- connected to a clear completed summary such as `Done (4 tool uses · 12,345 tokens · 1m 04s)`.

That last line is important. It turns a burst of low-level actions into an accountable receipt. The user knows work happened, how much happened, and how to expand if needed.

Fabric should do the same for long-running Fabric operations. For example, a report-generation branch should end with a compact receipt:

```text
Created inventory report (7 tool calls · 3 Fabric items · verifier passed · 2m 18s)
```

The underlying events should still be preserved, but the default visual artifact should be a human-readable receipt, not a list of raw runtime rows.

### 5. The UI Uses Low-Contrast Metadata Well

Claude Code leans heavily on dimmed metadata and compact bylines. Counts, timings, token usage, hidden-detail counts, and shortcut hints are present, but they do not overpower the main output. This is especially important in a fast stream: metadata helps trust, but it should not steal attention from the current action.

Fabric should separate visual hierarchy similarly:

- primary: what changed or what is happening,
- secondary: who/which agent did it,
- tertiary: time, event id, digest, latency, category, token/tool counts,
- expandable: raw payload summaries and diagnostic evidence.

Today, some Fabric support fields are available in tooltips or copy/export flows, which is good. The next step is to make the primary log surface feel calmer by pushing more diagnostic metadata into secondary or expandable positions.

### 6. The Visual Design Makes The Agent Feel Present

Claude Code's live stream communicates presence without overexplaining. A user sees motion, hierarchy, progress, and completion in the same area where they type. The UI says: the agent is working, here is the current thing, here is what just finished, and here is how to expand.

Mission Control should aim for the same feeling in a browser-native way:

- a live transcript/timeline that privileges current work,
- local expandable groups instead of global mode switches only,
- consistent status marks for running/done/failed/waiting/approval,
- compact receipts for completed tool batches and subagent tasks,
- dim metadata, not metadata-heavy rows,
- a persistent steering composer close to the live output.

In short: Claude Code's stream is not just more live. It is visually authored. Fabric should not only emit similar events; it should render them with the same care.

## Claude Code: Queueing, Steering, And Interruption

### Query Guard

`claude-code/src/utils/QueryGuard.ts` is a small but important synchronous state machine.

It has states that effectively mean:

- idle: no turn is active.
- dispatching: input has been accepted and a turn is being prepared.
- running: `onQuery()` has started the active model/tool loop.

The key property is that reservation happens synchronously before awaits. That prevents two input submissions from racing into two simultaneous main turns. `queryGuard.isActive` returns true for both dispatching and running, so the UI can treat both as busy states.

### Command Queue

`claude-code/src/utils/messageQueueManager.ts` implements the central command queue.

The queue supports priorities:

- `now`
- `next`
- `later`

Priority order is `now > next > later`, with FIFO order within the same priority. This is not just a background notification list. It is the user's steering buffer. User messages, task notifications, orphaned permission results, slash commands, and other queued commands all flow through one manager.

`claude-code/src/hooks/useQueueProcessor.ts` subscribes to the queue and the guard via `useSyncExternalStore`. When the guard becomes idle, `processQueueIfReady()` drains the next appropriate command set.

`claude-code/src/utils/queueProcessor.ts` is careful about batching:

- Slash commands are processed one at a time.
- Bash-mode commands are processed one at a time for error isolation.
- Normal prompt/task-notification commands can be batched when they share the same mode.
- Main-thread processing does not get blocked by subagent-targeted notifications.

### Submit While Busy

`claude-code/src/utils/handlePromptSubmit.ts` owns the user input boundary.

When the user submits while a query is active, it queues the input instead of starting a second turn. If the current running work is interruptible, it aborts the active abort controller with reason `interrupt`. Otherwise, the message remains queued for the next turn.

This gives the user a strong interaction model:

- They can type while the agent is working.
- Their message is not lost.
- Their message does not race or corrupt the current turn.
- The current turn can be interrupted when safe.
- The queued input runs automatically once the active turn ends.

That behavior is central to the feeling of steering.

## Claude Code: Collapsing Detail Into A High-Level Overview

This is the piece that matters for users who cannot read the raw event stream as fast as the agent produces it. Claude Code uses several collapse layers at different scopes.

### Layer 1: Current Detail, Stable Past

While a response is streaming, the UI shows transient streaming text and streaming tool-use state. When the final assistant message arrives, `handleMessageFromStream()` clears streaming text and commits the final message into the transcript.

The result is a clean boundary:

- Current output can be partial and fast-moving.
- Past output becomes stable and readable.
- The UI does not duplicate the streamed text and final text.

This is not a summarizer, but it is the first readability layer. It prevents visual churn from leaking into the committed transcript.

### Layer 2: Progress View Shows Only Recent Detail

`claude-code/src/tools/AgentTool/UI.tsx` contains a good example of UI-level collapse in the subagent progress display.

The progress renderer processes subagent progress messages, groups repeated search/read/REPL operations, and shows only the most recent processed messages in the normal view. Older tool activity is represented as a count like `+N more tool uses` with a shortcut hint to expand the transcript.

The UI also has a terminal-size-aware condensed mode. If the terminal is too small to show all active dynamic content, it falls back to a one-line summary such as:

```text
In progress... · 4 tool uses · 12,345 tokens · ctrl+o expand
```

This is exactly the pattern the user is asking about: the user gets a high-level overview when the detailed stream is moving too quickly or when the display cannot comfortably fit it.

The key behavior is not simply truncation. It is semantic compression:

- Consecutive search/read operations become one summary row.
- The live view shows the latest useful events, not the entire backlog.
- Hidden work is acknowledged with counts.
- The full transcript remains available on demand.

### Layer 3: Search/Read Grouping

`claude-code/src/utils/collapseReadSearch.ts` identifies operations that are safe to group:

- file reads
- grep/glob/list operations
- REPL wrapper operations whose inner primitive calls are emitted as virtual messages
- some memory-file writes/edits
- some meta-operations that should be absorbed silently

The grouping logic avoids treating every low-level lookup as a full narrative event. That keeps the user-facing stream from becoming a wall of `read`, `grep`, `glob`, `read`, `grep`, `glob`.

Instead, the user sees compact summaries such as:

- searched files
- read files
- performed several lookups
- `+N more tool uses`

The important design principle is that repeated exploratory operations are usually not individually meaningful to the user. They are evidence-gathering. They should be available in the transcript, but they should not dominate the high-level view.

### Layer 4: Tool-Use Summary Messages

`claude-code/src/services/toolUseSummary/toolUseSummaryGenerator.ts` generates human-readable labels for completed tool batches. The prompt explicitly asks for a short single-line label, around 30 characters, in a commit-subject style:

```text
Searched in auth/
Fixed NPE in UserService
Created signup endpoint
Read config.json
Ran failing tests
```

In `query.ts`, after a tool batch completes, Claude Code starts summary generation asynchronously. It does not block the next model call. On the next recursive loop, if the summary promise resolved, the loop yields a `tool_use_summary` message.

The REPL ignores `tool_use_summary`, but SDK/mobile clients can use it as a high-level row for the completed tool batch. This shows a useful architectural distinction:

- The runtime can emit full detail.
- Another consumer can choose summary rows.
- The summary is tied to specific preceding tool-use IDs, so it can replace or annotate a known detail group.

### Layer 5: Microcompact For Model Context

`claude-code/src/services/compact/microCompact.ts` is about context size rather than UI readability, but it follows the same philosophy: preserve recent useful detail and clear older bulky tool results.

It identifies compactable tool results from tools such as read, shell, grep, glob, web search, web fetch, edit, and write. Depending on the active path, it can:

- Use cached microcompact and API cache edits to delete older tool results from the server-side prompt context while preserving cache behavior.
- Use time-based microcompact after a long idle gap to replace old tool-result content with `[Old tool result content cleared]`.
- Keep the most recent compactable tool results so the model still has working context.

This is not the same as the visible UI collapse, but it is the model-context version of the same principle: old detail should not drown the next step.

### Layer 6: Auto Compact For Conversation History

`claude-code/src/services/compact/autoCompact.ts` handles full conversation compaction near context limits. It reserves output budget for the compaction summary, checks token thresholds, and calls `compactConversation(...)` when needed.

The result is a summarized conversation state that can continue the workflow without carrying every raw message forward. `query.ts` applies microcompact before autocompact, so it tries to keep granular context when cheaper compaction is enough and only falls back to full summarization when pressure requires it.

### What This Means For The User

Claude Code gives the user multiple reading speeds:

- Current detailed stream: what is happening right now.
- Recent condensed progress: the last few meaningful actions.
- Grouped high-level rows: what repeated lookups or tool batches accomplished.
- Hidden counts: how much detail was skipped from the default view.
- Expandable transcript: full detail when the user wants it.
- Context summary: continuity after long sessions or context pressure.

That stack is the reason a user can keep up with the workflow even when the agent is doing many rapid actions.

## Fabric Developer Hub: Current Event Streaming Model

### Backend Event Source Of Truth

Fabric's equivalent runtime is `Developer Hub/Backend/src/services/agenthub/orchestrator_engine.py`.

The `_JobExecution.emit()` method is the backbone. For each public event, it:

- Assigns a monotonic `seq`.
- Stamps `sessionId`, `ts`, `eventId`, and `logCategory`.
- Creates a `payloadDigest`.
- Creates a compact `payloadSummary`.
- Adds the event to an in-memory ring buffer.
- Persists the event to SQLite via `session_event_store.append_event(...)`.
- Writes an operational support ledger event via `record_event(...)`.
- Adds audit log entries.
- Pushes the event to the live `event_queue` for SSE.

Trace events are handled separately. They get `traceSeq` instead of public `seq`, go to a trace ring and support ledger, and do not enter the public event stream.

This is a good foundation. Fabric already has stronger event persistence and replay behavior than many agent UIs.

### Event Categories

`_event_log_category()` maps event types into categories:

- `high_level`
- `detailed`
- `diagnostic`
- `trace`

High-level event types include mission and orchestration milestones such as:

- `composition_ready`
- `mission_seeded`
- `task_created`
- `generalist_check_in`
- `generalist_steering`
- `subagent_result`
- `verifier_verdict`
- `mission_completed`
- `job_complete`

Diagnostic event types include:

- `tool_call_started`
- `tool_call_ended`
- `tool_progress`
- `diagnostic_required`
- `subagent_inspected`
- `subagent_stale`

Trace-only event types include internal resource locks and subagent heartbeat events.

This is Fabric's current equivalent to a detail hierarchy. It gives the UI enough metadata to show a high-level view by default and allow deeper detail on demand.

### SSE And Replay

`_JobExecution.events()` streams from the in-memory queue and supports replay with `last_seq`. It yields ring-buffered events with `seq > last_seq`, then live events. It also emits heartbeat frames so proxies and browsers do not silently close idle streams.

The FastAPI route `/api/sessions/{session_id}/events` exposes that stream. If there is no live execution, it replays persisted public events from SQLite and closes the stream. `/api/sessions/{session_id}/events.json` exposes the same event history as JSON for tests, catch-up polling, and reopening completed sessions.

### Frontend Stream Hook

`Developer Hub/Frontend/src/components/AgentHub/mission/useMissionStream.ts` handles the browser side.

Important behaviors:

- It waits for a Fabric token before connecting.
- It uses fetch-based SSE because browser `EventSource` cannot send the required Fabric headers inside the iframe environment.
- It passes `lastEventId` from the reducer's last known `seq`.
- It reconnects on errors.
- It classifies empty replay streams to avoid infinite reconnect loops.
- It polls `events.json` every few seconds as a catch-up safety net.
- It also polls full session state as a terminal-state recovery fallback.
- It drops duplicate events by sequence.
- It drops trace events from the public reducer path.

This is robust. The stream transport and replay system are already close to what is needed.

### Frontend Reducer And Presentation

`Developer Hub/Frontend/src/components/AgentHub/mission/missionReducer.ts` converts backend events into `MissionState` and log entries.

It translates structural events into readable user-facing lines. Examples:

- `mission_seeded` -> generalist created mission plan.
- `task_created` -> generalist queued task.
- `generalist_check_in` -> checkpoint counts.
- `agent_context_received` -> generalist delegated structured context.
- `generalist_state_decision` -> generalist reviewed specialist feedback.
- `generalist_steering` -> generalist intervened and steered a specialist.
- `subagent_spawned` -> specialist started.
- `subagent_result` -> specialist result summary.
- `verifier_verdict` -> verifier passed/rejected with evidence summary.
- `tool_call_started` -> formatted tool start.
- `tool_call_ended` -> formatted tool completion.
- `tool_progress` -> long-running tool step update.

`Developer Hub/Frontend/src/components/AgentHub/mission/logVisibility.ts` defines the high-signal and category filtering behavior. `high_level` is always visible in high-signal contexts. Warnings, errors, actions, decisions, and important phase events are also treated as high-signal.

`MissionControlPage.tsx` defines the category labels:

- High level
- Detailed
- Diagnostics

It also uses `logVisibleInCategory(...)`, where selecting a deeper category includes shallower categories. In other words:

- High level view shows high-level entries.
- Detailed view shows high-level plus detailed entries.
- Diagnostics view shows high-level plus detailed plus diagnostic entries.

This is already a good foundation for the user's readability requirement.

## Fabric Developer Hub: Current Steering Model

Fabric has backend steering primitives, but they are not yet surfaced with the same clarity as Claude Code.

### Backend Message Injection

The API client has:

```ts
sendMessage(sessionId, message, targetAgentId, opts)
```

The FastAPI route `/api/sessions/{session_id}/message` calls:

```py
get_orchestrator_engine().inject_message(session_id, req.message, req.target_agent_id)
```

`inject_message()` puts the message into the target agent queue if a target is supplied. Otherwise, it broadcasts the message to all agent queues for the running job.

Agents consume queued messages at the top of `_run_agent()` rounds:

```py
while not user_queue.empty():
    user_msg = user_queue.get_nowait()
    messages.append({"role": "user", "content": user_msg})
    execution.emit("agent_status", currentStep="Processing user message...")
```

So Fabric does have an equivalent to queued user steering. The limitation is timing and visibility:

- The message is consumed between model/tool rounds, not during a blocking tool call.
- The backend does not emit an authoritative public `user_message_queued` event when the message is accepted.
- The backend does not emit a public `user_message_delivered` event when the agent actually consumes it.
- The UI does not currently appear to expose a Mission Control composer that uses this API.

### Dynamic Generalist Steering

Dynamic orchestration has its own steering path.

`_RuntimeSubagentExecutor.steer()` pushes an `ORCHESTRATOR DIRECTIVE (...)` message into a subagent's queue and emits `generalist_steering` with `delivered=True`.

`DynamicMissionController.steer_subagent()` appends the directive to mission state, records an action, and emits both:

- `subagent_steered`
- `generalist_steering`

This is an important Fabric strength. The system can inspect a running specialist, detect loops or stale behavior, steer it, and show that intervention in Mission Control.

## Fabric Developer Hub: Current Collapse And High-Level Overview

Fabric already has three useful collapse/readability mechanisms.

### Mechanism 1: Event Categories

The backend categorizes each event. The frontend can filter by category depth. This gives users a high-level view without losing detailed evidence.

This is the closest current equivalent to Claude Code's visible collapse.

### Mechanism 2: Payload Summaries

`_event_payload_summary()` creates compact, redacted summaries for support and log consumption. It extracts the meaningful fields per event type, such as:

- task title and status for `task_created`.
- tool name, status, duration, latency, and error preview for tool events.
- run/task/agent IDs and summary text for subagent results.
- verifier verdict, structural failures, and evidence counts for verifier events.
- mission status and task/run counts for mission events.

These summaries are not the same as user-facing high-level rows, but they are the raw material for them.

### Mechanism 3: Reducer-Level Human Formatting

The frontend reducer turns detailed event payloads into short human-readable lines. For example:

- `Specialist result for task_x: completed - Created report...`
- `Verifier PASSED for task_y - ... [urls=1 · screenshots=2 · visualsRendered=yes]`
- `Generalist checkpoint: 2 ready for assignment, 1 specialists running, 3 complete, 0 blocked, 0 failed`

This is strong. It is already doing semantic compression. The remaining issue is that it is event-by-event compression, not lifecycle-based collapse.

## Fabric Gaps Against Claude Code's Collapse Model

### Gap 1: No Automatic Roll-Up Of Older Detail

Claude Code shows granular current activity, but older repeated work collapses into summary rows or hidden counts. Fabric filters by category, but it does not yet automatically turn old detailed bursts into summary cards or timeline checkpoints as the mission advances.

For example, if a Fabric specialist emits 30 tool progress events, Mission Control can filter them, but it does not yet synthesize a row such as:

```text
FabricDataEngineer finished data setup: created lakehouse, wrote notebook, validated table rows (+24 detailed events)
```

That is the missing readability layer.

### Gap 2: No Clear Current Detail Versus Past Summary Boundary

Claude Code treats the current operation differently from older operations. The current operation can show live detail. Older operations become condensed.

Fabric should make this explicit:

- Current active node: show detailed live events.
- Recently completed node: show a compact result summary and latest important events.
- Older completed node: collapse to one durable summary row with expandable detail.

### Gap 3: Payload Summaries Are Not Yet User Roll-Ups

`payloadSummary` is support-friendly and event-scoped. The user needs mission-stage summaries that combine multiple events into one readable state transition.

Event-scoped summary:

```text
toolName=fabric_create_item status=ok durationMs=1840
```

User-facing roll-up:

```text
Created the report shell and bound it to the generated semantic model.
```

Both are useful, but they serve different audiences.

### Gap 4: No Steering Composer In Mission Control

Fabric has `sendMessage()` and per-agent queues, but the Mission Control page currently reads like a monitoring surface. Claude Code's main screen is always a control surface. To reach parity, Mission Control needs a composer that makes steering visible and reliable.

### Gap 5: No Public Queue Events

Claude Code's queue is internal, but the terminal UI immediately reflects queued/interrupted behavior. Fabric is distributed, so it needs explicit public events for that same confidence.

Recommended event types:

- `user_message_queued`
- `user_message_delivered`
- `user_message_broadcast`
- `user_message_failed`
- `turn_interrupt_requested`
- `turn_interrupted`
- `steering_pending`

### Gap 6: Non-Streaming Model Calls

Fabric `_run_agent()` calls Copilot with `stream: False`. Tool calls and assistant content are only available after the full response returns. Claude Code can show assistant text and tool-use intent while the model is still streaming.

Fabric can still feel good with milestone events, but it will not feel like Claude Code until at least some model response events are streamed.

### Gap 7: Visual Stream Hierarchy Is Weaker

Fabric has many of the right event types, but the default visual treatment still makes too many events look like equivalent log rows. Claude Code makes the stream easier to read because it assigns visual roles:

- primary assistant/action prose,
- indented subordinate tool output,
- dimmed metadata,
- active markers,
- completed receipts,
- hidden-detail counts,
- local expansion affordances.

Fabric should treat Mission Control's log as a live transcript/timeline, not only as a filtered event list. The UI should visually answer these questions without requiring the user to parse every row:

- What is happening right now?
- What just completed?
- What is waiting on me?
- Which details were hidden, and how much?
- Where is the full evidence if I need it?

## Recommended Fabric Parity Design

### 1. Preserve The Event-Sourced Core

Do not replace the current SSE architecture. It is the right backbone.

Keep:

- monotonic `seq`
- `eventId`
- `payloadDigest`
- `payloadSummary`
- public SQLite event persistence
- trace-only internal events
- SSE replay via `lastEventId`
- `events.json` catch-up polling
- reducer idempotency by sequence number

These are the right primitives for a Fabric iframe workload.

### 2. Add A Live Steering Composer

Mission Control should expose a composer near the live canvas or rail.

Suggested behavior:

- Default target: active specialist if exactly one is running, otherwise broadcast to mission.
- Target menu: active agents, generalist, all agents.
- Submit action: call `sendMessage(sessionId, message, targetAgentSessionId, ...)`.
- Optimistic UI: show `Queued directive` immediately.
- Authoritative UI: replace optimistic row when backend emits `user_message_queued`.
- Delivery UI: show when the target agent consumes the message.

The user should never wonder whether their steering was heard.

### 3. Make Queue Semantics Public

Add backend events around `inject_message()` and `_run_agent()` queue consumption.

Proposed event contract:

```json
{
  "type": "user_message_queued",
  "seq": 101,
  "targetAgentSessionId": "...",
  "targetMode": "agent|broadcast|generalist",
  "messagePreview": "Use the existing lakehouse instead...",
  "queuedAt": "..."
}
```

```json
{
  "type": "user_message_delivered",
  "seq": 108,
  "agentSessionId": "...",
  "messagePreview": "Use the existing lakehouse instead...",
  "deliveredAtRound": 4
}
```

This is the distributed equivalent of Claude Code's internal queue state.

### 4. Add Interrupt Semantics Separately From Queueing

Claude Code distinguishes queueing from interruption. Fabric should too.

Possible controls:

- Send next instruction: queue for the next agent round.
- Interrupt and steer: request cancellation of the current LLM/tool turn when safe, then inject the user's message.
- Stop mission: current existing cancellation behavior.

For safety, interruption should be conservative:

- Interrupt LLM calls by racing the cancel event, which Fabric already partially does.
- Do not interrupt non-idempotent write tools mid-call unless the tool runtime explicitly supports cancellation.
- If a tool cannot be interrupted, emit `turn_interrupt_requested` and deliver the steering message at the next safe boundary.

### 5. Introduce A Mission Roll-Up Layer

Add a roll-up reducer or backend summarizer that watches event bursts and creates durable summary events.

Recommended event type:

```json
{
  "type": "activity_rollup",
  "seq": 150,
  "scope": "run|task|tool_batch|mission",
  "runId": "...",
  "taskId": "...",
  "agentId": "fabric-data-engineer",
  "summary": "Created the lakehouse, wrote the notebook, and validated row counts.",
  "coveredSeqStart": 121,
  "coveredSeqEnd": 149,
  "detailCount": 29,
  "status": "completed|in_progress|failed",
  "logCategory": "high_level"
}
```

This should not delete the covered events. It should sit above them. The UI can show the roll-up by default and keep the detailed events expandable.

### 6. Collapse By Lifecycle Stage

The UI should display detail according to lifecycle:

- Active run: show live detailed updates and high-signal events.
- Recently completed run: show roll-up plus warnings/errors/tool writes.
- Older completed run: show one summary row plus counts and expandable detail.
- Failed run: show failure summary, blocker, last useful events, and recovery/next action.

This mimics Claude Code's pattern: detailed now, summarized past.

### 7. Group Repetitive Tool Activity

Fabric should copy the principle of `collapseReadSearch.ts`, adapted to Fabric tools.

Candidate groups:

- Fabric inventory/list/get calls -> `Inspected workspace items (+N reads)`.
- Definition reads -> `Read report/model definitions (+N files)`.
- Validation calls -> `Validated artifacts (+N checks)`.
- Writes/creates -> keep visible individually or group carefully with artifact names.
- Diagnostics -> group if all passed; keep individual failures visible.

Do not collapse destructive or user-risky operations into vague summaries. For user trust, writes and permissions changes should remain clear.

### 8. Keep A High-Signal Rail Always Visible

Mission Control should preserve a high-level overview even while detail scrolls.

Recommended rail content:

- Current mission status.
- Active agent and active task.
- Last roll-up summary.
- Pending user/approval action.
- Latest warning/error.
- Artifacts created/updated.
- Verifier verdict state.

This rail is the Fabric equivalent of Claude Code's condensed progress line plus transcript summary.

### 9. Redesign The Log As A Visual Transcript

Fabric should adopt Claude Code's visual stream principles in a browser-native way:

- Use a timeline/transcript structure where major actions own their child tool output.
- Keep child tool output visually subordinate through indentation, quieter contrast, and compact terminal blocks.
- Use one consistent state marker system for running, completed, failed, blocked, approval-required, and queued steering states.
- End every substantial tool batch or subagent task with a receipt row: action completed, count of tool calls, artifacts changed, verifier state, duration, and hidden-detail count.
- Keep raw diagnostic metadata available through tooltips, detail drawers, or support export, but do not make it compete with primary prose.
- Animate only the active row or marker; completed rows should become stable.
- Add local expand/collapse for one activity group rather than forcing users to switch the entire log into diagnostic mode.

This is the main visual lesson from Claude Code: the stream should feel composed, not merely appended.

### 10. Consider Streaming Model Responses

For full parity, change `_run_agent()` from `stream: False` to streaming responses, or add a new streaming path for selected agent turns.

Then emit events like:

- `assistant_text_delta`
- `assistant_text_final`
- `assistant_tool_call_delta`
- `assistant_tool_call_ready`

This would let Mission Control show intent before a whole model response completes. It would also let tool execution begin earlier if the tool-call arguments are complete and validated.

This is a larger change than UI roll-ups, so it can come after the steering composer and roll-up events.

## Concrete Implementation Plan

### Phase 1: User Steering Visibility

Backend:

- Emit `user_message_queued` in `inject_message()`.
- Emit `user_message_delivered` when `_run_agent()` drains `user_queue`.
- Include target agent/session IDs and safe message previews.
- Persist both events like other public events.

Frontend:

- Add a Mission Control composer.
- Add target selection from active runtime slots.
- Show queued/delivered messages in the live log.
- Keep optimistic rows until authoritative events arrive.

### Phase 2: High-Level Roll-Ups

Backend or frontend:

- Track covered sequence ranges per run/task/tool batch.
- Emit or synthesize `activity_rollup` events when a task completes, a tool batch completes, or a run advances.
- Mark roll-ups as `high_level`.
- Keep original detail events available.

Frontend:

- Render roll-ups as primary timeline rows.
- Collapse covered detail under the roll-up row by default.
- Keep warnings/errors visible even when covered by a roll-up.

### Phase 3: Better Detail Grouping

Frontend:

- Add grouping logic for repeated read/list/inspect/validate events.
- Show `+N detail events` and an expand affordance.
- Keep create/update/delete/permission events visible unless grouped into a named artifact roll-up.
- Render grouped activity as a visual transcript: parent action, child tool block, compact receipt, local expansion.
- Use consistent running/done/failed/waiting markers across agents, tools, approvals, and verifier tasks.

Backend:

- Add consistent `toolKind` or `operationKind` metadata to tool events if current `toolName` parsing is too brittle.

### Phase 4: Interrupt Semantics

Backend:

- Add an interrupt endpoint or extend `sendMessage` with `mode: "queue" | "interrupt"`.
- Emit `turn_interrupt_requested` and either `turn_interrupted` or `turn_interrupt_deferred`.
- Race LLM calls with the cancel/interrupt signal.
- Defer interruption during unsafe tool calls.

Frontend:

- Provide two controls: send and interrupt/send.
- Show whether the instruction was delivered immediately or queued for the next safe boundary.

### Phase 5: Model Streaming

Backend:

- Introduce a streaming Copilot chat-completions path.
- Emit assistant text deltas and tool-call readiness events.
- Preserve current non-streaming path as fallback.

Frontend:

- Render current assistant text separately from committed log rows.
- Commit final text atomically, following Claude Code's streaming text pattern.

## Design Rules For Fabric Collapse

Use these rules to avoid making the UI less trustworthy while making it easier to read.

1. Collapse detail, not accountability.

Writes, deletes, permission changes, external actions, and approvals should remain visible or be summarized with concrete artifact names.

2. Keep current activity detailed.

The user wants to know what is happening now. Collapse older activity first.

3. Keep failures expanded.

Errors, warnings, verifier failures, and blocked states should remain high-signal even when their surrounding detail is collapsed.

4. Preserve exact replay.

Never replace persisted raw events with summaries. Add summaries above the raw stream.

5. Make counts honest.

If 24 events are hidden, say `+24 detailed events`. This helps the user trust that detail exists.

6. Make expansion local.

Expanding one roll-up should reveal only its covered event range, not turn the whole page into diagnostic mode.

7. Prefer deterministic roll-ups first.

Start with rule-based summaries from event payloads. Use model-generated summaries only after the event structure is stable.

## Current Fabric Strengths To Preserve

- Public events are sequenced and replayable.
- Trace events are separated from user-visible logs.
- Events persist after completion/backend restart.
- The frontend reducer is idempotent by `seq`.
- Tool start/end/progress are already modeled.
- Dynamic generalist/subagent/verifier events are semantically rich.
- `payloadSummary` gives compact support context without dumping raw payloads.
- Mission Control already has category filters and high-signal detection.

## Main Missing Pieces

- Live steering composer in Mission Control.
- Public queue/delivery events for user messages.
- Interrupt versus queue semantics.
- Automatic lifecycle-based collapse of older detail.
- Activity roll-up events or synthesized roll-up rows.
- Grouping for repetitive read/list/diagnostic tool activity.
- Stronger visual stream hierarchy: parent/child transcript layout, dimmed metadata, active markers, completed receipts, and local expansion.
- Optional model streaming for token-level assistant/tool-call visibility.

## Bottom Line

Claude Code's user experience comes from two complementary systems:

1. A live interactive turn loop that accepts steering while work is running and streams what is happening now.
2. A collapse/readability stack that prevents the user from drowning in the raw detail stream.

Fabric Developer Hub already has most of the event plumbing for the second system and some backend primitives for the first. To reach Claude Code parity, Mission Control should become both a control surface and a readability surface: let users steer the mission live, make delivery/queue semantics explicit, and collapse older detailed event bursts into durable high-level summaries while keeping the raw evidence one click away.